from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    api_token: str = field(default='', repr=False)
    admin_token: str = field(default='', repr=False)
    ingest_token: str = field(default='', repr=False)
    inference_token: str = field(default='', repr=False)
    alert_token: str = field(default='', repr=False)
    processor_url: str = 'http://log-processor:8012'
    inference_url: str = 'http://ml-inference:8013'
    alert_url: str = 'http://alert-manager:8014'
    cors_origins: tuple[str, ...] = ('http://localhost:5173', 'http://127.0.0.1:5173')
    max_body_bytes: int = 1048576
    rate_per_minute: int = 600
    timeout_seconds: float = 10

    def __post_init__(self):
        if len(self.api_token) < 24:
            raise ValueError('GATEWAY_API_TOKEN must contain at least 24 characters')
        for url in (self.processor_url, self.inference_url, self.alert_url):
            parsed = urlsplit(url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
                raise ValueError('invalid_service_url')
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError('invalid_timeout')
        if not 1024 <= self.max_body_bytes <= 16 * 1024 * 1024 or not 1 <= self.rate_per_minute <= 100000:
            raise ValueError('invalid_gateway_limit')

    @classmethod
    def from_env(cls):
        return cls(api_token=os.environ.get('GATEWAY_API_TOKEN',''), admin_token=os.environ.get('ADMIN_API_TOKEN',''),
            ingest_token=os.environ.get('INGEST_TOKEN',''), inference_token=os.environ.get('INFERENCE_API_TOKEN',''),
            alert_token=os.environ.get('ALERT_API_TOKEN',''),
            processor_url=os.environ.get('PROCESSOR_URL','http://log-processor:8012'),
            inference_url=os.environ.get('INFERENCE_URL','http://ml-inference:8013'),
            alert_url=os.environ.get('ALERT_MANAGER_URL','http://alert-manager:8014'),
            cors_origins=tuple(filter(None, os.environ.get('GATEWAY_CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(','))),
            max_body_bytes=int(os.environ.get('GATEWAY_MAX_BODY_BYTES','1048576')),
            rate_per_minute=int(os.environ.get('GATEWAY_RATE_PER_MINUTE','600')),
            timeout_seconds=float(os.environ.get('GATEWAY_TIMEOUT_SECONDS','10')))
