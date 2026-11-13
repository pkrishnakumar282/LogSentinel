from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from starlette.concurrency import run_in_threadpool

from shared.schemas import InvalidRecord
from shared.schemas.predictions import (BatchPredictionRequest, BatchPredictionResponse,
    PredictionResponse, ReloadRequest, SinglePredictionRequest)

from .config import InferenceSettings
from .engine import InferenceCapacity, InferenceConflict, InferenceEngine, InferenceUnavailable
from .middleware import InferenceGuard

logger = logging.getLogger('logsentinel.inference')


def create_app(settings: InferenceSettings | None = None, engine=None, executor=None) -> FastAPI:
    settings = settings or InferenceSettings.from_env()
    if len(settings.api_token) < 24:
        raise ValueError('INFERENCE_API_TOKEN must contain at least 24 characters')
    engine = engine or InferenceEngine(settings)
    execute = executor or run_in_threadpool
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(_app):
        await execute(engine.reload)
        yield

    app = FastAPI(title='LogSentinel ML Inference', version='0.5.0', lifespan=lifespan)
    app.state.engine = engine
    app.add_middleware(InferenceGuard, settings=settings)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc):
        return JSONResponse({'detail': 'invalid_prediction_request'}, status_code=422)

    @app.exception_handler(InvalidRecord)
    async def invalid(_request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.exception_handler(InferenceConflict)
    async def conflict(_request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(InferenceCapacity)
    async def capacity(_request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=429)

    @app.exception_handler(InferenceUnavailable)
    async def unavailable(_request, _exc):
        return JSONResponse({'detail': 'prediction_unavailable'}, status_code=503)

    @app.exception_handler(Exception)
    async def safe_error(_request, _exc):
        logger.error('inference_request_failed')
        return JSONResponse({'detail': 'internal_service_error'}, status_code=500)

    @app.get('/health/live')
    async def live():
        return {'status': 'ok'}

    @app.get('/health/ready')
    async def ready():
        state = engine.health()
        return JSONResponse({'status': state['status'], 'model_name': state['model_name'],
                             'model_version': state['model_version']},
                            status_code=200 if state['status'] == 'ready' else 503)

    @app.get('/health/model')
    async def model_health():
        state = engine.health()
        return JSONResponse(state, status_code=200 if state['status'] == 'ready' else 503)

    @app.get('/api/v1/model', dependencies=[Security(bearer)])
    async def model():
        return engine.health()

    @app.post('/api/v1/predict', response_model=PredictionResponse, dependencies=[Security(bearer)])
    async def predict(request: SinglePredictionRequest):
        return (await execute(engine.predict, [request.event.domain()], request.context_id))[0]

    @app.post('/api/v1/predict/batch', response_model=BatchPredictionResponse, dependencies=[Security(bearer)])
    async def batch(request: BatchPredictionRequest):
        predictions = await execute(engine.predict, [event.domain() for event in request.events], request.context_id)
        return {'predictions': predictions, 'context_id': request.context_id}

    @app.post('/api/v1/models/reload', dependencies=[Security(bearer)])
    async def reload(request: ReloadRequest):
        if request.model_version is not None and request.model_alias is not None:
            raise InvalidRecord('choose_version_or_alias')
        state = await execute(engine.reload, request.model_version, request.model_alias)
        logger.info('inference_reload outcome=%s version=%s',
                    'failed' if state['last_load_error'] else 'loaded', state['model_version'])
        # A failed reload is visible even when the last healthy model keeps serving.
        return JSONResponse(state, status_code=503 if state['last_load_error'] else 200)

    @app.post('/api/v1/contexts/{context_id}/reset', dependencies=[Security(bearer)])
    async def reset(context_id: str):
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', context_id):
            raise InvalidRecord('invalid_context_id')
        removed = engine.reset_context(context_id)
        logger.info('inference_context_reset removed=%s', removed)
        return {'status': 'reset' if removed else 'not_found'}

    return app
