from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'services/ml-inference')]
from ml.features import FeatureConfig
from ml.model import ModelBundle
from ml_inference.config import InferenceSettings
from ml_inference.engine import InferenceCapacity, InferenceConflict, InferenceEngine, InferenceUnavailable
from ml_inference.loader import LoadedModel, ModelLoadError, audited_registration
from shared.schemas import InvalidRecord, NormalizedLog


def event(index=0, **changes):
    values = dict(event_id=str(uuid5(NAMESPACE_URL, f'inference-unit:{index}')),
                  timestamp=(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index)).isoformat(),
                  source='application', service='worker', hostname='synthetic-host', severity='INFO',
                  message=f'task completed {index}', template='task completed <NUM>', metadata={'synthetic': True})
    return NormalizedLog(**(values | changes))


class TestBundle:
    feature_config = FeatureConfig(window_seconds=10, max_events=100)
    feature_version = '1.0.0'
    threshold = .5

    def score_rows(self, rows):
        # A deterministic test estimator, only for state/transaction tests; real ML tests are separate.
        return [row['event_frequency'] / 10 for row in rows]


def loaded(version='1'):
    return LoadedModel(TestBundle(), 'unit-model', version, 'development', 'package-' + version, 'evaluation')


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.settings = InferenceSettings(scope='development')
        self.loader = unittest.mock.Mock()
        self.loader.load.return_value = loaded()
        self.engine = InferenceEngine(self.settings, self.loader)
        self.engine.reload()

    def test_registry_version_and_batch_single_parity(self):
        events = [event(index) for index in range(15)]
        batch = self.engine.predict(events, 'batch')
        single = [self.engine.predict([item], 'single')[0] for item in events]
        self.assertEqual(batch, single)
        self.assertTrue(all(item['model_version'] == '1' for item in batch))
        self.assertTrue(batch[-1]['context_warm'])
        self.assertFalse(batch[0]['context_warm'])

    def test_duplicate_cache_and_same_id_conflict(self):
        first = self.engine.predict([event()])[0]
        duplicate = self.engine.predict([event(), event()])
        self.assertEqual(duplicate, [first, first])
        self.assertEqual(self.engine.health()['statistics']['events_predicted'], 1)
        with self.assertRaisesRegex(InferenceConflict, 'event_id_conflict'):
            self.engine.predict([event(message='changed')])

    def test_invalid_batch_rolls_back_all_history(self):
        self.engine.predict([event(0)])
        with self.assertRaisesRegex(InferenceConflict, 'out_of_order'):
            self.engine.predict([event(3), event(2)])
        result = self.engine.predict([event(1)])[0]
        self.assertEqual(result['anomaly_score'], .1)

    def test_scoring_failure_does_not_commit_features(self):
        self.engine.predict([event(0)])
        with patch.object(self.engine.loaded.bundle, 'score_rows', side_effect=ValueError('private-data')):
            with self.assertRaises(InferenceUnavailable) as caught:
                self.engine.predict([event(1)])
            self.assertNotIn('private-data', str(caught.exception))
        self.assertEqual(self.engine.predict([event(1)])[0]['anomaly_score'], .1)

    def test_failed_reload_keeps_last_healthy_model_and_context(self):
        self.engine.predict([event(0)])
        self.loader.load.side_effect = ModelLoadError('approved_version_not_found')
        state = self.engine.reload(model_version='9')
        self.assertEqual(state['status'], 'ready')
        self.assertFalse(state['loading'])
        self.assertEqual(state['last_load_error'], 'approved_version_not_found')
        self.assertEqual(self.engine.predict([event(1)])[0]['model_version'], '1')

    def test_confirmed_revocation_of_current_version_stops_serving(self):
        self.engine.predict([event(0)])
        self.loader.load.side_effect = ModelLoadError('registry_approval_mismatch', '1', True)
        state = self.engine.reload()
        self.assertEqual(state['status'], 'unavailable')
        self.assertEqual(state['active_contexts'], 0)
        with self.assertRaises(InferenceUnavailable):
            self.engine.predict([event(1)])

    def test_successful_reload_is_atomic_and_resets_context(self):
        self.engine.predict([event(0)])
        self.loader.load.return_value = loaded('2')
        state = self.engine.reload(model_version='2')
        self.assertEqual(state['active_contexts'], 0)
        self.assertEqual(state['generation'], 2)
        self.assertEqual(self.engine.predict([event(0)])[0]['model_version'], '2')
        self.engine.reload()
        self.assertEqual(self.loader.load.call_args.args[0].model_version, '2')

    def test_same_version_reload_keeps_context(self):
        self.engine.predict([event(0)])
        state = self.engine.reload()
        self.assertEqual(state['generation'], 1)
        self.assertEqual(self.engine.predict([event(1)])[0]['anomaly_score'], .1)

    def test_context_capacity_and_expiry(self):
        engine = InferenceEngine(replace(self.settings, max_contexts=1, context_ttl_seconds=10), self.loader)
        engine.reload()
        engine.predict([event()], 'one')
        with self.assertRaises(InferenceCapacity):
            engine.predict([event()], 'two')
        engine.contexts['one'].touched -= 11
        engine.predict([event()], 'two')
        self.assertNotIn('one', engine.contexts)

    def test_development_threshold_override_is_explicit(self):
        engine = InferenceEngine(replace(self.settings, threshold=-1), self.loader)
        engine.reload()
        result = engine.predict([event()])[0]
        self.assertTrue(result['is_anomaly'])
        self.assertEqual(result['threshold_source'], 'environment_override')
        self.assertFalse(engine.health()['evaluation_applies_to_threshold'])

    def test_absent_model_is_unready_and_predictions_unavailable(self):
        self.loader.load.side_effect = ModelLoadError('approved_version_not_found')
        engine = InferenceEngine(self.settings, self.loader)
        engine.reload()
        self.assertEqual(engine.health()['status'], 'unavailable')
        with self.assertRaises(InferenceUnavailable):
            engine.predict([event()])

    def test_sensitive_logs_rejected_and_payload_sanitized(self):
        with self.assertRaises(InvalidRecord):
            self.engine.predict([event(service='sshd.service')])
        self.engine.predict([event(message='task password=private', template='task password=private')])
        cached = self.engine.contexts['default'].features.pending[0][1]
        self.assertNotIn('private', cached.message)
        self.assertNotIn('private', cached.template)

    def test_configuration_rejects_unsafe_limits_and_production_override(self):
        for kwargs in [dict(threshold=.2), dict(model_version='-1'), dict(max_batch=0),
                       dict(context_ttl_seconds=float('nan')), dict(tracking_uri='https://remote.example')]:
            with self.assertRaises(ValueError):
                InferenceSettings(**kwargs)

    def test_no_secret_in_settings_representation(self):
        self.assertNotIn('private-secret', repr(replace(self.settings, api_token='private-secret')))

    def test_missing_evidence_does_not_create_a_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.db'
            with self.assertRaises(ModelLoadError):
                audited_registration(str(path), 'model', '1')
            self.assertFalse(path.exists())

    def test_duplicate_replays_after_counter_eviction_are_not_stale_scores(self):
        self.engine.predict([event(0)])
        changed = event(13, event_id=event(0).event_id)
        output = self.engine.predict([event(12), changed])
        self.assertEqual(output[1]['anomaly_score'], .1)

    def test_concurrent_duplicate_requests_commit_once(self):
        results, errors = [], []
        def request():
            try:
                results.append(self.engine.predict([event()])[0])
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=request) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(self.engine.health()['statistics']['events_predicted'], 1)


if __name__ == '__main__':
    unittest.main()
