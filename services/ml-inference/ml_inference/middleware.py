"""Inference specialization of the shared authenticated, body-bounded ASGI guard."""
from shared.common.http_guard import ServiceGuard, token_matches


class InferenceGuard(ServiceGuard):
    def __init__(self, app, settings):
        super().__init__(app, settings, lambda path, method:
                         path == '/api/v1/models/reload' or path.startswith('/api/v1/contexts/'))
