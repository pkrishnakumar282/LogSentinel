"""ASGI request limits/auth before JSON parsing, without private Request body mutation."""
from collections import deque
import secrets
import threading
import time

from starlette.responses import JSONResponse


def token_matches(header: bytes, token: str) -> bool:
    return len(token) >= 24 and secrets.compare_digest(header, b'Bearer ' + token.encode())


class ServiceGuard:
    def __init__(self, app, settings, admin_predicate):
        self.app, self.settings = app, settings
        self.admin_predicate = admin_predicate
        self.recent = deque()
        self.lock = threading.Lock()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
            return await self.app(scope, receive, send)
        path = scope['path']
        admin = self.admin_predicate(path, scope['method'])
        headers = dict(scope.get('headers', []))
        header = headers.get(b'authorization', b'')
        required = self.settings.admin_token if admin else self.settings.api_token
        if not token_matches(header, required):
            code = 403 if admin else 401
            return await JSONResponse({'detail': 'admin_authorization_required' if admin else 'unauthorized'},
                                      status_code=code)(scope, receive, send)
        now = time.monotonic()
        with self.lock:
            while self.recent and self.recent[0] <= now - 60:
                self.recent.popleft()
            limited = len(self.recent) >= self.settings.rate_per_minute
            if not limited:
                self.recent.append(now)
        if limited:
            return await JSONResponse({'detail': 'rate_limited'}, status_code=429)(scope, receive, send)
        if scope['method'] in ('POST', 'PUT', 'PATCH'):
            body = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                if len(body) + len(chunk) > self.settings.max_body_bytes:
                    return await JSONResponse({'detail': 'body_too_large'}, status_code=413)(scope, receive, send)
                body.extend(chunk)
                if not message.get('more_body', False):
                    break
            delivered = False
            original_receive = receive

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await original_receive()
            receive = replay
        await self.app(scope, receive, send)
