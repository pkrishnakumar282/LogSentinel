from dataclasses import dataclass
import hashlib
import json
import math
import os

LEVELS = ['INFO', 'WARNING', 'HIGH', 'CRITICAL']


@dataclass(frozen=True)
class AlertPolicy:
    group_window_seconds: float = 300
    require_warm_context: bool = True
    min_score: float | None = None
    high_margin: float = .15
    critical_margin: float = .30

    def __post_init__(self):
        values = [self.group_window_seconds, self.high_margin, self.critical_margin]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            raise ValueError('invalid_alert_policy')
        if self.group_window_seconds <= 0 or not 0 <= self.high_margin <= self.critical_margin:
            raise ValueError('invalid_alert_policy')
        if self.min_score is not None and (type(self.min_score) not in (int, float) or not math.isfinite(self.min_score)):
            raise ValueError('invalid_alert_minimum')
        if type(self.require_warm_context) is not bool:
            raise ValueError('invalid_alert_warmup_policy')

    @classmethod
    def from_env(cls):
        warm = os.environ.get('ALERT_REQUIRE_WARM_CONTEXT', 'true').lower()
        if warm not in ('true', 'false'):
            raise ValueError('invalid_alert_warmup_policy')
        minimum = os.environ.get('ALERT_MIN_SCORE', '')
        return cls(group_window_seconds=float(os.environ.get('ALERT_GROUP_WINDOW_SECONDS', '300')),
            require_warm_context=warm == 'true', min_score=float(minimum) if minimum else None,
            high_margin=float(os.environ.get('ALERT_HIGH_MARGIN', '.15')),
            critical_margin=float(os.environ.get('ALERT_CRITICAL_MARGIN', '.30')))

    def eligibility(self, prediction):
        if not prediction['is_anomaly']:
            return 'ignored_normal'
        if self.require_warm_context and not prediction['context_warm']:
            return 'ignored_warmup'
        if self.min_score is not None and prediction['anomaly_score'] < self.min_score:
            return 'ignored_score_floor'
        return 'eligible'

    def level(self, prediction, event):
        level = {'INFO': 0, 'WARNING': 1, 'ERROR': 2, 'CRITICAL': 3}[event.severity]
        margin = prediction['anomaly_score'] - prediction['threshold']
        if margin >= self.critical_margin:
            level = max(level, 3)
        elif margin >= self.high_margin:
            level = max(level, 2)
        return LEVELS[level], 4 - level

    def group_key(self, prediction, event):
        return hashlib.sha256(json.dumps([event.source, event.service, event.hostname,
            event.template, prediction['model_id']], separators=(',', ':')).encode()).hexdigest()
