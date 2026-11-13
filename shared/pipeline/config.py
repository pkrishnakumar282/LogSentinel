from dataclasses import dataclass, field
import math
import os
import re


@dataclass(frozen=True)
class StreamSettings:
    host: str = 'redis'
    port: int = 6379
    password: str = field(default='', repr=False)
    normalized_stream: str = 'logs:normalized:v1'
    prediction_stream: str = 'logs:predictions:v1'
    failed_stream: str = 'logs:failed:v1'
    inference_group: str = 'inference-v1'
    alert_group: str = 'alerts-v1'
    capacity: int = 100000
    dedup_ttl: int = 604800
    claim_idle_ms: int = 60000
    lease_ms: int = 30000
    max_retries: int = 5
    retry_seconds: float = 1.0
    max_retry_seconds: float = 30.0
    outbox_capacity: int = 10000

    def __post_init__(self):
        for name in ('normalized_stream', 'prediction_stream', 'failed_stream', 'inference_group', 'alert_group'):
            if not re.fullmatch(r'[A-Za-z0-9_.:{}-]{1,128}', getattr(self, name)):
                raise ValueError('invalid_stream_identifier')
        if len({self.normalized_stream, self.prediction_stream, self.failed_stream}) != 3:
            raise ValueError('streams_must_be_distinct')
        for name in ('port', 'capacity', 'dedup_ttl', 'claim_idle_ms', 'lease_ms', 'max_retries', 'outbox_capacity'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError('invalid_stream_limit')
        if self.capacity < 2:
            raise ValueError("stream_capacity_must_allow_retained_watermark")
        if self.max_retries > 20 or self.port > 65535 or self.claim_idle_ms < self.lease_ms:
            raise ValueError('invalid_stream_recovery_configuration')
        for value in (self.retry_seconds, self.max_retry_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid_retry_interval')

    @classmethod
    def from_env(cls):
        return cls(host=os.environ.get('REDIS_HOST', 'redis'), port=int(os.environ.get('REDIS_PORT', '6379')),
            password=os.environ.get('REDIS_PASSWORD', ''),
            normalized_stream=os.environ.get('NORMALIZED_STREAM', cls.normalized_stream),
            prediction_stream=os.environ.get('PREDICTION_STREAM', cls.prediction_stream),
            failed_stream=os.environ.get('DEAD_LETTER_STREAM', cls.failed_stream),
            inference_group=os.environ.get('INFERENCE_CONSUMER_GROUP', cls.inference_group),
            alert_group=os.environ.get('ALERT_CONSUMER_GROUP', cls.alert_group),
            capacity=int(os.environ.get('STREAM_MAX_LENGTH', '100000')),
            dedup_ttl=int(os.environ.get('DEDUP_TTL_SECONDS', '604800')),
            claim_idle_ms=int(os.environ.get('CONSUMER_CLAIM_IDLE_MS', '60000')),
            lease_ms=int(os.environ.get('CONSUMER_LEASE_MS', '30000')),
            max_retries=int(os.environ.get('CONSUMER_MAX_RETRIES', '5')),
            retry_seconds=float(os.environ.get('CONSUMER_RETRY_SECONDS', '1')),
            max_retry_seconds=float(os.environ.get('CONSUMER_MAX_RETRY_SECONDS', '30')),
            outbox_capacity=int(os.environ.get('PIPELINE_OUTBOX_MAX_PENDING', '10000')))
