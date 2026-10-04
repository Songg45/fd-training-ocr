from concurrent.futures import Future
import unittest

from fd_training_ocr.gui import (fireworks_response_popup_text,
                                 submission_close_action)


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


if __name__ == "__main__":
    unittest.main()
