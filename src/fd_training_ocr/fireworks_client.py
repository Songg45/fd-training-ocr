"""Single-attempt Fireworks API client and durable submission ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .fireworks import fireworks_payload_hash


class FireworksError(RuntimeError):
    """Base class for errors that never expose the bearer token."""


class FireworksConnectionError(FireworksError):
    """The token could not be validated without creating an activity."""


class FireworksSubmissionRejected(FireworksError):
    """Fireworks definitively rejected the activity request."""


class FireworksSubmissionUnknown(FireworksError):
    """Fireworks may have accepted the request; callers must not retry."""


class DuplicateSubmissionError(FireworksError):
    """The local ledger already has a submitted or indeterminate record."""


@dataclass(frozen=True)
class FireworksApiResponse:
    status_code: int
    payload: Mapping[str, Any]
    text: str


Transport = Callable[..., Any]


class FireworksClient:
    """Use one request per operation with no automatic POST retry."""

    def __init__(
            self, bearer_token: str, *,
            base_url: str = "https://webtrainingapi.eprsys.com/api",
            timeout: float = 20.0, transport: Transport | None = None):
        token = bearer_token.strip()
        if token.casefold().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            raise ValueError("a Fireworks bearer token is required")
        self._token: str | None = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport or urlopen
        self.connected = False

    def clear_token(self) -> None:
        self._token = None
        self.connected = False

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        if self._token is None:
            raise FireworksConnectionError("the Fireworks token is no longer available")
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {self._token}",
            "Origin": "https://training.eprsys.com",
            "Referer": "https://training.eprsys.com/",
            "User-Agent": "FDTrainingOCR/0.1",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _decode_response(response: Any) -> FireworksApiResponse:
        status_value = getattr(response, "status", None)
        if status_value is None:
            status_value = response.getcode()
        status = int(status_value)
        body = response.read()
        if isinstance(body, str):
            text = body
        else:
            text = bytes(body).decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FireworksSubmissionUnknown(
                f"Fireworks returned HTTP {status} with an unreadable response") from exc
        if not isinstance(payload, Mapping):
            raise FireworksSubmissionUnknown(
                f"Fireworks returned HTTP {status} with an unexpected response")
        return FireworksApiResponse(status, dict(payload), text)

    @staticmethod
    def _http_error_message(exc: HTTPError) -> str:
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        return f"Fireworks returned HTTP {exc.code}" + (f": {body[:500]}" if body else "")

    def validate_connection(self) -> FireworksApiResponse:
        """Make one harmless lookup request and retain no returned credentials."""
        request = Request(
            f"{self.base_url}/trnCommon/getTable?tbl=location",
            headers=self._headers(), method="GET")
        try:
            with self._transport(request, timeout=self.timeout) as response:
                result = self._decode_response(response)
        except HTTPError as exc:
            self.connected = False
            raise FireworksConnectionError(self._http_error_message(exc)) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            self.connected = False
            raise FireworksConnectionError(
                "Unable to validate the Fireworks connection") from exc
        except FireworksSubmissionUnknown as exc:
            self.connected = False
            raise FireworksConnectionError(str(exc)) from exc
        if result.status_code < 200 or result.status_code >= 300:
            self.connected = False
            raise FireworksConnectionError(
                f"Fireworks returned HTTP {result.status_code} during validation")
        if result.payload.get("rc") != 0 or not isinstance(
                result.payload.get("responseObj"), list):
            self.connected = False
            raise FireworksConnectionError(
                "Fireworks did not return the expected read-only location lookup")
        self.connected = True
        return result

    def post_activity(self, payload: Mapping[str, Any]) -> FireworksApiResponse:
        """Send exactly one POST; an uncertain outcome is never retried here."""
        if not self.connected:
            raise FireworksConnectionError("connect to Fireworks before submitting")
        body = json.dumps(
            dict(payload), ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self.base_url}/TrnCommon/addActivity", data=body,
            headers=self._headers(json_body=True), method="POST")
        try:
            with self._transport(request, timeout=self.timeout) as response:
                result = self._decode_response(response)
        except HTTPError as exc:
            message = self._http_error_message(exc)
            if 400 <= exc.code < 500:
                raise FireworksSubmissionRejected(message) from exc
            raise FireworksSubmissionUnknown(message) from exc
        except FireworksSubmissionUnknown:
            raise
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise FireworksSubmissionUnknown(
                "The POST outcome is unknown; verify Fireworks before attempting another submission") from exc
        if result.status_code < 200 or result.status_code >= 300:
            if 400 <= result.status_code < 500:
                raise FireworksSubmissionRejected(
                    f"Fireworks rejected the request with HTTP {result.status_code}")
            raise FireworksSubmissionUnknown(
                f"Fireworks returned HTTP {result.status_code}; verify before retrying")
        if result.payload.get("rc") not in (None, 0):
            raise FireworksSubmissionRejected(
                str(result.payload.get("description") or "Fireworks rejected the activity"))
        return result


def activity_id_from_response(payload: Mapping[str, Any]) -> int | None:
    """Extract a likely created-activity identifier without mistaking staff arrays."""
    for key in ("moneln", "activityId", "activityID"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    response = payload.get("responseObj")
    if isinstance(response, Mapping):
        return activity_id_from_response(response)
    if isinstance(response, list) and len(response) == 1 and isinstance(response[0], Mapping):
        return activity_id_from_response(response[0])
    return None


def _record_identity(record: Mapping[str, Any]) -> tuple[str, int | None]:
    digest = str(record.get("source_sha256") or "").strip()
    if not digest:
        raise ValueError("source_sha256 is required for Fireworks duplicate protection")
    page_value = record.get("page")
    if page_value is None:
        return digest, None
    try:
        return digest, int(page_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("record page must be numeric") from exc


class SubmissionLedger:
    """Append-only external receipt log used to prevent accidental duplicate POSTs."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()

    def entries(self) -> tuple[Mapping[str, Any], ...]:
        if not self.path.is_file():
            return ()
        result: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"submission ledger line {line_number} is invalid JSON") from exc
            if not isinstance(item, Mapping):
                raise ValueError(
                    f"submission ledger line {line_number} must be an object")
            result.append(dict(item))
        return tuple(result)

    def assert_may_submit(
            self, record: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        digest, page = _record_identity(record)
        payload_digest = fireworks_payload_hash(payload)
        for entry in self.entries():
            if (entry.get("source_sha256") == digest
                    and entry.get("page") == page
                    and entry.get("status") in {"submitted", "unknown"}):
                prior = "the same payload" if entry.get("payload_sha256") == payload_digest else "this PDF record"
                raise DuplicateSubmissionError(
                    f"The ledger already marks {prior} as {entry.get('status')}; "
                    "verify Fireworks before any resubmission")

    def append(
            self, *, record: Mapping[str, Any], payload: Mapping[str, Any],
            status: str, response: Mapping[str, Any] | None = None,
            error: str | None = None,
            recorded_at: str | None = None) -> Mapping[str, Any]:
        if status not in {"submitted", "rejected", "unknown"}:
            raise ValueError("invalid Fireworks submission status")
        digest, page = _record_identity(record)
        response_payload = dict(response) if isinstance(response, Mapping) else None
        entry: dict[str, Any] = {
            "schema_version": 1,
            "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
            "status": status,
            "source_file": record.get("source_file"),
            "source_sha256": digest,
            "page": page,
            "payload_sha256": fireworks_payload_hash(payload),
            "payload": dict(payload),
            "activity_id": (
                activity_id_from_response(response_payload)
                if response_payload is not None else None),
            "response": response_payload,
            "error": error,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return entry
