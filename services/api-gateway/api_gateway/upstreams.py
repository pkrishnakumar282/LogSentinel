"""Fixed upstream destinations, separate credentials, bounded I/O, sanitized failures."""
import asyncio
import httpx


class UpstreamError(Exception):
    def __init__(self, code=503):
        self.code = code


class Upstreams:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(settings.timeout_seconds, connect=2), follow_redirects=False,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=10), trust_env=False)
        self.services = {'processor': (settings.processor_url, settings.ingest_token),
            'inference': (settings.inference_url, settings.inference_token), 'alerts': (settings.alert_url, settings.alert_token)}

    async def request(self, name, method, path, data=None, params=None, admin=False):
        base, token = self.services[name]
        if admin:
            token = self.settings.admin_token
        try:
            response = await asyncio.wait_for(self.client.request(method, base.rstrip('/') + path,
                json=data, params=params, headers={'Authorization': 'Bearer ' + token}), self.settings.timeout_seconds)
            if response.status_code >= 400:
                # Never echo arbitrary upstream error bodies or downstream authentication details.
                raise UpstreamError(response.status_code if response.status_code in (404,409,422,429) else 503)
            if response.status_code >= 300 or len(response.content) > 8 * 1024 * 1024:
                raise UpstreamError()
            value = response.json()
            if not isinstance(value, (dict, list)):
                raise UpstreamError()
            return value
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
            raise UpstreamError() from None

    async def health(self):
        async def probe(name):
            try:
                await self.request(name, 'GET', '/health/ready')
                return name, 'ready'
            except UpstreamError:
                return name, 'unavailable'
        return dict(await asyncio.gather(*(probe(name) for name in self.services)))

    async def close(self):
        await self.client.aclose()
