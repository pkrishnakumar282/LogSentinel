"""Actual SQLAlchemy transactions and recovery, with an explicitly test-only broker."""
from dataclasses import replace
from datetime import UTC,datetime,timedelta
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL,uuid5

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'services/ml-inference'),str(ROOT/'services/alert-manager'),str(ROOT/'tests/support')]
READY=importlib.util.find_spec('sqlalchemy') is not None


def event(index=0,**changes):
    from shared.schemas import NormalizedLog
    value=dict(event_id=str(uuid5(NAMESPACE_URL,f'pipeline-test:{index}')),
        timestamp=(datetime(2026,1,1,tzinfo=UTC)+timedelta(seconds=index)).isoformat(),
        source='application',service='worker',hostname='synthetic-host',severity='INFO',
        message=f'task completed {index}',template='task completed <NUM>',metadata={'synthetic':True})
    return NormalizedLog(**(value|changes))


@unittest.skipUnless(READY,'SQLAlchemy dependency unavailable')
class PipelineTransactions(unittest.TestCase):
    def setUp(self):
        from shared.pipeline.database import make_engine
        from shared.pipeline.store import PipelineStore
        from shared.pipeline.worker import StreamWorker,OutboxRelay
        from ml_inference.config import InferenceSettings
        from ml_inference.engine import InferenceEngine
        from ml_inference.loader import LoadedModel
        from ml_inference.stream_worker import InferenceHandler
        from ml.features import FeatureConfig
        from alert_manager.service import AlertService,AlertHandler
        from alert_manager.policy import AlertPolicy
        from memory_stream import MemoryGroup,MemoryPublisher
        self.temp=tempfile.TemporaryDirectory()
        self.database=make_engine('sqlite:///'+str(Path(self.temp.name)/'pipeline.db'))
        self.store=PipelineStore(self.database)
        self.store.initialize_development()
        class TestBundle:
            feature_config=FeatureConfig(window_seconds=2,max_events=100)
            feature_version='1.0.0'
            threshold=.5
            def score_rows(inner,rows):
                # Deterministic fixture tests workflow, not learned detection effectiveness.
                return [.9 if 'unusual' in row['text'] else .1 for row in rows]
        loader=unittest.mock.Mock()
        loader.load.return_value=LoadedModel(TestBundle(),'test-model','1','development','a'*64,'b'*64)
        self.engine=InferenceEngine(InferenceSettings(scope='development'),loader)
        self.engine.reload()
        self.source=MemoryGroup()
        self.alert_stream=MemoryGroup('prediction-stream','alert-stage')
        self.inference=InferenceHandler(self.engine,self.store,self.source)
        self.worker=StreamWorker(self.source,self.store,self.inference)
        self.publisher=MemoryPublisher(self.alert_stream)
        self.relay=OutboxRelay(self.store,self.publisher)
        self.alert_service=AlertService(self.store,AlertPolicy(require_warm_context=False))
        self.alert_worker=StreamWorker(self.alert_stream,self.store,AlertHandler(self.alert_service,self.alert_stream.stage))

    def tearDown(self):
        self.database.dispose()
        self.temp.cleanup()

    def ingest(self,index=0,**changes):
        item=event(index,**changes)
        self.source.add(item.to_dict())
        result=self.worker.tick()
        self.relay.flush()
        while self.alert_stream.entries:
            self.alert_worker.tick()
        return item,result

    def count(self,table):
        from sqlalchemy import select,func
        with self.database.connect() as db:return db.execute(select(func.count()).select_from(table)).scalar_one()

    def test_normal_error_does_not_create_alert(self):
        self.ingest(0,severity='ERROR')
        self.assertEqual(self.alert_service.list()['total'],0)
        from shared.pipeline.models import predictions
        self.assertEqual(self.count(predictions),1)

    def test_anomaly_groups_and_duplicate_event_does_not_increment_count(self):
        first,_=self.ingest(0,message='unusual task 0')
        self.ingest(1,message='unusual task 1')
        self.source.add(first.to_dict())
        self.assertEqual(self.worker.tick(),'duplicate')
        self.relay.flush()
        alert=self.alert_service.list()['items'][0]
        self.assertEqual(alert['occurrence_count'],2)
        self.assertEqual(self.alert_service.list()['total'],1)

    def test_ack_failure_replays_committed_prediction_without_scoring_again(self):
        from shared.pipeline.models import predictions
        self.source.add(event().to_dict())
        self.source.fail_ack=True
        with self.assertRaises(ConnectionError):self.worker.tick()
        self.assertEqual(self.count(predictions),1)
        with patch.object(self.engine,'predict',side_effect=AssertionError('must use durable receipt')):
            self.assertEqual(self.worker.tick(),'duplicate')
        self.assertEqual(self.count(predictions),1)

    def test_transaction_failure_restores_prior_context(self):
        from shared.pipeline.contracts import Deferred
        self.ingest(0)
        self.source.add(event(1).to_dict())
        with patch.object(self.store,'save_prediction',side_effect=Deferred('database_unavailable')):
            self.assertEqual(self.worker.tick(),'deferred')
        self.assertEqual(self.store.checkpoint(self.source.stage)['revision'],1)
        self.assertEqual(self.worker.tick(),'predicted')
        self.assertEqual(self.store.checkpoint(self.source.stage)['revision'],2)

    def test_restart_rebuilds_frequency_context_from_committed_history(self):
        self.ingest(0)
        self.ingest(1)
        self.engine.reset_context('stream')
        self.inference.revision=None
        self.source.add(event(2).to_dict())
        self.assertEqual(self.worker.tick(),'predicted')
        state=self.engine.contexts['stream'].features
        self.assertEqual(state.services[('application','worker')],1)
        self.assertEqual(state.started_at,datetime(2026,1,1,tzinfo=UTC))

    def test_outbox_publish_failure_preserves_pending_rows(self):
        from shared.pipeline.contracts import Deferred
        self.source.add(event(message='unusual task').to_dict())
        self.assertEqual(self.worker.tick(),'predicted')
        self.publisher.fail=True
        with self.assertRaises(Deferred):self.relay.flush()
        self.publisher.fail=False
        self.assertEqual(self.relay.flush(),1)
        self.assertEqual(self.alert_worker.tick(),'alert_created')

    def test_publish_before_outbox_mark_crash_is_idempotent(self):
        from shared.pipeline.models import outbox
        from sqlalchemy import update
        self.source.add(event(message='unusual task').to_dict())
        self.worker.tick()
        row=self.store.claim_outbox()[0]
        self.publisher.publish(row['id'],row['payload'])
        # Simulated process death: lease expires without a published mark.
        with self.database.begin() as db:db.execute(update(outbox).values(lease_until=datetime(2000,1,1,tzinfo=UTC)))
        self.assertEqual(self.relay.flush(),1)
        self.assertEqual(len(self.alert_stream.entries),1)
        self.alert_worker.tick()
        self.assertEqual(self.alert_service.list()['items'][0]['occurrence_count'],1)

    def test_alert_commit_before_ack_crash_deduplicates_grouping(self):
        self.source.add(event(message='unusual task').to_dict())
        self.worker.tick();self.relay.flush()
        self.alert_stream.fail_ack=True
        with self.assertRaises(ConnectionError):self.alert_worker.tick()
        self.assertEqual(self.alert_worker.tick(),'duplicate')
        self.assertEqual(self.alert_service.list()['items'][0]['occurrence_count'],1)

    def test_unapproved_model_stays_pending_without_spending_retries(self):
        self.engine.loaded=None
        self.source.add(event().to_dict())
        for _ in range(5):self.assertEqual(self.worker.tick(),'deferred')
        self.assertEqual(len(self.source.entries),1)
        self.assertEqual(self.source.failures,{})
        self.assertFalse(self.source.dead)

    def test_malformed_and_conflicting_ids_quarantine_without_payload(self):
        self.source.add({'raw':'password=private-secret'})
        self.assertEqual(self.worker.tick(),'failed')
        self.assertNotIn('private-secret',json.dumps(self.source.dead))
        self.ingest(0)
        self.source.add(event(0,message='different event').to_dict())
        self.assertEqual(self.worker.tick(),'failed')
        self.assertEqual(self.source.dead[-1]['reason'],'event_id_conflict')

    def test_retries_are_bounded_and_database_failure_record_precedes_dlq(self):
        from shared.pipeline.contracts import Retryable
        delivery=self.source.add(event().to_dict())
        with patch.object(self.inference,'__call__',side_effect=Retryable('temporary_prediction_failure')):
            # Special methods resolve on the type, so inject an explicit callable into the worker.
            self.worker.handler=lambda item:(_ for _ in ()).throw(Retryable('temporary_prediction_failure'))
            self.assertEqual(self.worker.tick(),'retrying')
            self.assertEqual(self.worker.tick(),'retrying')
            self.assertEqual(self.worker.tick(),'failed')
        self.assertIsNotNone(self.store.failure(self.source.stage,delivery.entry_id))
        self.assertEqual(self.source.dead[0]['attempts'],3)

    def test_alert_transitions_are_audited_idempotently_and_resolved_recurrence_is_new(self):
        from alert_manager.service import AlertConflict
        self.ingest(0,message='unusual task')
        identity=self.alert_service.list()['items'][0]['id']
        self.alert_service.transition(identity,'acknowledge','operator','password=private-secret')
        self.alert_service.transition(identity,'acknowledge','operator')
        self.alert_service.transition(identity,'resolve','operator')
        self.assertEqual(len(self.alert_service.history(identity)),3)
        self.assertNotIn('private-secret',json.dumps(self.alert_service.history(identity)))
        with self.assertRaises(AlertConflict):self.alert_service.transition(identity,'acknowledge','operator')
        self.ingest(1,message='unusual task')
        self.assertEqual(self.alert_service.list()['total'],2)

    def test_warmup_and_score_floor_suppress_alerts(self):
        from alert_manager.policy import AlertPolicy
        self.alert_service.policy=AlertPolicy(require_warm_context=True)
        self.ingest(0,message='unusual task')
        self.assertEqual(self.alert_service.list()['total'],0)
        self.alert_service.policy=AlertPolicy(require_warm_context=False,min_score=.99)
        self.ingest(1,message='unusual task')
        self.assertEqual(self.alert_service.list()['total'],0)

    def test_checkpoint_revision_conflict_rolls_back_prediction_and_outbox(self):
        from shared.pipeline.contracts import StaleCheckpoint
        from shared.pipeline.models import predictions,outbox
        self.ingest(0)
        item=event(1)
        prediction=self.engine.predict([item],'other')[0]
        with self.assertRaises(StaleCheckpoint):
            self.store.save_prediction(self.source.stage,item,prediction,self.engine.loaded,{},0)
        self.assertEqual(self.count(predictions),1)
        self.assertEqual(self.count(outbox),1)


if __name__=='__main__':unittest.main()
