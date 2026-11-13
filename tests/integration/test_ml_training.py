"""Real scikit-learn training, serialization and replay integration (no mock estimators)."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'services/ml-training')]
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ['numpy', 'pandas', 'sklearn', 'joblib'])


@unittest.skipUnless(DEPENDENCIES, 'Install services/ml-training/requirements.txt to execute real ML tests')
class MLTrainingIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ml_training.config import TrainingConfig
        from ml_training.data import load_dataset, temporal_split
        from ml_training.synthetic import synthetic_cases
        from ml_training.train import train
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.dataset_path = cls.root / 'data.jsonl'
        cls.dataset_path.write_text('\n'.join(event.to_json() for event, _ in synthetic_cases(160)) + '\n')
        cls.dataset = load_dataset(str(cls.dataset_path))
        cls.config = TrainingConfig(n_estimators=(12, 24), max_samples=64)
        cls.split = temporal_split(cls.dataset.events)
        cls.artifact = train(cls.dataset, str(cls.root / 'artifacts'), cls.config)
        cls.report = json.loads((cls.artifact / 'report.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_four_candidates_share_actual_holdout_ids(self):
        self.assertEqual(len(self.report['candidates']), 4)
        ids = [event.event_id for event in self.split.holdout]
        for item in self.report['candidates']:
            path = self.artifact / (item['candidate_id'] + '.holdout.jsonl')
            predictions = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row['event_id'] for row in predictions], ids)
            self.assertGreater(item['feature_count'], 0)
            self.assertGreaterEqual(item['holdout_predict_ms_per_event'], 0)
        self.assertLess(self.report['split']['train_end'], self.report['split']['holdout_start'])

    def test_actual_fitted_vectorizer_scaler_and_training_only_threshold(self):
        import numpy as np
        from ml.features import FeatureExtractor
        from ml.features.contract import NUMERIC_COLUMNS
        from ml_training.artifacts import load_bundle
        rows = FeatureExtractor(self.config.feature_config).transform(self.split.train)
        for candidate in self.report['candidates']:
            bundle = load_bundle(str(self.artifact), candidate['candidate_id'])
            preprocess = bundle.pipeline.named_steps['features']
            scaler = preprocess.named_transformers_['numeric']
            means = np.array([[row[name] for name in NUMERIC_COLUMNS] for row in rows]).mean(axis=0)
            np.testing.assert_allclose(scaler.mean_, means)
            expected = np.quantile(bundle.score_rows(rows), self.config.threshold_quantile)
            self.assertAlmostEqual(bundle.threshold, float(expected))
            if bundle.variant == 'tfidf':
                vectorizer = preprocess.named_transformers_['text']
                self.assertNotIn('corruption', vectorizer.vocabulary_)
                self.assertIn('scheduled', vectorizer.vocabulary_)
            else:
                self.assertNotIn('text', preprocess.named_transformers_)

    def test_loaded_bundle_scores_match_batch_and_single_with_warmup(self):
        import numpy as np
        from ml_training.artifacts import load_bundle
        for candidate in self.report['candidates']:
            bundle = load_bundle(str(self.artifact), candidate['candidate_id'])
            batch = bundle.new_session(self.split.train).predict(self.split.holdout)
            session = bundle.new_session(self.split.train)
            single = [session.predict([event])[0] for event in self.split.holdout]
            expected = [json.loads(line)['anomaly_score'] for line in
                        (self.artifact / (candidate['candidate_id'] + '.holdout.jsonl')).read_text().splitlines()]
            np.testing.assert_allclose([item['anomaly_score'] for item in batch], expected, rtol=0, atol=1e-12)
            np.testing.assert_allclose([item['anomaly_score'] for item in single], expected, rtol=0, atol=1e-12)
            self.assertEqual([item['is_anomaly'] for item in single], [item['is_anomaly'] for item in batch])
            self.assertTrue(all(item['model_version'] == candidate['candidate_id'] for item in batch))

    def test_configurable_threshold_and_strict_decision_boundary(self):
        from dataclasses import replace
        from ml_training.artifacts import load_bundle
        bundle = load_bundle(str(self.artifact), self.report['candidates'][0]['candidate_id'])
        event = self.split.holdout[0]
        score = bundle.new_session(self.split.train).predict([event])[0]['anomaly_score']
        at_boundary = replace(bundle, threshold=score).new_session(self.split.train).predict([event])[0]
        above_boundary = replace(bundle, threshold=score - 0.001).new_session(self.split.train).predict([event])[0]
        self.assertFalse(at_boundary['is_anomaly'])
        self.assertTrue(above_boundary['is_anomaly'])
        self.assertEqual(above_boundary['prediction'], 'anomaly')
        self.assertEqual(above_boundary['anomaly_score'], at_boundary['anomaly_score'])

    def test_scoring_never_refits_and_handles_unknown_categories(self):
        from dataclasses import replace
        from ml_training.artifacts import load_bundle
        bundle = load_bundle(str(self.artifact), self.report['candidates'][0]['candidate_id'])
        event = replace(self.split.holdout[0], service='never-seen-service', message='brandnew vocabulary')
        with patch.object(bundle.pipeline, 'fit', side_effect=AssertionError('must not fit')):
            with patch.object(bundle.pipeline.named_steps['features'], 'fit_transform',
                              side_effect=AssertionError('must not fit preprocessing')):
                result = bundle.new_session(self.split.train).predict(iter([event]))
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0]['anomaly_score'], float)

    def test_repeat_training_is_deterministic(self):
        from ml_training.train import train
        repeated = train(self.dataset, str(self.root / 'repeat'), self.config)
        report = json.loads((repeated / 'report.json').read_text())
        self.assertEqual(report['dataset_version'], self.report['dataset_version'])
        self.assertEqual(report['split'], self.report['split'])
        for first, second in zip(self.report['candidates'], report['candidates'], strict=True):
            self.assertEqual(first['threshold'], second['threshold'])
            self.assertEqual(first['holdout_scores'], second['holdout_scores'])
            name = first['candidate_id'] + '.holdout.jsonl'
            self.assertEqual((self.artifact / name).read_text(), (repeated / name).read_text())

    def test_changed_holdout_does_not_change_fit_or_threshold(self):
        from dataclasses import replace
        from ml_training.data import load_dataset
        from ml_training.train import train
        changed = self.root / 'changed-holdout.jsonl'
        events = list(self.split.train) + [replace(event, message='futureonly vocabulary',
                    service='futureonly-service', severity='CRITICAL') for event in self.split.holdout]
        changed.write_text('\n'.join(event.to_json() for event in events) + '\n')
        dataset = load_dataset(str(changed))
        self.assertNotEqual(dataset.version, self.dataset.version)
        directory = train(dataset, str(self.root / 'changed-artifacts'), self.config)
        report = json.loads((directory / 'report.json').read_text())
        for original, altered in zip(self.report['candidates'], report['candidates'], strict=True):
            self.assertEqual(original['threshold'], altered['threshold'])
            self.assertEqual(original['train_scores'], altered['train_scores'])
            self.assertEqual(original['feature_count'], altered['feature_count'])

    def test_dependency_mismatch_rejected_before_unpickling(self):
        import joblib
        from ml_training.artifacts import load_bundle, write_checksums
        target = self.root / 'wrong-runtime'
        shutil.copytree(self.artifact, target)
        report = json.loads((target / 'report.json').read_text())
        report['dependencies']['scikit-learn'] = '0.0.0'
        (target / 'report.json').write_text(json.dumps(report))
        write_checksums(target)
        with patch.object(joblib, 'load', side_effect=AssertionError('must validate before load')):
            with self.assertRaisesRegex(ValueError, 'dependency_version_mismatch'):
                load_bundle(str(target), self.report['candidates'][0]['candidate_id'])

    def test_training_cli_from_dataset_to_artifact(self):
        import os
        import subprocess
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'services/ml-training')]))
        output = self.root / 'cli-artifacts'
        result = subprocess.run([sys.executable, '-m', 'ml_training', 'train', '--dataset',
                    str(self.dataset_path), '--output', str(output), '--estimators', '8'],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'candidate_not_approved')
        self.assertTrue((Path(receipt['artifact_directory']) / 'manifest.json').is_file())

    def test_unlabeled_report_does_not_invent_supervised_metrics_or_promotion(self):
        forbidden = {'accuracy', 'precision', 'recall', 'f1', 'f1_score', 'confusion_matrix', 'false_positive_rate'}
        def check(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & forbidden)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(self.report)
        self.assertEqual(self.report['status'], 'candidate_not_approved')
        self.assertEqual(self.report['dataset_provenance']['kind'], 'synthetic')

    def test_artifact_corruption_rejected_before_unpickling(self):
        import joblib
        from ml_training.artifacts import load_bundle
        target = self.root / 'corrupted'
        shutil.copytree(self.artifact, target)
        candidate_id = self.report['candidates'][0]['candidate_id']
        with (target / f'{candidate_id}.joblib').open('ab') as stream:
            stream.write(b'corrupted')
        with patch.object(joblib, 'load', side_effect=AssertionError('must verify before unpickling')):
            with self.assertRaisesRegex(ValueError, 'checksum_mismatch'):
                load_bundle(str(target), candidate_id)

    def test_failed_training_removes_partial_artifacts(self):
        from dataclasses import replace
        from ml_training.train import train
        broken = replace(self.dataset, events=tuple(replace(event, message='!!!') for event in self.dataset.events))
        output = self.root / 'failed'
        with self.assertRaises(ValueError):
            train(broken, str(output), self.config)
        self.assertEqual(list(output.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
