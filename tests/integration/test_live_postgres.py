"""Disposable-schema real PostgreSQL test; never substitutes SQLite results for PostgreSQL."""
import importlib.util
import os
import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'services/alert-manager')]
LIVE=(importlib.util.find_spec('psycopg') is not None and os.environ.get('RUN_POSTGRES_INTEGRATION')=='1')


@unittest.skipUnless(LIVE,'Real PostgreSQL requires psycopg, RUN_POSTGRES_INTEGRATION=1 and a disposable database')
class LivePostgres(unittest.TestCase):
    def test_actual_transaction_outbox_alert_and_row_locks(self):
        from sqlalchemy.schema import CreateSchema,DropSchema
        from shared.pipeline.database import make_engine
        from shared.pipeline.store import PipelineStore
        from shared.pipeline.models import metadata
        from shared.pipeline.contracts import PredictionNotice
        from shared.schemas import NormalizedLog
        from alert_manager.service import AlertService
        schema='test_phase6_'+uuid4().hex
        engine=make_engine()
        with engine.begin() as db:db.execute(CreateSchema(schema))
        mapped=engine.execution_options(schema_translate_map={None:schema})
        try:
            metadata.create_all(mapped)
            store=PipelineStore(mapped)
            event=NormalizedLog(str(uuid4()),'2026-01-01T00:00:00Z','application','worker','host',
                                 'INFO','controlled synthetic unusual task','controlled synthetic task',metadata={'synthetic':True})
            prediction={'threshold':.5,'threshold_source':'artifact','feature_version':'1.0.0',
                        'anomaly_score':.9,'is_anomaly':True,'context_warm':True}
            loaded=SimpleNamespace(name='test-model',version='1',package_sha256='a'*64,scope='development')
            state={'started_at':event.timestamp,'last_timestamp':event.timestamp}
            result=store.save_prediction('inference',event,prediction,loaded,state,0)
            self.assertEqual(store.save_prediction('inference',event,prediction,loaded,state,0)['status'],'duplicate')
            row=store.claim_outbox()[0]
            store.finish_outbox(row['id'],row['lease_token'],True)
            alerts=AlertService(store)
            notice=PredictionNotice(result['prediction_id'],event.event_id)
            self.assertEqual(alerts.process('alerts',notice),'alert_created')
            identity=alerts.list()['items'][0]['id']
            self.assertEqual(alerts.transition(identity,'acknowledge','test-operator')['status'],'acknowledged')
        finally:
            with engine.begin() as db:db.execute(DropSchema(schema,cascade=True))
            engine.dispose()


if __name__=='__main__':unittest.main()
