"""Internal collector endpoint; gateway remains a separate later-phase service."""
import os
import secrets
import threading
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from shared.common.publishing import EventConflict, PublicationUnavailable, RedisPublisher, SQLitePublisher
from shared.schemas import InvalidRecord, RawLog
from shared.schemas.ingestion import Envelope

from .normalizer import Normalizer
from .pipeline import ProcessingPipeline
from .templates import TemplateEngine


def create_app(pipeline=None, token: str | None = None) -> FastAPI:
    token = token or os.environ.get("INGEST_TOKEN", "")
    if len(token) < 24:
        raise ValueError("INGEST_TOKEN must contain at least 24 characters")
    if pipeline is None:
        sink = os.environ.get("PROCESSOR_SINK", "redis")
        if sink == "sqlite":
            publisher = SQLitePublisher(os.environ.get("PROCESSOR_SQLITE_PATH", ".local/normalized.sqlite3"))
        elif sink == "redis":
            publisher = RedisPublisher(os.environ.get("REDIS_HOST", "redis"),
                                       int(os.environ.get("REDIS_PORT", "6379")),
                                       os.environ.get("REDIS_PASSWORD", ""),
                                       os.environ.get("NORMALIZED_STREAM", "logs:normalized:v1"),
                                       int(os.environ.get("STREAM_MAX_LENGTH", "100000")),
                                       int(os.environ.get("DEDUP_TTL_SECONDS", "604800")))
        else:
            raise ValueError("invalid_processor_sink")
        pipeline = ProcessingPipeline(Normalizer(TemplateEngine(os.environ.get("TEMPLATE_MODE", "drain3")),
                                                os.environ.get("LOG_TIMEZONE", "UTC")), publisher)
    app = FastAPI(title="LogSentinel Log Processor", version="0.2.0")
    recent = deque()
    rate_lock = threading.Lock()
    rate = int(os.environ.get("PROCESSOR_RATE_PER_MINUTE", "6000"))
    if rate < 1:
        raise ValueError("invalid_rate_limit")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path != "/api/v1/ingest":
            return await call_next(request)
        provided = request.headers.get("authorization", "")
        if not secrets.compare_digest(provided.encode(), f"Bearer {token}".encode()):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        now = time.monotonic()
        with rate_lock:
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if len(recent) >= rate:
                return JSONResponse({"detail": "rate_limited"}, status_code=429)
            recent.append(now)
        # Bound actual body bytes, including chunked requests, before JSON validation.
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 400000:
                return JSONResponse({"detail": "body_too_large"}, status_code=413)
        request._body = bytes(content)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc):
        # FastAPI's default validation response can echo secrets from input.
        return JSONResponse({"detail": "invalid_envelope"}, status_code=422)

    @app.exception_handler(InvalidRecord)
    async def invalid_record(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(EventConflict)
    async def conflict(_request, _exc):
        return JSONResponse({"detail": "event_id_conflict"}, status_code=409)

    @app.exception_handler(PublicationUnavailable)
    async def unavailable(_request, _exc):
        return JSONResponse({"detail": "publisher_unavailable"}, status_code=503)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            if pipeline.publisher.ping():
                return {"status": "ready"}
        except Exception:
            pass
        return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.post("/api/v1/ingest")
    async def ingest(envelope: Envelope):
        record = RawLog.from_dict(envelope.model_dump())
        receipt = await run_in_threadpool(pipeline.ingest, record)
        return {"event_id": receipt.event_id, "status": receipt.status}

    return app
