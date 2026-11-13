"""Bounded retry policy. HTTP error bodies and raw events are never logged."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from shared.common.publishing import PublicationUnavailable


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPTransport:
    def __init__(self, url: str, token: str, attempts: int = 3, timeout: float = 5):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("invalid_processor_url")
        if len(token) < 24 or not 1 <= attempts <= 10:
            raise ValueError("invalid_transport_configuration")
        self.url, self.token = url, token
        self.attempts, self.timeout = attempts, timeout
        self.opener = build_opener(NoRedirect())

    def send(self, record) -> str:
        data = json.dumps(record.to_dict()).encode()
        for attempt in range(self.attempts):
            request = Request(self.url, data=data, headers={"Content-Type": "application/json",
                              "Authorization": f"Bearer {self.token}"}, method="POST")
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    receipt = json.loads(response.read(4096))
                if receipt.get("event_id") != record.event_id or receipt.get("status") not in ("accepted", "duplicate"):
                    raise PublicationUnavailable("invalid_receipt")
                return receipt["status"]
            except HTTPError as exc:
                # Permanent malformed record; acknowledge only the processor's explicit rejection.
                exc.close()
                if exc.code == 422:
                    return "rejected"
                if exc.code not in (429, 500, 502, 503, 504):
                    raise PublicationUnavailable(f"processor_http_{exc.code}") from None
            except (URLError, TimeoutError, OSError, ValueError):
                pass
            if attempt + 1 < self.attempts:
                time.sleep(min(0.25 * 2 ** attempt, 2))
        raise PublicationUnavailable("processor_unavailable")
