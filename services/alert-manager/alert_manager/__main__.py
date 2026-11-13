import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description='LogSentinel alert API, consumer and outbox relay')
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8014)
    worker = sub.add_parser('worker')
    worker.add_argument('--once', action='store_true')
    relay = sub.add_parser('relay')
    relay.add_argument('--once', action='store_true')
    sub.add_parser('health')
    args = parser.parse_args()
    if args.command == 'health':
        from shared.pipeline.runtime import healthy
        return 0 if healthy(os.environ.get('WORKER_HEARTBEAT_PATH', '.local/worker-health.json')) else 1
    try:
        if args.command == 'serve':
            import uvicorn
            uvicorn.run('alert_manager.api:create_app', factory=True, host=args.host, port=args.port,
                        workers=1, limit_concurrency=32, access_log=False)
            return 0
        from shared.pipeline.config import StreamSettings
        from shared.pipeline.store import PipelineStore
        from shared.pipeline.redis_streams import RedisGroup, RedisJSONPublisher, connect
        from shared.pipeline.worker import OutboxRelay, StreamWorker
        from shared.pipeline.runtime import run
        from .service import AlertHandler, AlertService
        from .policy import AlertPolicy
        settings = StreamSettings.from_env()
        store = PipelineStore(outbox_capacity=settings.outbox_capacity)
        client = connect(settings)
        if args.command == 'relay':
            import time
            publisher = RedisJSONPublisher(client, settings.prediction_stream, settings)
            outbox = OutboxRelay(store, publisher)
            while True:
                from shared.pipeline.runtime import heartbeat
                count = outbox.flush()
                heartbeat(os.environ.get('WORKER_HEARTBEAT_PATH', '.local/worker-health.json'), 'ready')
                print(json.dumps({'published': count}), flush=True)
                if args.once:
                    return 0
                time.sleep(1)
        broker = RedisGroup(client, settings.prediction_stream, settings.alert_group, settings)
        handler = AlertHandler(AlertService(store, AlertPolicy.from_env()), broker.stage)
        return run(StreamWorker(broker, store, handler), store, once=args.once)
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('{"status":"unavailable","reason":"alert_dependency_or_configuration"}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
