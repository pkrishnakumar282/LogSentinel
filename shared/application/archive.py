"""Independent normalized-log archive consumer: works without an approved ML model."""
from dataclasses import replace
from uuid import UUID
from sqlalchemy import insert, select
from shared.common.privacy import is_authentication_record
from shared.common.publishing import fingerprint
from shared.common.redaction import redact
from shared.pipeline import models as m
from shared.pipeline.contracts import Rejected
from shared.pipeline.database import transaction
from shared.pipeline.store import now
from shared.schemas import NormalizedLog
from shared.schemas.logs import utc_time
from .jobs import data_lock


class ArchiveHandler:
    def __init__(self, store, stage):
        self.store, self.stage = store, stage
        self.current_event = None

    def __call__(self, delivery):
        self.current_event = None
        try:
            event = NormalizedLog(**delivery.payload())
            if is_authentication_record(event.service, event.message) or type(event.metadata.get('synthetic',False)) is not bool:
                raise ValueError
            event = replace(event,event_id=str(UUID(event.event_id)),timestamp=utc_time(event.timestamp).isoformat(),
                message=redact(event.message),template=redact(event.template),metadata={
                    'synthetic':event.metadata.get('synthetic',False),'redaction_version':'1'})
        except (ValueError,TypeError,AttributeError):
            raise Rejected('invalid_normalized_event') from None
        self.current_event = event
        with transaction(self.store.engine) as db:
            data_lock(db)
            previous = db.execute(select(m.receipts).where(m.receipts.c.stage == self.stage,
                m.receipts.c.event_key == event.event_id)).mappings().first()
            if previous:
                if previous['fingerprint'] != fingerprint(event):
                    raise Rejected('event_id_conflict')
                return 'duplicate'
            self.store._insert_event(db,event)
            db.execute(insert(m.receipts).values(stage=self.stage,event_key=event.event_id,
                fingerprint=fingerprint(event),outcome='archived',created_at=now()))
        return 'archived'


def main():
    import os
    from shared.pipeline.config import StreamSettings
    from shared.pipeline.redis_streams import RedisGroup, connect
    from shared.pipeline.runtime import run
    from shared.pipeline.store import PipelineStore
    from shared.pipeline.worker import StreamWorker
    settings = StreamSettings.from_env()
    store = PipelineStore()
    client = connect(settings)
    # Register both readers before either may trim entries acknowledged only by itself.
    RedisGroup(client,settings.normalized_stream,settings.inference_group,settings).initialize()
    group = RedisGroup(client,settings.normalized_stream,
        os.environ.get('ARCHIVE_CONSUMER_GROUP','archive-v1'),settings)
    raise SystemExit(run(StreamWorker(group,store,ArchiveHandler(store,group.stage)),store))


if __name__ == '__main__':
    main()
