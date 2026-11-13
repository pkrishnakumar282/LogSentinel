"""Long-running worker loop and bounded, payload-free local heartbeat."""
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import time


def heartbeat(path, status, reason=None):
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix('.tmp')
    temporary.write_text(json.dumps({'status': status, 'reason': reason,
        'timestamp': time.time(), 'checked_at': datetime.now(UTC).isoformat()}))
    temporary.replace(file)


def healthy(path):
    try:
        value = json.loads(Path(path).read_text())
        return value['status'] in ('ready', 'idle', 'archived', 'predicted', 'duplicate', 'alert_created',
            'alert_grouped', 'ignored_normal', 'ignored_warmup', 'ignored_score_floor', 'standby', 'failed') and time.time() - value['timestamp'] < 90
    except (OSError, ValueError, KeyError, TypeError):
        return False


def run(worker, store, relay=None, engine=None, once=False):
    path = os.environ.get('WORKER_HEARTBEAT_PATH', '.local/worker-health.json')
    last_reload = 0
    last_initialize = 0
    try:
        while True:
            try:
                if time.monotonic() - last_initialize > 30:
                    store.ping()
                    worker.broker.initialize()
                    last_initialize = time.monotonic()
                if relay:
                    relay.flush()
                if engine and time.monotonic() - last_reload > 10:
                    engine.reload()
                    last_reload = time.monotonic()
                status = worker.tick()
                if engine is not None and engine.loaded is None:
                    status = 'deferred'
                    worker.last_reason = 'approved_model_unavailable'
                heartbeat(path, status, worker.last_reason)
            except Exception:
                status = 'unavailable'
                heartbeat(path, status, 'pipeline_dependency_unavailable')
            print(json.dumps({'status': status}), flush=True)
            if once:
                return 0 if status not in ('unavailable', 'deferred', 'retrying') else 1
            time.sleep(1 if status in ('idle', 'standby', 'deferred', 'unavailable', 'retrying') else .01)
    except KeyboardInterrupt:
        return 0
