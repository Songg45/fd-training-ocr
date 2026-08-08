"""Provider-neutral, local-first handwriting recognition.

This module deliberately stops at transcription.  It does not normalize or validate
recognized values; those operations belong to Checkpoint 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import base64
import json
from typing import Callable, Mapping, Protocol, Sequence
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

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


class RecognitionProvider(Protocol):
    name: str
    model: str

    def recognize(self, request: RecognitionRequest) -> RecognitionResult: ...


_PROMPTS = {
    "date": "Transcribe only the handwritten date. Preserve its written format.",
    "time": "Transcribe only the handwritten time. Preserve its written format.",
    "hours": "Transcribe only the handwritten total-hours value.",
    "unit_id": "Transcribe only the handwritten fire department unit ID.",
    "print_name": "Transcribe only the handwritten printed attendee name.",
    "name": "Transcribe only the handwritten person's name.",
    "short_text": "Transcribe only the handwritten text in this field.",
    "description": "Transcribe only the handwritten training description.",
}


def field_type(region: Region) -> str:
    if region.name == "date": return "date"
    if region.name in {"start_time", "end_time"}: return "time"
    if region.name == "total_hours": return "hours"
    if region.name == "instructor": return "name"
    if region.name == "description": return "description"
    if region.name.endswith(".unit_id"): return "unit_id"
    if region.name.endswith(".print_name"): return "print_name"
    return "short_text"


def make_request(page: Image.Image, region: Region,
                 master: Image.Image | None = None) -> RecognitionRequest:
    """Crop one eligible field and construct its field-specific request."""
    if region.kind == "signature" or region.name.endswith(".signature"):
        raise RecognitionError("signature regions are never cropped or recognized")
    if region.kind not in {"text", "attendee_cell"}:
        raise RecognitionError(f"region {region.name!r} is not a handwriting field")
    box = region.pixel_box(*page.size)
    crop = page.crop(box).convert("L")
    if master is not None:
        if master.size != page.size:
            raise RecognitionError("master and completed page sizes differ")
        crop = suppress_printed_rules(master.crop(box).convert("L"), crop)
    kind = field_type(region)
    prompt = (_PROMPTS[kind] + " Return one JSON object with exactly these keys: "
              '"value" (string or null), "confidence" (0 to 1), and '
              '"alternatives" (array of strings). Do not infer missing text.')
    return RecognitionRequest(region.name, kind, prompt, crop, box)


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
                             request.source_region)


class MockRecognitionProvider:
    """Deterministic offline provider keyed by field name."""
    name = "mock"
    model = "deterministic-fixture-v1"

    def __init__(self, responses: Mapping[str, Mapping[str, object]] | None = None):
        self.responses = responses or {}
        self.requests: list[RecognitionRequest] = []

    def recognize(self, request: RecognitionRequest) -> RecognitionResult:
        self.requests.append(request)
        response = self.responses.get(request.field_name,
                                      {"value": None, "confidence": 0.0, "alternatives": []})
        raw = json.dumps(response, sort_keys=True, separators=(",", ":"))
        return parse_response(raw, request, self.name, self.model)


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
        buffer = BytesIO()
        request.image.save(buffer, format="PNG")
        body = json.dumps({"model": self.model, "stream": False, "format": "json",
                           "messages": [{"role": "user", "content": request.prompt,
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
        return parse_response(raw, request, self.name, self.model)


def recognize_fields(page: Image.Image, master: Image.Image,
                     template: TemplateDefinition, provider: RecognitionProvider,
                     populated_rows: Sequence[int] = ()) -> tuple[RecognitionResult, ...]:
    """Recognize text fields and the two non-signature cells of populated rows."""
    populated = set(populated_rows)
    results = []
    for region in template.regions:
        if region.kind in template.excluded_region_kinds or region.kind == "signature" or region.name.endswith(".signature"):
            continue
        if region.kind == "attendee_cell" and int(region.metadata["row"]) not in populated:
            continue
        if region.kind not in {"text", "attendee_cell"}:
            continue
        results.append(provider.recognize(make_request(page, region, master)))
    return tuple(results)
