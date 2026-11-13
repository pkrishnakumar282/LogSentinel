"""Deterministic, explicitly synthetic envelopes for development, never production evidence."""
from datetime import UTC, datetime, timedelta
import json
import random
from uuid import NAMESPACE_URL, uuid5

from shared.schemas import RawLog


def simulated_records(count: int, seed: int = 42):
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    messages = [("INFO", "worker completed task {n}"),
                ("INFO", "cache refresh completed in {n} ms"),
                ("ERROR", "scheduled retry for unavailable upstream task {n}"),
                ("WARNING", "queue depth reached {n}"),
                ("CRITICAL", "unexpected filesystem corruption on shard {n}")]
    for index in range(count):
        severity, message = rng.choice(messages)
        timestamp = (start + timedelta(seconds=index)).isoformat()
        raw = json.dumps({"timestamp": timestamp, "level": severity,
                          "message": message.format(n=rng.randint(1, 500)), "service": "demo-worker"})
        yield RawLog(str(uuid5(NAMESPACE_URL, f"logsentinel:synthetic:v1:{seed}:{index}")),
                     "application", "demo-worker", "synthetic-host", timestamp, raw, True)
