import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError, URLError

from fd_training_ocr.fireworks_client import (
    DuplicateSubmissionError, FireworksClient, FireworksConnectionError,
    FireworksSubmissionRejected, FireworksSubmissionUnknown, SubmissionLedger)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return self.body


class RecordingTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FireworksClientTests(unittest.TestCase):
    def connected_client(self, transport):
        client = FireworksClient("secret-token", transport=transport)
        client.connected = True
        return client

    def test_connection_uses_one_read_only_get_and_bearer_token(self):
        transport = RecordingTransport(FakeResponse({"responseObj": [], "rc": 0}))
        client = FireworksClient("Bearer secret-token", transport=transport)
        client.validate_connection()
        self.assertTrue(client.connected)
        self.assertEqual(len(transport.calls), 1)
        request, timeout = transport.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("getTable?tbl=location", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertGreater(timeout, 0)

    def test_post_sends_visible_payload_exactly_once(self):
        transport = RecordingTransport(
            FakeResponse({"responseObj": {"moneln": 12345}, "rc": 0}))
        client = self.connected_client(transport)
        payload = {"assignTitle": "Test", "staff": [20], "station": 54}
        response = client.post_activity(payload)
        self.assertEqual(response.payload["responseObj"]["moneln"], 12345)
        self.assertEqual(len(transport.calls), 1)
        request, _timeout = transport.calls[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/TrnCommon/addActivity"))
        self.assertEqual(json.loads(request.data.decode("utf-8")), payload)

    def test_uncertain_post_is_never_retried(self):
        transport = RecordingTransport(URLError("connection dropped"))
        client = self.connected_client(transport)
        with self.assertRaises(FireworksSubmissionUnknown):
            client.post_activity({"assignTitle": "One attempt"})
        self.assertEqual(len(transport.calls), 1)

    def test_http_400_is_a_definite_rejection(self):
        error = HTTPError(
            "https://example.invalid", 400, "Bad Request", {}, None)
        error.read = lambda: b'{"description":"invalid activity"}'
        transport = RecordingTransport(error)
        client = self.connected_client(transport)
        with self.assertRaises(FireworksSubmissionRejected):
            client.post_activity({"assignTitle": "Rejected"})
        self.assertEqual(len(transport.calls), 1)

    def test_token_is_discarded_and_cannot_be_used_again(self):
        client = self.connected_client(RecordingTransport())
        client.clear_token()
        self.assertFalse(client.connected)
        with self.assertRaises(FireworksConnectionError):
            client.post_activity({})


class SubmissionLedgerTests(unittest.TestCase):
    def record(self):
        return {
            "source_file": "Scan.pdf",
            "source_sha256": "a" * 64,
            "page": 1,
        }

    def test_success_receipt_prevents_duplicate_submission(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            payload = {"assignTitle": "Test", "staff": [20]}
            entry = ledger.append(
                record=self.record(), payload=payload, status="submitted",
                response={"responseObj": {"moneln": 2468}, "rc": 0},
                recorded_at="2026-09-19T20:00:00+00:00")
            self.assertEqual(entry["activity_id"], 2468)
            self.assertEqual(entry["status"], "submitted")
            with self.assertRaises(DuplicateSubmissionError):
                ledger.assert_may_submit(self.record(), payload)
            stored = ledger.entries()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["payload"], payload)

    def test_unknown_outcome_blocks_even_an_edited_payload(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            ledger.append(
                record=self.record(), payload={"assignTitle": "First"},
                status="unknown", error="connection dropped")
            with self.assertRaises(DuplicateSubmissionError):
                ledger.assert_may_submit(
                    self.record(), {"assignTitle": "Edited after timeout"})

    def test_rejected_request_can_be_corrected_and_retried(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            ledger.append(
                record=self.record(), payload={"assignTitle": "Bad"},
                status="rejected", error="validation")
            ledger.assert_may_submit(
                self.record(), {"assignTitle": "Corrected"})


if __name__ == "__main__":
    unittest.main()
