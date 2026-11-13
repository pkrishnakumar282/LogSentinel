"""Controlled synthetic dataset; labels stay in a separate optional sidecar."""
from datetime import UTC, datetime, timedelta
import random
from uuid import NAMESPACE_URL, uuid5

from shared.schemas import NormalizedLog


def synthetic_cases(count: int = 600, seed: int = 42):
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(count):
        source = ("application", "nginx", "linux")[index % 3]
        service = {"application": "demo-api", "nginx": "nginx", "linux": "demo-worker.service"}[source]
        unusual = index >= int(count * 0.85) and index % 23 == 0
        severity, status = "INFO", None
        if source == "nginx":
            status = 200
            message, template = "GET /health HTTP/1.1 status=200", "GET /health HTTP/<NUM> status=<NUM>"
        else:
            duration = rng.randint(5, 40)
            message, template = f"scheduled task completed in {duration} ms", "scheduled task completed in <NUM> ms"
        if index % 17 == 0:
            # Expected periodic errors are not automatically annotated anomalous.
            severity = "ERROR"
            status = 503 if source == "nginx" else None
            message, template = "scheduled upstream retry attempt 1", "scheduled upstream retry attempt <NUM>"
        if unusual:
            severity = "CRITICAL"
            status = 500 if source == "nginx" else None
            message = "unexpected filesystem corruption segmentation fault on unknown storage path"
            template = message
        event = NormalizedLog(str(uuid5(NAMESPACE_URL, f"logsentinel:phase3:v1:{seed}:{count}:{index}")),
                              (start + timedelta(seconds=index)).isoformat(), source, service,
                              "synthetic-host", severity, message, template, status,
                              {"synthetic": True, "redaction_version": "1", "template_engine": "synthetic_fixed"})
        yield event, {"event_id": event.event_id, "is_anomaly": unusual,
                      "annotation_origin": "synthetic_scenario_not_production_ground_truth"}


def future_cases(start, count: int = 300, seed: int = 84, role: str = "validation"):
    """Independent future controlled traffic, separate from the earlier training smoke data."""
    rng = random.Random(seed)
    for index in range(count):
        source = ("application", "nginx", "linux")[index % 3]
        service = {"application": "demo-api", "nginx": "nginx", "linux": "demo-worker.service"}[source]
        severity, status = "INFO", 200 if source == "nginx" else None
        duration = rng.randint(5, 40)
        message, template = f"scheduled task completed in {duration} ms", "scheduled task completed in <NUM> ms"
        if source == "nginx":
            message, template = "GET /health HTTP/1.1 status=200", "GET /health HTTP/<NUM> status=<NUM>"
        if index % 17 == 0:
            severity, status = "ERROR", 503 if source == "nginx" else None
            message, template = "scheduled upstream retry attempt 1", "scheduled upstream retry attempt <NUM>"
        unusual = index % 23 == 11
        if unusual:
            severity, status = "CRITICAL", 500 if source == "nginx" else None
            message = "unexpected storage checksum mismatch with filesystem corruption and unrecoverable shard metadata failure"
            template = message
        event = NormalizedLog(str(uuid5(NAMESPACE_URL, f"logsentinel:phase4:v1:{role}:{seed}:{count}:{index}")),
            (start + timedelta(seconds=index)).isoformat(), source, service, "synthetic-host",
            severity, message, template, status, {"synthetic": True, "redaction_version": "1"})
        yield event, {"event_id": event.event_id, "is_anomaly": unusual,
                      "annotation_origin": "synthetic_scenario_not_production_ground_truth"}
