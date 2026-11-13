"""Requires a disposable real Redis. Never represented by the test-only memory broker."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
LIVE=importlib.util.find_spec('redis') is not None and os.environ.get('RUN_REDIS_INTEGRATION')=='1'


@unittest.skipUnless(LIVE,'Real Redis integration requires redis-py, RUN_REDIS_INTEGRATION=1 and a server')
class LiveRedisStreams(unittest.TestCase):
    def setUp(self):
        from shared.pipeline.config import StreamSettings
        from shared.pipeline.redis_streams import connect
        self.prefix='test:phase6:'+uuid4().hex
        self.settings=StreamSettings(host=os.environ.get('REDIS_HOST','127.0.0.1'),
            port=int(os.environ.get('REDIS_PORT','6379')),password=os.environ.get('REDIS_PASSWORD',''),
            normalized_stream=self.prefix+':normal',prediction_stream=self.prefix+':pred',
            failed_stream=self.prefix+':failed',claim_idle_ms=1,lease_ms=1)
        self.client=connect(self.settings)
        self.client.ping()
        self.groups=[]

    def tearDown(self):
        keys=set(self.client.scan_iter(match=self.prefix+'*'))
        for group in self.groups:keys.update(self.client.scan_iter(match=group.key+'*'))
        if keys:self.client.delete(*keys)
        self.client.close()

    def group(self,consumer):
        from shared.pipeline.redis_streams import RedisGroup
        group=RedisGroup(self.client,self.settings.normalized_stream,'inference',self.settings,consumer)
        group.initialize();self.groups.append(group)
        return group

    def test_recovery_dedup_ack_and_consumer_safe_retention(self):
        from shared.pipeline.redis_streams import RedisJSONPublisher
        from shared.pipeline.contracts import Delivery
        import time
        first=self.group('crashed')
        publisher=RedisJSONPublisher(self.client,self.settings.normalized_stream,self.settings)
        for index in range(3):publisher.publish(str(index),{'index':index})
        self.assertTrue(first.lease())
        original=first.next()
        self.assertIsNotNone(original)
        # Keep a second group unread: retention must not destroy its backlog.
        self.client.xgroup_create(self.settings.normalized_stream,'unread',id='0-0')
        time.sleep(.01)
        recovered=self.group('recovered')
        self.assertTrue(recovered.lease())
        retry=recovered.next()
        self.assertEqual(retry.entry_id,original.entry_id)
        recovered.ack(retry)
        self.assertEqual(recovered.trim(),0)
        self.client.xgroup_destroy(self.settings.normalized_stream,'unread')
        for _ in range(2):recovered.ack(recovered.next())
        self.assertEqual(recovered.trim(),2)
        self.assertEqual(publisher.publish('0',{'index':0}),'duplicate')
        self.assertEqual(self.client.xpending(self.settings.normalized_stream,'inference')['pending'],0)

    def test_failed_message_is_reason_only_and_ack_is_atomic(self):
        from shared.pipeline.redis_streams import RedisJSONPublisher
        group=self.group('worker')
        RedisJSONPublisher(self.client,self.settings.normalized_stream,self.settings).publish('bad',{'raw':'password=private-secret'})
        message=group.next()
        group.dead_letter(message,'invalid_stream_payload','safe-failure-id',1)
        rows=self.client.xrange(self.settings.failed_stream)
        self.assertEqual(len(rows),1)
        self.assertNotIn('private-secret',str(rows))
        self.assertEqual(self.client.xpending(self.settings.normalized_stream,'inference')['pending'],0)


if __name__=='__main__':unittest.main()
