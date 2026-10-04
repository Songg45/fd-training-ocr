from concurrent.futures import Future
import unittest

from fd_training_ocr.gui import (fireworks_resolution_warning,
                                 fireworks_readiness_message,
                                 fireworks_response_popup_text,
                                 record_banner_state,
                                 roster_rows_with_instructor_ids,
                                 submission_close_action)
from fd_training_ocr.fireworks import (FireworksCategory, FireworksInstructor,
                                       FireworksLocation, FireworksMappings)


class SubmissionCloseTests(unittest.TestCase):
    def test_close_may_proceed_without_api_future(self):
        self.assertEqual(submission_close_action(None), "close")

    def test_incomplete_api_future_requires_wait(self):
        self.assertEqual(submission_close_action(Future()), "wait")

    def test_completed_unpolled_api_future_requires_finalization(self):
        future = Future()
        future.set_result(object())
        self.assertEqual(submission_close_action(future), "finalize")


class FireworksResponsePopupTests(unittest.TestCase):
    def test_complete_raw_response_and_receipt_evidence_are_visible(self):
        raw = '{"rc":1,"description":"new assignment added","responseObj":42}'

        text = fireworks_response_popup_text(
            status_code=200,
            response_text=raw,
            response_payload={"description": "not used when raw text exists"},
            response_body_sha256="abc123")

        self.assertIn("HTTP status: 200", text)
        self.assertIn("Response body SHA-256: abc123", text)
        self.assertIn(raw, text)
        self.assertNotIn("not used when raw text exists", text)

    def test_parsed_payload_is_shown_when_raw_body_is_unavailable(self):
        text = fireworks_response_popup_text(
            status_code=422,
            response_text=None,
            response_payload={"rc": 1, "description": "invalid"})

        self.assertIn('"rc": 1', text)
        self.assertIn('"description": "invalid"', text)


class RecordBannerTests(unittest.TestCase):
    def test_submitted_record_uses_green_banner_even_if_review_warnings_remain(self):
        record = {
            "status": "review_required",
            "warnings": ["one or more fields require review"],
            "fireworks_submission": {"status": "submitted", "activity_id": 94},
        }

        self.assertEqual(
            record_banner_state(record),
            ("Submitted to Fireworks — Activity ID: 94", "#286428"),
        )

    def test_submitted_record_without_activity_id_still_has_green_banner(self):
        record = {"fireworks_submission": {"status": "submitted"}}

        self.assertEqual(
            record_banner_state(record),
            ("Submitted to Fireworks", "#286428"),
        )

    def test_unsubmitted_review_record_keeps_red_banner(self):
        record = {
            "status": "review_required",
            "warnings": ["date requires review", "instructor requires review"],
        }

        self.assertEqual(
            record_banner_state(record),
            (
                "REVIEW REQUIRED — date requires review; instructor requires review",
                "#8b1e1e",
            ),
        )

    def test_completed_unsubmitted_record_has_no_banner(self):
        self.assertEqual(record_banner_state({"status": "complete"}), ("", ""))


class FireworksResolutionWarningTests(unittest.TestCase):
    def test_combines_unresolved_staff_and_instructor(self):
        self.assertEqual(
            fireworks_resolution_warning(
                ("Outside Member",), ("Instructor: Visiting Instructor",)),
            "Fireworks Staff ID unresolved for: Outside Member | "
            "Fireworks Instructor ID unresolved for: Visiting Instructor",
        )

    def test_shows_only_the_unresolved_group(self):
        self.assertEqual(
            fireworks_resolution_warning((), ("Instructor: Outside Trainer",)),
            "Fireworks Instructor ID unresolved for: Outside Trainer",
        )
        self.assertEqual(
            fireworks_resolution_warning(("Outside Member",), ()),
            "Fireworks Staff ID unresolved for: Outside Member",
        )

    def test_empty_resolution_warning_is_hidden(self):
        self.assertEqual(fireworks_resolution_warning((), ()), "")


class FireworksReadinessMessageTests(unittest.TestCase):
    def test_generated_request_is_ready_when_validation_passes(self):
        self.assertEqual(
            fireworks_readiness_message("generated", ()),
            "READY TO SUBMIT")

    def test_saved_request_combines_saved_and_ready_status(self):
        self.assertEqual(
            fireworks_readiness_message("reviewed", ()),
            "Formatted Request saved | READY TO SUBMIT")

    def test_validation_errors_never_show_ready(self):
        message = fireworks_readiness_message(
            "reviewed", ("assignCat must be configured", "location is required"))

        self.assertEqual(
            message,
            "REQUEST NOT READY — assignCat must be configured | location is required")
        self.assertNotIn("READY TO SUBMIT", message)


class RosterInstructorColumnTests(unittest.TestCase):
    def test_external_mapping_prefills_only_blank_instructor_ids(self):
        mappings = FireworksMappings(
            (FireworksCategory("Company Training", 1, "confirmed"),),
            (FireworksLocation("Fire Station", 3, "version"),),
            "Pilot FD", 54,
            (FireworksInstructor("Robert Austin Greene", 1,
                                 ("Austin Greene",)),))
        rows = [
            ("Austin Greene", "1154", "", "8", ""),
            ("Manual Instructor", "2254", "", "12", "99"),
        ]

        self.assertEqual(
            roster_rows_with_instructor_ids(rows, mappings),
            [
                ("Austin Greene", "1154", "", "8", "1"),
                ("Manual Instructor", "2254", "", "12", "99"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
