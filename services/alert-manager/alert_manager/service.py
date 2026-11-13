from dataclasses import asdict
from datetime import timedelta
import hashlib
import json
from uuid import uuid4

from sqlalchemy import func, insert, select, update

from shared.common.redaction import redact
from shared.pipeline import models as m
from shared.pipeline.contracts import PredictionNotice, Rejected, Retryable
from shared.pipeline.database import transaction
from shared.pipeline.store import aware, now
from shared.schemas.logs import identifier, utc_time

from .policy import AlertPolicy, LEVELS


class AlertNotFound(ValueError):
    pass


class AlertConflict(ValueError):
    pass


def public(row):
    return {key: aware(value).isoformat() if hasattr(value, 'tzinfo') else value for key, value in row.items()}


def audit(db, alert_id, action, actor, details):
    db.execute(insert(m.audit_events).values(id=str(uuid4()), alert_id=alert_id, action=action,
        actor=actor, details=details, created_at=now()))


class AlertService:
    def __init__(self, store, policy=None):
        self.store, self.policy = store, policy or AlertPolicy()

    def process(self, stage, notice):
        value = self.store.prediction(notice.prediction_id)
        if value is None:
            raise Retryable('prediction_reference_missing')
        prediction, event = value
        if event.event_id != notice.event_id:
            raise Rejected('prediction_notice_mismatch')
        digest = hashlib.sha256(json.dumps(notice.to_dict(), sort_keys=True).encode()).hexdigest()
        with transaction(self.store.engine) as db:
            previous = db.execute(select(m.receipts).where(m.receipts.c.stage == stage,
                m.receipts.c.event_key == notice.prediction_id)).mappings().first()
            if previous:
                if previous['fingerprint'] != digest:
                    raise Rejected('prediction_notice_conflict')
                return 'duplicate'
            outcome = self.policy.eligibility(prediction)
            reference = None
            if outcome == 'eligible':
                group = self.policy.group_key(prediction, event)
                if self.store.engine.dialect.name == 'postgresql':
                    lock_id = int.from_bytes(bytes.fromhex(group[:16]), 'big', signed=True)
                    db.execute(select(func.pg_advisory_xact_lock(lock_id)))
                event_at = utc_time(event.timestamp)
                window = timedelta(seconds=self.policy.group_window_seconds)
                existing = db.execute(select(m.alerts).where(m.alerts.c.group_key == group,
                    m.alerts.c.status.in_(['open', 'acknowledged']),
                    m.alerts.c.first_event_at <= event_at + window,
                    m.alerts.c.last_event_at >= event_at - window).order_by(m.alerts.c.last_event_at.desc())
                    .limit(1).with_for_update()).mappings().first()
                severity, priority = self.policy.level(prediction, event)
                if existing:
                    reference = existing['id']
                    if LEVELS.index(existing['severity']) > LEVELS.index(severity):
                        severity, priority = existing['severity'], existing['priority']
                    db.execute(update(m.alerts).where(m.alerts.c.id == reference).values(
                        occurrence_count=existing['occurrence_count'] + 1, severity=severity, priority=priority,
                        first_event_at=min(aware(existing['first_event_at']), event_at),
                        last_event_at=max(aware(existing['last_event_at']), event_at), updated_at=now()))
                    audit(db, reference, 'recurred', 'alert-worker', {'prediction_id': notice.prediction_id,
                        'occurrence_count': existing['occurrence_count'] + 1, 'severity': severity})
                    outcome = 'alert_grouped'
                else:
                    reference = str(uuid4())
                    db.execute(insert(m.alerts).values(id=reference, group_key=group,
                        prediction_id=notice.prediction_id, severity=severity, priority=priority, status='open',
                        occurrence_count=1, first_event_at=event_at, last_event_at=event_at,
                        created_at=now(), updated_at=now()))
                    audit(db, reference, 'created', 'alert-worker', {'prediction_id': notice.prediction_id,
                                                                  'severity': severity, 'policy': asdict(self.policy)})
                    outcome = 'alert_created'
                db.execute(insert(m.occurrences).values(prediction_id=notice.prediction_id,
                    alert_id=reference, event_at=event_at))
            db.execute(insert(m.receipts).values(stage=stage, event_key=notice.prediction_id,
                fingerprint=digest, outcome=outcome, reference_id=reference, created_at=now()))
            return outcome

    def list(self, severity=None, status=None, limit=50, offset=0):
        if severity is not None and severity not in LEVELS:
            raise ValueError('invalid_alert_severity')
        if status is not None and status not in ('open', 'acknowledged', 'resolved'):
            raise ValueError('invalid_alert_status')
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError('invalid_alert_pagination')
        clauses = []
        if severity:
            clauses.append(m.alerts.c.severity == severity)
        if status:
            clauses.append(m.alerts.c.status == status)
        with self.store.engine.connect() as db:
            total = db.execute(select(func.count()).select_from(m.alerts).where(*clauses)).scalar_one()
            rows = db.execute(select(m.alerts).where(*clauses).order_by(m.alerts.c.updated_at.desc(),
                m.alerts.c.id).limit(limit).offset(offset)).mappings().all()
        return {'items': [public(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}

    def detail(self, identity):
        with self.store.engine.connect() as db:
            row = db.execute(select(m.alerts).where(m.alerts.c.id == identity)).mappings().first()
        if row is None:
            raise AlertNotFound('alert_not_found')
        result = public(row)
        prediction, event = self.store.prediction(row['prediction_id'])
        result['event'] = event.to_dict()
        result['prediction'] = public(prediction)
        return result

    def history(self, identity):
        self.detail(identity)
        with self.store.engine.connect() as db:
            return [public(row) for row in db.execute(select(m.audit_events).where(
                m.audit_events.c.alert_id == identity).order_by(m.audit_events.c.created_at,
                m.audit_events.c.id)).mappings()]

    def transition(self, identity, action, actor, note=''):
        identifier(actor)
        if action not in ('acknowledge', 'resolve'):
            raise AlertConflict('invalid_alert_action')
        target = 'acknowledged' if action == 'acknowledge' else 'resolved'
        with transaction(self.store.engine) as db:
            row = db.execute(select(m.alerts).where(m.alerts.c.id == identity).with_for_update()).mappings().first()
            if row is None:
                raise AlertNotFound('alert_not_found')
            if row['status'] == target:
                return public(row)
            if row['status'] == 'resolved':
                raise AlertConflict('resolved_alert_cannot_be_acknowledged')
            changed = now()
            values = {'status': target, 'updated_at': changed,
                      'acknowledged_at' if target == 'acknowledged' else 'resolved_at': changed}
            db.execute(update(m.alerts).where(m.alerts.c.id == identity).values(**values))
            audit(db, identity, action, actor, {'from': row['status'], 'to': target, 'note': redact(note)[:256]})
            return public(dict(row) | values)


class AlertHandler:
    def __init__(self, service, stage):
        self.service, self.stage = service, stage

    def __call__(self, delivery):
        return self.service.process(self.stage, PredictionNotice.parse(delivery.payload()))
