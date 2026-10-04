"""Single-attempt Fireworks API client and durable submission ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .fireworks import fireworks_payload_hash, parse_fireworks_request


FIREWORKS_API_BASE = "https://webtrainingapi.eprsys.com/api"
FIREWORKS_API_HOST = "webtrainingapi.eprsys.com"
DEFINITE_REJECTION_STATUSES = frozenset({400, 401, 403, 422})


class FireworksError(RuntimeError):
    """Base class for errors that never expose the bearer token."""

    def __init__(
            self, message: str, *, status_code: int | None = None,
            response_payload: Mapping[str, Any] | None = None,
            response_text: str | None = None,
            response_body_sha256: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_payload = (
            dict(response_payload) if isinstance(response_payload, Mapping) else None)
        self.response_text = response_text
        self.response_body_sha256 = response_body_sha256


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
    body_sha256: str


Transport = Callable[..., Any]


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse redirects so an Authorization header is never forwarded."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, msg, headers, fp)


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler())


def _validated_api_base(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url.strip())
        port = parsed.port
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("the Fireworks API base URL is invalid") from exc
    if (parsed.scheme != "https"
            or parsed.hostname != FIREWORKS_API_HOST
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/api"):
        raise ValueError(
            f"the Fireworks API base must be exactly {FIREWORKS_API_BASE}")
    return FIREWORKS_API_BASE


def _body_evidence(body: Any) -> tuple[str, str]:
    if isinstance(body, str):
        raw = body.encode("utf-8")
        text = body
    else:
        raw = bytes(body)
        text = raw.decode("utf-8", errors="replace")
    return text, hashlib.sha256(raw).hexdigest()


def _response_error_kwargs(response: FireworksApiResponse) -> dict[str, Any]:
    return {
        "status_code": response.status_code,
        "response_payload": response.payload,
        "response_text": response.text,
        "response_body_sha256": response.body_sha256,
    }


class FireworksClient:
    """Use one request per operation with no automatic POST retry."""

    def __init__(
            self, bearer_token: str, *,
            base_url: str = FIREWORKS_API_BASE,
            timeout: float = 20.0, transport: Transport | None = None):
        token = bearer_token.strip()
        if token.casefold().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            raise ValueError("a Fireworks bearer token is required")
        self._token: str | None = token
        self.base_url = _validated_api_base(base_url)
        self.timeout = timeout
        self._transport = transport or _NO_REDIRECT_OPENER.open
        self.connected = False

    @property
    def activity_url(self) -> str:
        return f"{self.base_url}/TrnCommon/addActivity"

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
        text, body_sha256 = _body_evidence(response.read())
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FireworksSubmissionUnknown(
                f"Fireworks returned HTTP {status} with an unreadable response",
                status_code=status, response_text=text,
                response_body_sha256=body_sha256) from exc
        if not isinstance(payload, Mapping):
            raise FireworksSubmissionUnknown(
                f"Fireworks returned HTTP {status} with an unexpected response",
                status_code=status, response_text=text,
                response_body_sha256=body_sha256)
        return FireworksApiResponse(status, dict(payload), text, body_sha256)

    @staticmethod
    def _http_error_details(exc: HTTPError) -> tuple[str, dict[str, Any]]:
        try:
            text, body_sha256 = _body_evidence(exc.read())
        except Exception:
            text, body_sha256 = "", hashlib.sha256(b"").hexdigest()
        try:
            decoded = json.loads(text)
            payload = dict(decoded) if isinstance(decoded, Mapping) else None
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = None
        message = f"Fireworks returned HTTP {exc.code}"
        if text.strip():
            message += f": {text.strip()[:500]}"
        return message, {
            "status_code": int(exc.code),
            "response_payload": payload,
            "response_text": text,
            "response_body_sha256": body_sha256,
        }

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
            message, details = self._http_error_details(exc)
            raise FireworksConnectionError(message, **details) from exc
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
        if (type(result.payload.get("rc")) is not int
                or result.payload.get("rc") != 0
                or not isinstance(result.payload.get("responseObj"), list)):
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
            separators=(",", ":"), allow_nan=False).encode("utf-8")
        request = Request(
            self.activity_url, data=body,
            headers=self._headers(json_body=True), method="POST")
        try:
            with self._transport(request, timeout=self.timeout) as response:
                result = self._decode_response(response)
        except HTTPError as exc:
            message, details = self._http_error_details(exc)
            if exc.code in DEFINITE_REJECTION_STATUSES:
                raise FireworksSubmissionRejected(message, **details) from exc
            raise FireworksSubmissionUnknown(message, **details) from exc
        except FireworksSubmissionUnknown:
            raise
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise FireworksSubmissionUnknown(
                "The POST outcome is unknown; verify Fireworks before attempting another submission") from exc
        if result.status_code < 200 or result.status_code >= 300:
            if result.status_code in DEFINITE_REJECTION_STATUSES:
                raise FireworksSubmissionRejected(
                    f"Fireworks rejected the request with HTTP {result.status_code}",
                    **_response_error_kwargs(result))
            raise FireworksSubmissionUnknown(
                f"Fireworks returned HTTP {result.status_code}; verify before retrying",
                **_response_error_kwargs(result))
        rc = result.payload.get("rc")
        if type(rc) is int and rc != 0:
            raise FireworksSubmissionRejected(
                str(result.payload.get("description") or "Fireworks rejected the activity"),
                **_response_error_kwargs(result))
        if type(rc) is not int or rc != 0:
            raise FireworksSubmissionUnknown(
                "Fireworks did not affirm that the activity was created; verify before retrying",
                **_response_error_kwargs(result))
        if activity_id_from_response(result.payload) is None:
            raise FireworksSubmissionUnknown(
                "Fireworks reported success without a positive activity ID; verify before retrying",
                **_response_error_kwargs(result))
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


def activity_fingerprint(payload: Mapping[str, Any]) -> str | None:
    """Identify the same reviewed activity even when a rescan changes prose."""
    required = ("startDt", "endDt", "assignCat", "location", "station", "staff")
    if any(key not in payload for key in required):
        return None
    staff = payload.get("staff")
    if not isinstance(staff, list):
        return None
    material = {
        "startDt": payload.get("startDt"),
        "endDt": payload.get("endDt"),
        "assignCat": payload.get("assignCat"),
        "location": payload.get("location"),
        "station": payload.get("station"),
        "staff": sorted(staff, key=lambda item: (str(type(item)), str(item))),
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
                item = parse_fireworks_request(line)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(
                    f"submission ledger line {line_number} is invalid JSON") from exc
            if item.get("status") not in {
                    "attempting", "submitted", "rejected", "unknown"}:
                raise ValueError(
                    f"submission ledger line {line_number} has an invalid status")
            result.append(dict(item))
        return tuple(result)

    def assert_may_submit(
            self, record: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        digest, page = _record_identity(record)
        payload_digest = fireworks_payload_hash(payload)
        fingerprint = activity_fingerprint(payload)
        pending: dict[str, Mapping[str, Any]] = {}
        permanently_locked: list[Mapping[str, Any]] = []
        for line_number, entry in enumerate(self.entries(), 1):
            status = entry.get("status")
            attempt_id = str(entry.get("attempt_id") or "").strip()
            if status == "attempting":
                if attempt_id:
                    pending[attempt_id] = entry
                else:
                    # A legacy or damaged reservation cannot safely be assumed complete.
                    pending[f"ledger-line-{line_number}"] = entry
            elif status == "rejected":
                if attempt_id:
                    pending.pop(attempt_id, None)
            elif status in {"submitted", "unknown"}:
                if attempt_id:
                    pending.pop(attempt_id, None)
                permanently_locked.append(entry)

        for entry in (*permanently_locked, *pending.values()):
            same_source = (entry.get("source_sha256") == digest
                           and entry.get("page") == page)
            same_payload = entry.get("payload_sha256") == payload_digest
            same_activity = (fingerprint is not None
                             and entry.get("activity_fingerprint") == fingerprint)
            if same_source or same_payload or same_activity:
                if same_payload:
                    prior = "the same payload"
                elif same_activity:
                    prior = "the same activity from another scan"
                else:
                    prior = "this PDF record"
                raise DuplicateSubmissionError(
                    f"The ledger already marks {prior} as {entry.get('status')}; "
                    "verify Fireworks before any resubmission")

    def reserve(
            self, *, record: Mapping[str, Any], payload: Mapping[str, Any],
            endpoint: str | None = None, attempt_id: str | None = None,
            recorded_at: str | None = None) -> Mapping[str, Any]:
        """Durably reserve exactly one attempt before any network call."""
        self.assert_may_submit(record, payload)
        return self.append(
            record=record, payload=payload, status="attempting",
            attempt_id=attempt_id or str(uuid4()), endpoint=endpoint,
            recorded_at=recorded_at)

    def append(
            self, *, record: Mapping[str, Any], payload: Mapping[str, Any],
            status: str, response: Mapping[str, Any] | None = None,
            error: str | None = None,
            recorded_at: str | None = None, attempt_id: str | None = None,
            endpoint: str | None = None, response_status: int | None = None,
            response_text: str | None = None,
            response_body_sha256: str | None = None) -> Mapping[str, Any]:
        if status not in {"attempting", "submitted", "rejected", "unknown"}:
            raise ValueError("invalid Fireworks submission status")
        if status == "attempting" and not str(attempt_id or "").strip():
            raise ValueError("attempting ledger entries require an attempt ID")
        digest, page = _record_identity(record)
        response_payload = dict(response) if isinstance(response, Mapping) else None
        if response_text is not None and response_body_sha256 is None:
            response_body_sha256 = hashlib.sha256(
                response_text.encode("utf-8")).hexdigest()
        entry: dict[str, Any] = {
            "schema_version": 2,
            "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
            "status": status,
            "attempt_id": attempt_id,
            "endpoint": endpoint,
            "source_file": record.get("source_file"),
            "source_sha256": digest,
            "page": page,
            "payload_sha256": fireworks_payload_hash(payload),
            "activity_fingerprint": activity_fingerprint(payload),
            "payload": dict(payload),
            "activity_id": (
                activity_id_from_response(response_payload)
                if response_payload is not None else None),
            "response_status": response_status,
            "response": response_payload,
            "response_text": response_text,
            "response_body_sha256": response_body_sha256,
            "error": error,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(
                entry, ensure_ascii=False, separators=(",", ":"),
                allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return entry
