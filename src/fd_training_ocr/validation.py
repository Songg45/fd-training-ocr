"""Privacy-safe roster loading, validation, and deterministic review decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Iterable, Mapping

from .normalization import NormalizedValue, normalize_allowlisted, normalize_date, normalize_hours, normalize_time
from .recognition import RecognitionResult


class RosterError(ValueError): pass


@dataclass(frozen=True)
class RosterMember:
    name: str
    unit_ids: tuple[str, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Roster:
    members: tuple[RosterMember, ...]

    def match_name(self, raw: str) -> tuple[str | None, tuple[str, ...]]:
        exact = [(m.name, (*m.aliases, m.name)) for m in self.members]
        hits = tuple(name for name, names in exact if raw.strip().casefold() in {n.casefold() for n in names})
        return (hits[0] if len(hits) == 1 else None, hits)

    def valid_unit(self, raw: str) -> bool:
        return any(raw.strip().casefold() == unit.casefold() for m in self.members for unit in m.unit_ids)


def load_roster(path: Path, repository_root: Path) -> Roster:
    if not path.is_absolute(): raise RosterError("roster path must be absolute and outside the repository")
    try:
        resolved, root = path.resolve(strict=True), repository_root.resolve(strict=True)
        if resolved == root or root in resolved.parents:
            raise RosterError("roster path must be outside the Git repository")
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except RosterError: raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RosterError(f"could not read valid roster JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "members"} or payload["schema_version"] != 1 or not isinstance(payload["members"], list):
        raise RosterError("roster must contain only schema_version 1 and a members array")
    members = []
    for index, item in enumerate(payload["members"]):
        if not isinstance(item, dict) or set(item) - {"name", "unit_ids", "aliases"}:
            raise RosterError(f"members[{index}] has invalid keys")
        name, units, aliases = item.get("name"), item.get("unit_ids"), item.get("aliases", [])
        if not isinstance(name, str) or not name.strip() or not isinstance(units, list) or not units or not all(isinstance(x, str) and x.strip() for x in units) or not isinstance(aliases, list) or not all(isinstance(x, str) and x.strip() for x in aliases):
            raise RosterError(f"members[{index}] requires name, nonempty unit_ids, and optional string aliases")
        members.append(RosterMember(name.strip(), tuple(x.strip() for x in units), tuple(x.strip() for x in aliases)))
    return Roster(tuple(members))


@dataclass(frozen=True)
class ValidationPolicy:
    confidence_thresholds: Mapping[str, float] = field(default_factory=lambda: {"default": .85, "date": .90, "time": .90, "unit_id": .95, "print_name": .90})
    allow_overnight: bool = False
    duration_tolerance_hours: float = .05
    apparatus: tuple[str, ...] = ("Engine 54", "Tanker 54", "Brush 54", "Engine 254", "Tanker 854", "Brush 254")
    locations: tuple[str, ...] = ("District",)


@dataclass(frozen=True)
class FieldAssessment:
    field_name: str
    raw: str | None
    normalized: str | None
    confidence: float
    alternatives: tuple[str, ...]
    warnings: tuple[str, ...]
    review_required: bool


@dataclass(frozen=True)
class ValidationReport:
    fields: tuple[FieldAssessment, ...]
    warnings: tuple[str, ...]
    total_hours_calculated: float | None
    review_required: bool


def _normalize(result: RecognitionResult, roster: Roster | None, policy: ValidationPolicy) -> NormalizedValue:
    value = result.value
    if result.field_name == "date": return normalize_date(value)
    if result.field_name in {"start_time", "end_time"}: return normalize_time(value)
    if result.field_name == "total_hours": return normalize_hours(value)
    if result.field_name == "location": return normalize_allowlisted(value, policy.locations)
    if result.field_name.endswith(".print_name") and value and roster:
        match, _ = roster.match_name(value)
        return NormalizedValue(value, match or value.strip(), match is not None, None if match else "name not found uniquely in roster")
    if result.field_name.endswith(".unit_id") and value and roster:
        return NormalizedValue(value, value.strip(), roster.valid_unit(value), None if roster.valid_unit(value) else "unit ID not found in roster")
    return NormalizedValue(value, value.strip() if value else None, bool(value and value.strip()), None if value and value.strip() else "value is blank")


def validate(results: Iterable[RecognitionResult], *, roster: Roster | None = None,
             selected_apparatus: Iterable[str] = (), policy: ValidationPolicy = ValidationPolicy()) -> ValidationReport:
    items = tuple(results); assessments = []; global_warnings = []
    for result in items:
        normalized = _normalize(result, roster, policy); warnings = []
        if not normalized.valid: warnings.append(normalized.reason or "invalid value")
        threshold = policy.confidence_thresholds.get(result.field_name, policy.confidence_thresholds.get(result.field_name.rsplit(".", 1)[-1], policy.confidence_thresholds["default"]))
        if result.confidence < threshold: warnings.append(f"confidence {result.confidence:.2f} is below {threshold:.2f}")
        if result.alternatives: warnings.append("recognizer supplied alternatives")
        assessments.append(FieldAssessment(result.field_name, normalized.raw, normalized.normalized, result.confidence, result.alternatives, tuple(warnings), bool(warnings)))
    by_name = {a.field_name: a for a in assessments}
    calculated = None
    start, end = by_name.get("start_time"), by_name.get("end_time")
    if start and end and start.normalized and end.normalized:
        start_dt = datetime.strptime(start.normalized, "%H:%M"); end_dt = datetime.strptime(end.normalized, "%H:%M")
        if end_dt < start_dt:
            if policy.allow_overnight: end_dt += timedelta(days=1)
            else: global_warnings.append("end time is before start time and overnight training is disabled")
        if end_dt >= start_dt: calculated = (end_dt - start_dt).total_seconds() / 3600
    written = by_name.get("total_hours")
    if calculated is not None and written and written.normalized:
        if abs(calculated - float(written.normalized)) > policy.duration_tolerance_hours:
            global_warnings.append(f"written total hours ({written.normalized}) differs from calculated duration ({calculated:g})")
    unknown = sorted(set(selected_apparatus) - set(policy.apparatus))
    if unknown: global_warnings.append("unknown apparatus: " + ", ".join(unknown))
    attendee_pairs: dict[str, dict[str, FieldAssessment]] = {}
    for item in assessments:
        if item.field_name.startswith("attendee."):
            prefix, kind = item.field_name.rsplit(".", 1); attendee_pairs.setdefault(prefix, {})[kind] = item
    seen = set()
    for prefix, pair in attendee_pairs.items():
        if set(pair) != {"unit_id", "print_name"}: global_warnings.append(f"{prefix} is incomplete")
        key = tuple((pair.get(k).normalized if pair.get(k) else None) for k in ("unit_id", "print_name"))
        if key in seen: global_warnings.append(f"duplicate attendee at {prefix}")
        seen.add(key)
    return ValidationReport(tuple(assessments), tuple(global_warnings), calculated,
                            bool(global_warnings or any(a.review_required for a in assessments)))
