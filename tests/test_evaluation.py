import unittest
from fd_training_ocr.evaluation import evaluate

class EvaluationTests(unittest.TestCase):
    def test_metrics_remain_separate_by_field_type(self):
        truth=[{"record_id":"1","field_name":"date","value":"2026-01-01"},{"record_id":"1","field_name":"start_time","value":"16:00"},{"record_id":"1","field_name":"attendee.01.print_name","value":"A"}]
        predictions=[{"record_id":"1","field_name":"date","value":"wrong"},{"record_id":"1","field_name":"start_time","value":"16:00"},{"record_id":"1","field_name":"attendee.01.print_name","value":"A"}]
        metrics={x.field_type:x for x in evaluate(predictions,truth)}
        self.assertEqual(metrics["date"].exact_match,0); self.assertEqual(metrics["time"].exact_match,1); self.assertEqual(metrics["print_name"].exact_match,1)
