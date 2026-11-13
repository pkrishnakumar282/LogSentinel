from dataclasses import replace
from datetime import UTC, datetime, timedelta
import math
from pathlib import Path
import sys
import unittest
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ml.features import FeatureConfig, FeatureExtractor, FeatureStateError, NUMERIC_COLUMNS
from shared.schemas import NormalizedLog


def event(index=0, **overrides):
    values = dict(event_id=str(uuid5(NAMESPACE_URL, f'feature-test:{index}')),
                  timestamp=(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index)).isoformat(),
                  source='application', service='worker', hostname='host', severity='INFO',
                  message=f'completed task {index}', template='completed task <NUM>', status_code=None,
                  metadata={'synthetic': True})
    return NormalizedLog(**(values | overrides))


class FeatureTests(unittest.TestCase):
    def test_first_event_has_zero_history(self):
        row = FeatureExtractor().transform_one(event(severity='ERROR'))
        for name in ['event_frequency', 'source_frequency', 'global_frequency', 'template_frequency', 'error_rate']:
            self.assertEqual(row[name], 0)
        self.assertEqual(row['severity'], 'ERROR')
        self.assertEqual(row['window_is_warm'], 0)

    def test_frequency_error_rate_and_service_isolation(self):
        extractor = FeatureExtractor()
        extractor.transform_one(event(0, severity='ERROR'))
        second = extractor.transform_one(event(1))
        self.assertEqual(second['event_frequency'], 1)
        self.assertEqual(second['error_rate'], 1)
        third = extractor.transform_one(event(2, service='other'))
        self.assertEqual(third['event_frequency'], 0)
        self.assertEqual(third['global_frequency'], 2)
        fourth = extractor.transform_one(event(3))
        self.assertEqual(fourth['event_frequency'], 2)
        self.assertEqual(fourth['template_frequency'], 2)
        self.assertEqual(fourth['error_rate'], 0.5)
        self.assertAlmostEqual(fourth['events_per_second'], 2/60)

    def test_equal_timestamps_do_not_count_each_other(self):
        extractor = FeatureExtractor()
        one = event(0)
        two = event(1, timestamp=one.timestamp, severity='CRITICAL')
        rows = extractor.transform([one, two, event(2)])
        self.assertEqual(rows[0]['event_frequency'], 0)
        self.assertEqual(rows[1]['event_frequency'], 0)
        self.assertEqual(rows[2]['event_frequency'], 2)
        self.assertEqual(rows[2]['error_rate'], 0.5)

    def test_boundary_is_strict_and_counters_are_pruned(self):
        extractor = FeatureExtractor(FeatureConfig(window_seconds=10))
        extractor.transform([event(0), event(9)])
        row = extractor.transform_one(event(10))
        self.assertEqual(row['event_frequency'], 1)
        self.assertEqual(row['window_is_warm'], 1)
        row = extractor.transform_one(event(30, service='other'))
        self.assertEqual(row['global_frequency'], 0)
        self.assertEqual(len(extractor.services), 0)
        self.assertEqual(len(extractor.cache), 1)

    def test_batch_and_one_at_a_time_are_identical(self):
        events = [event(index, severity='ERROR' if index % 3 == 0 else 'INFO') for index in range(100)]
        batched = FeatureExtractor().transform(events)
        single = FeatureExtractor()
        self.assertEqual(batched, [single.transform_one(value) for value in events])

    def test_chunked_replay_matches_full_replay(self):
        events = [event(index) for index in range(100)]
        extractor = FeatureExtractor()
        rows = extractor.transform(events[:40]) + extractor.transform(events[40:])
        self.assertEqual(rows, FeatureExtractor().transform(events))

    def test_future_changes_do_not_change_prior_rows(self):
        first = [event(index) for index in range(10)]
        changed = first[:5] + [replace(value, severity='CRITICAL', message='never seen future') for value in first[5:]]
        self.assertEqual(FeatureExtractor().transform(first)[:5], FeatureExtractor().transform(changed)[:5])

    def test_labels_and_identifiers_are_not_features(self):
        row = FeatureExtractor().transform_one(event(metadata={'is_anomaly': True, 'label': 'leak', 'synthetic': True}))
        for field in ['event_id', 'timestamp', 'metadata', 'is_anomaly', 'label', 'hostname']:
            self.assertNotIn(field, row)
        for name in NUMERIC_COLUMNS:
            self.assertTrue(math.isfinite(row[name]))

    def test_http_features_and_text_redaction(self):
        row = FeatureExtractor().transform_one(event(source='nginx', status_code=503,
                                                     message='GET /items?token=secret email=demo@example.test'))
        self.assertEqual(row['status_code'], 503)
        self.assertEqual(row['has_http_status'], 1)
        self.assertEqual(row['http_5xx'], 1)
        self.assertNotIn('secret', row['text'])
        self.assertNotIn('demo@example.test', row['text'])

    def test_duplicates_return_cached_rows_without_increasing_counts(self):
        extractor = FeatureExtractor()
        first = event(0)
        row = extractor.transform_one(first)
        extractor.transform_one(event(1))
        self.assertEqual(extractor.transform_one(first), row)
        self.assertEqual(extractor.transform_one(event(2))['event_frequency'], 2)
        with self.assertRaisesRegex(FeatureStateError, 'event_id_conflict'):
            extractor.transform_one(replace(first, message='changed'))

    def test_out_of_order_rejected(self):
        extractor = FeatureExtractor()
        extractor.transform_one(event(2))
        with self.assertRaisesRegex(FeatureStateError, 'out_of_order_event'):
            extractor.transform_one(event(1))

    def test_capacity_does_not_silently_corrupt_features(self):
        extractor = FeatureExtractor(FeatureConfig(window_seconds=10, max_events=2))
        extractor.transform([event(0), event(1)])
        with self.assertRaisesRegex(FeatureStateError, 'capacity'):
            extractor.transform_one(event(2))
        self.assertEqual(extractor.transform_one(event(11))['event_frequency'], 0)

    def test_configuration_validation(self):
        for value in [0, -1, float('nan'), float('inf'), True]:
            with self.assertRaises(ValueError):
                FeatureConfig(window_seconds=value)
        with self.assertRaises(ValueError):
            FeatureConfig(max_events=0)


if __name__ == '__main__':
    unittest.main()
