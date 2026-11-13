import argparse
import json
import os
from pathlib import Path
import sys

from shared.schemas import InvalidRecord, NormalizedLog

from .config import InferenceSettings
from .engine import InferenceEngine


def main() -> int:
    parser = argparse.ArgumentParser(description='Approved-version inference service and local checks')
    sub = parser.add_subparsers(dest='command', required=True)
    worker = sub.add_parser('worker', help='Consume normalized Redis logs with durable prediction/outbox commits')
    worker.add_argument('--once', action='store_true')
    sub.add_parser('check', help='Load the configured approved model and print health; no token needed')
    predict = sub.add_parser('predict', help='Score normalized JSONL with one ordered local context')
    predict.add_argument('--input', required=True)
    predict.add_argument('--context-id', default='cli')
    serve = sub.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=int(os.environ.get('INFERENCE_PORT', '8013')))
    args = parser.parse_args()
    try:
        settings = InferenceSettings.from_env()
        if args.command == 'serve':
            import uvicorn
            uvicorn.run('ml_inference.api:create_app', factory=True, host=args.host, port=args.port,
                        workers=1, access_log=False, limit_concurrency=32, timeout_keep_alive=5)
            return 0
        engine = InferenceEngine(settings)
        if args.command == 'worker':
            from shared.pipeline.config import StreamSettings
            from shared.pipeline.redis_streams import RedisGroup, RedisJSONPublisher, connect
            from shared.pipeline.runtime import run
            from shared.pipeline.store import PipelineStore
            from shared.pipeline.worker import OutboxRelay, StreamWorker
            from .stream_worker import InferenceHandler
            streams = StreamSettings.from_env()
            store = PipelineStore(outbox_capacity=streams.outbox_capacity)
            client = connect(streams)
            broker = RedisGroup(client, streams.normalized_stream, streams.inference_group, streams)
            relay = OutboxRelay(store, RedisJSONPublisher(client, streams.prediction_stream, streams))
            worker = StreamWorker(broker, store, InferenceHandler(engine, store, broker))
            return run(worker, store, relay=relay, engine=engine, once=args.once)
        engine.reload()
        if args.command == 'check':
            print(json.dumps(engine.health(), indent=2))
            return 0 if engine.health()['status'] == 'ready' else 1
        if engine.health()['status'] != 'ready':
            print('{"detail":"approved_model_unavailable"}', file=sys.stderr)
            return 1
        with Path(args.input).open() as stream:
            while True:
                line = stream.readline(400001)
                if not line:
                    break
                if len(line) > 400000:
                    raise InvalidRecord('input_line_too_large')
                event = NormalizedLog(**json.loads(line))
                print(json.dumps(engine.predict([event], args.context_id)[0]))
        return 0
    except Exception:
        print('{"detail":"inference_operation_failed"}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
