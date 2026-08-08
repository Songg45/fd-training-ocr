"""Triggered contextual verification with immutable first-pass provenance."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    independent_pass: str | None
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
    attempts: list[dict[str, object]] = []
    current = request
    for number in (1, 2):
        try:
            result = provider.verify_context(current)
            item = _attempt(result); item["stage"] = 3; item["prompt"] = current.prompt
            attempts.append(item)
            return result, tuple(attempts)
        except RecognitionError as exc:
            attempts.append({"attempt": number, "stage": 3, "prompt": current.prompt,
                             "provider": provider.name, "model": provider.model,
                             "source_region": list(current.source_region), "error": str(exc)})
            current = replace(request, attempt=2, prompt=request.prompt +
                ' Schema repair: quote every value as a JSON string (including numbers), use null only for blank values, '
                'and make "alternatives" an object mapping every requested value key to an array of strings.')
    return None, tuple(attempts)


def _deterministically_consistent(values: Mapping[str, str | None]) -> bool:
    start, end = normalize_time(values.get("start_time")), normalize_time(values.get("end_time"))
    hours = normalize_hours(values.get("total_hours"))
    if not (start.valid and end.valid and hours.valid): return False
    a = datetime.strptime(start.normalized, "%H:%M")
    b = datetime.strptime(end.normalized, "%H:%M")
    if b < a: return False
    return abs((b - a).total_seconds() / 3600 - float(hours.normalized)) <= .05


def _time_pair_valid(values: Mapping[str, str | None]) -> bool:
    start, end = normalize_time(values.get("start_time")), normalize_time(values.get("end_time"))
    if not (start.valid and end.valid): return False
    return datetime.strptime(end.normalized, "%H:%M") >= datetime.strptime(start.normalized, "%H:%M")


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


def _comparison(field: str, value: str | None) -> str | None:
    if field in {"start_time", "end_time"}:
        item = normalize_time(value); return item.normalized if item.valid else None
    if field == "total_hours":
        item = normalize_hours(value); return item.normalized if item.valid else None
    return value.strip().casefold() if value and value.strip() else None


def _stage_pair(result: RecognitionResult) -> tuple[str | None, str | None]:
    attempts = result.attempts
    return (attempts[0].get("value") if attempts else result.value,
            attempts[1].get("value") if len(attempts) > 1 else None)  # type: ignore[return-value]


def _resolve(field: str, first: str | None, independent: str | None,
             second: str | None, candidate: str | None,
             attempts: tuple[dict[str, object], ...], *, supports: bool = False,
             deterministic: bool = False) -> FieldResolution:
    clean_first = first.strip() if first else None
    clean_second = second.strip() if second else None
    if clean_first and clean_second and _comparison(field, clean_first) == _comparison(field, clean_second):
        return FieldResolution(field, first, independent, second, candidate, clean_second,
                               "first pass and contextual pass agree", False, attempts)
    clean_independent = independent.strip() if independent else None
    if clean_independent and clean_second and _comparison(field, clean_independent) == _comparison(field, clean_second):
        return FieldResolution(field, first, independent, second, candidate, clean_second,
                               "independent pass and contextual pass agree", False, attempts)
    if clean_second and candidate and supports and clean_second.casefold() == candidate.casefold():
        return FieldResolution(field, first, independent, second, candidate, candidate,
                               "contextual pass and unambiguous roster candidate agree", False, attempts)
    if clean_second and deterministic:
        return FieldResolution(field, first, independent, second, candidate, clean_second,
                               "contextual pass and deterministic cross-field validation agree", False, attempts)
    return FieldResolution(field, first, independent, second, candidate, None, None, True, attempts)


def _requires_stage3(field: str, result: RecognitionResult, assessment: FieldAssessment,
                     global_warnings: Sequence[str]) -> bool:
    first, independent = _stage_pair(result)
    attempts = result.attempts
    invalid_stage = (len(attempts) < 2 or any(
        item.get("value") is None or float(item.get("confidence", 0)) < .85 or item.get("alternatives")
        for item in attempts[:2]))
    disagree = _comparison(field, first) != _comparison(field, independent)
    roster_issue = bool(assessment.suggested_canonical or assessment.suggestion_ambiguous)
    cross = field in {"start_time", "end_time", "total_hours"} and bool(global_warnings)
    return invalid_stage or disagree or assessment.review_required or roster_issue or cross


def verify_second_pass(page: Image.Image, template: TemplateDefinition,
                       provider: RecognitionProvider, first_pass: Sequence[RecognitionResult],
                       validation: ValidationReport, roster: Roster | None = None) -> SecondPassReport:
    """Verify only questionable fields using contextual, signature-free crops."""
    first = {item.field_name: item for item in first_pass}
    assessments = {item.field_name: item for item in validation.fields}
    resolutions: dict[str, FieldResolution] = {}
    calls = 0

    # Provisionally accept independent Stage-1/Stage-2 agreement only when validation is clean.
    for name, item in first.items():
        assessment = assessments[name]
        stage1, stage2 = _stage_pair(item)
        if not _requires_stage3(name, item, assessment, validation.warnings):
            resolutions[name] = FieldResolution(name, stage1, stage2, None,
                assessment.suggested_canonical, stage1,
                "stages 1 and 2 agree after normalized deterministic validation", False, ())

    time_names = ("start_time", "end_time", "total_hours")
    time_trigger = any(name in first and _requires_stage3(name, first[name], assessments[name],
                                                         validation.warnings) for name in time_names)
    if time_trigger and all(name in first for name in time_names):
        regions = [template.region(name) for name in time_names]
        prompt = ("Read the handwritten Start time, To/end time, and written Total Hours from this labeled group. "
                  "Times must be HH:MM in 24-hour format and hours numeric. Assess whether the three written values "
                  f"are mathematically consistent; do not change a transcription to force consistency. Stage 1/2 evidence: "
                  f"{ {name: _stage_pair(first[name]) for name in time_names} }. Return the same shape as this exact "
                  'example, with scalar quoted values rather than nested objects: {"start_time":"16:00",'
                  '"end_time":"17:00","total_hours":"2","internally_consistent":false,'
                  '"alternatives":{"start_time":[],"end_time":[],"total_hours":[]}}.')
        result, attempt = _verify(provider, _request(page, "time_group", "time_group", regions,
                                                     prompt, padding=110))
        calls += len(attempt)
        values = result.values if result else {name: None for name in time_names}
        group_deterministic = bool(result and result.internally_consistent and
                                   _deterministically_consistent(values))
        pair_deterministic = bool(result and _time_pair_valid(values))
        for name in time_names:
            stage1, stage2 = _stage_pair(first[name])
            resolutions[name] = _resolve(name, stage1, stage2, values[name], None,
                attempt, deterministic=pair_deterministic if name != "total_hours" else group_deterministic)

    instructor = assessments.get("instructor")
    instructor_candidate = _candidate_for("instructor", instructor, roster)
    if instructor and "instructor" in first and _requires_stage3("instructor", first["instructor"],
                                                                 instructor, validation.warnings):
        candidates = [instructor_candidate] if instructor_candidate else []
        prompt = ("Transcribe the handwritten instructor using this labeled field. Candidate roster names are "
                  f"{candidates!r}. A candidate is only valid when the handwriting supports it. Return exactly "
                  '{"instructor":string|null,"handwriting_supports_candidate":boolean,'
                  '"alternatives":{"instructor":string[]}}.')
        result, attempt = _verify(provider, _request(page, "instructor", "instructor",
                                                     [template.region("instructor")], prompt,
                                                     padding=70))
        calls += len(attempt)
        stage1, stage2 = _stage_pair(first["instructor"])
        resolutions["instructor"] = _resolve("instructor", stage1, stage2,
            result.values["instructor"] if result else None, instructor_candidate, attempt,
            supports=bool(result and result.handwriting_supports_candidate))

    prefixes = sorted({name.rsplit(".", 1)[0] for name in first if name.startswith("attendee.")})
    signatures = [r.pixel_box(*page.size)[0] for r in template.regions if r.kind == "signature"]
    signature_left = min(signatures) if signatures else page.width
    for prefix in prefixes:
        unit_name, print_name = prefix + ".unit_id", prefix + ".print_name"
        if unit_name not in first or print_name not in first: continue
        unit_assessment, name_assessment = assessments.get(unit_name), assessments.get(print_name)
        unit_member = roster.member_for_unit(unit_assessment.raw) if roster and unit_assessment else None
        name_member = roster.member_for_name(name_assessment.raw) if roster and name_assessment else None
        matched_member = None
        matched_reason = None
        if unit_member and (name_member is None or name_member == unit_member):
            matched_member = unit_member
            matched_reason = "exact unique roster unit ID resolved attendee pair"
        elif name_member and unit_member is None and len(name_member.unit_ids) == 1:
            matched_member = name_member
            matched_reason = "exact unique roster name or alias resolved attendee pair"
        if matched_member is not None:
            unit1, unit2 = _stage_pair(first[unit_name]); name1, name2 = _stage_pair(first[print_name])
            roster_unit = next((unit for unit in matched_member.unit_ids
                                if unit_assessment and unit_assessment.raw
                                and unit.casefold() == unit_assessment.raw.strip().casefold()),
                               matched_member.unit_ids[0])
            resolutions[unit_name] = FieldResolution(
                unit_name, unit1, unit2, None, roster_unit, roster_unit,
                matched_reason, False, ())
            resolutions[print_name] = FieldResolution(
                print_name, name1, name2, None, matched_member.name, matched_member.name,
                matched_reason, False, ())
            continue
        unit_candidate = _candidate_for(unit_name, unit_assessment, roster)
        name_candidate = _candidate_for(print_name, name_assessment, roster)
        trigger = any(_requires_stage3(name, first[name], assessments[name], validation.warnings)
                      for name in (unit_name, print_name))
        if not trigger: continue
        candidates = [{"unit_id": unit_candidate, "print_name": name_candidate}]
        candidates = [x for x in candidates if x["unit_id"] or x["print_name"]]
        prompt = ("Read only the unit ID and printed name from this attendee row; the excluded rightmost column is absent. "
                  f"Relevant unambiguous roster candidates are {candidates!r}. Mark support true only when the "
                  "handwriting supports the candidate pair. Return exactly "
                  '{"unit_id":string|null,"print_name":string|null,"handwriting_supports_candidate":boolean,'
                  '"alternatives":{"unit_id":string[],"print_name":string[]}}.')
        result, attempt = _verify(provider, _request(page, prefix, "attendee_row",
            [template.region(unit_name), template.region(print_name)], prompt, right_limit=signature_left))
        calls += len(attempt)
        supports = bool(result and result.handwriting_supports_candidate)
        values = result.values if result else {"unit_id": None, "print_name": None}
        unit1, unit2 = _stage_pair(first[unit_name]); name1, name2 = _stage_pair(first[print_name])
        resolutions[unit_name] = _resolve(unit_name, unit1, unit2, values["unit_id"],
                                          unit_candidate, attempt, supports=supports)
        resolutions[print_name] = _resolve(print_name, name1, name2, values["print_name"],
                                           name_candidate, attempt, supports=supports)

        # Stage 3 may recover the one exact roster key that the earlier passes
        # missed. Reconcile the pair again so a definitive ID (or a definitive
        # single-ID name) wins over an unrecognized companion value.
        unit_value = resolutions[unit_name].resolved_value or values["unit_id"]
        name_value = resolutions[print_name].resolved_value or values["print_name"]
        unit_member = roster.member_for_unit(unit_value) if roster and unit_value else None
        name_member = roster.member_for_name(name_value) if roster and name_value else None
        matched_member = None
        matched_reason = None
        if unit_member and (name_member is None or name_member == unit_member):
            matched_member = unit_member
            matched_reason = "exact Stage 3 roster unit ID resolved attendee pair"
        elif name_member and unit_member is None and len(name_member.unit_ids) == 1:
            matched_member = name_member
            matched_reason = "exact Stage 3 roster name or alias resolved attendee pair"
        if matched_member is not None:
            roster_unit = next((unit for unit in matched_member.unit_ids
                                if unit_value and unit.casefold() == unit_value.strip().casefold()),
                               matched_member.unit_ids[0])
            resolutions[unit_name] = FieldResolution(
                unit_name, unit1, unit2, values["unit_id"], roster_unit, roster_unit,
                matched_reason, False, attempt)
            resolutions[print_name] = FieldResolution(
                print_name, name1, name2, values["print_name"], matched_member.name,
                matched_member.name, matched_reason, False, attempt)

    grouped = set(time_names) | {"instructor"} | {
        name for name in first if name.startswith("attendee.")}
    for name, item in first.items():
        if name in grouped or name in resolutions: continue
        assessment = assessments[name]
        if not _requires_stage3(name, item, assessment, validation.warnings): continue
        stage1, stage2 = _stage_pair(item)
        prompt = (f"Independently verify the handwritten value in the labeled {name.replace('_', ' ')} field. "
                  f"Stage 1/2 evidence is {(stage1, stage2)!r}; use the larger labeled image to adjudicate it. "
                  'Return the same shape as this exact example, with a scalar quoted value: '
                  '{"value":"12/17/25","alternatives":{"value":[]}}.')
        result, attempt = _verify(provider, _request(page, name, "field",
                                                     [template.region(name)], prompt, padding=70))
        calls += len(attempt)
        value = result.values["value"] if result else None
        resolutions[name] = _resolve(name, stage1, stage2, value, None, attempt)
    return SecondPassReport(resolutions, calls)
