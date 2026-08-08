import json
import unittest

from PIL import Image

from fd_training_ocr.recognition import (MockRecognitionProvider, OllamaVisionProvider,
    RecognitionError, make_request, parse_response, recognize_fields)
from fd_training_ocr.template import Region, TemplateDefinition


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
                         ["date", "attendee.01.unit_id", "attendee.01.print_name"])
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

    def test_invalid_format_retries_sequentially_and_preserves_attempts(self):
        class SequenceProvider:
            name, model = "sequence", "test"
            def __init__(self): self.requests = []
            def recognize(self, request):
                self.requests.append(request)
                value = "not-a-date" if len(self.requests) < 3 else "01/02/26"
                return parse_response(json.dumps({"value": value, "confidence": .99,
                    "alternatives": []}), request, self.name, self.model)
        provider = SequenceProvider()
        result = recognize_fields(self.page, self.page, template(), provider)[0]
        self.assertEqual(result.value, "01/02/26")
        self.assertEqual([x.attempt for x in provider.requests], [1, 2, 3])
        self.assertEqual([x["variant"] for x in result.attempts], ["raw", "raw", "raw"])
        self.assertEqual([(x["provider"], x["model"]) for x in result.attempts],
                         [("sequence", "test")] * 3)
        self.assertGreater(provider.requests[2].image.width, provider.requests[1].image.width)


if __name__ == "__main__":
    unittest.main()
