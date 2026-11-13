"""Application provenance and durable asynchronous training jobs."""
from alembic import op
import sqlalchemy as sa
revision = "0002_application"
down_revision = "0001_pipeline"
branch_labels = None
depends_on = None

def upgrade():
    # Frozen revision-local DDL.
    op.create_table('data_sources',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('hostname', sa.String(length=128), nullable=False),
    sa.Column('total_records', sa.Integer(), nullable=False),
    sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_sources')),
    sa.UniqueConstraint('source', 'hostname', name=op.f('uq_data_sources_source'))
    )
    op.create_table('training_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('actor', sa.String(length=128), nullable=False),
    sa.Column('parameters', sa.JSON(), nullable=False),
    sa.Column('event_ids', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('failure_code', sa.String(length=80), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('claim_token', sa.String(length=36), nullable=True),
    sa.CheckConstraint("status IN ('queued','running','succeeded','failed')", name=op.f('ck_training_runs_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_training_runs'))
    )
    op.create_index('ix_training_runs_status_created', 'training_runs', ['status', 'created_at'], unique=False)
    op.create_table('log_events',
    sa.Column('event_id', sa.String(length=36), nullable=False),
    sa.Column('source_id', sa.String(length=64), nullable=False),
    sa.Column('normalized_event_id', sa.String(length=36), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['normalized_event_id'], ['normalized_logs.event_id'], name=op.f('fk_log_events_normalized_event_id_normalized_logs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['data_sources.id'], name=op.f('fk_log_events_source_id_data_sources')),
    sa.PrimaryKeyConstraint('event_id', name=op.f('pk_log_events')),
    sa.UniqueConstraint('normalized_event_id', name=op.f('uq_log_events_normalized_event_id'))
    )
    op.create_index('ix_log_events_received', 'log_events', ['received_at'], unique=False)
    op.create_index('ix_predictions_event_created', 'predictions', ['event_id','created_at','id'])
    op.create_index('ix_receipts_event_outcome', 'processing_receipts', ['event_key','outcome'])
    _backfill_provenance()

def _backfill_provenance():
    # Frozen revision-local tables: historical migrations never import mutable app metadata.
    import hashlib
    db = op.get_bind()
    logs = sa.table("normalized_logs", sa.column("event_id", sa.String()),
        sa.column("source", sa.String()), sa.column("hostname", sa.String()),
        sa.column("timestamp", sa.DateTime(timezone=True)), sa.column("created_at", sa.DateTime(timezone=True)))
    sources = sa.table("data_sources", sa.column("id"), sa.column("source"), sa.column("hostname"),
        sa.column("total_records", sa.Integer()), sa.column("first_seen", sa.DateTime(timezone=True)),
        sa.column("last_seen", sa.DateTime(timezone=True)))
    events = sa.table("log_events", sa.column("event_id"), sa.column("source_id"),
        sa.column("normalized_event_id"), sa.column("observed_at", sa.DateTime(timezone=True)),
        sa.column("received_at", sa.DateTime(timezone=True)))
    for source, host, count, first, last in db.execute(sa.select(logs.c.source, logs.c.hostname,
            sa.func.count(), sa.func.min(logs.c.created_at), sa.func.max(logs.c.created_at)).group_by(logs.c.source, logs.c.hostname)):
        identity = hashlib.sha256(f"{source}:{host}".encode()).hexdigest()
        db.execute(sources.insert().values(id=identity, source=source, hostname=host, total_records=count, first_seen=first, last_seen=last))
    for row in db.execute(sa.select(logs)).mappings():
        identity = hashlib.sha256(f"{row['source']}:{row['hostname']}".encode()).hexdigest()
        db.execute(events.insert().values(event_id=row["event_id"], source_id=identity, normalized_event_id=row["event_id"], observed_at=row["timestamp"], received_at=row["created_at"]))

def downgrade():
    op.drop_index('ix_receipts_event_outcome', table_name='processing_receipts')
    op.drop_index('ix_predictions_event_created', table_name='predictions')
    # Frozen revision-local DDL.
    op.drop_index('ix_log_events_received', table_name='log_events')
    op.drop_table('log_events')
    op.drop_index('ix_training_runs_status_created', table_name='training_runs')
    op.drop_table('training_runs')
    op.drop_table('data_sources')

