"""Read-only broker probes. Stream length counts retained entries, not unconsumed backlog."""
from shared.pipeline.config import StreamSettings
from shared.pipeline.redis_streams import connect


def status():
    client = None
    try:
        settings = StreamSettings.from_env()
        client = connect(settings)
        with client.pipeline(transaction=False) as pipeline:
            pipeline.ping()
            for name in (settings.normalized_stream,settings.prediction_stream,settings.failed_stream):
                pipeline.xlen(name)
            alive, normalized, predictions, failed = pipeline.execute()
        return {'status':'ready' if alive else 'unavailable', 'retained_entries':{
            'normalized':normalized,'predictions':predictions,'failed':failed},
            'scope':'retained Redis stream entries; not consumer backlog'}
    except Exception:
        return {'status':'unavailable','retained_entries':None}
    finally:
        if client is not None:
            client.close()
