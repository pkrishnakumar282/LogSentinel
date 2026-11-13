"""Real collector → parser → redaction → durable sink → export integration."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

ROOT = Path(__file__).resolve().parents[2]


class LocalIngestionIntegration(unittest.TestCase):
    def test_all_sources_restart_and_redacted_export(self):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([
            str(ROOT), str(ROOT / 'services/log-collector'), str(ROOT / 'services/log-processor')]))
        with tempfile.TemporaryDirectory() as directory:
            sink, state = str(Path(directory) / 'sink.db'), str(Path(directory) / 'state.db')
            command = [sys.executable, '-m', 'log_collector', 'collect', '--once',
                       '--config', 'docs/collector.example.json', '--state', state,
                       '--local-sink', sink, '--template-mode', 'masked']
            first = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(sum(json.loads(line)['accepted'] for line in first.stdout.splitlines()), 7)
            second = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(sum(json.loads(line)['accepted'] for line in second.stdout.splitlines()), 0)
            exported = subprocess.run([sys.executable, '-m', 'log_processor', '--sink', sink, '--export'],
                                      cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            events = [json.loads(line) for line in exported.stdout.splitlines()]
            self.assertEqual(len(events), 7)
            self.assertEqual({event['source'] for event in events}, {'linux', 'nginx', 'application'})
            for secret in ['fixture-secret', 'demo@example.test', '192.0.2.10', 'discard-me']:
                self.assertNotIn(secret, exported.stdout)
            self.assertTrue(all(event['metadata']['synthetic'] for event in events))
            self.assertEqual(len({event['event_id'] for event in events}), 7)
            with closing(sqlite3.connect(sink)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 7)

    def test_simulation_processor_pipeline(self):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([
            str(ROOT), str(ROOT / 'services/log-collector'), str(ROOT / 'services/log-processor')]))
        with tempfile.TemporaryDirectory() as directory:
            sink = str(Path(directory) / 'sink.db')
            simulated = subprocess.run([sys.executable, '-m', 'log_collector', 'simulate', '--count', '25'],
                                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(simulated.returncode, 0, simulated.stderr)
            command = [sys.executable, '-m', 'log_processor', '--sink', sink, '--template-mode', 'masked']
            for expected in ['accepted', 'duplicate']:
                result = subprocess.run(command, input=simulated.stdout, cwd=ROOT, env=env,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipts = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(len(receipts), 25)
                self.assertEqual({item['status'] for item in receipts}, {expected})


if __name__ == '__main__':
    unittest.main()
