"""Qt-independent controller helpers for the local desktop GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Mapping, MutableMapping

from .config import AppConfig
from .export import FormRecord, source_sha256
from .normalization import canonical_date
from .pipeline import processor_factory
from .recognition import OllamaVisionProvider
from .validation import ValidationPolicy, load_roster


@dataclass(frozen=True)
class GuiPaths:
    master: Path
    template: Path
    output_dir: Path
    pdftoppm: Path | None = None


def validate_pdf(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.suffix.casefold() != ".pdf" or not resolved.is_file():
        raise ValueError("Select a readable PDF file")
    return resolved


def validate_pdfs(paths: list[Path] | tuple[Path, ...]) -> tuple[Path, ...]:
    """Validate a GUI selection while preserving order and removing duplicates."""
    result = []
    seen = set()
    for path in paths:
        resolved = validate_pdf(path)
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    if not result:
        raise ValueError("Select at least one readable PDF file")
    return tuple(result)


def discover_pdfs(directory: Path) -> tuple[Path, ...]:
    """Return PDFs directly inside a folder in stable filename order."""
    resolved = directory.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("Select a readable folder")
    return tuple(sorted(
        (path.resolve() for path in resolved.iterdir()
         if path.is_file() and path.suffix.casefold() == ".pdf"),
        key=lambda path: (path.name.casefold(), path.name),
    ))


def index_after_removal(removed_index: int, remaining_count: int) -> int:
    """Choose the nearest valid queue index after removing the current PDF."""
    if remaining_count <= 0:
        return -1
    return min(removed_index, remaining_count - 1)


def queue_index_for_page(page_number: int, queue_count: int) -> int:
    """Convert a user-facing one-based Go To page into a queue index."""
    if queue_count < 1:
        raise ValueError("the PDF queue is empty")
    if not 1 <= page_number <= queue_count:
        raise ValueError(f"PDF number must be between 1 and {queue_count}")
    return page_number - 1


def unprocessed_sources(sources: list[Path] | tuple[Path, ...],
                        records: Mapping[Path, Any]) -> tuple[Path, ...]:
    """Return queued PDFs that do not yet have a completed in-memory result."""
    return tuple(source for source in sources if source not in records)


def display_value(field: Mapping[str, Any]) -> Any:
    for key in ("reviewed_value", "resolved_value", "normalized", "raw"):
        if field.get(key) is not None:
            return field[key]
    return None


def _labels(values: Any) -> str:
    if not values:
        return "None selected"
    return ", ".join(str(value).replace("_", " ").title() for value in values)


def _counted(value: Any, singular: str) -> str:
    return f"{value} {singular if value == 1 else singular + 's'}"


FACILITY_LABELS = {
    "classroom": "Classroom",
    "drill_ground": "Drill Ground",
    "outside_area": "Outside Area",
}

TRAINING_TYPE_LABELS = {
    "facilities": "Facilities",
    "company": "Company",
    "officers": "Officers",
    "driver": "Driver",
    "haz_mat": "Haz-mat",
    "new_driver": "New Driver",
    "recruit": "Recruit",
}

TRUCK_LABELS = {name: name for name in (
    "Engine 54", "Tanker 54", "Brush 54", "Engine 254", "Tanker 854", "Brush 254")}

EVENT_SELECTIONS = {
    "Training type": (TRAINING_TYPE_LABELS, "training_types", "reviewed_training_types"),
    "Truck": (TRUCK_LABELS, "trucks_used", "reviewed_trucks_used"),
    "Facilities": (FACILITY_LABELS, "facilities", "reviewed_facilities"),
}


def effective_facilities(event: Mapping[str, Any]) -> Any:
    reviewed = event.get("reviewed_facilities")
    return reviewed if reviewed is not None else event.get("facilities")


def effective_event_selection(event: Mapping[str, Any], selection_name: str) -> Any:
    if selection_name not in EVENT_SELECTIONS:
        raise ValueError(f"unknown event selection: {selection_name}")
    _, machine_key, reviewed_key = EVENT_SELECTIONS[selection_name]
    reviewed = event.get(reviewed_key)
    return reviewed if reviewed is not None else event.get(machine_key)


def _event_labels(event: Mapping[str, Any], selection_name: str) -> str:
    labels, _, _ = EVENT_SELECTIONS[selection_name]
    values = effective_event_selection(event, selection_name)
    if not values:
        return "None selected"
    return ", ".join(labels.get(value, str(value)) for value in values)


def structured_rows(record: Mapping[str, Any]) -> tuple[tuple[str, str, str, bool], ...]:
    rows = []
    for name, field in record.get("fields", {}).items():
        warnings = [str(item) for item in field.get("warnings", ())]
        stage3 = field.get("stage_3")
        if (field.get("second_pass_review_required") and stage3 is not None
                and str(stage3) != str(display_value(field))):
            warnings.append(f"Stage 3 suggests: {stage3}")
        rows.append((str(name), "" if display_value(field) is None else str(display_value(field)),
                     "; ".join(warnings), True))
    event = record.get("event", {})
    calculated = event.get("total_hours_calculated")
    rows.extend((
        ("Training type", _event_labels(event, "Training type"),
         "Double-click to select training types", False),
        ("Truck", _event_labels(event, "Truck"),
         "Double-click to select trucks", False),
        ("Facilities", _event_labels(event, "Facilities"),
         "Double-click to select facilities", False),
        ("Calculated duration", "" if calculated is None else _counted(calculated, "hour"), "", False),
    ))
    stage3_fields = [field for field in record.get("fields", {}).values()
                     if field.get("stage_3") is not None]
    unresolved = sum(bool(field.get("second_pass_review_required")) for field in stage3_fields)
    calls = event.get("second_pass_call_count", 0)
    resolved = len(stage3_fields) - unresolved
    rows.append(("Stage 3 resolution", f"{_counted(calls, 'call')}; {_counted(resolved, 'field')} resolved; "
                 f"{unresolved} unresolved", "", False))
    return tuple(rows)


def alignment_fallback_record(source: Path, failure: str) -> dict[str, Any]:
    """Create an editable manual-entry record when safe OCR alignment fails."""
    stamp = datetime.now(timezone.utc).isoformat()

    def field() -> dict[str, Any]:
        return {"raw": None, "normalized": None, "reviewed_value": None,
                "confidence": 0.0, "alternatives": [], "provider": "manual",
                "model": None, "source_region": None,
                "warnings": ["manual entry required because alignment failed"],
                "review": {"status": "unreviewed", "reviewed_at": None},
                "resolved_value": None, "stage_3": None,
                "second_pass_review_required": False}

    fields = {name: field() for name in (
        "date", "start_time", "end_time", "location", "total_hours", "instructor",
        "attendee.01.unit_id", "attendee.01.print_name",
        "attendee.02.unit_id", "attendee.02.print_name", "description")}
    return {
        "source_file": source.name,
        "source_sha256": source_sha256(source),
        "page": 1,
        "form_type": "pilot_fd_training_sign_in",
        "form_version": "manual_alignment_fallback",
        "status": "review_required",
        "fields": fields,
        "event": {"total_hours_calculated": None, "training_types": [],
                  "facilities": [], "trucks_used": [], "second_pass_call_count": 0},
        "attendees": [{"row": 1, "unit_id": None, "print_name": None},
                      {"row": 2, "unit_id": None, "print_name": None}],
        "warnings": [failure, "OCR was not run; complete the manual field template"],
        "review": {"status": "pending", "corrections_applied": False,
                   "reviewed_at": stamp, "alignment_fallback": True},
    }


def _recalculate_duration(record: MutableMapping[str, Any]) -> None:
    fields = record.get("fields", {})
    if not isinstance(fields, Mapping):
        return
    start_field, end_field = fields.get("start_time"), fields.get("end_time")
    start_value = display_value(start_field) if isinstance(start_field, Mapping) else None
    end_value = display_value(end_field) if isinstance(end_field, Mapping) else None
    calculated = None
    try:
        start = datetime.strptime(str(start_value), "%H:%M")
        end = datetime.strptime(str(end_value), "%H:%M")
        if end >= start:
            calculated = (end - start).total_seconds() / 3600
    except (TypeError, ValueError):
        pass
    record.setdefault("event", {})["total_hours_calculated"] = calculated


def apply_gui_edit(record: MutableMapping[str, Any], field_name: str, value: str,
                   reviewed_at: str | None = None) -> None:
    """Store a GUI correction separately without changing machine evidence."""
    fields = record.get("fields")
    if not isinstance(fields, MutableMapping) or field_name not in fields:
        raise ValueError(f"unknown reviewed field: {field_name}")
    field = fields[field_name]
    if not isinstance(field, MutableMapping):
        raise ValueError(f"invalid reviewed field: {field_name}")
    if field_name == "date" and value:
        formatted = canonical_date(value)
        if formatted is None:
            raise ValueError("date must be a valid MM/DD/YY date")
        value = formatted
    stamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    field["reviewed_value"] = value if value != "" else None
    field["review"] = {"status": "corrected", "reviewed_at": stamp}
    review = record.setdefault("review", {})
    review["corrections_applied"] = True
    review["reviewed_at"] = stamp
    attendee_match = re.fullmatch(r"attendee\.(\d+)\.(unit_id|print_name)", field_name)
    if attendee_match:
        row, key = int(attendee_match.group(1)), attendee_match.group(2)
        attendees = list(record.get("attendees", ()))
        attendee = next((item for item in attendees
                         if isinstance(item, MutableMapping) and item.get("row") == row), None)
        if attendee is None:
            attendee = {"row": row, "unit_id": None, "print_name": None}
            attendees.append(attendee)
        attendee[key] = value if value else None
        attendees.sort(key=lambda item: int(item.get("row", 0)))
        record["attendees"] = attendees
    if field_name in {"start_time", "end_time"}:
        _recalculate_duration(record)


def apply_roster_linked_unit_edit(record: MutableMapping[str, Any], field_name: str,
                                  value: str, roster_path: Path,
                                  repository_root: Path,
                                  reviewed_at: str | None = None) -> str | None:
    """Edit an attendee ID and fill its name from an exact unique roster match."""
    match = re.fullmatch(r"attendee\.(\d+)\.unit_id", field_name)
    if match is None:
        raise ValueError(f"not an attendee unit ID field: {field_name}")
    roster = load_roster(roster_path, repository_root)
    stamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    apply_gui_edit(record, field_name, value, stamp)
    member = roster.member_for_unit(value)
    row = int(match.group(1))
    canonical_name = member.name if member is not None else None
    if canonical_name is not None:
        name_field = f"attendee.{row:02d}.print_name"
        apply_gui_edit(record, name_field, canonical_name, stamp)

    attendees = []
    for attendee in record.get("attendees", ()):
        if isinstance(attendee, Mapping) and attendee.get("row") == row:
            updated = dict(attendee)
            updated["unit_id"] = value
            if canonical_name is not None:
                updated["print_name"] = canonical_name
            attendees.append(updated)
        else:
            attendees.append(attendee)
    record["attendees"] = attendees
    return canonical_name


def populate_unit_from_roster_name(record: MutableMapping[str, Any], field_name: str,
                                   value: str, roster_path: Path,
                                   repository_root: Path,
                                   reviewed_at: str | None = None) -> str | None:
    """Fill an attendee ID when an edited/accepted name exactly identifies one ID."""
    match = re.fullmatch(r"attendee\.(\d+)\.print_name", field_name)
    if match is None:
        raise ValueError(f"not an attendee print-name field: {field_name}")
    roster = load_roster(roster_path, repository_root)
    member = roster.member_for_name(value)
    unit_id = member.unit_ids[0] if member is not None and len(member.unit_ids) == 1 else None
    row = int(match.group(1))
    if unit_id is not None:
        apply_gui_edit(record, f"attendee.{row:02d}.unit_id", unit_id, reviewed_at)
    attendees = []
    for attendee in record.get("attendees", ()):
        if isinstance(attendee, Mapping) and attendee.get("row") == row:
            updated = dict(attendee)
            updated["print_name"] = value
            if unit_id is not None:
                updated["unit_id"] = unit_id
            attendees.append(updated)
        else:
            attendees.append(attendee)
    record["attendees"] = attendees
    return unit_id


def populate_name_from_roster_unit(record: MutableMapping[str, Any], field_name: str,
                                   value: str, roster_path: Path,
                                   repository_root: Path,
                                   reviewed_at: str | None = None) -> str | None:
    """Fill an attendee name when an edited/accepted ID has one exact roster match."""
    match = re.fullmatch(r"attendee\.(\d+)\.unit_id", field_name)
    if match is None:
        raise ValueError(f"not an attendee unit ID field: {field_name}")
    roster = load_roster(roster_path, repository_root)
    member = roster.member_for_unit(value)
    canonical_name = member.name if member is not None else None
    row = int(match.group(1))
    if canonical_name is not None:
        apply_gui_edit(record, f"attendee.{row:02d}.print_name", canonical_name, reviewed_at)
    attendees = []
    for attendee in record.get("attendees", ()):
        if isinstance(attendee, Mapping) and attendee.get("row") == row:
            updated = dict(attendee)
            updated["unit_id"] = value
            if canonical_name is not None:
                updated["print_name"] = canonical_name
            attendees.append(updated)
        else:
            attendees.append(attendee)
    record["attendees"] = attendees
    return canonical_name


def roster_linked_attendee_values(unit_id: str, print_name: str, roster_path: Path,
                                  repository_root: Path,
                                  changed: str) -> tuple[str, str]:
    """Resolve exact roster values for the Add Attendee dialog without losing manual text."""
    roster = load_roster(roster_path, repository_root)
    if changed == "unit_id":
        member = roster.member_for_unit(unit_id)
        return (unit_id, member.name if member is not None else print_name)
    if changed == "print_name":
        member = roster.member_for_name(print_name)
        resolved = member.unit_ids[0] if member is not None and len(member.unit_ids) == 1 else unit_id
        return (resolved, member.name if member is not None else print_name)
    raise ValueError(f"unknown attendee field: {changed}")


def stage3_suggestion(record: Mapping[str, Any], field_name: str) -> Any:
    field = record.get("fields", {}).get(field_name)
    if (not isinstance(field, Mapping) or not field.get("second_pass_review_required")
            or field.get("stage_3") is None):
        return None
    return field.get("stage_3")


def accept_stage3_suggestion(record: MutableMapping[str, Any], field_name: str,
                             reviewed_at: str | None = None) -> None:
    """Accept an unresolved Stage-3 value as an explicit human correction."""
    suggestion = stage3_suggestion(record, field_name)
    if suggestion is None:
        raise ValueError(f"field {field_name} has no unresolved Stage 3 suggestion")
    fields = record.get("fields")
    if not isinstance(fields, MutableMapping) or not isinstance(fields.get(field_name), MutableMapping):
        raise ValueError(f"unknown reviewed field: {field_name}")
    if field_name == "date":
        suggestion = canonical_date(str(suggestion))
        if suggestion is None:
            raise ValueError("Stage 3 date is not a complete valid date")
    stamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    field = fields[field_name]
    field["reviewed_value"] = suggestion
    field["second_pass_review_required"] = False
    field["resolution_reason"] = "Stage 3 suggestion accepted by reviewer"
    field["review"] = {"status": "corrected", "reviewed_at": stamp,
                       "source": "stage_3"}
    review = record.setdefault("review", {})
    review.setdefault("accepted_stage3", []).append(
        {"field_name": field_name, "value": suggestion, "reviewed_at": stamp})
    review["corrections_applied"] = True
    review["reviewed_at"] = stamp


def attendee_row_from_field(field_name: str) -> int | None:
    match = re.fullmatch(r"attendee\.(\d+)\.(?:unit_id|print_name)", field_name)
    return int(match.group(1)) if match else None


def remove_attendee(record: MutableMapping[str, Any], row: int,
                    reviewed_at: str | None = None) -> None:
    """Remove an active attendee while retaining its machine evidence for audit."""
    if row < 1:
        raise ValueError("attendee row must be positive")
    prefix = f"attendee.{row:02d}."
    fields = record.get("fields")
    if not isinstance(fields, MutableMapping):
        raise ValueError("record has no editable fields")
    removed_fields = {name: fields.pop(name) for name in list(fields) if name.startswith(prefix)}
    attendees = record.get("attendees", [])
    removed_attendees = []
    if isinstance(attendees, (list, tuple)):
        removed_attendees = [item for item in attendees
                             if isinstance(item, Mapping) and item.get("row") == row]
        record["attendees"] = [item for item in attendees
                               if not (isinstance(item, Mapping) and item.get("row") == row)]
    if not removed_fields and not removed_attendees:
        raise ValueError(f"attendee row {row} is not present")
    stamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    review = record.setdefault("review", {})
    removed = review.setdefault("removed_attendees", [])
    removed.append({"row": row, "fields": removed_fields,
                    "attendees": removed_attendees, "reviewed_at": stamp})
    review["corrections_applied"] = True
    review["reviewed_at"] = stamp


def first_available_attendee_row(record: Mapping[str, Any], maximum: int = 19) -> int | None:
    fields = record.get("fields", {})
    occupied = {attendee_row_from_field(str(name)) for name in fields}
    occupied.discard(None)
    attendees = record.get("attendees", ())
    if isinstance(attendees, (list, tuple)):
        occupied.update(int(item["row"]) for item in attendees
                        if isinstance(item, Mapping) and isinstance(item.get("row"), int))
    return next((row for row in range(1, maximum + 1) if row not in occupied), None)


def add_attendee(record: MutableMapping[str, Any], row: int, unit_id: str, print_name: str,
                 reviewed_at: str | None = None, maximum: int = 19) -> None:
    """Add a manually reviewed attendee to an unused form row."""
    unit_id = unit_id.strip()
    print_name = print_name.strip()
    if not 1 <= row <= maximum:
        raise ValueError(f"attendee row must be between 1 and {maximum}")
    if not unit_id or not print_name:
        raise ValueError("an added attendee requires both Unit ID and Print Name")
    fields = record.setdefault("fields", {})
    if not isinstance(fields, MutableMapping):
        raise ValueError("record has no editable fields")
    prefix = f"attendee.{row:02d}"
    attendees = list(record.get("attendees", ()))
    if (any(str(name).startswith(prefix + ".") for name in fields)
            or any(isinstance(item, Mapping) and item.get("row") == row for item in attendees)):
        raise ValueError(f"attendee row {row} is already occupied")
    stamp = reviewed_at or datetime.now(timezone.utc).isoformat()

    def manual_field(value: str) -> dict[str, Any]:
        return {"raw": None, "normalized": None, "reviewed_value": value,
                "confidence": 1.0, "alternatives": [], "provider": "manual",
                "model": None, "source_region": None, "warnings": [],
                "review": {"status": "corrected", "reviewed_at": stamp},
                "resolved_value": None, "second_pass_review_required": False}

    fields[prefix + ".unit_id"] = manual_field(unit_id)
    fields[prefix + ".print_name"] = manual_field(print_name)
    attendee = {"row": row, "unit_id": unit_id, "print_name": print_name}
    attendees.append(attendee)
    attendees.sort(key=lambda item: int(item.get("row", 0)) if isinstance(item, Mapping) else 0)
    record["attendees"] = attendees
    review = record.setdefault("review", {})
    review.setdefault("added_attendees", []).append({**attendee, "reviewed_at": stamp})
    review["corrections_applied"] = True
    review["reviewed_at"] = stamp


def apply_facilities_edit(record: MutableMapping[str, Any], facilities: list[str],
                          reviewed_at: str | None = None) -> None:
    """Store reviewed facilities separately from the detector's machine result."""
    apply_event_selection(record, "Facilities", facilities, reviewed_at)


def apply_event_selection(record: MutableMapping[str, Any], selection_name: str,
                          values: list[str], reviewed_at: str | None = None) -> None:
    """Store a reviewed event selection separately from machine detections."""
    if selection_name not in EVENT_SELECTIONS:
        raise ValueError(f"unknown event selection: {selection_name}")
    labels, _, reviewed_key = EVENT_SELECTIONS[selection_name]
    invalid = [value for value in values if value not in labels]
    if invalid:
        raise ValueError(f"unknown {selection_name.lower()} selection: {', '.join(invalid)}")
    stamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    event = record.setdefault("event", {})
    event[reviewed_key] = list(dict.fromkeys(values))
    event[reviewed_key.removeprefix("reviewed_") + "_review"] = {
        "status": "corrected", "reviewed_at": stamp}
    review = record.setdefault("review", {})
    review["corrections_applied"] = True
    review["reviewed_at"] = stamp


def export_record(record: Mapping[str, Any], destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return destination


def automatic_export_stem(record: Mapping[str, Any]) -> str:
    """Build a Windows-safe, sortable filename stem from the reviewed/effective date."""
    field = record.get("fields", {}).get("date", {})
    value = display_value(field) if isinstance(field, Mapping) else None
    if value is not None:
        text = str(value).strip()
        for date_format in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(text, date_format).date().isoformat()
            except ValueError:
                pass
    source_stem = Path(str(record.get("source_file", "form"))).stem
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", source_stem).strip(".-") or "form"
    return f"undated-{safe}"


def automatic_export(record: Mapping[str, Any], directory: Path) -> Path:
    """Export one current file per source hash without overwriting another form."""
    directory = directory.expanduser().resolve()
    digest = record.get("source_sha256")
    owned_paths = []
    if digest and directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if existing.get("source_sha256") == digest:
                owned_paths.append(path.resolve())
    stem = automatic_export_stem(record)
    candidate = directory / f"{stem}.json"
    suffix = 2
    while candidate.exists():
        try:
            existing = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if digest and existing.get("source_sha256") == digest:
            break
        candidate = directory / f"{stem}-{suffix}.json"
        suffix += 1
    destination = export_record(record, candidate)
    for obsolete in owned_paths:
        if obsolete != destination:
            obsolete.unlink(missing_ok=True)
    return destination


def create_startup_backup(*, backup_dir: Path, export_dir: Path,
                          state_file: Path, config_file: Path | None,
                          roster_file: Path | None, keep: int = 20,
                          snapshot_at: datetime | None = None) -> Path | None:
    """Create an immutable MM-DD-YYYY/HH-MM-SS startup data snapshot."""
    if keep < 1:
        raise ValueError("backup retention must be at least 1")
    root = backup_dir.expanduser().resolve()
    sources: list[tuple[Path, Path]] = []
    exports = export_dir.expanduser().resolve()
    if exports.is_dir():
        sources.extend((path, Path("Exported") / path.name)
                       for path in sorted(exports.glob("*.json")) if path.is_file())
    candidates = ((config_file, Path("Configuration") / "config.toml"),
                  (roster_file, Path("Roster") / "roster.json"),
                  (state_file, Path("State") / "gui-state.json"))
    for source, archive_path in candidates:
        if source is not None:
            resolved = source.expanduser().resolve()
            if resolved.is_file():
                sources.append((resolved, archive_path))
    if not sources:
        return None

    files = []
    for source, archive_path in sources:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        files.append({"path": archive_path.as_posix(), "sha256": digest,
                      "size": source.stat().st_size})
    files.sort(key=lambda item: str(item["path"]).casefold())

    root.mkdir(parents=True, exist_ok=True)
    timestamp = snapshot_at or datetime.now()
    date_directory = root / timestamp.strftime("%m-%d-%Y")
    date_directory.mkdir(exist_ok=True)
    base = timestamp.strftime("%H-%M-%S")
    destination = date_directory / base
    suffix = 2
    while destination.exists():
        destination = date_directory / f"{base}-{suffix}"
        suffix += 1
    destination.mkdir()
    for source, archive_path in sources:
        target = destination / archive_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest = {"schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(), "files": files}
    (destination / "backup-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    dated_snapshots: list[tuple[datetime, Path]] = []
    for day_path in root.iterdir():
        if not day_path.is_dir():
            continue
        try:
            day = datetime.strptime(day_path.name, "%m-%d-%Y")
        except ValueError:
            continue
        for snapshot in day_path.iterdir():
            if not snapshot.is_dir():
                continue
            match = re.fullmatch(r"(\d{2}-\d{2}-\d{2})(?:-\d+)?", snapshot.name)
            if match is None:
                continue
            try:
                clock = datetime.strptime(match.group(1), "%H-%M-%S").time()
            except ValueError:
                continue
            dated_snapshots.append((datetime.combine(day.date(), clock), snapshot))
    dated_snapshots.sort()
    for _, obsolete in dated_snapshots[:-keep]:
        shutil.rmtree(obsolete)
    for day_path in root.iterdir():
        if day_path.is_dir() and not any(day_path.iterdir()):
            day_path.rmdir()
    return destination


def save_gui_state(state_file: Path, sources: list[Path] | tuple[Path, ...],
                   current_index: int, records: Mapping[Path, Any],
                   failures: Mapping[Path, str]) -> Path:
    """Atomically persist the desktop queue without modifying any source PDF."""
    destination = state_file.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "sources": [str(source.resolve()) for source in sources],
        "current_index": current_index,
        "records": {str(source.resolve()): record for source, record in records.items()
                    if source in sources},
        "failures": {str(source.resolve()): str(message) for source, message in failures.items()
                     if source in sources},
    }
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(destination)
    return destination


def load_gui_state(state_file: Path) -> tuple[list[Path], int, dict[Path, Any], dict[Path, str]]:
    """Restore existing PDFs from a versioned state file and ignore stale paths."""
    source = state_file.expanduser().resolve()
    if not source.is_file():
        return [], -1, {}, {}
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("unsupported GUI state file")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError("invalid GUI state sources")
    sources = []
    seen = set()
    for raw_path in raw_sources:
        try:
            candidate = Path(str(raw_path)).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if (candidate not in seen and candidate.is_file()
                and candidate.suffix.casefold() == ".pdf"):
            sources.append(candidate)
            seen.add(candidate)
    records_payload = payload.get("records", {})
    failures_payload = payload.get("failures", {})
    records = {}
    failures = {}
    for candidate in sources:
        key = str(candidate)
        if isinstance(records_payload, Mapping) and isinstance(records_payload.get(key), Mapping):
            records[candidate] = dict(records_payload[key])
        if isinstance(failures_payload, Mapping) and failures_payload.get(key) is not None:
            failures[candidate] = str(failures_payload[key])
    requested_index = payload.get("current_index", 0)
    try:
        requested_index = int(requested_index)
    except (TypeError, ValueError):
        requested_index = 0
    current_index = min(max(requested_index, 0), len(sources) - 1) if sources else -1
    return sources, current_index, records, failures


def roster_table_rows(roster_path: Path, repository_root: Path) -> list[tuple[str, str, str]]:
    """Load a validated external roster into editable table-shaped strings."""
    roster = load_roster(roster_path, repository_root)
    return [(member.name, ", ".join(member.unit_ids), ", ".join(member.aliases))
            for member in roster.members]


def _split_roster_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]


def save_roster_table(roster_path: Path, repository_root: Path,
                      rows: list[tuple[str, str, str]]) -> Path:
    """Validate and atomically save editable roster rows outside the repository."""
    destination = roster_path.expanduser().resolve()
    root = repository_root.expanduser().resolve()
    if not roster_path.is_absolute() or destination == root or root in destination.parents:
        raise ValueError("roster path must be absolute and outside the Git repository")
    members = []
    seen_units = {}
    for row_number, (raw_name, raw_units, raw_aliases) in enumerate(rows, start=1):
        name = raw_name.strip()
        units = _split_roster_values(raw_units)
        aliases = _split_roster_values(raw_aliases)
        if not name and not units and not aliases:
            continue
        if not name or not units:
            raise ValueError(f"roster row {row_number} requires a name and at least one unit ID")
        for unit in units:
            key = unit.casefold()
            if key in seen_units:
                raise ValueError(
                    f"unit ID {unit} is duplicated by {seen_units[key]} and {name}")
            seen_units[key] = name
        members.append({"name": name, "unit_ids": units, "aliases": aliases})
    if not members:
        raise ValueError("roster must contain at least one member")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "members": members}
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(destination)
    load_roster(destination, root)
    return destination


def build_processor(config: AppConfig, paths: GuiPaths,
                    provider_factory: Callable[..., Any] = OllamaVisionProvider):
    primary = provider_factory(config.ollama_model, config.ollama_endpoint,
                               config.ollama_timeout_seconds)
    stage3 = provider_factory(config.ollama_stage3_model, config.ollama_endpoint,
                              config.ollama_timeout_seconds)
    return processor_factory(
        work_dir=paths.output_dir / "work", master_path=paths.master,
        template_path=paths.template, provider=primary, stage3_provider=stage3,
        repository_root=Path.cwd(), roster_path=config.roster_path,
        pdftoppm=paths.pdftoppm,
        recognition_crop_padding=config.recognition_crop_padding_pixels,
        recognition_max_attempts=config.recognition_max_attempts,
        policy=ValidationPolicy(apparatus=config.valid_apparatus,
                                locations=config.valid_locations,
                                location_aliases=config.location_aliases))


def process_pdf(source: Path, processor: Callable[[Path, str], FormRecord]) -> dict[str, Any]:
    source = validate_pdf(source)
    return asdict(processor(source, source_sha256(source)))
