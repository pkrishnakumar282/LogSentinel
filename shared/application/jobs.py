"""Durable bounded training queue. Jobs freeze event IDs and never approve models."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import insert, select, update
from shared.pipeline import models as m
from shared.pipeline.database import transaction
from shared.pipeline.store import now


def data_lock(db):
    if db.dialect.name == 'postgresql':
        db.exec_driver_sql('SELECT pg_advisory_xact_lock(741907)')


class JobConflict(ValueError):
    pass


class Jobs:
    def __init__(self, engine):
        self.engine = engine

    def enqueue(self, actor, parameters):
        with transaction(self.engine) as db:
            data_lock(db)
            if db.execute(select(m.training_runs.c.id).where(m.training_runs.c.status.in_(['queued','running']))).first():
                raise JobConflict('training_job_already_active')
            query = select(m.normalized_logs).order_by(m.normalized_logs.c.timestamp, m.normalized_logs.c.event_id)
            if parameters.get('source'):
                query = query.where(m.normalized_logs.c.source == parameters['source'])
            # JSON filtering in Python is portable; bounded input and explicit provenance selection.
            rows = db.execute(query.limit(10001)).mappings().all()
            if len(rows) > 10000:
                raise JobConflict('training_selection_too_large_use_offline_cli')
            ids = [row['event_id'] for row in rows if row['record_metadata'].get('synthetic') is parameters['synthetic']]
            if len(ids) < 40:
                raise JobConflict('training_requires_40_matching_records')
            identity = str(uuid4())
            db.execute(insert(m.training_runs).values(id=identity, status='queued', actor=actor,
                parameters=parameters, event_ids=ids, created_at=now()))
            db.execute(insert(m.audit_events).values(id=str(uuid4()), action='training_enqueue', actor=actor,
                details={'job_id': identity, 'records': len(ids), 'synthetic': parameters['synthetic']}, created_at=now()))
        return self.get(identity)

    def list(self, limit=50, offset=0):
        with self.engine.connect() as db:
            return {'items': [self.public(row) for row in db.execute(select(m.training_runs).order_by(
                m.training_runs.c.created_at.desc()).limit(limit).offset(offset)).mappings()]}

    @staticmethod
    def public(row):
        return {key: row[key] for key in ('id','status','actor','parameters','created_at','started_at','finished_at','result','failure_code')} | {'records': len(row['event_ids'])}

    def get(self, identity):
        with self.engine.connect() as db:
            row = db.execute(select(m.training_runs).where(m.training_runs.c.id == identity)).mappings().first()
            return self.public(row) if row else None

    def claim(self, timeout_seconds=3600):
        with transaction(self.engine) as db:
            data_lock(db)
            # Expired jobs fail closed. No second worker silently retrains the same job.
            db.execute(update(m.training_runs).where(m.training_runs.c.status == 'running',
                m.training_runs.c.lease_until < now()).values(status='failed', failure_code='worker_lease_expired', finished_at=now()))
            row = db.execute(select(m.training_runs).where(m.training_runs.c.status == 'queued')
                .order_by(m.training_runs.c.created_at).limit(1).with_for_update(skip_locked=True)).mappings().first()
            if not row:
                return None
            token = str(uuid4())
            db.execute(update(m.training_runs).where(m.training_runs.c.id == row['id']).values(
                status='running', started_at=now(), lease_until=now() + timedelta(seconds=timeout_seconds + 60), claim_token=token))
            return dict(row) | {'claim_token': token}

    def finish(self, job, result=None, failure=None):
        with transaction(self.engine) as db:
            updated = db.execute(update(m.training_runs).where(m.training_runs.c.id == job['id'],
                m.training_runs.c.status == 'running', m.training_runs.c.claim_token == job['claim_token']).values(
                status='failed' if failure else 'succeeded', result=result, failure_code=failure,
                finished_at=now(), lease_until=None)).rowcount
            if updated:
                db.execute(insert(m.audit_events).values(id=str(uuid4()), action='training_finished', actor='training-worker',
                    details={'job_id': job['id'], 'status': 'failed' if failure else 'succeeded'}, created_at=now()))
            return bool(updated)
