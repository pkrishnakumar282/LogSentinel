import asyncio
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tests/integration')]
import test_pipeline_transactions as fixtures


@unittest.skipUnless(fixtures.READY,'SQLAlchemy unavailable')
class AlertAPIIntegration(unittest.TestCase):
    def test_real_store_api_filters_actions_history_and_auth(self):
        from alert_manager.api import AlertAPISettings,create_app
        from test_inference import asgi_request,immediate
        fixture=fixtures.PipelineTransactions()
        fixture.setUp()
        try:
            fixture.ingest(0,message='unusual task')
            identity=fixture.alert_service.list()['items'][0]['id']
            settings=AlertAPISettings(api_token='test-only-alert-token-32-characters',
                                      admin_token='test-only-admin-token-32-characters')
            app=create_app(settings,fixture.alert_service,executor=immediate)
            async def exercise():
                self.assertEqual((await asgi_request(app,'GET','/health/ready'))[0],200)
                self.assertEqual((await asgi_request(app,'GET','/api/v1/alerts'))[0],401)
                status,body=await asgi_request(app,'GET','/api/v1/alerts',token=settings.api_token)
                self.assertEqual(status,200)
                self.assertEqual(body['total'],1)
                path=f'/api/v1/alerts/{identity}/acknowledge'
                self.assertEqual((await asgi_request(app,'POST',path,token=settings.api_token,data={'actor':'operator'}))[0],403)
                status,body=await asgi_request(app,'POST',path,token=settings.admin_token,data={'actor':'operator','note':'password=private-secret'})
                self.assertEqual(status,200,body)
                self.assertEqual(body['status'],'acknowledged')
                status,body=await asgi_request(app,'GET',f'/api/v1/alerts/{identity}/history',token=settings.api_token)
                self.assertEqual(status,200)
                self.assertEqual(len(body['items']),2)
                self.assertNotIn('private-secret',str(body))
                self.assertEqual((await asgi_request(app,'POST',f'/api/v1/alerts/{identity}/resolve',token=settings.admin_token,data={'actor':'operator'}))[0],200)
                self.assertEqual((await asgi_request(app,'POST',path,token=settings.admin_token,data={'actor':'operator'}))[0],409)
                self.assertEqual((await asgi_request(app,'POST',path,token=settings.admin_token,data={'actor':None,'note':'never-echo'}))[0],422)
            asyncio.run(exercise())
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main()
