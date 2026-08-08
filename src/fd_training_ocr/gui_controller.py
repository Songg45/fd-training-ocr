"""Qt-independent controller helpers for the local desktop GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping

from .config import AppConfig
from .export import FormRecord, source_sha256
from .pipeline import processor_factory
from .recognition import OllamaVisionProvider
from .validation import ValidationPolicy


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


def effective_facilities(event: Mapping[str, Any]) -> Any:
    reviewed = event.get("reviewed_facilities")
    return reviewed if reviewed is not None else event.get("facilities")


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
        ("Training type", _labels(event.get("training_types")), "", False),
        ("Truck", _labels(event.get("trucks_used")), "", False),
        ("Facilities", _labels(effective_facilities(event)),
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


def apply_gui_edit(record: MutableMapping[str, Any], field_name: str, value: str,
                   reviewed_at: str | None = None) -> None:
    """Store a GUI correction separately without changing machine evidence."""
    fields = record.get("fields")
    if not isinstance(fields, MutableMapping) or field_name not in fields:
        raise ValueError(f"unknown reviewed field: {field_name}")
    field = fields[field_name]
    if not isinstance(field, MutableMapping):
        raise ValueError(f"invalid reviewed field: {field_name}")
    stamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    field["reviewed_value"] = value if value != "" else None
    field["review"] = {"status": "corrected", "reviewed_at": stamp}
    review = record.setdefault("review", {})
    review["corrections_applied"] = True
    review["reviewed_at"] = stamp


def apply_facilities_edit(record: MutableMapping[str, Any], facilities: list[str],
                          reviewed_at: str | None = None) -> None:
    """Store reviewed facilities separately from the detector's machine result."""
    invalid = [value for value in facilities if value not in FACILITY_LABELS]
    if invalid:
        raise ValueError(f"unknown facilities selection: {', '.join(invalid)}")
    stamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    event = record.setdefault("event", {})
    event["reviewed_facilities"] = list(dict.fromkeys(facilities))
    event["facilities_review"] = {"status": "corrected", "reviewed_at": stamp}
    review = record.setdefault("review", {})
    review["corrections_applied"] = True
    review["reviewed_at"] = stamp


def export_record(record: Mapping[str, Any], destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
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
                                locations=config.valid_locations))


def process_pdf(source: Path, processor: Callable[[Path, str], FormRecord]) -> dict[str, Any]:
    source = validate_pdf(source)
    return asdict(processor(source, source_sha256(source)))
