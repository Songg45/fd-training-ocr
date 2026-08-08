import json
import unittest

from PIL import Image, ImageDraw

from fd_training_ocr.recognition import (ContextVerificationRequest, MockRecognitionProvider,
    RecognitionError, RecognitionResult, parse_context_response)
from fd_training_ocr.second_pass import verify_second_pass
from fd_training_ocr.template import Region, TemplateDefinition
from fd_training_ocr.validation import Roster, RosterMember, validate


def result(name, value, confidence=.99, alternatives=()):
    return RecognitionResult(name, value, value, confidence, tuple(alternatives), "raw", "mock",
                             "pass1", (0, 0, 10, 10))


def template():
    regions = (
        Region("start_time", "text", (.10, .10, .12, .08), {}),
        Region("end_time", "text", (.25, .10, .12, .08), {}),
        Region("total_hours", "text", (.40, .10, .10, .08), {}),
        Region("instructor", "text", (.10, .24, .30, .08), {}),
        Region("attendee.01.unit_id", "attendee_cell", (.10, .40, .15, .08), {"row": 1}),
        Region("attendee.01.print_name", "attendee_cell", (.25, .40, .30, .08), {"row": 1}),
        Region("attendee.01.signature", "signature", (.55, .40, .35, .08), {"row": 1}),
    )
    return TemplateDefinition("test", "v1", "normalized_xywh", (1000, 1000),
                              frozenset({"signature"}), {}, regions)


class ContextParserTests(unittest.TestCase):
    def test_strict_time_schema_rejects_extra_or_incomplete_data(self):
        request = ContextVerificationRequest("time_group", "x", Image.new("L", (2, 2)),
                                             (0, 0, 2, 2), "time_group")
        with self.assertRaises(RecognitionError):
            parse_context_response(json.dumps({"start_time": "16:00"}), request, "mock", "m")
        payload = {"start_time": "16:00", "end_time": "17:00", "total_hours": "1",
                   "internally_consistent": True,
                   "alternatives": {"start_time": [], "end_time": [], "total_hours": []},
                   "unexpected": True}
        with self.assertRaises(RecognitionError):
            parse_context_response(json.dumps(payload), request, "mock", "m")


class SecondPassTests(unittest.TestCase):
    def test_clean_fields_do_not_trigger_context_calls(self):
        first = (result("start_time", "16:00"), result("end_time", "17:00"),
                 result("total_hours", "1"), result("instructor", "Synthetic Instructor"))
        report = validate(first)
        provider = MockRecognitionProvider()
        verified = verify_second_pass(Image.new("L", (1000, 1000), 255), template(),
                                      provider, first, report)
        self.assertEqual((verified.call_count, provider.context_requests), (0, []))

    def test_valid_looking_contradictory_times_trigger_and_two_signals_resolve(self):
        first = (result("start_time", "16:20"), result("end_time", "17:00"),
                 result("total_hours", "2"))
        report = validate(first)
        self.assertTrue(any("duration" in warning for warning in report.warnings))
        provider = MockRecognitionProvider(context_responses={"time_group": {
            "start_time": "16:00", "end_time": "17:00", "total_hours": "1",
            "internally_consistent": True,
            "alternatives": {"start_time": [], "end_time": [], "total_hours": []}}})
        verified = verify_second_pass(Image.new("L", (1000, 1000), 255), template(),
                                      provider, first, report)
        self.assertEqual(verified.call_count, 1)
        self.assertEqual(verified.resolutions["start_time"].resolved_value, "16:00")
        self.assertFalse(verified.resolutions["end_time"].review_required)  # Pass 1 + Pass 2 agree.
        self.assertIn("deterministic", verified.resolutions["start_time"].resolution_reason)
        attempt = verified.resolutions["start_time"].attempts[0]
        self.assertEqual((attempt["provider"], attempt["model"]), ("mock", "deterministic-fixture-v1"))

    def test_roster_and_context_resolve_instructor_and_row_without_overwriting_first_pass(self):
        roster = Roster((RosterMember("Nick Sledge", ("4354",), ()),))
        first = (result("instructor", "Nick Sledar"), result("attendee.01.unit_id", "U354"),
                 result("attendee.01.print_name", "Nick Sleder"))
        report = validate(first, roster=roster)
        provider = MockRecognitionProvider(context_responses={
            "instructor": {"instructor": "Nick Sledge", "handwriting_supports_candidate": True,
                           "alternatives": {"instructor": []}},
            "attendee.01": {"unit_id": "4354", "print_name": "Nick Sledge",
                            "handwriting_supports_candidate": True,
                            "alternatives": {"unit_id": [], "print_name": []}}})
        verified = verify_second_pass(Image.new("L", (1000, 1000), 255), template(), provider,
                                      first, report, roster)
        instructor = verified.resolutions["instructor"]
        self.assertEqual((instructor.first_pass, instructor.second_pass, instructor.roster_suggestion),
                         ("Nick Sledar", "Nick Sledge", "Nick Sledge"))
        self.assertEqual(instructor.resolved_value, "Nick Sledge")
        self.assertEqual(verified.resolutions["attendee.01.unit_id"].resolved_value, "4354")

    def test_disagreement_stays_review_required_and_signature_is_outside_crop(self):
        roster = Roster((RosterMember("Nick Sledge", ("4354",), ()),))
        first = (result("attendee.01.unit_id", "U354"),
                 result("attendee.01.print_name", "Nick Sleder"))
        report = validate(first, roster=roster)
        provider = MockRecognitionProvider(context_responses={"attendee.01": {
            "unit_id": "8354", "print_name": "Nick Slade", "handwriting_supports_candidate": False,
            "alternatives": {"unit_id": ["4354"], "print_name": ["Nick Sledge"]}}})
        page = Image.new("L", (1000, 1000), 255)
        ImageDraw.Draw(page).rectangle((550, 400, 899, 479), fill=0)
        verified = verify_second_pass(page, template(), provider, first, report, roster)
        self.assertTrue(verified.resolutions["attendee.01.unit_id"].review_required)
        self.assertIsNone(verified.resolutions["attendee.01.unit_id"].resolved_value)
        request = provider.context_requests[0]
        self.assertLessEqual(request.source_region[2], 550)
        self.assertEqual(request.image.getextrema(), (255, 255))
        self.assertNotIn("signed", request.prompt.casefold())

    def test_malformed_context_result_is_preserved_as_review_evidence(self):
        first = (result("instructor", "Maybe", alternatives=("May Bee",)),)
        report = validate(first)
        provider = MockRecognitionProvider(context_responses={"instructor": {"bad": "schema"}})
        verified = verify_second_pass(Image.new("L", (1000, 1000), 255), template(), provider,
                                      first, report)
        resolution = verified.resolutions["instructor"]
        self.assertTrue(resolution.review_required)
        self.assertIn("error", resolution.attempts[0])


if __name__ == "__main__":
    unittest.main()
