import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'services/api-gateway'),str(ROOT/'services/ml-training')]
READY=all(importlib.util.find_spec(name) for name in ('sqlalchemy','alembic'))


@unittest.skipUnless(READY,'Install application database dependencies')
class ApplicationMetadata(unittest.TestCase):
    def test_upgrade_populated_phase6_database_backfills_provenance_without_duplicate_messages(self):
        from sqlalchemy import func,select,inspect
        from shared.pipeline.database import make_engine
        from shared.pipeline import models as m
        from shared.pipeline.store import now
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db'
            env=dict(os.environ,PIPELINE_DB_BACKEND='sqlite',PIPELINE_SQLITE_PATH=str(path))
            def migrate(revision):
                result=subprocess.run([sys.executable,str(ROOT/'scripts/manage_pipeline.py'),'upgrade','--revision',revision],
                    env=env,cwd=ROOT,capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
            migrate('0001_pipeline')
            engine=make_engine('sqlite:///'+str(path))
            identity=str(uuid4())
            with engine.begin() as db:
                db.execute(m.normalized_logs.insert().values(event_id=identity,timestamp=now(),source='application',service='test',
                    hostname='host',severity='INFO',message='synthetic test',template='synthetic test',record_metadata={'synthetic':True},
                    fingerprint='a'*64,created_at=now()))
            migrate('head')
            with engine.connect() as db:
                self.assertEqual(db.execute(select(func.count()).select_from(m.log_events)).scalar_one(),1)
                self.assertEqual(db.execute(select(m.data_sources.c.total_records)).scalar_one(),1)
                self.assertEqual(db.execute(select(m.log_events.c.normalized_event_id)).scalar_one(),identity)
                self.assertEqual(db.execute(select(m.normalized_logs.c.message)).scalar_one(),'synthetic test')
            self.assertNotIn('message',{col['name'] for col in inspect(engine).get_columns('log_events')})
            engine.dispose()

    def test_lifecycle_import_is_readonly_idempotent_redacted_and_filterable(self):
        from ml_training.audit import LifecycleAudit
        from shared.application.audit_sync import synchronize
        from shared.application.repository import Repository
        from shared.pipeline.store import PipelineStore
        from shared.pipeline.database import make_engine
        with tempfile.TemporaryDirectory() as directory:
            ledger=Path(directory)/'audit.db'
            audit=LifecycleAudit(str(ledger))
            audit.record('promote','engineer','succeeded',{'model_name':'test-model','version':'1','alias':'staging',
                'reason':'password=secret','bundle':'/private/path','token':'secret'})
            before=ledger.read_bytes()
            engine=make_engine('sqlite:///'+str(Path(directory)/'app.db'))
            PipelineStore(engine).initialize_development()
            self.assertEqual(synchronize(engine,ledger)['imported'],1)
            self.assertEqual(synchronize(engine,ledger)['imported'],0)
            result=Repository(engine).audits(action='model_promote')['items']
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['details']['alias'],'staging')
            self.assertNotIn('secret',json.dumps(result,default=str))
            self.assertNotIn('/private/path',json.dumps(result,default=str))
            self.assertEqual(ledger.read_bytes(),before)
            engine.dispose()

    @unittest.skipUnless(importlib.util.find_spec('mlflow'),'Install MLflow metadata client')
    def test_actual_mlflow_runs_models_and_aliases_read_without_artifact_loading(self):
        from ml_training.tracking import client_for
        from api_gateway.registry import Registry
        with tempfile.TemporaryDirectory() as directory:
            uri='sqlite:///'+str(Path(directory)/'tracking.db')
            client=client_for(uri)
            experiment=client.create_experiment('gateway-test',artifact_location=str(Path(directory)/'artifacts'))
            run=client.create_run(experiment,tags={'mlflow.runName':'gateway-real-metadata'})
            client.log_param(run.info.run_id,'algorithm','IsolationForest')
            client.log_param(run.info.run_id,'password','secret')
            client.log_metric(run.info.run_id,'duration_seconds',1.25)
            client.set_terminated(run.info.run_id)
            client.create_registered_model('metadata-test-only')
            version=client.create_model_version('metadata-test-only',source=str(Path(directory)/'unloaded-artifact'),run_id=run.info.run_id)
            client.set_registered_model_alias('metadata-test-only','staging',version.version)
            registry=Registry(uri)
            experiments=registry.experiments()
            self.assertIn('gateway-real-metadata',json.dumps(experiments))
            self.assertNotIn('secret',json.dumps(experiments))
            self.assertEqual(registry.models()['items'][0]['aliases']['staging'],'1')

    def test_missing_registry_database_is_not_created(self):
        from api_gateway.registry import Registry
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'missing.db'
            with self.assertRaises(ValueError):
                Registry('sqlite:///'+str(path)).client()
            self.assertFalse(path.exists())


if __name__=='__main__':
    unittest.main()
