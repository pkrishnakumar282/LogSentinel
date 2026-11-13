from dataclasses import dataclass, field
import math
import os
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class InferenceSettings:
    tracking_uri: str = 'sqlite:///.local/mlflow/tracking.db'
    audit_path: str = '.local/mlflow/lifecycle.sqlite3'
    model_name: str = 'logsentinel-model'
    model_version: str = ''
    model_alias: str = 'champion'
    scope: str = 'production'
    threshold: float | None = None
    api_token: str = field(default='', repr=False)
    admin_token: str = field(default='', repr=False)
    max_batch: int = 256
    max_body_bytes: int = 1048576
    max_contexts: int = 32
    context_ttl_seconds: float = 900
    rate_per_minute: int = 6000

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', self.model_name):
            raise ValueError('invalid_model_name')
        if self.model_version:
            if not re.fullmatch(r'[1-9][0-9]{0,18}', self.model_version):
                raise ValueError('invalid_model_version')
        elif self.model_alias not in ('champion', 'staging'):
            raise ValueError('invalid_model_alias')
        if self.scope not in ('production', 'development'):
            raise ValueError('invalid_inference_scope')
        if self.threshold is not None:
            if (type(self.threshold) not in (int, float) or not math.isfinite(self.threshold)
                    or self.scope != 'development'):
                raise ValueError('threshold_override_requires_development_scope')
        for name, maximum in [('max_batch', 4096), ('max_body_bytes', 16777216),
                              ('max_contexts', 1024), ('rate_per_minute', 100000)]:
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= maximum:
                raise ValueError('invalid_inference_limit')
        if (type(self.context_ttl_seconds) not in (int, float)
                or not math.isfinite(self.context_ttl_seconds) or self.context_ttl_seconds <= 0):
            raise ValueError('invalid_context_ttl')
        if not isinstance(self.api_token, str) or not isinstance(self.admin_token, str):
            raise ValueError('invalid_inference_credentials')
        if self.tracking_uri.startswith('sqlite:///') and self.tracking_uri != 'sqlite:///:memory:':
            return
        parsed = urlsplit(self.tracking_uri)
        if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError('invalid_inference_tracking_uri')
        if parsed.hostname not in ('localhost', '127.0.0.1', '::1', 'mlflow'):
            if os.environ.get('MLFLOW_ALLOW_REMOTE') != 'true':
                raise ValueError('remote_registry_requires_explicit_configuration')

    @classmethod
    def from_env(cls):
        threshold = os.environ.get('INFERENCE_THRESHOLD', '')
        return cls(tracking_uri=os.environ.get('MLFLOW_TRACKING_URI', cls.tracking_uri),
                   audit_path=os.environ.get('REGISTRY_AUDIT_PATH', cls.audit_path),
                   model_name=os.environ.get('MODEL_NAME', cls.model_name),
                   model_version=os.environ.get('MODEL_VERSION', ''),
                   model_alias=os.environ.get('MODEL_ALIAS', 'champion'),
                   scope=os.environ.get('INFERENCE_SCOPE', 'production'),
                   threshold=float(threshold) if threshold else None,
                   api_token=os.environ.get('INFERENCE_API_TOKEN', ''),
                   admin_token=os.environ.get('ADMIN_API_TOKEN', ''),
                   max_batch=int(os.environ.get('INFERENCE_MAX_BATCH', '256')),
                   max_body_bytes=int(os.environ.get('INFERENCE_MAX_BODY_BYTES', '1048576')),
                   max_contexts=int(os.environ.get('INFERENCE_MAX_CONTEXTS', '32')),
                   context_ttl_seconds=float(os.environ.get('INFERENCE_CONTEXT_TTL_SECONDS', '900')),
                   rate_per_minute=int(os.environ.get('INFERENCE_RATE_PER_MINUTE', '6000')))
