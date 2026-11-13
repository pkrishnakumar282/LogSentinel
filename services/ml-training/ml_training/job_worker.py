"""Separate on-demand queue worker: trusted export paths, bounded child process, no promotion."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from sqlalchemy import select
from shared.application.jobs import Jobs
from shared.pipeline import models as m
from shared.pipeline.database import make_engine
from shared.pipeline.store import normalized


def run_one(jobs, root, timeout=3600, runner=None):
    job = jobs.claim(timeout)
    if not job:
        return False
    try:
        directory = Path(root) / job['id']
        directory.mkdir(parents=True, exist_ok=False)
        dataset = directory / 'events.jsonl'
        ids = set(job['event_ids'])
        with jobs.engine.connect() as db, dataset.open('x') as stream:
            # Chunked IN avoids SQLite parameter limits and giant PostgreSQL parameter lists.
            ordered = sorted(ids)
            count = 0
            for index in range(0, len(ordered), 500):
                for row in db.execute(select(m.normalized_logs).where(m.normalized_logs.c.event_id.in_(ordered[index:index+500]))).mappings():
                    stream.write(normalized(row).to_json() + '\n')
                    count += 1
        if count != len(ids):
            raise ValueError('training_snapshot_missing')
        p = job['parameters']
        provenance = directory / 'provenance.json'
        provenance.write_text(json.dumps({'kind': 'synthetic'} if p['synthetic'] else {
            'kind': 'local', 'dataset_name': 'local-canonical-snapshot-' + job['id']}))
        command = [sys.executable, '-m', 'ml_training', 'train', '--dataset', str(dataset),
            '--provenance', str(provenance), '--output', str(directory / 'artifacts'),
            '--seed', str(p['seed']), '--estimators', *map(str, p['estimators'])]
        # Child has no access to service admin/database/broker credentials through its environment.
        allowed = ('PATH','PYTHONPATH','VIRTUAL_ENV','HOME','LANG','LC_ALL','TMPDIR',
                   'MLFLOW_TRACKING_URI','MLFLOW_ARTIFACT_ROOT','MLFLOW_EXPERIMENT_NAME',
                   'MLFLOW_ENABLE_TELEMETRY','MLFLOW_DISABLE_AGENT_HINT')
        env = {key: os.environ[key] for key in allowed if key in os.environ}
        if runner:
            outcome = runner(command, env, timeout)
        else:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, env=env, start_new_session=True) as process:
                try:
                    output, _ = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    raise TimeoutError from None
                if process.returncode:
                    raise RuntimeError('training_process_failed')
            outcome = json.loads(output)
        artifact_root = Path(outcome['artifact_directory'])
        report_files = list(artifact_root.glob('*/report.json'))
        report = json.loads(report_files[0].read_text()) if len(report_files) == 1 else {}
        candidates = [{'variant':item.get('variant'),'algorithm':item.get('algorithm'),
            'n_estimators':item.get('n_estimators'),'threshold':item.get('threshold'),
            'holdout_predicted_anomaly_fraction':item.get('holdout_predicted_anomaly_fraction'),
            'fit_seconds':item.get('fit_seconds'),'holdout_predict_ms_per_event':item.get('holdout_predict_ms_per_event'),
            'feature_count':item.get('feature_count')} for item in report.get('candidates',[])]
        # Artifact paths are internal; these are unsupervised diagnostics, not accuracy metrics.
        result={'dataset_version':outcome['dataset_version'],'approval_status':'candidate_not_approved',
                'tracking_run_id':outcome.get('tracking_run_id'),'training_duration_seconds':report.get('duration_seconds'),
                'candidates':candidates}
        jobs.finish(job,result=result)
    except TimeoutError:
        jobs.finish(job, failure='training_timeout')
    except Exception:
        jobs.finish(job, failure='training_execution_failed')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--root', default=os.environ.get('TRAINING_JOB_ROOT', '.local/training-jobs'))
    args = parser.parse_args()
    jobs = Jobs(make_engine())
    while True:
        processed = run_one(jobs, args.root)
        if args.once:
            return
        if not processed:
            time.sleep(2)


if __name__ == '__main__':
    main()
