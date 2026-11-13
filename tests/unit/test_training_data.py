from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'services/ml-training')]
from ml_training.config import TrainingConfig
from ml_training.data import DatasetError, load_dataset, temporal_split
from ml_training.synthetic import synthetic_cases


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'dataset.jsonl'
        self.events = [event for event, _ in synthetic_cases(100)]

    def tearDown(self):
        self.temp.cleanup()

    def write(self, events):
        self.path.write_text('\n'.join(event.to_json() for event in events) + '\n')

    def test_canonical_version_independent_of_input_order(self):
        self.write(self.events)
        first = load_dataset(str(self.path))
        self.write(list(reversed(self.events)))
        second = load_dataset(str(self.path))
        self.assertEqual(first.version, second.version)
        self.assertEqual(first.events, second.events)
        self.assertEqual(first.provenance['kind'], 'synthetic')

    def test_duplicate_ids_and_duplicate_logical_events_removed(self):
        self.write(self.events + [self.events[0], replace(self.events[1], event_id=self.events[-1].event_id)])
        with self.assertRaisesRegex(DatasetError, 'conflicting_dataset_event_id'):
            load_dataset(str(self.path))
        from uuid import uuid4
        self.write(self.events + [self.events[0], replace(self.events[1], event_id=str(uuid4()))])
        dataset = load_dataset(str(self.path))
        self.assertEqual(len(dataset.events), 100)
        self.assertEqual(dataset.duplicates_removed, 2)

    def test_time_split_and_same_timestamp_boundary(self):
        events = [replace(event, timestamp=self.events[(index // 3) * 3].timestamp)
                  for index, event in enumerate(self.events)]
        split = temporal_split(events)
        self.assertLess(split.train[-1].timestamp, split.holdout[0].timestamp)
        self.assertFalse({event.event_id for event in split.train} & {event.event_id for event in split.holdout})
        self.assertEqual(split.manifest()['train_count'] + split.manifest()['holdout_count'], 100)

    def test_insufficient_distinct_timestamps_rejected(self):
        events = [replace(event, timestamp=self.events[0].timestamp) for event in self.events]
        with self.assertRaises(DatasetError):
            temporal_split(events)
        with self.assertRaises(DatasetError):
            temporal_split(self.events[:7])

    def test_authentication_records_rejected_at_training_boundary(self):
        self.write([replace(self.events[0], service='sshd.service', message='user details')])
        with self.assertRaisesRegex(DatasetError, 'invalid_normalized_dataset_record'):
            load_dataset(str(self.path))

    def test_labels_are_not_accepted_as_input_columns(self):
        value = self.events[0].to_dict() | {'is_anomaly': True}
        self.path.write_text(json.dumps(value) + '\n')
        with self.assertRaisesRegex(DatasetError, 'invalid_normalized_dataset_record'):
            load_dataset(str(self.path))

    def test_resanitizes_before_building_snapshot(self):
        self.write([replace(self.events[0], message='password=private token=hidden',
                            template='password=private token=hidden')])
        dataset = load_dataset(str(self.path))
        self.assertNotIn('private', dataset.events[0].to_json())
        self.assertNotIn('hidden', dataset.events[0].to_json())

    def test_real_data_requires_explicit_provenance(self):
        events = [replace(event, metadata={'synthetic': False}) for event in self.events]
        self.write(events)
        with self.assertRaisesRegex(DatasetError, 'requires_provenance'):
            load_dataset(str(self.path))
        dataset = load_dataset(str(self.path), {'kind': 'local', 'dataset_name': 'approved-worker-logs'})
        self.assertEqual(dataset.provenance['kind'], 'local')
        self.assertNotEqual(dataset.provenance['kind'], 'synthetic')

    def test_synthetic_misrepresentation_and_mixed_data_rejected(self):
        self.write(self.events)
        with self.assertRaisesRegex(DatasetError, 'synthetic_provenance_mismatch'):
            load_dataset(str(self.path), {'kind': 'local'})
        self.write(self.events + [replace(self.events[0], metadata={'synthetic': False})])
        with self.assertRaises(DatasetError):
            load_dataset(str(self.path))

    def test_public_data_requires_recorded_license_verification(self):
        self.write([replace(self.events[0], metadata={'synthetic': False})])
        with self.assertRaisesRegex(DatasetError, 'license_verification'):
            load_dataset(str(self.path), {'kind': 'public', 'dataset_name': 'unverified'})

    def test_record_limit_and_malformed_json(self):
        self.write(self.events)
        with self.assertRaisesRegex(DatasetError, 'record_limit'):
            load_dataset(str(self.path), max_records=5)
        self.path.write_text('{password=do-not-echo}\n')
        with self.assertRaises(DatasetError) as caught:
            load_dataset(str(self.path))
        self.assertNotIn('do-not-echo', str(caught.exception))

    def test_training_config_rejects_unsafe_values(self):
        for kwargs in [dict(random_seed=-1), dict(holdout_fraction=1), dict(n_estimators=()),
                       dict(n_estimators=(10, 10)), dict(max_samples=0), dict(threshold_quantile=1),
                       dict(feature_config=None), dict(min_holdout=True), dict(holdout_fraction=None)]:
            with self.assertRaises(ValueError):
                TrainingConfig(**kwargs)

    def test_synthetic_error_retry_labels_can_be_normal(self):
        cases = list(synthetic_cases(100))
        self.assertTrue(all(event.metadata['synthetic'] for event, _ in cases))
        self.assertTrue(any(event.severity == 'ERROR' and not label['is_anomaly'] for event, label in cases))
        self.assertTrue(any(label['is_anomaly'] for _, label in cases))


if __name__ == '__main__':
    unittest.main()
