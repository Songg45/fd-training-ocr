import json
from io import BytesIO
import unittest
from urllib.error import HTTPError, URLError

from PIL import Image

from fd_training_ocr.recognition import (MockRecognitionProvider, OllamaVisionProvider,
    RecognitionError, RecognitionServiceError, make_request, parse_response, recognize_fields)
from fd_training_ocr.template import Region, TemplateDefinition
from fd_training_ocr.validation import validate


def template():
    regions = (
        Region("date", "text", (.1, .1, .2, .1), {}),
        Region("attendee.01.unit_id", "attendee_cell", (.1, .3, .2, .1), {"row": 1}),
        Region("attendee.01.print_name", "attendee_cell", (.3, .3, .3, .1), {"row": 1}),
        Region("attendee.01.signature", "signature", (.6, .3, .3, .1), {"row": 1}),
        Region("attendee.02.unit_id", "attendee_cell", (.1, .5, .2, .1), {"row": 2}),
        Region("attendee.02.print_name", "attendee_cell", (.3, .5, .3, .1), {"row": 2}),
        Region("attendee.02.signature", "signature", (.6, .5, .3, .1), {"row": 2}),
    )
    return TemplateDefinition("test", "v1", "normalized_xywh", (100, 100),
                              frozenset({"signature"}), {}, regions)


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.page = Image.new("L", (100, 100), 255)

    def test_mock_is_deterministic_and_preserves_metadata(self):
        provider = MockRecognitionProvider({"date": {"value": "12/17/25", "confidence": .92,
                                                      "alternatives": ["12/11/25"]}})
        result = recognize_fields(self.page, self.page, template(), provider)[0]
        self.assertEqual(result.value, result.normalized_as_returned)
        self.assertEqual(result.alternatives, ("12/11/25",))
        self.assertEqual((result.provider, result.model), ("mock", "deterministic-fixture-v1"))
        self.assertEqual(result.source_region, (10, 10, 30, 20))
        self.assertEqual(result.raw_output, '{"alternatives":["12/11/25"],"confidence":0.92,"value":"12/17/25"}')

    def test_attendee_cells_are_separate_and_empty_rows_skipped(self):
        responses = {name: {"value": "x", "confidence": .99, "alternatives": []}
                     for name in ("date", "attendee.01.unit_id", "attendee.01.print_name")}
        responses["date"]["value"] = "01/02/26"
        provider = MockRecognitionProvider(responses)
        recognize_fields(self.page, self.page, template(), provider, populated_rows=[1])
        self.assertEqual([r.field_name for r in provider.requests],
                         ["date", "date", "attendee.01.unit_id", "attendee.01.unit_id",
                          "attendee.01.print_name", "attendee.01.print_name"])
        self.assertNotEqual(provider.requests[1].prompt, provider.requests[2].prompt)

    def test_signatures_are_never_cropped_serialized_or_sent(self):
        provider = MockRecognitionProvider()
        recognize_fields(self.page, self.page, template(), provider, populated_rows=[1, 2])
        self.assertFalse(any("signature" in r.field_name for r in provider.requests))
        with self.assertRaisesRegex(RecognitionError, "never cropped"):
            make_request(self.page, template().region("attendee.01.signature"))

    def test_strict_parser_rejects_malformed_or_extra_data(self):
        request = make_request(self.page, template().region("date"))
        for raw in ("not json", '{"value":"x","confidence":2,"alternatives":[]}',
                    '{"value":"x","confidence":.5,"alternatives":[],"note":"x"}'):
            with self.subTest(raw=raw), self.assertRaises(RecognitionError):
                parse_response(raw, request, "test", "test")

    def test_ollama_payload_is_local_structured_and_contains_only_crop(self):
        captured = {}
        def transport(url, body, timeout):
            captured.update(url=url, body=json.loads(body), timeout=timeout)
            return json.dumps({"message": {"content": json.dumps(
                {"value": "AB12", "confidence": .7, "alternatives": []})}}).encode()
        provider = OllamaVisionProvider(model="tiny-vision", timeout_seconds=12, transport=transport)
        result = provider.recognize(make_request(self.page, template().region("attendee.01.unit_id")))
        self.assertEqual(result.value, "AB12")
        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(captured["body"]["format"], "json")
        self.assertEqual(captured["timeout"], 12)
        self.assertNotIn("signature", json.dumps(captured["body"]))

    def test_remote_ollama_endpoint_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be local"):
            OllamaVisionProvider(endpoint="https://example.com")

    def test_ollama_repeat_error_reports_http_model_and_field_not_install_advice(self):
        def transport(url, body, timeout):
            raise HTTPError(url, 500, "Internal Server Error", {}, BytesIO(
                b'{"error":"prediction aborted, token repeat limit reached"}'))
        provider = OllamaVisionProvider(transport=transport)
        with self.assertRaises(RecognitionError) as caught:
            provider.recognize(make_request(self.page, template().region("attendee.02.unit_id")))
        self.assertNotIsInstance(caught.exception, RecognitionServiceError)
        message = str(caught.exception)
        for detail in ("HTTP 500", "qwen2.5vl:7b", "attendee.02.unit_id",
                       "prediction aborted, token repeat limit reached"):
            self.assertIn(detail, message)
        self.assertNotIn("Install Ollama", message)
        self.assertIsInstance(caught.exception.__cause__, HTTPError)

    def test_ollama_error_body_is_bounded_and_can_be_non_json(self):
        class BoundedBody(BytesIO):
            def read(self, size=-1):
                self.requested = size
                return super().read(size)
        error_body = BoundedBody(b"upstream broke " + b"x" * 20000)
        def transport(url, body, timeout):
            raise HTTPError(url, 502, "Bad Gateway", {}, error_body)
        with self.assertRaises(RecognitionServiceError) as caught:
            OllamaVisionProvider(transport=transport).recognize(
                make_request(self.page, template().region("date")))
        self.assertEqual(error_body.requested, 8192)
        self.assertTrue(error_body.closed)
        self.assertIn("HTTP 502 Bad Gateway; upstream broke", str(caught.exception))
        self.assertLess(len(str(caught.exception)), 1800)

    def test_service_failure_does_not_retry_each_field(self):
        for code, detail in ((404, "model not found"), (500, "out of memory"), (None, "connection refused")):
            calls = []
            def transport(url, body, timeout):
                calls.append(url)
                if code is None:
                    raise URLError(detail)
                raise HTTPError(url, code, "service failure", {},
                                BytesIO(json.dumps({"error": detail}).encode()))
            with self.subTest(code=code), self.assertRaisesRegex(RecognitionServiceError, detail):
                recognize_fields(self.page, self.page, template(),
                                 OllamaVisionProvider(transport=transport), populated_rows=[1, 2])
            self.assertEqual(len(calls), 1)

    def test_repeat_error_in_success_envelope_is_also_recoverable(self):
        provider = OllamaVisionProvider(transport=lambda *args: json.dumps(
            {"error": "prediction aborted, token repeat limit reached"}).encode())
        with self.assertRaises(RecognitionError) as caught:
            provider.recognize(make_request(self.page, template().region("date")))
        self.assertNotIsInstance(caught.exception, RecognitionServiceError)
        self.assertIn("token repeat limit", str(caught.exception))

    def test_actual_http_repeat_failure_can_recover_as_a_valid_blank_read(self):
        calls = []
        def transport(url, body, timeout):
            calls.append(json.loads(body))
            if len(calls) == 1:
                raise HTTPError(url, 500, "Internal Server Error", {}, BytesIO(
                    b'{"error":"prediction aborted, token repeat limit reached"}'))
            return json.dumps({"message": {"content": json.dumps(
                {"value": None, "confidence": 0.0, "alternatives": []})}}).encode()
        result = recognize_fields(self.page, self.page, template(),
                                  OllamaVisionProvider(transport=transport))[0]
        self.assertEqual(len(calls), 3)  # Failed Stage 1, recovery, independent Stage 2.
        self.assertIsNone(result.value)
        self.assertEqual(len(result.attempts), 2)
        self.assertIn("HTTP 500", result.attempts[0]["retry_attempts"][0]["error"])
        self.assertIsNone(result.attempts[0]["retry_attempts"][1]["value"])
        self.assertNotIn("error", result.attempts[0])
        self.assertTrue(validate((result,)).review_required)

    def test_failed_blank_cell_is_bounded_null_and_does_not_abort_other_fields(self):
        class BlankCellProvider(MockRecognitionProvider):
            def recognize(self, request):
                if request.field_name == "attendee.02.unit_id":
                    self.requests.append(request)
                    raise RecognitionError("HTTP 500: prediction aborted, token repeat limit reached")
                return super().recognize(request)
        provider = BlankCellProvider({"date": {"value": "01/02/26", "confidence": .99, "alternatives": []},
            "attendee.01.unit_id": {"value": "4354", "confidence": .99, "alternatives": []},
            "attendee.02.print_name": {"value": "Example Person", "confidence": .99, "alternatives": []}})
        results = recognize_fields(self.page, self.page, template(), provider, populated_rows=[1, 2])
        by_name = {item.field_name: item for item in results}
        failed = by_name["attendee.02.unit_id"]
        self.assertIsNone(failed.value)
        self.assertEqual(failed.confidence, 0)
        self.assertEqual(failed.raw_output, "")
        self.assertEqual(failed.alternatives, ())
        self.assertEqual([item["stage"] for item in failed.attempts], [1, 2])
        self.assertTrue(all(len(item["retry_attempts"]) == 3 for item in failed.attempts))
        requests = [r for r in provider.requests if r.field_name == failed.field_name]
        self.assertEqual(len(requests), 6)
        # Recovery changes resolution/border, never expands into another row.
        for stage in (1, 2):
            stage_requests = [r for r in requests if r.attempt == stage]
            self.assertEqual(len({r.source_region for r in stage_requests}), 1)
            self.assertGreater(stage_requests[1].image.width, stage_requests[0].image.width)
            self.assertNotEqual(stage_requests[0].prompt, stage_requests[1].prompt)
            self.assertIn("no legible handwritten characters", stage_requests[1].prompt)
        self.assertEqual(by_name["date"].value, "01/02/26")
        self.assertEqual(by_name["attendee.01.unit_id"].value, "4354")
        self.assertEqual(by_name["attendee.02.print_name"].value, "Example Person")
        self.assertFalse(any("signature" in r.field_name for r in provider.requests))
        assessment = next(item for item in validate(results).fields if item.field_name == failed.field_name)
        self.assertTrue(assessment.review_required)
        self.assertTrue(any("OCR stage 1 failed after 3" in warning and "token repeat limit" in warning
                            for warning in assessment.warnings))

    def test_recovery_preserves_independent_stage_slots_and_failures_for_audit(self):
        class RecoveryProvider(MockRecognitionProvider):
            def recognize(self, request):
                if not self.requests:
                    self.requests.append(request)
                    raise RecognitionError("malformed response")
                return super().recognize(request)
        provider = RecoveryProvider({"date": {"value": "12/17/25", "confidence": .99, "alternatives": []}})
        result = recognize_fields(self.page, self.page, template(), provider)[0]
        self.assertEqual(result.value, "12/17/25")
        self.assertEqual([r.attempt for r in provider.requests], [1, 1, 2])
        self.assertEqual([s["stage"] for s in result.attempts], [1, 2])
        self.assertEqual(len(result.attempts[0]["retry_attempts"]), 2)
        self.assertEqual(result.attempts[0]["retry_attempts"][0]["error"], "malformed response")
        self.assertEqual(result.attempts[0]["retry_attempts"][1]["value"], "12/17/25")
        self.assertNotIn("error", result.attempts[0])
        self.assertNotIn("12/17/25", provider.requests[-1].prompt)
        self.assertFalse(validate((result,)).review_required)

    def test_retry_limit_applies_per_stage_and_blank_success_is_not_a_failed_request(self):
        class FailingProvider(MockRecognitionProvider):
            def recognize(self, request):
                self.requests.append(request)
                raise RecognitionError("unreadable response")
        for limit in (1, 2, 3):
            provider = FailingProvider()
            result = recognize_fields(self.page, self.page, template(), provider, max_attempts=limit)[0]
            self.assertEqual(len(provider.requests), limit * 2)
            self.assertEqual(len(result.attempts), 2)
        provider = MockRecognitionProvider()
        result = recognize_fields(self.page, self.page, template(), provider)[0]
        self.assertEqual(len(provider.requests), 2)
        self.assertTrue(all("error" not in stage for stage in result.attempts))
        for invalid in (0, 4, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                recognize_fields(self.page, self.page, template(), provider, max_attempts=invalid)

    def test_crop_is_white_padded_without_changing_source_box(self):
        request = make_request(self.page, template().region("date"), padding=7,
                               force_variant="raw")
        self.assertEqual(request.source_region, (10, 10, 30, 20))
        self.assertEqual(request.image.size, (34, 24))
        self.assertEqual(request.image.getpixel((0, 0)), 255)

    def test_suppression_falls_back_to_raw_when_no_new_ink_is_preserved(self):
        request = make_request(self.page, template().region("date"), self.page,
                               force_variant=None)
        self.assertEqual(request.variant, "raw")

    def test_two_independent_stages_run_sequentially_and_preserve_stage1(self):
        class SequenceProvider:
            name, model = "sequence", "test"
            def __init__(self): self.requests = []
            def recognize(self, request):
                self.requests.append(request)
                value = "not-a-date" if len(self.requests) == 1 else "01/02/26"
                return parse_response(json.dumps({"value": value, "confidence": .99,
                    "alternatives": []}), request, self.name, self.model)
        provider = SequenceProvider()
        result = recognize_fields(self.page, self.page, template(), provider)[0]
        self.assertEqual(result.value, "not-a-date")
        self.assertEqual([x.attempt for x in provider.requests], [1, 2])
        self.assertEqual([x["value"] for x in result.attempts], ["not-a-date", "01/02/26"])
        self.assertEqual([x["variant"] for x in result.attempts], ["raw", "raw"])
        self.assertEqual([(x["provider"], x["model"]) for x in result.attempts],
                         [("sequence", "test")] * 2)
        self.assertGreater(provider.requests[1].image.width, provider.requests[0].image.width)
        self.assertNotEqual(provider.requests[0].prompt, provider.requests[1].prompt)
        self.assertNotIn("not-a-date", provider.requests[1].prompt)


if __name__ == "__main__":
    unittest.main()
