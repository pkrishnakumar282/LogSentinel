"""Dependency-backed tests skip visibly when dependencies/services are unavailable."""
import importlib.util
import json
import os
import socket
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'services/log-processor')]


def available(name):
    return importlib.util.find_spec(name) is not None


def thread_wakeup_available():
    # TestClient uses asyncio cross-thread wakeups; this sandbox can deny socketpair.send.
    try:
        left, right = socket.socketpair()
        try:
            left.send(b'x')
        finally:
            left.close()
            right.close()
        return True
    except OSError:
        return False


@unittest.skipUnless(thread_wakeup_available(), 'Sandbox denies asyncio socketpair wakeups')
@unittest.skipUnless(available('fastapi') and available('pydantic') and available('httpx'),
                     'FastAPI/Pydantic/httpx dependencies unavailable')
class ProcessorAPITests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from log_processor.api import create_app
        from log_processor.normalizer import Normalizer
        from log_processor.pipeline import ProcessingPipeline
        from log_processor.templates import TemplateEngine
        from shared.common.publishing import SQLitePublisher
        self.temp = tempfile.TemporaryDirectory()
        self.pipeline = ProcessingPipeline(Normalizer(TemplateEngine('masked')),
                                           SQLitePublisher(str(Path(self.temp.name) / 'sink.db')))
        self.client = TestClient(create_app(self.pipeline, 'x' * 32))
        self.headers = {'Authorization': 'Bearer ' + 'x' * 32}
        self.event = dict(event_id=str(uuid4()), source='application', service='worker', hostname='host',
                          observed_at='2026-01-01T00:00:00Z',
                          raw='2026-01-01T00:00:00Z INFO worker password=never-echo')

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_ingest_duplicate_and_redaction(self):
        for expected in ['accepted', 'duplicate']:
            response = self.client.post('/api/v1/ingest', headers=self.headers, json=self.event)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['status'], expected)
        self.assertNotIn('never-echo', '\n'.join(self.pipeline.publisher.export()))

    def test_auth_and_health(self):
        self.assertEqual(self.client.post('/api/v1/ingest', json=self.event).status_code, 401)
        self.assertEqual(self.client.get('/health/live').status_code, 200)
        self.assertEqual(self.client.get('/health/ready').status_code, 200)

    def test_rejections_never_echo_input(self):
        for event in [self.event | {'extra': 'never-echo'}, self.event | {'raw': 'never-echo'},
                      self.event | {'event_id': 'never-echo'}]:
            response = self.client.post('/api/v1/ingest', headers=self.headers, json=event)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn('never-echo', response.text)

    def test_body_limit(self):
        response = self.client.post('/api/v1/ingest', headers=self.headers,
                                    content=b'x' * 400001)
        self.assertEqual(response.status_code, 413)

    def test_dependency_failure(self):
        from unittest.mock import patch
        from shared.common.publishing import PublicationUnavailable
        with patch.object(self.pipeline.publisher, 'publish', side_effect=PublicationUnavailable('offline')):
            self.assertEqual(self.client.post('/api/v1/ingest', headers=self.headers,
                                              json=self.event).status_code, 503)


@unittest.skipUnless(available('drain3'), 'Drain3 dependency unavailable')
class DrainTests(unittest.TestCase):
    def test_actual_drain3_templates(self):
        from log_processor.templates import TemplateEngine
        engine = TemplateEngine()
        self.assertEqual(engine.extract('completed task 12'), engine.extract('completed task 99'))
        self.assertIsNotNone(engine.miner)


@unittest.skipUnless(available('redis') and os.environ.get('RUN_REDIS_INTEGRATION') == '1',
                     'Set RUN_REDIS_INTEGRATION=1 with redis-py and a disposable Redis server')
class RedisTests(unittest.TestCase):
    def test_real_stream_publish_dedup_conflict_and_backpressure(self):
        from dataclasses import replace
        from log_processor.normalizer import Normalizer
        from log_processor.templates import TemplateEngine
        from shared.common.publishing import EventConflict, QueueFull, RedisPublisher
        from shared.schemas import RawLog
        stream = f'test:normalized:{uuid4()}'
        publisher = RedisPublisher(os.environ.get('REDIS_HOST', '127.0.0.1'),
                                    int(os.environ.get('REDIS_PORT', '6379')),
                                    os.environ.get('REDIS_PASSWORD', ''), stream, capacity=1)
        event = Normalizer(TemplateEngine('masked')).normalize(RawLog(
            str(uuid4()), 'application', 'worker', 'host', '2026-01-01T00:00:00Z',
            '2026-01-01T00:00:00Z INFO worker password=never-echo'))
        try:
            self.assertTrue(publisher.ping())
            self.assertEqual(publisher.publish(event).status, 'accepted')
            self.assertEqual(publisher.publish(event).status, 'duplicate')
            with self.assertRaises(EventConflict):
                publisher.publish(replace(event, message='different'))
            with self.assertRaises(QueueFull):
                publisher.publish(replace(event, event_id=str(uuid4())))
            rows = publisher.client.xrange(stream)
            self.assertEqual(len(rows), 1)
            self.assertNotIn('never-echo', rows[0][1]['payload'])
        finally:
            publisher.client.delete(stream, f'{stream}:dedup:{event.event_id}')


if __name__ == '__main__':
    unittest.main()
