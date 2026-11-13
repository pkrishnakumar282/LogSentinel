"""Durable predictions/checkpoints/outbox: a single transaction precedes every input ACK."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import and_, func, insert, select, update

from shared.common.publishing import fingerprint
from shared.schemas import NormalizedLog
from shared.schemas.logs import utc_time

from . import models as m
from .contracts import Deferred, PredictionNotice, Rejected, StaleCheckpoint
from .database import make_engine, transaction


def now():
    return datetime.now(UTC)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalized(row):
    return NormalizedLog(event_id=row['event_id'], timestamp=aware(row['timestamp']).isoformat(),
        source=row['source'], service=row['service'], hostname=row['hostname'], severity=row['severity'],
        message=row['message'], template=row['template'], status_code=row['status_code'], metadata=row['record_metadata'])


class PipelineStore:
    def __init__(self, engine=None, outbox_capacity: int = 10000):
        self.engine = engine or make_engine()
        self.outbox_capacity = outbox_capacity

    def initialize_development(self):
        if self.engine.dialect.name != 'sqlite':
            raise ValueError('use_alembic_for_postgres')
        m.metadata.create_all(self.engine)

    def ping(self):
        with self.engine.connect() as db:
            db.execute(select(m.checkpoints.c.stage).limit(1))
        return True

    def receipt(self, stage: str, event_key: str):
        with self.engine.connect() as db:
            return db.execute(select(m.receipts).where(m.receipts.c.stage == stage,
                m.receipts.c.event_key == event_key)).mappings().first()

    def checkpoint(self, stage: str):
        with self.engine.connect() as db:
            row = db.execute(select(m.checkpoints).where(m.checkpoints.c.stage == stage)).mappings().first()
        return dict(row) if row else {'revision': 0, 'state': {}}

    def history(self, stage: str, checkpoint: dict, window_seconds: float):
        state = checkpoint['state']
        if not state:
            return []
        last = utc_time(state['last_timestamp'])
        query = select(m.normalized_logs).join(m.receipts,
            m.normalized_logs.c.event_id == m.receipts.c.event_key).where(
            m.receipts.c.stage == stage, m.receipts.c.outcome == 'predicted',
            m.receipts.c.revision <= checkpoint['revision'],
            m.normalized_logs.c.timestamp > last - timedelta(seconds=window_seconds),
            m.normalized_logs.c.timestamp <= last).order_by(m.receipts.c.revision)
        with self.engine.connect() as db:
            return [normalized(row) for row in db.execute(query).mappings()]

    def _insert_event(self, db, event):
        from shared.application.jobs import data_lock
        data_lock(db)
        existing = db.execute(select(m.normalized_logs.c.fingerprint).where(
            m.normalized_logs.c.event_id == event.event_id)).scalar_one_or_none()
        digest = fingerprint(event)
        if existing is not None:
            if existing != digest:
                raise Rejected('event_id_conflict')
            return
        db.execute(insert(m.normalized_logs).values(event_id=event.event_id,
            timestamp=utc_time(event.timestamp), source=event.source, service=event.service,
            hostname=event.hostname, severity=event.severity, message=event.message, template=event.template,
            status_code=event.status_code, record_metadata=event.metadata, fingerprint=digest, created_at=now()))

        from shared.application.provenance import record_event
        record_event(db, event)

    def save_prediction(self, stage, event, prediction, loaded, state, expected_revision):
        config = {'name': loaded.name, 'version': loaded.version, 'package': loaded.package_sha256,
                  'threshold': prediction['threshold'], 'threshold_source': prediction['threshold_source'],
                  'feature_version': prediction['feature_version'], 'scope': loaded.scope}
        model_id = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        prediction_id = str(uuid5(NAMESPACE_URL, f'logsentinel:prediction:{event.event_id}:{model_id}'))
        notice = PredictionNotice(prediction_id, event.event_id)
        with transaction(self.engine) as db:
            previous = db.execute(select(m.receipts).where(m.receipts.c.stage == stage,
                                  m.receipts.c.event_key == event.event_id)).mappings().first()
            if previous:
                if previous['fingerprint'] != fingerprint(event):
                    raise Rejected('event_id_conflict')
                return {'status': 'duplicate', 'prediction_id': previous['reference_id']}
            pending = db.execute(select(func.count()).select_from(m.outbox).where(m.outbox.c.published_at.is_(None))).scalar_one()
            if pending >= self.outbox_capacity:
                raise Deferred('outbox_capacity_reached')
            checkpoint = db.execute(select(m.checkpoints).where(m.checkpoints.c.stage == stage).with_for_update()).mappings().first()
            actual_revision = checkpoint['revision'] if checkpoint else 0
            if actual_revision != expected_revision:
                raise StaleCheckpoint('checkpoint_changed')
            self._insert_event(db, event)
            if db.execute(select(m.model_versions.c.id).where(m.model_versions.c.id == model_id)).first() is None:
                db.execute(insert(m.model_versions).values(id=model_id, name=loaded.name, version=loaded.version,
                    package_sha256=loaded.package_sha256, threshold=prediction['threshold'],
                    threshold_source=prediction['threshold_source'], scope=loaded.scope,
                    feature_version=prediction['feature_version'], created_at=now()))
            db.execute(insert(m.predictions).values(id=prediction_id, event_id=event.event_id, model_id=model_id,
                anomaly_score=prediction['anomaly_score'], is_anomaly=prediction['is_anomaly'],
                context_warm=prediction['context_warm'], created_at=now()))
            db.execute(insert(m.outbox).values(id=prediction_id, prediction_id=prediction_id,
                payload=notice.to_dict(), created_at=now(), attempts=0))
            revision = actual_revision + 1
            db.execute(insert(m.receipts).values(stage=stage, event_key=event.event_id,
                fingerprint=fingerprint(event), outcome='predicted', revision=revision,
                reference_id=prediction_id, created_at=now()))
            if checkpoint:
                changed = db.execute(update(m.checkpoints).where(m.checkpoints.c.stage == stage,
                    m.checkpoints.c.revision == expected_revision).values(revision=revision,
                    state=state, updated_at=now())).rowcount
                if changed != 1:
                    raise StaleCheckpoint('checkpoint_changed')
            else:
                db.execute(insert(m.checkpoints).values(stage=stage, revision=revision, state=state, updated_at=now()))
        return {'status': 'predicted', 'prediction_id': prediction_id, 'revision': revision}

    def claim_outbox(self, limit=100, lease_seconds=30):
        claimed = []
        with transaction(self.engine) as db:
            current = now()
            rows = db.execute(select(m.outbox).where(m.outbox.c.published_at.is_(None),
                (m.outbox.c.lease_until.is_(None) | (m.outbox.c.lease_until <= current)))
                .order_by(m.outbox.c.created_at, m.outbox.c.id).limit(limit).with_for_update(skip_locked=True)).mappings().all()
            for row in rows:
                token = str(uuid4())
                db.execute(update(m.outbox).where(m.outbox.c.id == row['id']).values(
                    lease_token=token, lease_until=current + timedelta(seconds=lease_seconds),
                    attempts=row['attempts'] + 1))
                claimed.append(dict(row) | {'lease_token': token})
        return claimed

    def finish_outbox(self, identity: str, token: str, success: bool):
        with transaction(self.engine) as db:
            changes = {'lease_until': None, 'lease_token': None}
            if success:
                changes['published_at'] = now()
            db.execute(update(m.outbox).where(m.outbox.c.id == identity,
                m.outbox.c.lease_token == token).values(**changes))

    def failure(self, stage, entry_id):
        with self.engine.connect() as db:
            return db.execute(select(m.failures).where(m.failures.c.stage == stage,
                m.failures.c.entry_id == entry_id)).mappings().first()

    def record_failure(self, stage, stream, delivery, reason, attempts, event=None):
        identity = hashlib.sha256(f'{stage}:{delivery.entry_id}'.encode()).hexdigest()
        with transaction(self.engine) as db:
            if db.execute(select(m.failures.c.id).where(m.failures.c.id == identity)).first():
                return identity
            if event is not None:
                # Only callers that already validated/redacted the event may supply it.
                try:
                    self._insert_event(db, event)
                except Rejected:
                    pass  # A conflicting ID never replaces the original canonical event.
            db.execute(insert(m.failures).values(id=identity, stage=stage, stream=stream,
                entry_id=delivery.entry_id, reason=reason, attempts=attempts,
                event_id=event.event_id if event else None, created_at=now()))
        return identity

    def prediction(self, identity):
        query = select(m.predictions, m.model_versions.c.name, m.model_versions.c.version,
            m.model_versions.c.threshold, m.model_versions.c.threshold_source, m.model_versions.c.scope,
            m.model_versions.c.feature_version).join(m.model_versions, m.predictions.c.model_id == m.model_versions.c.id)
        with self.engine.connect() as db:
            row = db.execute(query.where(m.predictions.c.id == identity)).mappings().first()
            if row is None:
                return None
            event_row = db.execute(select(m.normalized_logs).where(
                m.normalized_logs.c.event_id == row['event_id'])).mappings().one()
            return dict(row), normalized(event_row)
