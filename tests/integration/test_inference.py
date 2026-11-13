"""Approved real MLflow bundles, real scoring, and socket-free inference ASGI requests."""
import asyncio
from dataclasses import replace
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'services/ml-training'), str(ROOT / 'services/ml-inference'), str(ROOT / 'services/alert-manager')]
READY = all(importlib.util.find_spec(name) for name in ['mlflow', 'sklearn', 'pandas', 'fastapi', 'pydantic'])
os.environ.setdefault('MLFLOW_DISABLE_AGENT_HINT', '1')
os.environ.setdefault('MLFLOW_ENABLE_TELEMETRY', 'false')


async def immediate(function, *args):
    # Test executor avoids sandbox-denied asyncio thread wakeups. Production uses a threadpool.
    return function(*args)


async def asgi_request(app, method, path, data=None, token=None, raw=None):
    payload = raw if raw is not None else json.dumps(data).encode() if data is not None else b''
    headers = [(b'content-type', b'application/json')]
    if token is not None:
        headers.append((b'authorization', b'Bearer ' + token.encode()))
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method,
             'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': b'',
             'root_path': '', 'headers': headers, 'server': ('localhost', 8013), 'client': ('test', 1)}
    messages, delivered = [], False
    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {'type': 'http.request', 'body': payload, 'more_body': False}
        return {'type': 'http.disconnect'}
    async def send(message):
        messages.append(message)
    await app(scope, receive, send)
    status = next(message['status'] for message in messages if message['type'] == 'http.response.start')
    body = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
    return status, json.loads(body)


@unittest.skipUnless(READY, 'Install inference/training dependencies for real registered-model tests')
class InferenceIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ml_training.config import TrainingConfig
        from ml_training.data import load_dataset
        from ml_training.evaluation import GatePolicy
        from ml_training.lifecycle import calibrate, evaluate
        from ml_training.registry import register_evaluation, promote
        from ml_training.synthetic import synthetic_cases, future_cases
        from ml_training.train import train
        from ml_training.tracking import client_for
        from ml_inference.config import InferenceSettings
        from shared.schemas.logs import utc_time
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.uri = 'sqlite:///' + str(cls.root / 'mlflow.db')
        cls.audit = str(cls.root / 'audit.db')
        cls.token = 'test-only-inference-token-not-for-production'
        cls.admin = 'test-only-admin-token-not-for-production'
        def write(name, cases):
            cases = list(cases)
            path, labels = cls.root / f'{name}.jsonl', cls.root / f'{name}.labels.jsonl'
            path.write_text('\n'.join(event.to_json() for event, _ in cases) + '\n')
            labels.write_text('\n'.join(json.dumps(label) for _, label in cases) + '\n')
            return load_dataset(str(path)), str(labels)
        cls.history, _ = write('history', synthetic_cases(100))
        start = utc_time(cls.history.events[-1].timestamp) + timedelta(seconds=1)
        cls.validation, val_labels = write('validation', future_cases(start, 100, 84, 'validation'))
        cls.test, test_labels = write('test', future_cases(start + timedelta(seconds=100), 100, 85, 'test'))
        cls.training = train(cls.history, str(cls.root / 'training'), TrainingConfig(n_estimators=(8,), max_samples=64))
        policy = GatePolicy(min_test_records=1, min_positives=1, min_negatives=1,
                            min_precision=0, min_recall=0, min_f1=0, max_false_positive_rate=1)
        cls.registrations = []
        cls.evaluations = []
        for minimum in (1, 2):
            plan = calibrate(str(cls.training), cls.history, cls.validation, val_labels,
                             str(cls.root / 'plans'), replace(policy, min_test_records=minimum))
            evaluated = evaluate(str(cls.training), str(plan), cls.history, cls.validation, cls.test,
                                 test_labels, str(cls.root / 'evaluations'))
            cls.evaluations.append(evaluated)
            with patch.dict(os.environ, {'ADMIN_API_TOKEN': cls.admin}):
                registered = register_evaluation(str(evaluated), cls.uri, cls.audit, cls.admin,
                    'inference-test', 'inference-test-model', artifact_root=str(cls.root / 'artifacts'))
            cls.registrations.append(registered)
        with patch.dict(os.environ, {'ADMIN_API_TOKEN': cls.admin}):
            promote(cls.uri, cls.audit, cls.admin, 'inference-test', 'inference-test-model', '1',
                    'staging', 'temporary integration model only')
        cls.settings = InferenceSettings(tracking_uri=cls.uri, audit_path=cls.audit,
            model_name='inference-test-model', model_version='1', scope='development',
            api_token=cls.token, admin_token=cls.admin)
        cls.client = client_for(cls.uri)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def engine(self, **kwargs):
        from ml_inference.engine import InferenceEngine
        engine = InferenceEngine(replace(self.settings, **kwargs))
        state = engine.reload()
        self.assertEqual(state['status'], 'ready', state)
        return engine

    def test_actual_approved_loader_and_shared_pipeline_score_parity(self):
        from ml_training.integrity import read_sealed
        engine = self.engine()
        engine.predict(tuple(self.history.events) + tuple(self.validation.events), 'warm')
        predictions = engine.predict(self.test.events, 'warm')
        report = read_sealed(str(self.evaluations[0]), 'evaluation.json')
        expected = [json.loads(line) for line in (self.evaluations[0] /
                    (report['selected_candidate_id'] + '.predictions.jsonl')).read_text().splitlines()]
        import numpy as np
        np.testing.assert_allclose([item['anomaly_score'] for item in predictions],
                                   [item['anomaly_score'] for item in expected], rtol=0, atol=1e-12)
        self.assertTrue(all(item['model_version'] == '1' for item in predictions))
        self.assertEqual([item['is_anomaly'] for item in predictions], [item['is_anomaly'] for item in expected])

    def test_real_model_single_batch_and_duplicate_parity(self):
        engine = self.engine()
        batch = engine.predict(self.test.events, 'batch')
        single = [engine.predict([event], 'single')[0] for event in self.test.events]
        self.assertEqual(batch, single)
        self.assertEqual(engine.predict([self.test.events[-1]], 'single')[0], single[-1])

    def test_absent_approval_production_scope_and_missing_version_fail_closed(self):
        from ml_inference.loader import ModelLoadError, RegistryLoader
        for settings in [replace(self.settings, audit_path=str(self.root / 'missing.db')),
                         replace(self.settings, scope='production'), replace(self.settings, model_version='999')]:
            with patch('joblib.load', side_effect=AssertionError('must not unpickle')):
                with self.assertRaises(ModelLoadError):
                    RegistryLoader().load(settings)

    def test_runtime_mismatch_rejected_before_unpickle(self):
        from ml.model import runtime_versions
        from ml_inference.loader import ModelLoadError, RegistryLoader
        wrong = runtime_versions() | {'numpy': '0.0.0'}
        with patch('ml_inference.loader.runtime_versions', return_value=wrong):
            with patch('joblib.load', side_effect=AssertionError('must verify runtime first')):
                with self.assertRaisesRegex(ModelLoadError, 'runtime_incompatible'):
                    RegistryLoader().load(self.settings)

    def test_registry_tag_rejection_prevents_unpickle(self):
        from ml_inference.loader import ModelLoadError, RegistryLoader
        self.client.set_model_version_tag('inference-test-model', '1', 'logsentinel.validation_status', 'rejected')
        try:
            with patch('joblib.load', side_effect=AssertionError('must not unpickle')):
                with self.assertRaisesRegex(ModelLoadError, 'approval_mismatch'):
                    RegistryLoader().load(self.settings)
        finally:
            self.client.set_model_version_tag('inference-test-model', '1', 'logsentinel.validation_status', 'passed')

    def test_corrupted_package_is_rejected_before_unpickling(self):
        import mlflow.artifacts
        from ml_inference.loader import ModelLoadError, RegistryLoader
        from shared.common.model_integrity import artifact_location
        source = self.client.get_model_version('inference-test-model', '1').source
        with tempfile.TemporaryDirectory() as directory:
            package = Path(mlflow.artifacts.download_artifacts(artifact_uri=artifact_location(self.client, source), tracking_uri=self.uri,
                            registry_uri=self.uri, dst_path=directory))
            (package / 'MLmodel').write_text('corrupted')
            with patch('mlflow.artifacts.download_artifacts', return_value=str(package)):
                with patch('joblib.load', side_effect=AssertionError('must not unpickle')):
                    with self.assertRaisesRegex(ModelLoadError, 'package_checksum'):
                        RegistryLoader().load(self.settings)

    def test_alias_resolves_once_and_requires_explicit_reload(self):
        self.client.set_registered_model_alias('inference-test-model', 'staging', '1')
        engine = self.engine(model_version='', model_alias='staging')
        self.client.set_registered_model_alias('inference-test-model', 'staging', '2')
        try:
            self.assertEqual(engine.predict([self.test.events[0]])[0]['model_version'], '1')
            self.assertEqual(engine.reload()['model_version'], '2')
            self.assertEqual(engine.health()['active_contexts'], 0)
        finally:
            self.client.set_registered_model_alias('inference-test-model', 'staging', '1')

    def test_current_version_revocation_withdraws_readiness(self):
        engine = self.engine()
        self.client.set_model_version_tag('inference-test-model', '1', 'logsentinel.validation_status', 'rejected')
        try:
            self.assertEqual(engine.reload()['status'], 'unavailable')
        finally:
            self.client.set_model_version_tag('inference-test-model', '1', 'logsentinel.validation_status', 'passed')

    def test_failed_real_reload_preserves_serving_version(self):
        engine = self.engine()
        engine.predict([self.test.events[0]])
        state = engine.reload(model_version='999')
        self.assertEqual(state['status'], 'ready')
        self.assertIsNotNone(state['last_load_error'])
        self.assertEqual(engine.predict([self.test.events[1]])[0]['model_version'], '1')

    def test_asgi_auth_prediction_batch_validation_and_reload(self):
        from ml_inference.api import create_app
        async def exercise():
            app = create_app(self.settings, executor=immediate)
            async with app.router.lifespan_context(app):
                status, body = await asgi_request(app, 'GET', '/health/ready')
                self.assertEqual(status, 200, body)
                self.assertEqual(body['model_version'], '1')
                status, _ = await asgi_request(app, 'POST', '/api/v1/predict', data={})
                self.assertEqual(status, 401)
                status, prediction = await asgi_request(app, 'POST', '/api/v1/predict', token=self.token,
                    data={'event': self.test.events[0].to_dict()})
                self.assertEqual(status, 200, prediction)
                self.assertEqual(prediction['model_version'], '1')
                status, batch = await asgi_request(app, 'POST', '/api/v1/predict/batch', token=self.token,
                    data={'events': [event.to_dict() for event in self.test.events[1:4]]})
                self.assertEqual(status, 200, batch)
                self.assertEqual(len(batch['predictions']), 3)
                status, invalid = await asgi_request(app, 'POST', '/api/v1/predict', token=self.token,
                    data={'event': self.test.events[0].to_dict() | {'source': 'private-secret'}})
                self.assertEqual(status, 422)
                self.assertNotIn('private-secret', json.dumps(invalid))
                status, _ = await asgi_request(app, 'POST', '/api/v1/models/reload', token=self.token, data={})
                self.assertEqual(status, 403)
                status, body = await asgi_request(app, 'POST', '/api/v1/models/reload', token=self.admin,
                                                  data={'model_version': '999'})
                self.assertEqual(status, 503, body)
                self.assertEqual(body['model_version'], '1')
                status, body = await asgi_request(app, 'POST', '/api/v1/models/reload', token=self.admin,
                                                  data={'model_version': '2'})
                self.assertEqual(status, 200, body)
                self.assertEqual(body['model_version'], '2')
        asyncio.run(exercise())

    def test_durable_stream_restart_matches_real_model_scores(self):
        from sqlalchemy import select,func
        from shared.pipeline.database import make_engine
        from shared.pipeline.store import PipelineStore
        from shared.pipeline.worker import StreamWorker,OutboxRelay
        from shared.pipeline import models
        from ml_inference.stream_worker import InferenceHandler
        from alert_manager.service import AlertService,AlertHandler
        from alert_manager.policy import AlertPolicy
        sys.path[:0]=[str(ROOT/'tests/support'),str(ROOT/'services/alert-manager')]
        from memory_stream import MemoryGroup,MemoryPublisher
        database=make_engine('sqlite:///'+str(self.root/'durable-stream.db'))
        store=PipelineStore(database)
        store.initialize_development()
        source=MemoryGroup()
        engine=self.engine()
        handler=InferenceHandler(engine,store,source)
        worker=StreamWorker(source,store,handler)
        expected=engine.loaded.bundle.new_session().predict(self.test.events)
        try:
            for index,event in enumerate(self.test.events):
                source.add(event.to_dict())
                if index==80:
                    source.fail_ack=True
                    with self.assertRaises(ConnectionError):worker.tick()
                    engine=self.engine()
                    handler=InferenceHandler(engine,store,source)
                    worker=StreamWorker(source,store,handler)
                    self.assertEqual(worker.tick(),'duplicate')
                else:
                    self.assertEqual(worker.tick(),'predicted')
            with database.connect() as connection:
                self.assertEqual(connection.execute(select(func.count()).select_from(models.predictions)).scalar_one(),len(expected))
            for event,result in zip(self.test.events,expected,strict=True):
                receipt=store.receipt(source.stage,event.event_id)
                prediction,_=store.prediction(receipt['reference_id'])
                self.assertAlmostEqual(prediction['anomaly_score'],result['anomaly_score'],places=12)
            destination=MemoryGroup('predictions','alerts')
            relay=OutboxRelay(store,MemoryPublisher(destination))
            relay.flush()
            alerts=AlertService(store,AlertPolicy(require_warm_context=False))
            alert_worker=StreamWorker(destination,store,AlertHandler(alerts,destination.stage))
            while destination.entries:alert_worker.tick()
            with database.connect() as connection:
                self.assertEqual(connection.execute(select(func.count()).select_from(models.occurrences)).scalar_one(),sum(result['is_anomaly'] for result in expected))
        finally:database.dispose()

    def test_cli_load_health_and_prediction_without_training_imports(self):
        import subprocess
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'services/ml-inference'), str(ROOT / 'services/alert-manager')]),
                   MLFLOW_TRACKING_URI=self.uri, REGISTRY_AUDIT_PATH=self.audit,
                   MODEL_NAME='inference-test-model', MODEL_VERSION='1', INFERENCE_SCOPE='development')
        check = subprocess.run([sys.executable, '-m', 'ml_inference', 'check'], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(json.loads(check.stdout)['model_version'], '1')
        predict = subprocess.run([sys.executable, '-m', 'ml_inference', 'predict', '--input',
                                  str(self.root / 'test.jsonl')], cwd=ROOT,
                                  env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(predict.returncode, 0, predict.stderr)
        records = [json.loads(line) for line in predict.stdout.splitlines()]
        self.assertEqual(len(records), len(self.test.events))
        self.assertTrue(all(row['model_version'] == '1' for row in records))

    def test_asgi_absent_model_liveness_and_prediction_failure(self):
        from ml_inference.api import create_app
        async def exercise():
            app = create_app(replace(self.settings, model_version='999'), executor=immediate)
            async with app.router.lifespan_context(app):
                self.assertEqual((await asgi_request(app, 'GET', '/health/live'))[0], 200)
                self.assertEqual((await asgi_request(app, 'GET', '/health/ready'))[0], 503)
                status, _ = await asgi_request(app, 'POST', '/api/v1/predict', token=self.token,
                                               data={'event': self.test.events[0].to_dict()})
                self.assertEqual(status, 503)
        asyncio.run(exercise())

    def test_asgi_body_rate_and_batch_limits(self):
        from ml_inference.api import create_app
        async def exercise():
            engine = self.engine()
            app = create_app(replace(self.settings, max_body_bytes=10), engine=engine, executor=immediate)
            self.assertEqual((await asgi_request(app, 'POST', '/api/v1/predict', token=self.token,
                                                 raw=b'x' * 11))[0], 413)
            app = create_app(replace(self.settings, rate_per_minute=1), engine=engine, executor=immediate)
            self.assertEqual((await asgi_request(app, 'GET', '/api/v1/model', token=self.token))[0], 200)
            self.assertEqual((await asgi_request(app, 'GET', '/api/v1/model', token=self.token))[0], 429)
            limited = self.engine(max_batch=1)
            app = create_app(limited.settings, engine=limited, executor=immediate)
            status, body = await asgi_request(app, 'POST', '/api/v1/predict/batch', token=self.token,
                data={'events': [event.to_dict() for event in self.test.events[:2]]})
            self.assertEqual(status, 422, body)
        asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
