from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'services/alert-manager')]
from shared.pipeline.config import StreamSettings
from shared.pipeline.contracts import Delivery,PredictionNotice,Rejected,reason_code
from alert_manager.policy import AlertPolicy


class StreamContractTests(unittest.TestCase):
    def test_retry_configuration_and_distinct_streams(self):
        for kwargs in [dict(max_retries=0),dict(claim_idle_ms=1),dict(capacity=0),dict(capacity=1),
                       dict(prediction_stream='logs:normalized:v1'),dict(retry_seconds=float('inf'))]:
            with self.assertRaises(ValueError):StreamSettings(**kwargs)

    def test_invalid_payload_does_not_echo_message(self):
        for fields in [{},{'payload':'password=private-secret'},{'payload':'[]'}]:
            with self.assertRaises(Rejected) as caught:Delivery('1-0',fields).payload()
            self.assertNotIn('private-secret',str(caught.exception))

    def test_notice_is_small_and_versioned(self):
        notice=PredictionNotice(str(uuid4()),str(uuid4()))
        self.assertEqual(PredictionNotice.parse(notice.to_dict()),notice)
        with self.assertRaises(Rejected):PredictionNotice.parse(notice.to_dict()|{'raw':'private'})
        with self.assertRaises(Rejected):PredictionNotice(notice.prediction_id,notice.event_id,2)

    def test_safe_reason_codes(self):
        self.assertEqual(reason_code('database_unavailable'),'database_unavailable')
        self.assertEqual(reason_code('password=private-secret'),'processing_failed')

    def test_alert_requires_anomaly_not_error_severity(self):
        policy=AlertPolicy()
        self.assertEqual(policy.eligibility({'is_anomaly':False}),'ignored_normal')
        self.assertEqual(policy.eligibility({'is_anomaly':True,'context_warm':False}),'ignored_warmup')

    def test_alert_configuration_validation(self):
        for kwargs in [dict(group_window_seconds=0),dict(high_margin=-1),dict(min_score=float('nan')),
                       dict(high_margin=1,critical_margin=.5),dict(require_warm_context='false')]:
            with self.assertRaises(ValueError):AlertPolicy(**kwargs)


if __name__=='__main__':unittest.main()
