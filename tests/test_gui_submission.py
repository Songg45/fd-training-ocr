from concurrent.futures import Future
import unittest

from fd_training_ocr.gui import submission_close_action


class SubmissionCloseTests(unittest.TestCase):
    def test_close_may_proceed_without_api_future(self):
        self.assertEqual(submission_close_action(None), "close")

    def test_incomplete_api_future_requires_wait(self):
        self.assertEqual(submission_close_action(Future()), "wait")

    def test_completed_unpolled_api_future_requires_finalization(self):
        future = Future()
        future.set_result(object())
        self.assertEqual(submission_close_action(future), "finalize")


if __name__ == "__main__":
    unittest.main()
