"""Triggered contextual verification with immutable first-pass provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from PIL import Image

from .normalization import normalize_hours, normalize_time
from .recognition import (ContextVerificationRequest, ContextVerificationResult,
                          RecognitionError, RecognitionProvider, RecognitionResult)
from .template import Region, TemplateDefinition
from .validation import FieldAssessment, Roster, ValidationReport


@dataclass(frozen=True)
class FieldResolution:
    field_name: str
    first_pass: str | None
    second_pass: str | None
    roster_suggestion: str | None
    resolved_value: str | None
    resolution_reason: str | None
    review_required: bool
    attempts: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class SecondPassReport:
    resolutions: Mapping[str, FieldResolution]
    call_count: int


def _union_box(regions: Sequence[Region], size: tuple[int, int], padding: int = 24,
               right_limit: int | None = None) -> tuple[int, int, int, int]:
    boxes = [region.pixel_box(*size) for region in regions]
    left = max(0, min(x[0] for x in boxes) - padding)
    top = max(0, min(x[1] for x in boxes) - padding)
    right = min(size[0], max(x[2] for x in boxes) + padding)
    bottom = min(size[1], max(x[3] for x in boxes) + padding)
    if right_limit is not None: right = min(right, right_limit)
    if right <= left or bottom <= top: raise ValueError("invalid contextual crop")
    return left, top, right, bottom


def _request(page: Image.Image, verification_id: str, schema: str, regions: Sequence[Region],
             prompt: str, *, right_limit: int | None = None,
             padding: int = 24) -> ContextVerificationRequest:
    if any(r.kind == "signature" or r.name.endswith(".signature") for r in regions):
        raise ValueError("signature regions are forbidden in contextual verification")
    box = _union_box(regions, page.size, padding=padding, right_limit=right_limit)
    crop = page.crop(box).convert("L")
    return ContextVerificationRequest(verification_id, prompt, crop, box, schema)


def _attempt(result: ContextVerificationResult) -> dict[str, object]:
    return {"attempt": result.attempt, "provider": result.provider, "model": result.model,
            "source_region": list(result.source_region), "values": dict(result.values),
            "alternatives": {k: list(v) for k, v in result.alternatives.items()},
            "internally_consistent": result.internally_consistent,
            "handwriting_supports_candidate": result.handwriting_supports_candidate,
            "raw_output": result.raw_output}


def _verify(provider: RecognitionProvider, request: ContextVerificationRequest
            ) -> tuple[ContextVerificationResult | None, tuple[dict[str, object], ...]]:
    """A malformed Pass-2 response is evidence for review, never a batch failure."""
    try:
        result = provider.verify_context(request)
        return result, (_attempt(result),)
    except RecognitionError as exc:
        return None, ({"attempt": request.attempt, "provider": provider.name,
                       "model": provider.model, "source_region": list(request.source_region),
                       "error": str(exc)},)


def _deterministically_consistent(values: Mapping[str, str | None]) -> bool:
    start, end = normalize_time(values.get("start_time")), normalize_time(values.get("end_time"))
    hours = normalize_hours(values.get("total_hours"))
    if not (start.valid and end.valid and hours.valid): return False
    a = datetime.strptime(start.normalized, "%H:%M")
    b = datetime.strptime(end.normalized, "%H:%M")
    if b < a: return False
    return abs((b - a).total_seconds() / 3600 - float(hours.normalized)) <= .05


def _candidate_for(field: str, assessment: FieldAssessment | None, roster: Roster | None) -> str | None:
    if assessment and assessment.suggested_canonical and not assessment.suggestion_ambiguous:
        return assessment.suggested_canonical
    if not roster or not assessment or not assessment.raw: return None
    if field == "instructor" or field.endswith(".print_name"):
        candidate, ambiguous, _ = roster.suggest_name(assessment.raw)
    elif field.endswith(".unit_id"):
        candidate, ambiguous, _ = roster.suggest_unit(assessment.raw)
    else: return None
    return candidate if candidate and not ambiguous else None


def _resolve(field: str, first: str | None, second: str | None, candidate: str | None,
             attempts: tuple[dict[str, object], ...], *, supports: bool = False,
             deterministic: bool = False) -> FieldResolution:
    clean_first = first.strip() if first else None
    clean_second = second.strip() if second else None
    if clean_first and clean_second and clean_first.casefold() == clean_second.casefold():
        return FieldResolution(field, first, second, candidate, clean_second,
                               "first pass and contextual pass agree", False, attempts)
    if clean_second and candidate and supports and clean_second.casefold() == candidate.casefold():
        return FieldResolution(field, first, second, candidate, candidate,
                               "contextual pass and unambiguous roster candidate agree", False, attempts)
    if clean_second and deterministic:
        return FieldResolution(field, first, second, candidate, clean_second,
                               "contextual pass and deterministic cross-field validation agree", False, attempts)
    return FieldResolution(field, first, second, candidate, None, None, True, attempts)


def verify_second_pass(page: Image.Image, template: TemplateDefinition,
                       provider: RecognitionProvider, first_pass: Sequence[RecognitionResult],
                       validation: ValidationReport, roster: Roster | None = None) -> SecondPassReport:
    """Verify only questionable fields using contextual, signature-free crops."""
    first = {item.field_name: item for item in first_pass}
    assessments = {item.field_name: item for item in validation.fields}
    resolutions: dict[str, FieldResolution] = {}
    calls = 0

    time_names = ("start_time", "end_time", "total_hours")
    time_trigger = any(assessments.get(name) and assessments[name].review_required for name in time_names)
    time_trigger = time_trigger or any("duration" in warning or "time" in warning for warning in validation.warnings)
    if time_trigger and all(name in first for name in time_names):
        regions = [template.region(name) for name in time_names]
        prompt = ("Read the handwritten Start time, To/end time, and written Total Hours from this labeled group. "
                  "Times must be HH:MM in 24-hour format and hours numeric. Assess whether the three written values "
                  "are mathematically consistent; do not change a transcription to force consistency. Return exactly "
                  '{"start_time":string|null,"end_time":string|null,"total_hours":string|null,'
                  '"internally_consistent":boolean,"alternatives":{"start_time":string[],"end_time":string[],'
                  '"total_hours":string[]}}.')
        result, attempt = _verify(provider, _request(page, "time_group", "time_group", regions,
                                                     prompt, padding=110))
        calls += 1
        values = result.values if result else {name: None for name in time_names}
        deterministic = bool(result and result.internally_consistent and _deterministically_consistent(values))
        for name in time_names:
            resolutions[name] = _resolve(name, first[name].value, values[name], None,
                                         attempt, deterministic=deterministic)

    instructor = assessments.get("instructor")
    instructor_candidate = _candidate_for("instructor", instructor, roster)
    if instructor and (instructor.review_required or instructor_candidate):
        candidates = [instructor_candidate] if instructor_candidate else []
        prompt = ("Transcribe the handwritten instructor using this labeled field. Candidate roster names are "
                  f"{candidates!r}. A candidate is only valid when the handwriting supports it. Return exactly "
                  '{"instructor":string|null,"handwriting_supports_candidate":boolean,'
                  '"alternatives":{"instructor":string[]}}.')
        result, attempt = _verify(provider, _request(page, "instructor", "instructor",
                                                     [template.region("instructor")], prompt,
                                                     padding=70))
        calls += 1
        resolutions["instructor"] = _resolve("instructor", first["instructor"].value,
            result.values["instructor"] if result else None, instructor_candidate, attempt,
            supports=bool(result and result.handwriting_supports_candidate))

    prefixes = sorted({name.rsplit(".", 1)[0] for name in first if name.startswith("attendee.")})
    signatures = [r.pixel_box(*page.size)[0] for r in template.regions if r.kind == "signature"]
    signature_left = min(signatures) if signatures else page.width
    for prefix in prefixes:
        unit_name, print_name = prefix + ".unit_id", prefix + ".print_name"
        if unit_name not in first or print_name not in first: continue
        unit_assessment, name_assessment = assessments.get(unit_name), assessments.get(print_name)
        unit_candidate = _candidate_for(unit_name, unit_assessment, roster)
        name_candidate = _candidate_for(print_name, name_assessment, roster)
        trigger = any(x and x.review_required for x in (unit_assessment, name_assessment)) or bool(unit_candidate or name_candidate)
        if not trigger: continue
        candidates = [{"unit_id": unit_candidate, "print_name": name_candidate}]
        candidates = [x for x in candidates if x["unit_id"] or x["print_name"]]
        prompt = ("Read only the unit ID and printed name from this attendee row; the signature column is absent. "
                  f"Relevant unambiguous roster candidates are {candidates!r}. Mark support true only when the "
                  "handwriting supports the candidate pair. Return exactly "
                  '{"unit_id":string|null,"print_name":string|null,"handwriting_supports_candidate":boolean,'
                  '"alternatives":{"unit_id":string[],"print_name":string[]}}.')
        result, attempt = _verify(provider, _request(page, prefix, "attendee_row",
            [template.region(unit_name), template.region(print_name)], prompt, right_limit=signature_left))
        calls += 1
        supports = bool(result and result.handwriting_supports_candidate)
        values = result.values if result else {"unit_id": None, "print_name": None}
        resolutions[unit_name] = _resolve(unit_name, first[unit_name].value, values["unit_id"],
                                          unit_candidate, attempt, supports=supports)
        resolutions[print_name] = _resolve(print_name, first[print_name].value, values["print_name"],
                                           name_candidate, attempt, supports=supports)
    return SecondPassReport(resolutions, calls)
