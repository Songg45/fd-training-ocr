"""Provider-neutral, local-first handwriting recognition.

This module deliberately stops at transcription.  It does not normalize or validate
recognized values; those operations belong to Checkpoint 6.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
import base64
import json
import re
from typing import Callable, Mapping, Protocol, Sequence
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

from .table_extraction import suppress_printed_rules
from .template import Region, TemplateDefinition


class RecognitionError(RuntimeError):
    """A recognition request or response was unsafe or invalid."""


@dataclass(frozen=True)
class RecognitionRequest:
    field_name: str
    field_type: str
    prompt: str
    image: Image.Image
    source_region: tuple[int, int, int, int]
    variant: str = "raw"
    attempt: int = 1


@dataclass(frozen=True)
class RecognitionResult:
    field_name: str
    value: str | None
    normalized_as_returned: str | None
    confidence: float
    alternatives: tuple[str, ...]
    raw_output: str
    provider: str
    model: str
    source_region: tuple[int, int, int, int]
    variant: str = "raw"
    attempts: tuple[dict[str, object], ...] = ()


class RecognitionProvider(Protocol):
    name: str
    model: str

    def recognize(self, request: RecognitionRequest) -> RecognitionResult: ...

    def verify_context(self, request: "ContextVerificationRequest") -> "ContextVerificationResult": ...


@dataclass(frozen=True)
class ContextVerificationRequest:
    verification_id: str
    prompt: str
    image: Image.Image
    source_region: tuple[int, int, int, int]
    schema: str
    attempt: int = 1


@dataclass(frozen=True)
class ContextVerificationResult:
    verification_id: str
    values: Mapping[str, str | None]
    alternatives: Mapping[str, tuple[str, ...]]
    internally_consistent: bool | None
    handwriting_supports_candidate: bool | None
    raw_output: str
    provider: str
    model: str
    source_region: tuple[int, int, int, int]
    attempt: int = 1


_PROMPTS = {
    "date": "Transcribe only the handwritten date. Expected format: MM/DD/YY (or MM/DD/YYYY). Do not read the printed Date label.",
    "time": "Transcribe only the handwritten time. Expected format: HH:MM using the 24-hour clock. Do not read Start, To, or other printed labels.",
    "hours": "Transcribe only the handwritten total-hours value. Return a numeric value only, without words or units.",
    "unit_id": "Transcribe only the handwritten fire department unit ID. It is an alphanumeric roster identifier; preserve every digit and any leading zero.",
    "print_name": "Transcribe only the handwritten printed attendee name. Preserve spelling and suffixes; do not infer a roster match.",
    "name": "Transcribe only the handwritten person's name. Preserve spelling; do not infer a roster match.",
    "location": "Transcribe only the handwritten location value. Do not read the printed Location label.",
    "short_text": "Transcribe only the handwritten text in this field.",
    "description": "Transcribe only the handwritten training description.",
}


def field_type(region: Region) -> str:
    if region.name == "date": return "date"
    if region.name in {"start_time", "end_time"}: return "time"
    if region.name == "total_hours": return "hours"
    if region.name == "instructor": return "name"
    if region.name == "location": return "location"
    if region.name == "description": return "description"
    if region.name.endswith(".unit_id"): return "unit_id"
    if region.name.endswith(".print_name"): return "print_name"
    return "short_text"


def _expanded_box(box: tuple[int, int, int, int], size: tuple[int, int], pixels: int
                  ) -> tuple[int, int, int, int]:
    left, top, right, bottom = box; width, height = size
    return (max(0, left - pixels), max(0, top - pixels),
            min(width, right + pixels), min(height, bottom + pixels))


def _padded(image: Image.Image, pixels: int) -> Image.Image:
    if pixels <= 0: return image
    canvas = Image.new("L", (image.width + 2 * pixels, image.height + 2 * pixels), 255)
    canvas.paste(image, (pixels, pixels))
    return canvas


def _suppression_preserves_ink(master: Image.Image, raw: Image.Image,
                               suppressed: Image.Image, minimum: float = .82) -> bool:
    """Require rule suppression to retain most ink newly present versus the master."""
    from .checkbox_detection import difference_mask
    expected = difference_mask(master, raw)
    expected_count = int(expected.sum())
    if expected_count == 0: return False
    retained_mask = np.asarray(suppressed.convert("L")) < 190
    return float((retained_mask & expected).sum()) / expected_count >= minimum


def make_request(page: Image.Image, region: Region,
                 master: Image.Image | None = None, *, padding: int = 12,
                 expand: int = 0, force_variant: str | None = None,
                 attempt: int = 1, stronger: bool = False,
                 independent_second: bool = False) -> RecognitionRequest:
    """Crop one eligible field and construct its field-specific request."""
    if region.kind == "signature" or region.name.endswith(".signature"):
        raise RecognitionError("signature regions are never cropped or recognized")
    if region.kind not in {"text", "attendee_cell"}:
        raise RecognitionError(f"region {region.name!r} is not a handwriting field")
    box = _expanded_box(region.pixel_box(*page.size), page.size, expand)
    raw = page.crop(box).convert("L")
    crop, variant = raw, "raw"
    if master is not None:
        if master.size != page.size:
            raise RecognitionError("master and completed page sizes differ")
        master_crop = master.crop(box).convert("L")
        suppressed = suppress_printed_rules(master_crop, raw)
        if force_variant == "suppressed" or (force_variant is None and
                _suppression_preserves_ink(master_crop, raw, suppressed)):
            crop, variant = suppressed, "suppressed"
    if force_variant == "raw": crop, variant = raw, "raw"
    crop = _padded(crop, padding)
    kind = field_type(region)
    reinforcement = " Treat any output outside the expected format as invalid; inspect each character again." if stronger else ""
    instruction = _PROMPTS[kind]
    if independent_second:
        instruction = ("Independently inspect the pen strokes in this image and report only the handwritten "
                       f"{kind.replace('_', ' ')} value. Work character-by-character from the image; do not infer "
                       "from any prior transcription. " + instruction.split(". ", 1)[-1])
    prompt = (instruction + reinforcement + " Return one JSON object with exactly these keys: "
              '"value" (string or null), "confidence" (0 to 1), and '
              '"alternatives" (array of strings). Do not infer missing text.')
    return RecognitionRequest(region.name, kind, prompt, crop, box, variant, attempt)


def parse_response(raw: str, request: RecognitionRequest, provider: str,
                   model: str) -> RecognitionResult:
    """Strictly parse the provider-independent structured response."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecognitionError(f"recognizer returned malformed JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or set(data) != {"value", "confidence", "alternatives"}:
        raise RecognitionError("response must contain exactly value, confidence, and alternatives")
    value, confidence, alternatives = data["value"], data["confidence"], data["alternatives"]
    if value is not None and not isinstance(value, str):
        raise RecognitionError("response value must be a string or null")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise RecognitionError("response confidence must be a number from 0 to 1")
    if not isinstance(alternatives, list) or not all(isinstance(item, str) for item in alternatives):
        raise RecognitionError("response alternatives must be an array of strings")
    return RecognitionResult(request.field_name, value, value, float(confidence),
                             tuple(alternatives), raw, provider, model,
                             request.source_region, request.variant)


def _acceptable(result: RecognitionResult) -> bool:
    if result.value is None or result.confidence < .85: return False
    text = result.value.strip()
    patterns = {
        "date": r"\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})",
        "time": r"(?:[01]?\d|2[0-3]):[0-5]\d",
        "hours": r"\d+(?:\.\d+)?",
        "unit_id": r"[A-Za-z0-9-]+",
    }
    return bool(re.fullmatch(patterns.get(result.field_name if result.field_name in patterns else
        result.field_name.rsplit(".", 1)[-1], r".+"), text))


def _attempt_record(result: RecognitionResult, request: RecognitionRequest) -> dict[str, object]:
    return {"attempt": request.attempt, "variant": request.variant,
            "stage": request.attempt, "prompt": request.prompt,
            "provider": result.provider, "model": result.model,
            "source_region": list(request.source_region), "value": result.value,
            "confidence": result.confidence, "alternatives": list(result.alternatives),
            "raw_output": result.raw_output}


class MockRecognitionProvider:
    """Deterministic offline provider keyed by field name."""
    name = "mock"
    model = "deterministic-fixture-v1"

    def __init__(self, responses: Mapping[str, Mapping[str, object]] | None = None,
                 context_responses: Mapping[str, Mapping[str, object]] | None = None):
        self.responses = responses or {}
        self.context_responses = context_responses or {}
        self.requests: list[RecognitionRequest] = []
        self.context_requests: list[ContextVerificationRequest] = []

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        self.requests.append(request)
        response = self.responses.get(request.field_name,
                                      {"value": None, "confidence": 0.0, "alternatives": []})
        raw = json.dumps(response, sort_keys=True, separators=(",", ":"))
        return parse_response(raw, request, self.name, self.model)

    def verify_context(self, request: ContextVerificationRequest) -> ContextVerificationResult:
        self.context_requests.append(request)
        defaults = {
            "time_group": {"start_time": None, "end_time": None, "total_hours": None,
                           "internally_consistent": False,
                           "alternatives": {"start_time": [], "end_time": [], "total_hours": []}},
            "instructor": {"instructor": None, "handwriting_supports_candidate": False,
                           "alternatives": {"instructor": []}},
            "attendee_row": {"unit_id": None, "print_name": None,
                             "handwriting_supports_candidate": False,
                             "alternatives": {"unit_id": [], "print_name": []}},
            "field": {"value": None, "alternatives": {"value": []}},
        }
        raw = json.dumps(self.context_responses.get(request.verification_id, defaults[request.schema]),
                         sort_keys=True, separators=(",", ":"))
        return parse_context_response(raw, request, self.name, self.model)


Transport = Callable[[str, bytes, float], bytes]


def _http_post(url: str, body: bytes, timeout: float) -> bytes:
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return response.read()


class OllamaVisionProvider:
    """Optional Ollama vision provider using the local HTTP API."""
    name = "ollama"

    def __init__(self, model: str = "qwen2.5vl:7b", endpoint: str = "http://127.0.0.1:11434",
                 timeout_seconds: float = 90.0, transport: Transport = _http_post):
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Ollama endpoint must be local to preserve the privacy boundary")
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        raw = self._chat(request.prompt, request.image)
        return parse_response(raw, request, self.name, self.model)

    def _chat(self, prompt: str, image: Image.Image) -> str:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        body = json.dumps({"model": self.model, "stream": False, "format": "json",
                           "messages": [{"role": "user", "content": prompt,
                                         "images": [base64.b64encode(buffer.getvalue()).decode("ascii")]}],
                           "options": {"temperature": 0}}).encode("utf-8")
        try:
            envelope = json.loads(self._transport(f"{self.endpoint}/api/chat", body,
                                                  self.timeout_seconds))
            raw = envelope["message"]["content"]
        except (URLError, OSError) as exc:
            raise RecognitionError("Ollama is unavailable. Install Ollama, start it, and pull the configured vision model.") from exc
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RecognitionError("Ollama returned an invalid chat envelope") from exc
        if not isinstance(raw, str):
            raise RecognitionError("Ollama message content was not text")
        return raw

    def verify_context(self, request: ContextVerificationRequest) -> ContextVerificationResult:
        return parse_context_response(self._chat(request.prompt, request.image), request,
                                      self.name, self.model)


def parse_context_response(raw: str, request: ContextVerificationRequest,
                           provider: str, model: str) -> ContextVerificationResult:
    """Parse one of the deliberately small, strict Pass-2 schemas."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecognitionError(f"context verifier returned malformed JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise RecognitionError("context verifier response must be an object")
    schemas = {
        "time_group": ({"start_time", "end_time", "total_hours", "internally_consistent", "alternatives"},
                       ("start_time", "end_time", "total_hours")),
        "instructor": ({"instructor", "handwriting_supports_candidate", "alternatives"}, ("instructor",)),
        "attendee_row": ({"unit_id", "print_name", "handwriting_supports_candidate", "alternatives"},
                         ("unit_id", "print_name")),
        "field": ({"value", "alternatives"}, ("value",)),
    }
    if request.schema not in schemas:
        raise RecognitionError("unknown context verification schema")
    keys, value_keys = schemas[request.schema]
    if set(data) != keys:
        raise RecognitionError(f"{request.schema} response has unexpected keys")
    values = {key: data[key] for key in value_keys}
    if not all(value is None or isinstance(value, str) for value in values.values()):
        raise RecognitionError("context values must be strings or null")
    alternatives = data["alternatives"]
    if not isinstance(alternatives, dict) or set(alternatives) != set(value_keys) or not all(
            isinstance(items, list) and all(isinstance(item, str) for item in items)
            for items in alternatives.values()):
        raise RecognitionError("context alternatives must map every value field to a string array")
    consistent = data.get("internally_consistent")
    supports = data.get("handwriting_supports_candidate")
    if request.schema == "time_group" and not isinstance(consistent, bool):
        raise RecognitionError("internally_consistent must be boolean")
    if request.schema in {"instructor", "attendee_row"} and not isinstance(supports, bool):
        raise RecognitionError("handwriting_supports_candidate must be boolean")
    return ContextVerificationResult(request.verification_id, values,
        {key: tuple(items) for key, items in alternatives.items()}, consistent, supports,
        raw, provider, model, request.source_region, request.attempt)


def recognize_fields(page: Image.Image, master: Image.Image,
                     template: TemplateDefinition, provider: RecognitionProvider,
                     populated_rows: Sequence[int] = (), *, crop_padding: int = 12,
                     max_attempts: int = 3) -> tuple[RecognitionResult, ...]:
    """Run two independent reads of every eligible non-signature handwriting field."""
    populated = set(populated_rows)
    results = []
    for region in template.regions:
        if region.kind in template.excluded_region_kinds or region.kind == "signature" or region.name.endswith(".signature"):
            continue
        if region.kind == "attendee_cell" and int(region.metadata["row"]) not in populated:
            continue
        if region.kind not in {"text", "attendee_cell"}:
            continue
        attempts: list[dict[str, object]] = []
        result = None
        # Stage 2 is always independent: raw, wider, differently worded, and receives no Stage-1 value.
        specifications = ((None, crop_padding, 0, False, False),
                          ("raw", crop_padding + 8, 10, True, True))
        for number, (variant, padding, expand, stronger, independent) in enumerate(specifications, 1):
            request = make_request(page, region, master, padding=padding, expand=expand,
                                   force_variant=variant, attempt=number, stronger=stronger,
                                   independent_second=independent)
            result = provider.recognize(request)
            attempts.append(_attempt_record(result, request))
        assert result is not None
        # Stage 1 remains the raw machine transcription; later stages select without overwriting it.
        first = attempts[0]
        results.append(replace(result, value=first["value"], normalized_as_returned=first["value"],
                               confidence=float(first["confidence"]),
                               alternatives=tuple(first["alternatives"]),
                               raw_output=str(first["raw_output"]), variant=str(first["variant"]),
                               source_region=tuple(first["source_region"]),
                               attempts=tuple(attempts)))
    return tuple(results)
