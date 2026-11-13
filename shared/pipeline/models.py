"""Application and durable pipeline tables. Alembic owns production schema creation/evolution."""
from sqlalchemy import (JSON, Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey,
                        Index, Integer, MetaData, String, Table, Text, UniqueConstraint)

metadata = MetaData(naming_convention={'ix': 'ix_%(table_name)s_%(column_0_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s', 'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s', 'ck': 'ck_%(table_name)s_%(constraint_name)s'})
normalized_logs = Table('normalized_logs', metadata,
    Column('event_id', String(36), primary_key=True), Column('timestamp', DateTime(timezone=True), nullable=False),
    Column('source', String(16), nullable=False), Column('service', String(128), nullable=False),
    Column('hostname', String(128), nullable=False), Column('severity', String(16), nullable=False),
    Column('message', Text, nullable=False), Column('template', Text, nullable=False),
    Column('status_code', Integer), Column('record_metadata', JSON, nullable=False),
    Column('fingerprint', String(64), nullable=False), Column('created_at', DateTime(timezone=True), nullable=False),
    CheckConstraint('status_code IS NULL OR (status_code >= 100 AND status_code <= 599)', name='http_status'))
Index('ix_normalized_logs_time_source', normalized_logs.c.timestamp, normalized_logs.c.source)
model_versions = Table('model_versions', metadata,
    Column('id', String(64), primary_key=True), Column('name', String(128), nullable=False),
    Column('version', String(32), nullable=False), Column('package_sha256', String(64), nullable=False),
    Column('threshold', Float, nullable=False), Column('threshold_source', String(32), nullable=False),
    Column('scope', String(16), nullable=False), Column('feature_version', String(32), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False))
predictions = Table('predictions', metadata,
    Column('id', String(36), primary_key=True),
    Column('event_id', String(36), ForeignKey('normalized_logs.event_id'), nullable=False),
    Column('model_id', String(64), ForeignKey('model_versions.id'), nullable=False),
    Column('anomaly_score', Float, nullable=False), Column('is_anomaly', Boolean, nullable=False),
    Column('context_warm', Boolean, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('event_id', 'model_id'))
Index('ix_predictions_anomaly_created', predictions.c.is_anomaly, predictions.c.created_at)
checkpoints = Table('worker_checkpoints', metadata,
    Column('stage', String(160), primary_key=True), Column('revision', Integer, nullable=False),
    Column('state', JSON, nullable=False), Column('updated_at', DateTime(timezone=True), nullable=False))
receipts = Table('processing_receipts', metadata,
    Column('stage', String(160), primary_key=True), Column('event_key', String(80), primary_key=True),
    Column('fingerprint', String(64), nullable=False), Column('outcome', String(32), nullable=False),
    Column('revision', Integer), Column('reference_id', String(80)),
    Column('created_at', DateTime(timezone=True), nullable=False))
Index('ix_receipts_stage_revision', receipts.c.stage, receipts.c.revision)
outbox = Table('outbox_events', metadata,
    Column('id', String(36), primary_key=True),
    Column('prediction_id', String(36), ForeignKey('predictions.id'), nullable=False, unique=True),
    Column('payload', JSON, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False),
    Column('published_at', DateTime(timezone=True)), Column('lease_token', String(36)),
    Column('lease_until', DateTime(timezone=True)), Column('attempts', Integer, nullable=False, default=0))
Index('ix_outbox_pending', outbox.c.published_at, outbox.c.lease_until)
failures = Table('failed_messages', metadata,
    Column('id', String(64), primary_key=True), Column('stage', String(160), nullable=False),
    Column('stream', String(128), nullable=False), Column('entry_id', String(64), nullable=False),
    Column('reason', String(80), nullable=False), Column('attempts', Integer, nullable=False),
    Column('event_id', String(36)), Column('created_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('stage', 'entry_id'))
alerts = Table('alerts', metadata,
    Column('id', String(36), primary_key=True), Column('group_key', String(64), nullable=False),
    Column('prediction_id', String(36), ForeignKey('predictions.id'), nullable=False),
    Column('severity', String(16), nullable=False), Column('priority', Integer, nullable=False),
    Column('status', String(16), nullable=False), Column('occurrence_count', Integer, nullable=False),
    Column('first_event_at', DateTime(timezone=True), nullable=False),
    Column('last_event_at', DateTime(timezone=True), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False), Column('updated_at', DateTime(timezone=True), nullable=False),
    Column('acknowledged_at', DateTime(timezone=True)), Column('resolved_at', DateTime(timezone=True)),
    CheckConstraint("status IN ('open','acknowledged','resolved')", name='status'),
    CheckConstraint("severity IN ('INFO','WARNING','HIGH','CRITICAL')", name='severity'))
Index('ix_alerts_group_status_time', alerts.c.group_key, alerts.c.status, alerts.c.last_event_at)
Index('ix_alerts_status_severity', alerts.c.status, alerts.c.severity)
occurrences = Table('alert_occurrences', metadata,
    Column('prediction_id', String(36), ForeignKey('predictions.id'), primary_key=True),
    Column('alert_id', String(36), ForeignKey('alerts.id'), nullable=False),
    Column('event_at', DateTime(timezone=True), nullable=False))
audit_events = Table('audit_events', metadata,
    Column('id', String(36), primary_key=True), Column('alert_id', String(36), ForeignKey('alerts.id')),
    Column('action', String(32), nullable=False), Column('actor', String(128), nullable=False),
    Column('details', JSON, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False))
Index('ix_audit_alert_time', audit_events.c.alert_id, audit_events.c.created_at)

# Provenance only: canonical message/template remain exclusively in normalized_logs.
data_sources = Table('data_sources', metadata,
    Column('id', String(64), primary_key=True), Column('source', String(16), nullable=False),
    Column('hostname', String(128), nullable=False), Column('total_records', Integer, nullable=False),
    Column('first_seen', DateTime(timezone=True), nullable=False),
    Column('last_seen', DateTime(timezone=True), nullable=False),
    UniqueConstraint('source', 'hostname'))
log_events = Table('log_events', metadata,
    Column('event_id', String(36), primary_key=True),
    Column('source_id', String(64), ForeignKey('data_sources.id'), nullable=False),
    Column('normalized_event_id', String(36), ForeignKey('normalized_logs.event_id', ondelete='SET NULL'), unique=True),
    Column('observed_at', DateTime(timezone=True), nullable=False),
    Column('received_at', DateTime(timezone=True), nullable=False))
Index('ix_log_events_received', log_events.c.received_at)
training_runs = Table('training_runs', metadata,
    Column('id', String(36), primary_key=True), Column('status', String(16), nullable=False),
    Column('actor', String(128), nullable=False), Column('parameters', JSON, nullable=False),
    Column('event_ids', JSON, nullable=False), Column('result', JSON), Column('failure_code', String(80)),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('started_at', DateTime(timezone=True)), Column('finished_at', DateTime(timezone=True)),
    Column('lease_until', DateTime(timezone=True)), Column('claim_token', String(36)),
    CheckConstraint("status IN ('queued','running','succeeded','failed')", name='status'))
Index('ix_training_runs_status_created', training_runs.c.status, training_runs.c.created_at)

Index('ix_predictions_event_created', predictions.c.event_id, predictions.c.created_at, predictions.c.id)
Index('ix_receipts_event_outcome', receipts.c.event_key, receipts.c.outcome)
