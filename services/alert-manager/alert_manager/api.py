from dataclasses import dataclass, field
import os
from typing import Literal

from fastapi import FastAPI, Query, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from shared.common.http_guard import ServiceGuard
from shared.pipeline.store import PipelineStore

from .service import AlertConflict, AlertNotFound, AlertService


@dataclass(frozen=True)
class AlertAPISettings:
    api_token: str = field(default='', repr=False)
    admin_token: str = field(default='', repr=False)
    max_body_bytes: int = 8192
    rate_per_minute: int = 600

    @classmethod
    def from_env(cls):
        return cls(api_token=os.environ.get('ALERT_API_TOKEN', ''),
                   admin_token=os.environ.get('ADMIN_API_TOKEN', ''))


class AlertAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    actor: str = Field(min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9_.@:-]+$')
    note: str = Field(default='', max_length=256, repr=False)


def create_app(settings=None, service=None, executor=None):
    settings = settings or AlertAPISettings.from_env()
    if len(settings.api_token) < 24:
        raise ValueError('ALERT_API_TOKEN must contain at least 24 characters')
    service = service or AlertService(PipelineStore())
    execute = executor or run_in_threadpool
    app = FastAPI(title='LogSentinel Alert Manager', version='0.6.0')
    bearer = HTTPBearer(auto_error=False)
    app.add_middleware(ServiceGuard, settings=settings,
                       admin_predicate=lambda path, method: method == 'POST')

    @app.exception_handler(RequestValidationError)
    async def validation(_request, _exc):
        return JSONResponse({'detail': 'invalid_alert_request'}, status_code=422)

    @app.exception_handler(AlertNotFound)
    async def missing(_request, _exc):
        return JSONResponse({'detail': 'alert_not_found'}, status_code=404)

    @app.exception_handler(AlertConflict)
    async def conflict(_request, _exc):
        return JSONResponse({'detail': 'invalid_alert_transition'}, status_code=409)

    @app.exception_handler(SQLAlchemyError)
    async def unavailable(_request, _exc):
        return JSONResponse({'detail': 'alert_store_unavailable'}, status_code=503)

    @app.exception_handler(Exception)
    async def safe_error(_request, _exc):
        return JSONResponse({'detail': 'alert_service_error'}, status_code=500)

    @app.get('/health/live')
    async def live():
        return {'status': 'ok'}

    @app.get('/health/ready')
    async def ready():
        try:
            await execute(service.store.ping)
            return {'status': 'ready'}
        except Exception:
            return JSONResponse({'status': 'unavailable'}, status_code=503)

    @app.get('/api/v1/alerts', dependencies=[Security(bearer)])
    async def list_alerts(severity: Literal['INFO','WARNING','HIGH','CRITICAL'] | None = None,
                          status: Literal['open','acknowledged','resolved'] | None = None,
                          limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
        return await execute(service.list, severity, status, limit, offset)

    @app.get('/api/v1/alerts/{identity}', dependencies=[Security(bearer)])
    async def detail(identity: str):
        return await execute(service.detail, identity)

    @app.get('/api/v1/alerts/{identity}/history', dependencies=[Security(bearer)])
    async def history(identity: str):
        return {'items': await execute(service.history, identity)}

    @app.post('/api/v1/alerts/{identity}/acknowledge', dependencies=[Security(bearer)])
    async def acknowledge(identity: str, action: AlertAction):
        return await execute(service.transition, identity, 'acknowledge', action.actor, action.note)

    @app.post('/api/v1/alerts/{identity}/resolve', dependencies=[Security(bearer)])
    async def resolve(identity: str, action: AlertAction):
        return await execute(service.transition, identity, 'resolve', action.actor, action.note)

    return app
