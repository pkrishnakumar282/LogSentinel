"""Idempotent provenance in the same transaction as the canonical event."""
import hashlib
from sqlalchemy import insert, select, update
from shared.pipeline import models as m
from shared.pipeline.store import now


def record_event(db, event):
    identity = hashlib.sha256(f'{event.source}:{event.hostname}'.encode()).hexdigest()
    # PostgreSQL serializes updates for a source, including the first insert.
    if db.dialect.name == 'postgresql':
        db.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)', (int(identity[:15], 16),))
    if db.execute(select(m.log_events.c.event_id).where(m.log_events.c.event_id == event.event_id)).first():
        return
    current = now()
    exists = db.execute(select(m.data_sources.c.id).where(m.data_sources.c.id == identity)).first()
    if exists:
        db.execute(update(m.data_sources).where(m.data_sources.c.id == identity).values(
            total_records=m.data_sources.c.total_records + 1, last_seen=current))
    else:
        db.execute(insert(m.data_sources).values(id=identity, source=event.source,
            hostname=event.hostname, total_records=1, first_seen=current, last_seen=current))
    from shared.schemas.logs import utc_time
    db.execute(insert(m.log_events).values(event_id=event.event_id, source_id=identity,
        normalized_event_id=event.event_id, observed_at=utc_time(event.timestamp), received_at=current))
