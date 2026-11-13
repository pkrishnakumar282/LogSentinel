"""Explicit, conservative retention. Recovery references and idempotency receipts survive."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import delete, insert, select
from shared.pipeline import models as m
from shared.pipeline.database import transaction
from shared.pipeline.store import now
from .jobs import data_lock


def retain(engine, days, actor, dry_run=True, limit=500):
    if not 1 <= days <= 3650 or not 1 <= limit <= 5000:
        raise ValueError('invalid_retention_configuration')
    with transaction(engine) as db:
        data_lock(db)
        protected = set()
        for ids in db.execute(select(m.training_runs.c.event_ids).where(m.training_runs.c.status.in_(['queued','running']))).scalars():
            protected.update(ids)
        # Stream history compaction must coordinate every consumer and checkpoint. Until then,
        # scored and archived-stream events survive, including unscored pending inference input.
        n = m.normalized_logs
        prediction = select(m.predictions.c.id).where(m.predictions.c.event_id == n.c.event_id).exists()
        receipt = select(m.receipts.c.event_key).where(m.receipts.c.event_key == n.c.event_id,
            m.receipts.c.outcome.in_(['predicted','archived'])).exists()
        query = select(n.c.event_id).where(n.c.created_at < now() - timedelta(days=days), ~prediction, ~receipt)
        candidates = []
        scanned = 0
        for identity in db.execute(query.order_by(n.c.created_at,n.c.event_id).limit(10000)).scalars():
            scanned += 1
            if identity not in protected:
                candidates.append(identity)
            if len(candidates) >= limit:
                break
        if not dry_run and candidates:
            db.execute(delete(n).where(n.c.event_id.in_(candidates)))
        db.execute(insert(m.audit_events).values(id=str(uuid4()), action='retention_preview' if dry_run else 'retention_apply',
            actor=actor, details={'days': days, 'eligible': len(candidates), 'scanned': scanned}, created_at=now()))
        return {'dry_run': dry_run, 'eligible': len(candidates), 'deleted': 0 if dry_run else len(candidates),
                'scanned': scanned, 'scan_limit': 10000, 'policy': 'all predictions, archived stream events, checkpoint receipts, alerts and active training snapshots preserved'}
