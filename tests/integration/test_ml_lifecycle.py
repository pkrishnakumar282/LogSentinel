"""Real MLflow SQLite tracking/registry plus actual scikit-learn evaluation; no mock registry."""
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
sys.path[:0] = [str(ROOT), str(ROOT / 'services/ml-training')]
READY = all(importlib.util.find_spec(name) for name in ['mlflow', 'sklearn', 'pandas', 'joblib'])
os.environ.setdefault('MLFLOW_DISABLE_AGENT_HINT', '1')
os.environ.setdefault('MLFLOW_ENABLE_TELEMETRY', 'false')


@unittest.skipUnless(READY, 'Install requirements-mlflow.txt to run real MLflow lifecycle integration')
class LifecycleIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ml_training.config import TrainingConfig
        from ml_training.data import load_dataset
        from ml_training.synthetic import synthetic_cases, future_cases
        from ml_training.train import train
        from shared.schemas.logs import utc_time
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.paths = {}
        def write(role, cases):
            data, labels = cls.root / f'{role}.jsonl', cls.root / f'{role}.labels.jsonl'
            cases = list(cases)
            data.write_text('\n'.join(event.to_json() for event, _ in cases) + '\n')
            labels.write_text('\n'.join(json.dumps(label) for _, label in cases) + '\n')
            cls.paths[role] = (data, labels)
            return load_dataset(str(data))
        cls.history = write('history', synthetic_cases(160))
        start = utc_time(cls.history.events[-1].timestamp) + timedelta(seconds=1)
        cls.validation = write('validation', future_cases(start, 160, 84, 'validation'))
        cls.test = write('test', future_cases(start + timedelta(seconds=160), 160, 85, 'test'))
        cls.training = train(cls.history, str(cls.root / 'training'), TrainingConfig(n_estimators=(12,), max_samples=64))
        cls.uri = 'sqlite:///' + str(cls.root / 'mlflow.db')
        cls.artifact_root = str(cls.root / 'mlflow-artifacts')
        cls.audit = str(cls.root / 'audit.db')
        cls.token = 'test-only-admin-token-not-a-production-secret'

    @classmethod
    def tearDownClass(cls):
        # Dispose SQLAlchemy connection pools before temporary directory cleanup.
        import gc
        gc.collect()
        cls.temp.cleanup()

    def make_evaluation(self, policy=None, unlabeled=False):
        from ml_training.lifecycle import calibrate, evaluate
        plan = calibrate(str(self.training), self.history, self.validation,
                         str(self.paths['validation'][1]), str(self.root / 'plans'), policy)
        evaluation = evaluate(str(self.training), str(plan), self.history, self.validation, self.test,
                              None if unlabeled else str(self.paths['test'][1]), str(self.root / 'evaluations'))
        return plan, evaluation

    def test_real_tracking_records_parameters_metrics_and_artifacts_idempotently(self):
        from ml_training.tracking import client_for, track_training
        first = track_training(str(self.training), self.uri, artifact_root=self.artifact_root)
        second = track_training(str(self.training), self.uri, artifact_root=self.artifact_root)
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertEqual(second['status'], 'already_tracked')
        client = client_for(self.uri)
        run = client.get_run(first['run_id'])
        self.assertEqual(run.info.status, 'FINISHED')
        self.assertEqual(run.data.params['dataset_version'], self.history.version)
        self.assertIn('duration_seconds', run.data.metrics)
        self.assertIn('report.json', {file.path for file in client.list_artifacts(first['run_id'])})

    def test_calibration_frozen_selection_and_same_test_set_for_all_candidates(self):
        from ml_training.integrity import read_sealed
        plan, evaluation = self.make_evaluation()
        frozen = read_sealed(str(plan), 'plan.json')
        report = read_sealed(str(evaluation), 'evaluation.json')
        self.assertEqual(report['selected_candidate_id'], frozen['selected_candidate_id'])
        ids = [event.event_id for event in self.test.events]
        for candidate in report['candidates']:
            predictions = [json.loads(line) for line in (evaluation / (candidate['candidate_id'] + '.predictions.jsonl')).read_text().splitlines()]
            self.assertEqual([item['event_id'] for item in predictions], ids)
        self.assertEqual(report['selected_result']['test_metrics']['sample_count'], 160)

    def test_overlap_and_wrong_history_fail_before_evaluation(self):
        from ml_training.lifecycle import calibrate, evaluate
        from ml_training.integrity import read_sealed
        with self.assertRaisesRegex(ValueError, 'strictly_after'):
            calibrate(str(self.training), self.history, self.history, None, str(self.root / 'bad'))
        plan, _ = self.make_evaluation()
        with self.assertRaisesRegex(ValueError, 'strictly_after'):
            evaluate(str(self.training), str(plan), self.history, self.validation, self.validation,
                     str(self.paths['validation'][1]), str(self.root / 'bad'))

    def test_unlabeled_test_does_not_create_supervised_metrics_or_pass_gate(self):
        from ml_training.integrity import read_sealed
        _, evaluation = self.make_evaluation(unlabeled=True)
        report = read_sealed(str(evaluation), 'evaluation.json')
        self.assertIsNone(report['selected_result']['test_metrics'])
        self.assertFalse(report['gate']['passed'])
        self.assertIn('labeled_test_required', report['gate']['reasons'])

    def test_bad_evaluation_never_registers_and_is_audited(self):
        from ml_training.integrity import read_sealed
        from ml_training.registry import ApprovalDenied, register_evaluation
        from ml_training.audit import LifecycleAudit
        _, evaluation = self.make_evaluation(unlabeled=True)
        with patch.dict(os.environ, {'ADMIN_API_TOKEN': self.token}):
            with self.assertRaisesRegex(ApprovalDenied, 'quality_gate'):
                register_evaluation(str(evaluation), self.uri, self.audit, self.token, 'test-operator')
        self.assertTrue(any(row['status'] == 'rejected' for row in LifecycleAudit(self.audit).entries()))

    def test_real_model_registry_alias_and_pyfunc_reload(self):
        import pandas as pd
        from ml.features import FeatureExtractor
        from ml.features.contract import NUMERIC_COLUMNS
        from ml_training.evaluation import GatePolicy
        from ml_training.integrity import read_sealed
        from ml_training.registry import ApprovalDenied, promote, register_evaluation
        from ml_training.tracking import client_for, tracking_context
        from ml_training.audit import LifecycleAudit
        import mlflow.pyfunc
        # Deliberately permissive, frozen TEST POLICY checks registry plumbing, not model quality.
        # Production/default quality gates are tested independently; this cannot create champion.
        policy = GatePolicy(min_test_records=1, min_positives=1, min_negatives=1,
                            min_precision=0, min_recall=0, min_f1=0, max_false_positive_rate=1)
        _, evaluation = self.make_evaluation(policy)
        report = read_sealed(str(evaluation), 'evaluation.json')
        self.assertTrue(report['gate']['passed'])
        with patch.dict(os.environ, {'ADMIN_API_TOKEN': self.token}):
            registered = register_evaluation(str(evaluation), self.uri, self.audit, self.token, 'test-operator',
                                             'test-lifecycle-model', artifact_root=self.artifact_root)
            repeated = register_evaluation(str(evaluation), self.uri, self.audit, self.token, 'test-operator',
                                           'test-lifecycle-model', artifact_root=self.artifact_root)
            self.assertEqual(registered['version'], repeated['version'])
            result = promote(self.uri, self.audit, self.token, 'test-operator', 'test-lifecycle-model',
                             registered['version'], 'staging', 'controlled integration test')
            self.assertEqual(result['alias'], 'staging')
            with self.assertRaisesRegex(ApprovalDenied, 'production_scope'):
                promote(self.uri, self.audit, self.token, 'test-operator', 'test-lifecycle-model',
                        registered['version'], 'champion', 'must be rejected')
        client = client_for(self.uri)
        self.assertEqual(str(client.get_model_version_by_alias('test-lifecycle-model', 'staging').version), registered['version'])
        with tracking_context(self.uri):
            loaded = mlflow.pyfunc.load_model('models:/test-lifecycle-model@staging')
            features = FeatureExtractor()
            features.transform(self.history.events)
            features.transform(self.validation.events)
            frame = pd.DataFrame(features.transform(self.test.events))
            frame[NUMERIC_COLUMNS] = frame[NUMERIC_COLUMNS].astype(float)
            predictions = loaded.predict(frame)
        self.assertEqual(len(predictions), len(self.test.events))
        self.assertEqual(set(predictions.columns), {'anomaly_score', 'is_anomaly'})
        expected = [json.loads(line)['anomaly_score'] for line in
                    (evaluation / (report['selected_candidate_id'] + '.predictions.jsonl')).read_text().splitlines()]
        import numpy as np
        np.testing.assert_allclose(predictions.anomaly_score.to_numpy(), expected, rtol=0, atol=1e-12)
        entries = LifecycleAudit(self.audit).entries()
        self.assertTrue(any(row['action'] == 'promote' and row['status'] == 'succeeded' for row in entries))

    def test_cli_calibration_evaluation_and_tracking(self):
        import subprocess
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'services/ml-training')]))
        common = ['--run-dir', str(self.training), '--history-dataset', str(self.paths['history'][0]),
                  '--validation-dataset', str(self.paths['validation'][0]), '--output', str(self.root / 'cli')]
        calibration = subprocess.run([sys.executable, '-m', 'ml_training', 'calibrate', *common,
            '--validation-labels', str(self.paths['validation'][1])],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(calibration.returncode, 0, calibration.stderr)
        plan = json.loads(calibration.stdout)['plan_directory']
        evaluated = subprocess.run([sys.executable, '-m', 'ml_training', 'evaluate', *common,
            '--plan-dir', plan, '--test-dataset', str(self.paths['test'][0]),
            '--test-labels', str(self.paths['test'][1]), '--tracking-uri', self.uri,
            '--artifact-root', self.artifact_root], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=30)
        self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
        receipt = json.loads(evaluated.stdout)
        self.assertEqual(receipt['status'], 'evaluated')
        self.assertIn('mlflow_run_id', receipt)

    def test_tracking_context_restores_environment_for_later_cli_jobs(self):
        from ml_training.tracking import tracking_context
        before = {key: os.environ.get(key) for key in ('MLFLOW_TRACKING_URI', 'MLFLOW_REGISTRY_URI')}
        with tracking_context(self.uri):
            self.assertEqual(os.environ.get('MLFLOW_TRACKING_URI'), self.uri)
        self.assertEqual({key: os.environ.get(key) for key in before}, before)

    def test_registration_requires_admin_token(self):
        from ml_training.registry import ApprovalDenied, register_evaluation
        with patch.dict(os.environ, {'ADMIN_API_TOKEN': self.token}):
            with self.assertRaises(ApprovalDenied):
                register_evaluation('unused', self.uri, self.audit, 'wrong', 'test-operator')


if __name__ == '__main__':
    unittest.main()
