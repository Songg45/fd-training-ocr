import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request

from fd_training_ocr.fireworks_client import (
    activity_id_from_response,
    DuplicateSubmissionError, FireworksClient, FireworksConnectionError,
    FireworksSubmissionRejected, FireworksSubmissionUnknown, SubmissionLedger,
    SubmissionCoordinator, _NoRedirectHandler)


class FakeResponse:
    def __init__(self, payload, status=200, *, raw_body=None):
        self.status = status
        self.body = (raw_body if raw_body is not None
                     else json.dumps(payload).encode("utf-8"))

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
        api_payload = {"responseObj": {"moneln": 12345}, "rc": 0}
        api_body = json.dumps(api_payload).encode("utf-8")
        transport = RecordingTransport(FakeResponse(api_payload))
        client = self.connected_client(transport)
        payload = {"assignTitle": "Test", "staff": [20], "station": 54}
        response = client.post_activity(payload)
        self.assertEqual(response.payload["responseObj"]["moneln"], 12345)
        self.assertEqual(len(transport.calls), 1)
        request, _timeout = transport.calls[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/TrnCommon/addActivity"))
        self.assertEqual(json.loads(request.data.decode("utf-8")), payload)
        self.assertEqual(
            response.body_sha256,
            hashlib.sha256(api_body).hexdigest())

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
        with self.assertRaises(FireworksSubmissionRejected) as raised:
            client.post_activity({"assignTitle": "Rejected"})
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.response_payload,
            {"description": "invalid activity"})
        self.assertEqual(
            raised.exception.response_body_sha256,
            hashlib.sha256(b'{"description":"invalid activity"}').hexdigest())
        self.assertEqual(len(transport.calls), 1)

    def test_ambiguous_http_408_is_unknown(self):
        error = HTTPError(
            "https://webtrainingapi.eprsys.com/api/TrnCommon/addActivity",
            408, "Request Timeout", {}, None)
        error.read = lambda: b'{"description":"timeout"}'
        client = self.connected_client(RecordingTransport(error))
        with self.assertRaises(FireworksSubmissionUnknown) as raised:
            client.post_activity({"assignTitle": "Uncertain"})
        self.assertEqual(raised.exception.status_code, 408)

    def test_http_422_is_a_definite_rejection(self):
        error = HTTPError(
            "https://webtrainingapi.eprsys.com/api/TrnCommon/addActivity",
            422, "Unprocessable Entity", {}, None)
        error.read = lambda: b'{"description":"invalid activity"}'
        client = self.connected_client(RecordingTransport(error))
        with self.assertRaises(FireworksSubmissionRejected):
            client.post_activity({"assignTitle": "Rejected"})

    def test_success_requires_affirmative_rc_and_activity_id(self):
        for response in (
                {},
                {"rc": 0},
                {"rc": True, "responseObj": {"moneln": 123}},
                {"rc": "0", "responseObj": {"moneln": 123}}):
            with self.subTest(response=response):
                client = self.connected_client(
                    RecordingTransport(FakeResponse(response)))
                with self.assertRaises(FireworksSubmissionUnknown):
                    client.post_activity({"assignTitle": "Ambiguous"})

    def test_explicit_nonzero_rc_is_rejected(self):
        client = self.connected_client(RecordingTransport(FakeResponse({
            "rc": 1, "description": "validation failed"})))
        with self.assertRaises(FireworksSubmissionRejected):
            client.post_activity({"assignTitle": "Rejected"})

    def test_observed_success_description_overrides_nonzero_rc_without_activity_id(self):
        api_payload = {
            "rc": 1,
            "description": "  New   activity added!  ",
            "responseObj": None,
        }
        client = self.connected_client(
            RecordingTransport(FakeResponse(api_payload)))

        response = client.post_activity({"assignTitle": "Accepted"})

        self.assertEqual(response.payload, api_payload)

    def test_observed_assignment_success_returns_top_level_activity_id(self):
        api_payload = {
            "id": 94,
            "wishObject": {},
            "rc": 1,
            "description": "new assignment added",
        }
        client = self.connected_client(
            RecordingTransport(FakeResponse(api_payload)))

        response = client.post_activity({"assignTitle": "Accepted"})

        self.assertEqual(response.payload, api_payload)
        self.assertEqual(activity_id_from_response(response.payload), 94)

    def test_api_base_is_pinned_to_the_production_https_origin(self):
        invalid = (
            "http://webtrainingapi.eprsys.com/api",
            "https://attacker.invalid/api",
            "https://webtrainingapi.eprsys.com.evil.invalid/api",
            "https://webtrainingapi.eprsys.com/other",
            "https://user@webtrainingapi.eprsys.com/api",
        )
        for base_url in invalid:
            with self.subTest(base_url=base_url), self.assertRaises(ValueError):
                FireworksClient("secret-token", base_url=base_url)

    def test_redirect_handler_refuses_to_forward_authorization(self):
        handler = _NoRedirectHandler()
        request = Request(
            "https://webtrainingapi.eprsys.com/api/trnCommon/getTable",
            headers={"Authorization": "Bearer secret-token"})
        with self.assertRaises(HTTPError):
            handler.redirect_request(
                request, None, 302, "Found", {},
                "https://attacker.invalid/capture")

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

    def test_attempting_reservation_is_fsynced_and_locks_until_terminal_result(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            payload = self.activity_payload()
            reservation = ledger.reserve(
                record=self.record(), payload=payload,
                endpoint="https://webtrainingapi.eprsys.com/api/TrnCommon/addActivity",
                recorded_at="2026-09-19T19:59:59+00:00")
            self.assertEqual(reservation["status"], "attempting")
            self.assertTrue(reservation["attempt_id"])
            self.assertEqual(ledger.entries()[0]["attempt_id"], reservation["attempt_id"])
            with self.assertRaises(DuplicateSubmissionError):
                ledger.assert_may_submit(self.record(), payload)

            ledger.append(
                record=self.record(), payload=payload, status="rejected",
                attempt_id=reservation["attempt_id"], response_status=422,
                response_text='{"description":"invalid"}',
                response_body_sha256="b" * 64, error="invalid")
            ledger.assert_may_submit(self.record(), payload)

    def test_unknown_and_submitted_terminal_results_remain_locked(self):
        for status in ("unknown", "submitted"):
            with self.subTest(status=status), TemporaryDirectory() as name:
                ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
                payload = self.activity_payload()
                reservation = ledger.reserve(record=self.record(), payload=payload)
                response = ({"rc": 0, "responseObj": {"moneln": 123}}
                            if status == "submitted" else None)
                ledger.append(
                    record=self.record(), payload=payload, status=status,
                    attempt_id=reservation["attempt_id"], response=response,
                    error="uncertain" if status == "unknown" else None)
                with self.assertRaises(DuplicateSubmissionError):
                    ledger.assert_may_submit(self.record(), payload)

    def test_same_payload_from_a_rescan_is_locked(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            payload = self.activity_payload()
            other_record = dict(self.record(), source_sha256="c" * 64)
            ledger.append(
                record=self.record(), payload=payload, status="submitted",
                response={"rc": 0, "responseObj": {"moneln": 123}})
            with self.assertRaises(DuplicateSubmissionError):
                ledger.assert_may_submit(other_record, payload)

    def test_same_activity_with_nonsemantic_payload_change_is_locked(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            payload = self.activity_payload()
            other_record = dict(self.record(), source_sha256="c" * 64)
            ledger.append(
                record=self.record(), payload=payload, status="submitted",
                response={"rc": 0, "responseObj": {"moneln": 123}})
            edited = dict(payload, assignInst="<p>Corrected spelling</p>")
            with self.assertRaises(DuplicateSubmissionError):
                ledger.assert_may_submit(other_record, edited)

    def test_receipt_retains_full_response_evidence(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            payload = self.activity_payload()
            entry = ledger.append(
                record=self.record(), payload=payload, status="rejected",
                attempt_id="attempt-1", response_status=422,
                response={"rc": 1, "description": "bad"},
                response_text='{"rc":1,"description":"bad"}',
                response_body_sha256="d" * 64,
                endpoint="https://webtrainingapi.eprsys.com/api/TrnCommon/addActivity",
                error="bad")
            self.assertEqual(entry["attempt_id"], "attempt-1")
            self.assertEqual(entry["response_status"], 422)
            self.assertEqual(entry["response_text"], '{"rc":1,"description":"bad"}')
            self.assertEqual(entry["response_body_sha256"], "d" * 64)
            self.assertTrue(entry["endpoint"].endswith("/TrnCommon/addActivity"))

    def test_ambiguous_or_corrupt_ledger_line_fails_closed(self):
        with TemporaryDirectory() as name:
            path = Path(name) / "submissions.jsonl"
            path.write_text(
                '{"status":"rejected","status":"submitted"}\n',
                encoding="utf-8")
            ledger = SubmissionLedger(path)
            with self.assertRaises(ValueError):
                ledger.assert_may_submit(self.record(), self.activity_payload())

    def test_coordinator_reconciles_stale_record_from_terminal_ledger_state(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            coordinator = SubmissionCoordinator(ledger)
            record = self.record()
            payload = self.activity_payload()
            reservation = coordinator.reserve(
                record=record, payload=payload,
                endpoint="https://webtrainingapi.eprsys.com/api/TrnCommon/addActivity")
            ledger.append(
                record=record, payload=payload, status="rejected",
                attempt_id=reservation["attempt_id"], response_status=422,
                error="invalid")
            self.assertEqual(record["fireworks_submission"]["status"], "attempting")

            changed, entry = coordinator.reconcile_record(record)
            self.assertTrue(changed)
            self.assertEqual(entry["status"], "rejected")
            self.assertEqual(record["fireworks_submission"]["status"], "rejected")
            ledger.assert_may_submit(record, payload)

    def test_coordinator_repairs_explicit_success_that_was_recorded_as_rejected(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            coordinator = SubmissionCoordinator(ledger)
            record = self.record()
            payload = self.activity_payload()
            success = {
                "rc": 1,
                "description": "new activity added",
                "responseObj": None,
            }
            ledger.append(
                record=record, payload=payload, status="rejected",
                attempt_id="attempt-1", response=success,
                response_status=200,
                response_text=json.dumps(success),
                error="new activity added")

            changed, entry = coordinator.reconcile_record(record)

            self.assertTrue(changed)
            self.assertEqual(entry["status"], "submitted")
            self.assertIsNone(entry["activity_id"])
            self.assertEqual(
                entry["reconciliation"]["decision"],
                "explicit_success_response_reclassified")
            with self.assertRaises(DuplicateSubmissionError):
                ledger.assert_may_submit(record, payload)

    def test_coordinator_repairs_observed_assignment_success_with_id(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            coordinator = SubmissionCoordinator(ledger)
            record = self.record()
            payload = self.activity_payload()
            success = {
                "id": 94,
                "wishObject": {},
                "rc": 1,
                "description": "new assignment added",
            }
            ledger.append(
                record=record, payload=payload, status="rejected",
                attempt_id="attempt-94", response=success,
                response_status=200,
                response_text=json.dumps(success),
                error="new assignment added")

            changed, entry = coordinator.reconcile_record(record)

            self.assertTrue(changed)
            self.assertEqual(entry["status"], "submitted")
            self.assertEqual(entry["activity_id"], 94)
            self.assertEqual(
                record["fireworks_submission"]["activity_id"], 94)
            with self.assertRaises(DuplicateSubmissionError):
                ledger.assert_may_submit(record, payload)

    def test_manual_not_submitted_reconciliation_releases_unknown_attempt(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            coordinator = SubmissionCoordinator(ledger)
            record = self.record()
            payload = self.activity_payload()
            reservation = coordinator.reserve(
                record=record, payload=payload,
                endpoint="https://webtrainingapi.eprsys.com/api/TrnCommon/addActivity")
            coordinator.finalize(
                record=record, payload=payload, status="unknown",
                attempt_id=reservation["attempt_id"],
                endpoint=reservation["endpoint"], error="connection dropped")

            entry = coordinator.manual_reconcile(
                record=record, created=False,
                recorded_at="2026-09-20T12:00:00+00:00")
            self.assertEqual(entry["status"], "reconciled_not_submitted")
            self.assertEqual(
                entry["reconciliation"]["decision"], "confirmed_not_submitted")
            self.assertEqual(
                record["fireworks_submission"]["status"],
                "reconciled_not_submitted")
            ledger.assert_may_submit(record, payload)

    def test_manual_submitted_reconciliation_remains_locked_with_activity_id(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            coordinator = SubmissionCoordinator(ledger)
            record = self.record()
            payload = self.activity_payload()
            coordinator.reserve(
                record=record, payload=payload,
                endpoint="https://webtrainingapi.eprsys.com/api/TrnCommon/addActivity")

            entry = coordinator.manual_reconcile(
                record=record, created=True, activity_id=9876)
            self.assertEqual(entry["status"], "submitted")
            self.assertEqual(entry["activity_id"], 9876)
            self.assertEqual(
                entry["reconciliation"]["decision"], "confirmed_submitted")
            with self.assertRaises(DuplicateSubmissionError):
                ledger.assert_may_submit(record, payload)

    def test_coordinator_reservation_exists_before_transport_is_called(self):
        with TemporaryDirectory() as name:
            ledger = SubmissionLedger(Path(name) / "submissions.jsonl")
            coordinator = SubmissionCoordinator(ledger)
            record = self.record()
            payload = self.activity_payload()
            observed = []

            def transport(request, *, timeout):
                entry = ledger.effective_entry(record, payload)
                observed.append((entry["status"], request.get_method(), timeout))
                return FakeResponse({"responseObj": {"moneln": 2468}, "rc": 0})

            client = FireworksClient("secret-token", transport=transport)
            client.connected = True
            reservation = coordinator.reserve(
                record=record, payload=payload, endpoint=client.activity_url)
            response = client.post_activity(payload)
            coordinator.finalize(
                record=record, payload=payload, status="submitted",
                attempt_id=reservation["attempt_id"],
                endpoint=client.activity_url, response=response.payload,
                response_status=response.status_code,
                response_text=response.text,
                response_body_sha256=response.body_sha256)

            self.assertEqual(observed, [("attempting", "POST", 20.0)])
            self.assertEqual(record["fireworks_submission"]["status"], "submitted")
            self.assertEqual(record["fireworks_submission"]["activity_id"], 2468)

    @staticmethod
    def activity_payload():
        return {
            "assignTitle": "Test",
            "startDt": "2026-09-19T18:00:00.000Z",
            "endDt": "2026-09-19T19:00:00.000Z",
            "assignCat": 101,
            "location": 201,
            "station": 54,
            "staff": [20, 26],
            "assignInst": "<p>Original</p>",
        }


if __name__ == "__main__":
    unittest.main()
