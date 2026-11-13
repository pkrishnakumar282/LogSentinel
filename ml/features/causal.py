"""Causal rolling features shared by batch replay and inference sessions.

For an event at t, history is strictly (t-window, t), not including the event itself
or any peer with the same timestamp. Out-of-order events are rejected. Capacity is a
hard error instead of silently dropping in-window history and corrupting counts.
"""
from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
import re

from shared.common.redaction import redact
from shared.schemas import NormalizedLog
from shared.schemas.logs import utc_time

from .contract import FEATURE_COLUMNS, NUMERIC_COLUMNS


class FeatureStateError(ValueError):
    pass


@dataclass(frozen=True)
class FeatureConfig:
    window_seconds: float = 60.0
    max_events: int = 100000

    def __post_init__(self):
        if (type(self.window_seconds) not in (int, float)
                or not math.isfinite(self.window_seconds) or self.window_seconds <= 0):
            raise ValueError("invalid_feature_window")
        if type(self.max_events) is not int or self.max_events < 1:
            raise ValueError("invalid_feature_capacity")

    def to_dict(self):
        return asdict(self)


def event_digest(event: NormalizedLog) -> str:
    return hashlib.sha256(event.to_json().encode()).hexdigest()


class FeatureExtractor:
    def __init__(self, config: FeatureConfig | None = None):
        self.config = config or FeatureConfig()
        self.history = deque()
        self.pending = []
        self.cache = {}
        self.services = Counter()
        self.sources = Counter()
        self.templates = Counter()
        self.errors = Counter()
        self.current_time: datetime | None = None
        self.started_at: datetime | None = None

    def _keys(self, event):
        service = (event.source, event.service)
        return service, event.source, (*service, redact(event.template))

    def _adjust(self, event, delta):
        service, source, template = self._keys(event)
        for counter, key in ((self.services, service), (self.sources, source),
                             (self.templates, template)):
            counter[key] += delta
            if not counter[key]:
                del counter[key]
        if event.severity in ("ERROR", "CRITICAL"):
            self.errors[service] += delta
            if not self.errors[service]:
                del self.errors[service]

    def transform_one(self, event: NormalizedLog) -> dict:
        dt = utc_time(event.timestamp)
        digest = event_digest(event)
        previous = self.cache.get(event.event_id)
        if previous:
            if previous[0] != digest:
                raise FeatureStateError("event_id_conflict")
            return dict(previous[1])
        if self.current_time is not None and dt < self.current_time:
            raise FeatureStateError("out_of_order_event")
        if self.current_time is not None and dt > self.current_time:
            for pending in self.pending:
                self.history.append(pending)
                self._adjust(pending[1], 1)
            self.pending.clear()
        # All history entries are strictly earlier than dt; peers remain in pending.
        while self.history and (dt - self.history[0][0]).total_seconds() >= self.config.window_seconds:
            _, expired = self.history.popleft()
            self._adjust(expired, -1)
            self.cache.pop(expired.event_id, None)
        if len(self.history) + len(self.pending) >= self.config.max_events:
            raise FeatureStateError("feature_window_capacity_reached")
        self.current_time = dt
        self.started_at = self.started_at or dt
        service, source, template = self._keys(event)
        count = self.services[service]
        status = event.status_code or 0
        message = redact(event.message)
        text = re.sub(r"\b\d+(?:\.\d+)?\b", " num ", message.lower())
        observed = min((dt - self.started_at).total_seconds(), self.config.window_seconds)
        row = {
            "text": text, "severity": event.severity, "source": event.source, "service": event.service,
            "status_code": status, "has_http_status": int(event.status_code is not None),
            "http_4xx": int(400 <= status < 500), "http_5xx": int(status >= 500),
            "message_length": len(message), "event_frequency": count,
            "source_frequency": self.sources[source], "global_frequency": len(self.history),
            "events_per_second": count / self.config.window_seconds,
            "template_frequency": self.templates[template],
            "error_rate": self.errors[service] / count if count else 0.0,
            "window_observed_seconds": observed,
            "window_is_warm": int(observed >= self.config.window_seconds),
        }
        if set(row) != set(FEATURE_COLUMNS) or any(not math.isfinite(row[key]) for key in NUMERIC_COLUMNS):
            raise FeatureStateError("invalid_feature_values")
        self.pending.append((dt, event))
        self.cache[event.event_id] = (digest, dict(row))
        return row

    def fork(self):
        """Independent mutable containers for transactional inference; predictor semantics unchanged."""
        from copy import copy
        forked = copy(self)
        forked.history = deque(self.history)
        forked.pending = list(self.pending)
        forked.cache = dict(self.cache)
        for name in ('services', 'sources', 'templates', 'errors'):
            setattr(forked, name, getattr(self, name).copy())
        return forked

    def transform(self, events) -> list[dict]:
        return [self.transform_one(event) for event in events]
