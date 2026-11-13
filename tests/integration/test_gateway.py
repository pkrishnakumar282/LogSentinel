"""Actual SQL/Alembic and socket-free ASGI, with explicit upstream transport doubles."""
import asyncio
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT),str(ROOT/'services/api-gateway'),str(ROOT/'services/ml-training'),str(ROOT/'tests/integration')]
READY = all(importlib.util.find_spec(name) for name in ('sqlalchemy','fastapi','httpx'))


async def immediate(fn,*args):
    # Sandbox disallows asyncio's socket wakeups; production uses run_in_threadpool.
    return fn(*args)


async def request(app,method,path,data=None,token=None,raw=None,extra_headers=()):
    parsed=urlsplit(path)
    body=raw if raw is not None else json.dumps(data).encode() if data is not None else b''
    headers=[(b'content-type',b'application/json'),*extra_headers]
    if token:
        headers.append((b'authorization',b'Bearer '+token.encode()))
    scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':method,
        'scheme':'http','path':parsed.path,'raw_path':parsed.path.encode(),'query_string':parsed.query.encode(),
        'root_path':'','headers':headers,'server':('localhost',8000),'client':('test',1)}
    delivered=False
    messages=[]
    async def receive():
        nonlocal delivered
        if delivered:
            return {'type':'http.disconnect'}
        delivered=True
        return {'type':'http.request','body':body,'more_body':False}
    async def send(message):
        messages.append(message)
    await app(scope,receive,send)
    start=next(message for message in messages if message['type']=='http.response.start')
    rawbody=b''.join(message.get('body',b'') for message in messages if message['type']=='http.response.body')
    try:
        result=json.loads(rawbody)
    except ValueError:
        result=rawbody.decode()
    return start['status'],result,dict(start['headers'])


@unittest.skipUnless(READY,'Install gateway dependencies')
class GatewayTests(unittest.TestCase):
    def setUp(self):
        from api_gateway.api import create_app
        from api_gateway.config import Settings
        from api_gateway.upstreams import Upstreams
        from shared.application.repository import Repository
        from shared.pipeline.store import PipelineStore
        from shared.pipeline.database import make_engine
        import httpx
        self.temp=tempfile.TemporaryDirectory()
        self.engine=make_engine('sqlite:///'+str(Path(self.temp.name)/'db'))
        self.store=PipelineStore(self.engine)
        self.store.initialize_development()
        self.repository=Repository(self.engine)
        self.token='gateway-test-token-only-123456789'
        self.admin='admin-test-token-only-123456789'
        self.settings=Settings(api_token=self.token,admin_token=self.admin,ingest_token='processor-secret',
            inference_token='inference-secret',alert_token='alert-secret')
        self.calls=[]
        def respond(req):
            self.calls.append(req)
            if req.url.path.endswith('/predict'):
                return httpx.Response(503,json={'secret':'must-never-leak'})
            return httpx.Response(200,json={'status':'ready','items':[]})
        self.client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.upstreams=Upstreams(self.settings,self.client)
        class NoRegistry:
            def models(self):
                raise ConnectionError('registry-secret')
            def experiments(self):
                return {'items':[]}
        self.app=create_app(self.settings,self.repository,self.upstreams,NoRegistry(),immediate)

    def tearDown(self):
        asyncio.run(self.client.aclose())
        self.engine.dispose()
        self.temp.cleanup()

    def call(self,method,path,data=None,admin=False,**kwargs):
        return asyncio.run(request(self.app,method,path,data,self.admin if admin else self.token,**kwargs))

    def archive(self,count=1):
        from shared.application.archive import ArchiveHandler
        from shared.pipeline.contracts import Delivery
        from test_pipeline_transactions import event
        handler=ArchiveHandler(self.store,'archive-test')
        events=[]
        for index in range(count):
            item=event(index)
            handler(Delivery(str(index),{'payload':item.to_json()}))
            events.append(item)
        return events

    def test_auth_admin_validation_body_limits_and_cors(self):
        self.assertEqual(asyncio.run(request(self.app,'GET','/api/v1/logs'))[0],401)
        self.assertEqual(self.call('POST','/api/v1/training/jobs',{'actor':'engineer','synthetic':True})[0],403)
        status,body,_=self.call('POST','/api/v1/training/jobs',{'actor':'engineer','synthetic':True,'password':'hidden'},admin=True)
        self.assertEqual(status,422)
        self.assertNotIn('hidden',json.dumps(body))
        self.assertEqual(self.call('POST','/api/v1/ingest',raw=b'x'*1048577)[0],413)
        status,_,headers=asyncio.run(request(self.app,'OPTIONS','/api/v1/logs',extra_headers=[
            (b'origin',b'http://localhost:5173'),(b'access-control-request-method',b'GET'),
            (b'access-control-request-headers',b'authorization')]))
        self.assertEqual(status,200)
        self.assertEqual(headers[b'access-control-allow-origin'],b'http://localhost:5173')

    def test_archive_without_model_and_idempotent_source_counts(self):
        from shared.application.archive import ArchiveHandler
        from shared.pipeline.contracts import Delivery,Rejected
        from dataclasses import replace
        item=self.archive()[0]
        handler=ArchiveHandler(self.store,'archive-test')
        self.assertEqual(handler(Delivery('again',{'payload':item.to_json()})),'duplicate')
        with self.assertRaises(Rejected):
            handler(Delivery('conflict',{'payload':replace(item,message='changed').to_json()}))
        status,body,_=self.call('GET','/api/v1/dashboard')
        self.assertEqual(status,200)
        self.assertEqual((body['total_logs'],body['unscored_logs'],body['anomalies']),(1,1,0))
        self.assertEqual(self.call('GET','/api/v1/sources')[1]['items'][0]['total_records'],1)

    def test_filters_search_details_pagination_and_time_validation(self):
        events=self.archive(3)
        status,body,_=self.call('GET','/api/v1/logs?q=completed%201&severity=INFO&source=application&limit=1')
        self.assertEqual(status,200)
        self.assertEqual((body['total'],len(body['items'])),(1,1))
        self.assertEqual(self.call('GET','/api/v1/logs?q=%25')[1]['total'],0)
        self.assertEqual(self.call('GET','/api/v1/logs?offset=2')[1]['total'],3)
        detail=self.call('GET','/api/v1/logs/'+events[0].event_id)[1]
        self.assertTrue(detail['synthetic'])
        self.assertEqual(detail['predictions'],[])
        self.assertEqual(self.call('GET','/api/v1/logs?since=2026-01-01T00:00:00')[0],422)
        self.assertEqual(self.call('GET','/api/v1/logs?limit=0')[0],422)
        self.assertEqual(self.call('GET','/api/v1/logs/not-a-uuid')[0],422)

    def test_latest_prediction_counts_event_once(self):
        from sqlalchemy import insert
        from shared.pipeline import models as m
        from shared.pipeline.store import now
        item=self.archive()[0]
        with self.engine.begin() as db:
            for index in (1,2):
                db.execute(insert(m.model_versions).values(id=str(index),name='test',version=str(index),package_sha256='a'*64,
                    threshold=.5,threshold_source='artifact',scope='development',feature_version='1',created_at=now()))
                db.execute(insert(m.predictions).values(id=str(index),event_id=item.event_id,model_id=str(index),
                    anomaly_score=.9 if index==2 else .1,is_anomaly=index==2,context_warm=True,
                    created_at=now()+timedelta(seconds=index)))
        body=self.call('GET','/api/v1/dashboard')[1]
        self.assertEqual((body['total_logs'],body['anomalies'],body['normal_logs']),(1,1,0))
        self.assertEqual(self.call('GET','/api/v1/logs?anomaly=true')[1]['items'][0]['model_version'],'2')
        self.assertEqual(len(self.call('GET','/api/v1/logs/'+item.event_id)[1]['predictions']),2)

    def test_safe_registry_failures_upstream_credentials_and_invalid_ingestion(self):
        self.assertEqual(self.call('GET','/api/v1/models')[0],503)
        self.assertNotIn('secret',json.dumps(self.call('GET','/api/v1/models')[1]))
        self.assertEqual(self.call('GET','/api/v1/alerts?severity=HIGH')[0],200)
        self.assertEqual(self.calls[-1].headers['authorization'],'Bearer alert-secret')
        identity='00000000-0000-0000-0000-000000000001'
        self.assertEqual(self.call('POST',f'/api/v1/alerts/{identity}/resolve',{'actor':'operator'},admin=True)[0],200)
        self.assertEqual(self.calls[-1].headers['authorization'],'Bearer '+self.admin)
        event=self.archive()[0]
        status,body,_=self.call('POST','/api/v1/predict',{'event':event.to_dict()})
        self.assertEqual(status,503)
        self.assertNotIn('secret',json.dumps(body))
        self.assertEqual(self.call('POST','/api/v1/ingest',{'raw':'password=secret'})[0],422)

    def test_training_queue_snapshot_conflict_claim_and_completion(self):
        from shared.application.jobs import Jobs
        events=self.archive(40)
        status,job,_=self.call('POST','/api/v1/training/jobs',{'actor':'engineer','synthetic':True,'estimators':[4]},admin=True)
        self.assertEqual(status,202)
        self.assertEqual(job['records'],40)
        self.assertEqual(self.call('POST','/api/v1/training/jobs',{'actor':'engineer','synthetic':True},admin=True)[0],409)
        queue=Jobs(self.engine)
        claimed=queue.claim()
        self.assertEqual(set(claimed['event_ids']),{event.event_id for event in events})
        self.assertIsNone(queue.claim())
        self.assertTrue(queue.finish(claimed,{'approval_status':'candidate_not_approved'}))
        self.assertFalse(queue.finish(claimed,failure='stale_worker'))
        self.assertEqual(queue.get(job['id'])['status'],'succeeded')
        self.assertGreaterEqual(len(self.call('GET','/api/v1/audit-events')[1]['items']),2)

    def test_retention_preview_apply_keeps_provenance_and_active_snapshot(self):
        from sqlalchemy import update
        from shared.pipeline import models as m
        from shared.pipeline.store import now
        from shared.application.jobs import Jobs
        from test_pipeline_transactions import event
        # Standalone quarantined/offline canonical rows have no pending stream receipts.
        with self.engine.begin() as db:
            for index in range(40):
                self.store._insert_event(db,event(index))
        with self.engine.begin() as db:
            db.execute(update(m.normalized_logs).values(created_at=now()-timedelta(days=40)))
        body={'actor':'operator','days':30}
        self.assertEqual(self.call('POST','/api/v1/maintenance/retention',body,admin=True)[1]['eligible'],40)
        job=Jobs(self.engine).enqueue('operator',{'synthetic':True,'seed':42,'estimators':[4],'source':None})
        self.assertEqual(self.call('POST','/api/v1/maintenance/retention',body,admin=True)[1]['eligible'],0)
        queue=Jobs(self.engine);claimed=queue.claim();queue.finish(claimed,failure='test_only')
        self.assertEqual(self.call('POST','/api/v1/maintenance/retention',body|{'dry_run':False},admin=True)[1]['deleted'],40)
        self.assertEqual(self.repository.logs()['total'],0)
        self.assertEqual(self.repository.sources()['items'][0]['total_records'],40)
        self.assertIsNotNone(queue.get(job['id']))

    def test_archive_redacts_and_excludes_authentication(self):
        from shared.application.archive import ArchiveHandler
        from shared.pipeline.contracts import Delivery,Rejected
        from test_pipeline_transactions import event
        handler=ArchiveHandler(self.store,'archive-test')
        handler(Delivery('1',{'payload':event(message='password=hunter2 completed').to_json()}))
        self.assertNotIn('hunter2',json.dumps(self.repository.logs()))
        with self.assertRaises(Rejected):
            handler(Delivery('2',{'payload':event(1,service='sshd',message='Accepted password for root').to_json()}))

    @unittest.skipUnless(importlib.util.find_spec('sklearn'),'Compiled training dependencies unavailable')
    def test_real_offline_job_subprocess_trains_candidate(self):
        from ml_training.synthetic import synthetic_cases
        from ml_training.job_worker import run_one
        from shared.application.archive import ArchiveHandler
        from shared.application.jobs import Jobs
        from shared.pipeline.contracts import Delivery
        handler=ArchiveHandler(self.store,'archive-test')
        for index,(event,_) in enumerate(synthetic_cases(80)):
            handler(Delivery(str(index),{'payload':event.to_json()}))
        queue=Jobs(self.engine)
        job=queue.enqueue('test-engineer',{'synthetic':True,'source':None,'seed':42,'estimators':[4]})
        with patch.dict(os.environ,{'PYTHONPATH':str(ROOT)+':'+str(ROOT/'services/ml-training')},clear=True):
            self.assertTrue(run_one(queue,Path(self.temp.name)/'jobs',timeout=60))
        result=queue.get(job['id'])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(result['result']['approval_status'],'candidate_not_approved')
        self.assertEqual(len(result['result']['dataset_version']),64)

    def test_pending_archived_stream_input_is_never_retained_away(self):
        from sqlalchemy import update
        from shared.application.retention import retain
        from shared.pipeline import models as m
        from shared.pipeline.store import now
        self.archive(2)
        with self.engine.begin() as db:
            db.execute(update(m.normalized_logs).values(created_at=now()-timedelta(days=40)))
        self.assertEqual(retain(self.engine,30,'operator',False)['deleted'],0)
        self.assertEqual(self.repository.logs()['total'],2)

    def test_gateway_calls_real_alert_asgi_api_and_persists_action_history(self):
        import httpx
        from types import SimpleNamespace
        from shared.pipeline.contracts import PredictionNotice
        from alert_manager.api import AlertAPISettings,create_app
        from alert_manager.service import AlertService
        from test_pipeline_transactions import event
        item=event()
        prediction={'threshold':.5,'threshold_source':'artifact','feature_version':'1',
                    'anomaly_score':.9,'is_anomaly':True,'context_warm':True}
        model=SimpleNamespace(name='test-model',version='1',package_sha256='a'*64,scope='development')
        result=self.store.save_prediction('test-stage',item,prediction,model,{},0)
        service=AlertService(self.store)
        service.process('alerts-test',PredictionNotice(result['prediction_id'],item.event_id))
        identity=service.list()['items'][0]['id']
        alert_app=create_app(AlertAPISettings(api_token=self.settings.alert_token+'-long-enough-for-test',admin_token=self.admin),service,immediate)
        self.upstreams.services['alerts']=(self.settings.alert_url,self.settings.alert_token+'-long-enough-for-test')
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=alert_app)) as client:
                self.upstreams.client=client
                status,body,_=await request(self.app,'GET','/api/v1/alerts',token=self.token)
                self.assertEqual(status,200)
                self.assertEqual(body['total'],1)
                status,body,_=await request(self.app,'POST',f'/api/v1/alerts/{identity}/acknowledge',
                    {'actor':'operator','note':'password=hidden'},self.admin)
                self.assertEqual(status,200)
                self.assertEqual(body['status'],'acknowledged')
                status,history,_=await request(self.app,'GET',f'/api/v1/alerts/{identity}/history',token=self.token)
                self.assertEqual(status,200)
                self.assertNotIn('hidden',json.dumps(history))
        asyncio.run(check())
        self.assertEqual(self.repository.dashboard()['active_alerts'],1)

    def test_system_health_and_database_unavailable(self):
        self.assertEqual(self.call('GET','/api/v1/system')[1]['services']['database'],'ready')
        with patch.object(self.repository,'ping',side_effect=ConnectionError('secret')):
            self.assertEqual(self.call('GET','/health/ready')[0],503)

    def test_upstream_transport_timeout_is_safe(self):
        import httpx
        from api_gateway.upstreams import UpstreamError,Upstreams
        def fail(_request):
            raise httpx.ConnectTimeout('credential-secret')
        async def check():
            async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
                upstream=Upstreams(self.settings,client)
                with self.assertRaises(UpstreamError):
                    await upstream.request('alerts','GET','/health/ready')
                self.assertEqual(set((await upstream.health()).values()),{'unavailable'})
        asyncio.run(check())


if __name__=='__main__':
    unittest.main()
