import json
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'services/ml-training')]
from ml_training.data import load_dataset
from ml_training.evaluation import GatePolicy, calibrate_threshold, gate_result, load_labels, supervised_metrics
from ml_training.integrity import read_sealed, seal
from ml_training.synthetic import synthetic_cases
from ml_training.tracking import validate_tracking_uri


class MetricTests(unittest.TestCase):
    def test_known_confusion_matrix_and_metrics(self):
        result = supervised_metrics([False, False, True, True], [.1, .9, .4, .8], .5)
        for key in ['precision', 'recall', 'f1', 'false_positive_rate']:
            self.assertEqual(result[key], .5)
        self.assertEqual(result['confusion_matrix']['values'], [[1, 1], [1, 1]])

    def test_undefined_metrics_are_null_not_fabricated(self):
        result = supervised_metrics([False, False], [.1, .2], .5)
        self.assertIsNone(result['precision'])
        self.assertIsNone(result['recall'])
        self.assertIsNone(result['f1'])
        self.assertEqual(result['false_positive_rate'], 0)
        self.assertFalse(gate_result(result, .1, GatePolicy(), ['local'] * 3)['passed'])

    def test_threshold_equality_is_normal(self):
        result = supervised_metrics([False, True], [.5, .5], .5)
        self.assertEqual(result['confusion_matrix']['values'], [[1, 0], [1, 0]])

    def test_calibration_uses_tied_score_groups(self):
        threshold, metrics = calibrate_threshold([False, True, False, True], [.1, .8, .2, .9], 0)
        self.assertEqual(threshold, .2)
        self.assertEqual(metrics['f1'], 1)
        self.assertEqual(metrics['false_positive_rate'], 0)
        tied, metrics = calibrate_threshold([False, True], [.7, .7], 0)
        self.assertEqual(tied, .7)
        self.assertEqual(metrics['recall'], 0)

    def test_calibration_can_choose_all_positive_when_policy_allows(self):
        threshold, metrics = calibrate_threshold([False, True], [.7, .7], 1)
        self.assertLess(threshold, .7)
        self.assertEqual(metrics['recall'], 1)

    def test_calibration_requires_both_classes(self):
        with self.assertRaises(ValueError):
            calibrate_threshold([False, False], [.1, .2], .05)

    def test_nonfinite_or_nonboolean_vectors_rejected(self):
        for truth, scores in [([False], [float('nan')]), ([0], [.1]), ([True], [])]:
            with self.assertRaises(ValueError):
                supervised_metrics(truth, scores, .5)

    def test_production_cannot_use_synthetic_evidence(self):
        metrics = supervised_metrics([False] * 95 + [True] * 5, [0.] * 95 + [1.] * 5, .5)
        self.assertTrue(gate_result(metrics, 1, GatePolicy(), ['synthetic'] * 3)['passed'])
        result = gate_result(metrics, 1, GatePolicy(scope='production'), ['synthetic'] * 3)
        self.assertFalse(result['passed'])
        self.assertIn('production_requires_nonsynthetic_training_and_evaluation', result['reasons'])

    def test_unlabeled_data_and_high_latency_fail_closed(self):
        result = gate_result(None, float('inf'), GatePolicy(), ['local'] * 3)
        self.assertFalse(result['passed'])
        self.assertIn('labeled_test_required', result['reasons'])
        self.assertIn('batch_latency_above_gate', result['reasons'])

    def test_gate_configuration_validation(self):
        for kwargs in [dict(min_precision=2), dict(scope='unknown'), dict(min_positives=0),
                       dict(max_batch_ms_per_event=float('nan'))]:
            with self.assertRaises(ValueError):
                GatePolicy(**kwargs)


class EvaluationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        cases = list(synthetic_cases(100))
        path = self.root / 'data.jsonl'
        path.write_text('\n'.join(event.to_json() for event, _ in cases) + '\n')
        self.labels = [label for _, label in cases]
        self.dataset = load_dataset(str(path))
        self.path = self.root / 'labels.jsonl'

    def tearDown(self):
        self.temp.cleanup()

    def write_labels(self, labels):
        self.path.write_text('\n'.join(json.dumps(label) for label in labels) + '\n')

    def test_labels_require_exact_coverage(self):
        self.write_labels(self.labels)
        values, version = load_labels(str(self.path), self.dataset)
        self.assertEqual(len(values), 100)
        self.assertEqual(len(version), 64)
        self.write_labels(self.labels[:-1])
        with self.assertRaisesRegex(ValueError, 'coverage'):
            load_labels(str(self.path), self.dataset)

    def test_duplicate_and_nonboolean_labels_rejected(self):
        for labels in [self.labels + [self.labels[0]],
                       [self.labels[0] | {'is_anomaly': 1}] + self.labels[1:]]:
            self.write_labels(labels)
            with self.assertRaises(ValueError):
                load_labels(str(self.path), self.dataset)

    def test_label_origin_cannot_be_model_predictions(self):
        self.write_labels([label | {'annotation_origin': 'model_prediction'} for label in self.labels])
        with self.assertRaises(ValueError):
            load_labels(str(self.path), self.dataset)

    def test_unlabeled_datasets_have_no_label_version(self):
        self.assertEqual(load_labels(None, self.dataset), (None, None))

    def test_manifest_detects_changed_evaluation(self):
        (self.root / 'plan.json').write_text('{"status":"frozen"}')
        seal(self.root, 'plan.json')
        self.assertEqual(read_sealed(str(self.root), 'plan.json')['status'], 'frozen')
        (self.root / 'plan.json').write_text('{"status":"modified"}')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            read_sealed(str(self.root), 'plan.json')

    def test_registry_code_uploads_contain_python_only(self):
        from ml_training.registry import curated_code
        with curated_code() as paths:
            files = [file for path in paths for file in Path(path).rglob('*') if file.is_file()]
            self.assertTrue(files)
            self.assertTrue(all(file.suffix == '.py' for file in files))
            self.assertFalse(any('datasets' in file.parts or 'artifacts' in file.parts for file in files))

    def test_tracking_uri_defaults_to_local_only(self):
        self.assertEqual(validate_tracking_uri('sqlite:///.local/store.db'), 'sqlite:///.local/store.db')
        for uri in ['file:///tmp/old-store', 'https://user:secret@localhost:5000', 'https://external.example:5000']:
            with self.assertRaises(ValueError):
                validate_tracking_uri(uri)


if __name__ == '__main__':
    unittest.main()
