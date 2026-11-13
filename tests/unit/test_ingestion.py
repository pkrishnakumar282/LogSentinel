from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'services/log-processor'), str(ROOT / 'services/log-collector')]

from log_collector.files import FileCollector
from log_collector.journal import JournalCollector
from log_collector.simulation import simulated_records
from log_collector.state import CursorStore
from log_collector.transport import HTTPTransport
from log_processor.normalizer import Normalizer
from log_processor.templates import TemplateEngine
from shared.common.publishing import EventConflict, PublicationUnavailable, SQLitePublisher
from shared.common.redaction import redact
from shared.schemas import InvalidRecord, RawLog


def record(raw, source='application', **changes):
    values = dict(event_id=str(uuid4()), source=source, service='demo-api', hostname='fixture-host',
                  observed_at='2026-01-01T00:00:00Z', raw=raw, synthetic=True)
    return RawLog(**(values | changes))


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = Normalizer(TemplateEngine('masked'))

    def normalize(self, raw, source='application', **changes):
        return self.normalizer.normalize(record(raw, source, **changes))

    def test_application_json_normalized_and_unknown_fields_discarded(self):
        event = self.normalize(json.dumps(dict(timestamp='2026-01-01T05:30:00+05:30', level='WARN',
                                             message='slow query password=fixture-secret',
                                             email='discard@example.test', metadata={'secret': 'discard'})))
        self.assertEqual(event.timestamp, '2026-01-01T00:00:00+00:00')
        self.assertEqual(event.severity, 'WARNING')
        self.assertNotIn('fixture-secret', event.to_json())
        self.assertNotIn('discard', event.to_json())
        self.assertTrue(event.metadata['synthetic'])

    def test_application_python_default_timestamp(self):
        event = self.normalize('2026-01-01 05:30:00,123 ERROR worker - expected retry',
                               service='worker')
        self.assertEqual(event.severity, 'ERROR')
        self.assertEqual(event.timestamp, '2026-01-01T05:30:00.123000+00:00')
        self.assertNotIn('is_anomaly', event.to_dict())

    def test_configured_timezone_for_naive_timestamp(self):
        normalizer = Normalizer(TemplateEngine('masked'), 'Asia/Kolkata')
        event = normalizer.normalize(record('2026-01-01 05:30:00 INFO worker started'))
        self.assertEqual(event.timestamp, '2026-01-01T00:00:00+00:00')

    def test_nginx_status_and_identity_removal(self):
        line = (ROOT / 'tests/fixtures/nginx-access.txt').read_text().splitlines()[1]
        event = self.normalize(line, 'nginx', service='nginx')
        self.assertEqual(event.status_code, 503)
        self.assertEqual(event.severity, 'ERROR')
        self.assertNotIn('192.0.2.10', event.message)
        self.assertNotIn('fixture-secret', event.message)
        self.assertNotIn('synthetic-agent', event.message)
        self.assertNotIn('demo ', event.message)

    def test_nginx_error(self):
        line = (ROOT / 'tests/fixtures/nginx-error.txt').read_text().strip()
        event = self.normalize(line, 'nginx', service='nginx')
        self.assertEqual(event.metadata['format'], 'nginx_error')
        self.assertEqual(event.severity, 'ERROR')
        self.assertIn('[IP]', event.message)

    def test_journal(self):
        line = (ROOT / 'tests/fixtures/journal.jsonl').read_text().splitlines()[1]
        event = self.normalize(line, 'linux')
        self.assertEqual(event.timestamp, '2026-01-01T00:00:07+00:00')
        self.assertEqual(event.severity, 'CRITICAL')
        self.assertEqual(event.service, 'demo-worker.service')
        self.assertNotIn('__CURSOR', event.to_json())

    def test_syslog_year_rollover(self):
        event = self.normalize('Dec 31 23:59:50 host worker[22]: INFO job complete', 'linux')
        self.assertEqual(event.timestamp, '2025-12-31T23:59:50+00:00')
        self.assertTrue(event.metadata['year_inferred'])

    def test_severity_mapping(self):
        for source_level, expected in [('DEBUG', 'INFO'), ('WARN', 'WARNING'), ('FATAL', 'CRITICAL')]:
            event = self.normalize(f'2026-01-01T00:00:00Z {source_level} worker event')
            self.assertEqual(event.severity, expected)

    def test_authentication_filtered(self):
        for raw, source in [('Jan  1 00:00:00 host sshd[3]: Accepted publickey for demo', 'linux'),
                            ('2026-01-01T00:00:00Z INFO worker authentication failed for demo', 'application')]:
            with self.assertRaisesRegex(InvalidRecord, 'sensitive_auth_record'):
                self.normalize(raw, source)

    def test_invalid_records_have_safe_reason_codes(self):
        for raw, source in [('password=never-echo', 'nginx'), ('{}', 'linux'),
                            ('{"timestamp":"bad","message":"secret"}', 'application'),
                            ('{"timestamp":"2026-01-01Z","message":[]}', 'application')]:
            with self.assertRaises(InvalidRecord) as caught:
                self.normalize(raw, source)
            self.assertNotIn('never-echo', str(caught.exception))
            self.assertNotIn('secret', str(caught.exception))

    def test_invalid_status_values(self):
        for status in [True, '200', 99, 600]:
            raw = json.dumps(dict(timestamp='2026-01-01T00:00:00Z', message='event', status_code=status))
            with self.assertRaises(InvalidRecord):
                self.normalize(raw)

    def test_contract_limits(self):
        for changes in [dict(raw=''), dict(raw='x' * 65537), dict(event_id='bad'),
                        dict(hostname='bad host'), dict(observed_at='2026-01-01'),
                        dict(synthetic='false'), dict(source='unknown')]:
            with self.assertRaises(InvalidRecord):
                record('valid', **changes) if 'raw' not in changes else RawLog(
                    str(uuid4()), 'application', 'worker', 'host', '2026-01-01T00:00:00Z', changes['raw'])

    def test_invalid_unicode_is_safely_rejected(self):
        with self.assertRaisesRegex(InvalidRecord, 'invalid_text_encoding'):
            record('message \ud800')

    def test_templates_mask_numbers(self):
        first = self.normalize('2026-01-01T00:00:00Z INFO worker completed task 12')
        second = self.normalize('2026-01-01T00:00:01Z INFO worker completed task 77')
        self.assertEqual(first.template, second.template)
        self.assertNotEqual(first.message, second.message)

    def test_redaction_patterns_and_idempotence(self):
        cases = [
            ('password="top secret" token=abc api_key=xyz', ['top secret', 'abc', 'xyz']),
            ('Authorization: Bearer secret-token', ['secret-token']),
            ('Authorization: Basic dXNlcjpwYXNz', ['dXNlcjpwYXNz']),
            ('Cookie: session=private; name=demo', ['private', 'demo']),
            ('connect https://demo:private@host/path?anything=secret', ['demo:private', 'secret']),
            ('mail demo@example.test from 192.0.2.1 and 2001:db8::1', ['demo@example.test', '192.0.2.1', '2001:db8::1']),
            (r'password\x3dprivate', ['private']),
            ('\x1b[31mERROR\x1b[0m\x00 token=private\nnext', ['private', '\x1b', '\x00']),
        ]
        for message, sensitive in cases:
            clean = redact(message)
            for value in sensitive:
                self.assertNotIn(value, clean)
            self.assertEqual(redact(clean), clean)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'app.txt'
        self.path.write_text('2026-01-01T00:00:00Z INFO worker task 12\n')
        self.store = CursorStore(str(self.root / 'cursor.db'))
        self.sent = []
        class Transport:
            def send(inner, event):
                self.sent.append(event)
                return 'accepted'
        self.transport = Transport()
        self.collector = FileCollector(str(self.path), 'application', 'worker', 'host', self.store,
                                       self.transport, True)

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_and_partial_lines(self):
        self.assertEqual(self.collector.poll()['accepted'], 1)
        with self.path.open('a') as stream:
            stream.write('2026-01-01T00:00:01Z INFO worker partial')
        self.assertEqual(self.collector.poll()['accepted'], 0)
        with self.path.open('a') as stream:
            stream.write('\n')
        self.assertEqual(self.collector.poll()['accepted'], 1)
        self.assertEqual(self.collector.poll()['accepted'], 0)

    def test_failed_publish_preserves_cursor_and_event_id(self):
        attempts = []
        def fail(event):
            attempts.append(event.event_id)
            raise PublicationUnavailable('offline')
        with patch.object(self.transport, 'send', side_effect=fail):
            with self.assertRaises(PublicationUnavailable):
                self.collector.poll()
        self.assertEqual(self.store.get(self.collector.key)['offset'], 0)
        self.collector.poll()
        self.assertEqual(attempts[0], self.sent[0].event_id)

    def test_permanent_rejection_moves_cursor(self):
        with patch.object(self.transport, 'send', return_value='rejected'):
            self.assertEqual(self.collector.poll()['rejected'], 1)
        self.assertEqual(self.collector.poll()['accepted'], 0)

    def test_rotation_starts_new_identity(self):
        self.collector.poll()
        self.path.rename(self.root / 'app.1')
        self.path.write_text('2026-01-01T00:00:00Z INFO worker task 12\n')
        self.collector.poll()
        self.assertNotEqual(self.sent[0].event_id, self.sent[1].event_id)

    def test_copytruncate_and_rewrite(self):
        self.collector.poll()
        self.path.write_text('2026-01-01T00:00:01Z INFO worker task 77\n')
        self.assertEqual(self.collector.poll()['accepted'], 1)
        self.assertNotEqual(self.sent[0].event_id, self.sent[1].event_id)

    def test_oversized_and_invalid_utf8_records_skipped(self):
        self.path.write_bytes(b'x' * 70000 + b'\n\xff\n2026-01-01T00:00:00Z INFO worker good\n')
        result = self.collector.poll()
        self.assertEqual(result, dict(accepted=1, rejected=2, duplicate=0))

    def test_simulation_is_reproducible_and_explicit(self):
        first = [r.to_dict() for r in simulated_records(10)]
        self.assertEqual(first, [r.to_dict() for r in simulated_records(10)])
        self.assertTrue(all(r['synthetic'] for r in first))

    def test_user_journal_command_and_resume(self):
        collector = JournalCollector('host', self.store, self.transport, ['demo.service'])
        command = collector.command({'cursor': 'safe-cursor'})
        self.assertIn('--user', command)
        self.assertIn('--after-cursor=safe-cursor', command)
        self.assertNotIn('sudo', command)

    def test_journal_checkpoints_only_after_delivery(self):
        line = (ROOT / 'tests/fixtures/journal.jsonl').read_bytes().splitlines()[0] + b'\n'
        process = unittest.mock.Mock()
        process.stdout = io.BytesIO(line)
        process.wait.return_value = 0
        process.poll.return_value = 0
        collector = JournalCollector('host', self.store, self.transport)
        with patch('log_collector.journal.subprocess.Popen', return_value=process):
            self.assertEqual(collector.poll()['accepted'], 1)
        self.assertEqual(self.store.get(collector.key)['cursor'], 'synthetic-cursor-1')

    def test_transport_configuration(self):
        for url in ['file:///tmp/a', 'http://user:secret@host', 'broken']:
            with self.assertRaises(ValueError):
                HTTPTransport(url, 'x' * 24)


class PersistenceTests(unittest.TestCase):
    def test_duplicate_and_event_id_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            sink = SQLitePublisher(str(Path(directory) / 'events.db'))
            event = Normalizer(TemplateEngine('masked')).normalize(
                record('2026-01-01T00:00:00Z INFO worker task 12'))
            self.assertEqual(sink.publish(event).status, 'accepted')
            self.assertEqual(sink.publish(event).status, 'duplicate')
            self.assertEqual(sink.publish(replace(event, template='evolved template')).status, 'duplicate')
            with self.assertRaises(EventConflict):
                sink.publish(replace(event, message='different event'))
            self.assertEqual(len(list(sink.export())), 1)
            self.assertTrue(sink.ping())


class DeliveryFailureTests(unittest.TestCase):
    def test_http_retries_are_bounded_and_payload_is_not_in_errors(self):
        from urllib.error import URLError
        transport = HTTPTransport('http://localhost:8012/api/v1/ingest', 'x' * 32, attempts=3)
        event = record('2026-01-01T00:00:00Z INFO worker password=never-echo')
        with patch.object(transport.opener, 'open', side_effect=URLError('offline')) as opener:
            with patch('log_collector.transport.time.sleep'):
                with self.assertRaises(PublicationUnavailable) as caught:
                    transport.send(event)
        self.assertEqual(opener.call_count, 3)
        self.assertNotIn('never-echo', str(caught.exception))

    def test_http_permanent_rejection_and_auth_failure(self):
        from urllib.error import HTTPError
        transport = HTTPTransport('http://localhost:8012/api/v1/ingest', 'x' * 32)
        event = record('2026-01-01T00:00:00Z INFO worker event')
        for code in [422, 401, 409, 302]:
            error = HTTPError(transport.url, code, 'safe-message', {}, io.BytesIO())
            with patch.object(transport.opener, 'open', side_effect=error) as opener:
                if code == 422:
                    self.assertEqual(transport.send(event), 'rejected')
                else:
                    with self.assertRaises(PublicationUnavailable):
                        transport.send(event)
                self.assertEqual(opener.call_count, 1)

    def test_http_receipt_identity_checked(self):
        transport = HTTPTransport('http://localhost:8012/api/v1/ingest', 'x' * 32)
        event = record('2026-01-01T00:00:00Z INFO worker event')
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"event_id":"wrong","status":"accepted"}'
        with patch.object(transport.opener, 'open', return_value=response):
            with self.assertRaisesRegex(PublicationUnavailable, 'invalid_receipt'):
                transport.send(event)

    def test_journal_failure_preserves_cursor(self):
        line = (ROOT / 'tests/fixtures/journal.jsonl').read_bytes().splitlines()[0] + b'\n'
        with tempfile.TemporaryDirectory() as directory:
            store = CursorStore(str(Path(directory) / 'state.db'))
            transport = unittest.mock.Mock()
            transport.send.side_effect = PublicationUnavailable('offline')
            process = unittest.mock.Mock()
            process.stdout = io.BytesIO(line)
            process.poll.return_value = 0
            collector = JournalCollector('host', store, transport)
            with patch('log_collector.journal.subprocess.Popen', return_value=process):
                with self.assertRaises(PublicationUnavailable):
                    collector.poll()
            self.assertNotIn('cursor', store.get(collector.key))
            self.assertIn('since', store.get(collector.key))

    def test_empty_file_append_then_truncate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.txt'
            path.touch()
            store = CursorStore(str(Path(directory) / 'state.db'))
            transport = unittest.mock.Mock()
            transport.send.return_value = 'accepted'
            collector = FileCollector(str(path), 'application', 'worker', 'host', store, transport)
            collector.poll()
            path.write_text('2026-01-01T00:00:00Z INFO worker first\n')
            self.assertEqual(collector.poll()['accepted'], 1)
            path.write_text('2026-01-01T00:00:00Z INFO worker other\n')
            self.assertEqual(collector.poll()['accepted'], 1)


if __name__ == '__main__':
    unittest.main()
