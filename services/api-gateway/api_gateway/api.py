from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID
import resource
import asyncio

from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from shared.application.jobs import JobConflict, Jobs
from shared.application.repository import Repository
from shared.application.retention import retain
from shared.common.http_guard import ServiceGuard
from shared.pipeline.database import make_engine
from shared.schemas.ingestion import Envelope
from shared.schemas.predictions import SinglePredictionRequest, BatchPredictionRequest
from .broker import status as broker_status
from .config import Settings
from .registry import Registry
from .upstreams import UpstreamError, Upstreams


class Actor(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    actor: str = Field(min_length=1, max_length=128, pattern=r'^[a-zA-Z0-9_.@:-]+$')


class TrainingRequest(Actor):
    synthetic: bool
    source: Literal['linux','nginx','application'] | None = None
    seed: int = Field(default=42, ge=0, lt=2**32)
    estimators: list[int] = Field(default_factory=lambda: [100,200], min_length=1, max_length=3)

    @field_validator('estimators')
    @classmethod
    def trees(cls, values):
        if len(set(values)) != len(values) or any(not 1 <= n <= 500 for n in values):
            raise ValueError('invalid_estimator_grid')
        return values


class AlertAction(Actor):
    note: str = Field(default='', max_length=256, repr=False)


class RetentionRequest(Actor):
    days: int = Field(default=30, ge=1, le=3650)
    dry_run: bool = True
    limit: int = Field(default=500, ge=1, le=5000)


def create_app(settings=None, repository=None, upstreams=None, registry=None, executor=None):
    settings = settings or Settings.from_env()
    repository = repository or Repository(make_engine())
    jobs, registry = Jobs(repository.engine), registry or Registry()
    upstreams = upstreams or Upstreams(settings)
    execute = executor or run_in_threadpool
    @asynccontextmanager
    async def lifespan(_app):
        yield
        await upstreams.close()
    app = FastAPI(title='LogSentinel API Gateway', version='0.7.0', lifespan=lifespan)
    public_posts = {'/api/v1/ingest','/api/v1/predict','/api/v1/predict/batch'}
    app.add_middleware(ServiceGuard, settings=settings,
        admin_predicate=lambda path, method: method in ('POST','PATCH','PUT','DELETE') and path not in public_posts)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
        allow_methods=['GET','POST'], allow_headers=['Authorization','Content-Type'])
    bearer = HTTPBearer(auto_error=False)
    deps = [Security(bearer)]

    @app.exception_handler(RequestValidationError)
    async def invalid(_request, _error):
        return JSONResponse({'detail':'invalid_request'}, status_code=422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request, _error):
        return JSONResponse({'detail':'database_unavailable'}, status_code=503)

    @app.exception_handler(UpstreamError)
    async def upstream_error(_request, error):
        return JSONResponse({'detail':'upstream_request_failed'}, status_code=error.code)

    @app.exception_handler(JobConflict)
    async def job_error(_request, error):
        return JSONResponse({'detail':str(error)}, status_code=409)

    @app.exception_handler(Exception)
    async def safe_error(_request, _error):
        return JSONResponse({'detail':'gateway_operation_failed'}, status_code=500)

    @app.get('/health/live')
    async def live():
        return {'status':'ok'}

    @app.get('/health/ready')
    async def ready():
        try:
            await execute(repository.ping)
            return {'status':'ready', 'scope':'gateway_database; upstream readiness reported by /api/v1/system'}
        except Exception:
            return JSONResponse({'status':'unavailable'}, status_code=503)

    @app.get('/api/v1/logs', dependencies=deps)
    async def logs(q: str | None = Query(None, max_length=256), source: Literal['linux','nginx','application'] | None=None,
                   service: str | None=Query(None,max_length=128), severity: Literal['INFO','WARNING','ERROR','CRITICAL'] | None=None,
                   since: datetime | None=None, until: datetime | None=None, anomaly: bool | None=None,
                   limit: int=Query(50,ge=1,le=200), offset: int=Query(0,ge=0,le=1000000)):
        if any(value is not None and value.tzinfo is None for value in (since, until)) or (since and until and since > until):
            raise HTTPException(422, 'invalid_time_range')
        return await execute(lambda: repository.logs(q=q,source=source,service=service,severity=severity,
            since=since,until=until,anomaly=anomaly,limit=limit,offset=offset))

    @app.get('/api/v1/logs/{identity}', dependencies=deps)
    async def detail(identity: UUID):
        result = await execute(repository.detail, str(identity))
        if result is None:
            raise HTTPException(404,'log_not_found')
        return result

    @app.get('/api/v1/dashboard', dependencies=deps)
    async def dashboard(hours: int=Query(24,ge=1,le=168)):
        return await execute(repository.dashboard, hours)

    @app.get('/api/v1/sources', dependencies=deps)
    async def sources():
        return await execute(repository.sources)

    @app.get('/api/v1/models/observed', dependencies=deps)
    async def observed_models():
        return await execute(repository.models)

    async def registry_call(function):
        try:
            return await asyncio.wait_for(execute(function), settings.timeout_seconds)
        except Exception:
            raise HTTPException(503,'model_registry_unavailable') from None

    @app.get('/api/v1/models', dependencies=deps)
    async def models():
        return await registry_call(registry.models)

    @app.get('/api/v1/experiments', dependencies=deps)
    async def experiments():
        return await registry_call(registry.experiments)

    @app.get('/api/v1/model/status', dependencies=deps)
    async def model_status():
        return await upstreams.request('inference','GET','/health/model')

    @app.get('/api/v1/audit-events', dependencies=deps)
    async def audits(limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0,le=1000000),
                     action: Literal['model_register','model_promote','training_enqueue','training_finished','acknowledge','resolve','created','recurred','retention_preview','retention_apply'] | None=None):
        return await execute(repository.audits,limit,offset,action)

    @app.post('/api/v1/ingest', dependencies=deps)
    async def ingest(body: Envelope):
        return await upstreams.request('processor','POST','/api/v1/ingest',body.model_dump())

    @app.post('/api/v1/predict', dependencies=deps)
    async def predict(body: SinglePredictionRequest):
        return await upstreams.request('inference','POST','/api/v1/predict',body.model_dump())

    @app.post('/api/v1/predict/batch', dependencies=deps)
    async def batch(body: BatchPredictionRequest):
        return await upstreams.request('inference','POST','/api/v1/predict/batch',body.model_dump())

    @app.get('/api/v1/alerts', dependencies=deps)
    async def alerts(severity: Literal['INFO','WARNING','HIGH','CRITICAL'] | None=None,
                     status: Literal['open','acknowledged','resolved'] | None=None,
                     limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0,le=1000000)):
        params = {key:value for key,value in dict(severity=severity,status=status,limit=limit,offset=offset).items() if value is not None}
        return await upstreams.request('alerts','GET','/api/v1/alerts',params=params)

    @app.get('/api/v1/alerts/{identity}', dependencies=deps)
    async def alert_detail(identity: UUID):
        return await upstreams.request('alerts','GET','/api/v1/alerts/' + str(identity))

    @app.get('/api/v1/alerts/{identity}/history', dependencies=deps)
    async def history(identity: UUID):
        return await upstreams.request('alerts','GET','/api/v1/alerts/' + str(identity) + '/history')

    @app.post('/api/v1/alerts/{identity}/{action}', dependencies=deps)
    async def alert_action(identity: UUID,action: Literal['acknowledge','resolve'],body: AlertAction):
        return await upstreams.request('alerts','POST',f'/api/v1/alerts/{identity}/{action}',body.model_dump(),admin=True)

    @app.get('/api/v1/training/jobs', dependencies=deps)
    async def training_jobs(limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0,le=1000000)):
        return await execute(jobs.list,limit,offset)

    @app.get('/api/v1/training/jobs/{identity}', dependencies=deps)
    async def training_job(identity: UUID):
        result = await execute(jobs.get,str(identity))
        if result is None:
            raise HTTPException(404,'job_not_found')
        return result

    @app.post('/api/v1/training/jobs', dependencies=deps, status_code=202)
    async def training(body: TrainingRequest):
        return await execute(jobs.enqueue,body.actor,body.model_dump(exclude={'actor'}))

    @app.post('/api/v1/maintenance/retention', dependencies=deps)
    async def retention(body: RetentionRequest):
        return await execute(retain,repository.engine,body.days,body.actor,body.dry_run,body.limit)

    @app.get('/api/v1/system', dependencies=deps)
    async def system():
        services = await upstreams.health()
        try:
            await execute(repository.ping)
            services['database'] = 'ready'
        except Exception:
            services['database'] = 'unavailable'
        try:
            broker = await asyncio.wait_for(execute(broker_status),settings.timeout_seconds)
        except Exception:
            broker = {'status':'unavailable','retained_entries':None}
        services['redis'] = broker['status']
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return {'services': services, 'status':'ready' if all(value=='ready' for value in services.values()) else 'degraded',
                'process':{'scope':'gateway_process','cpu_seconds':usage.ru_utime+usage.ru_stime,'peak_rss_bytes':usage.ru_maxrss*1024},
                'broker':broker}

    return app
