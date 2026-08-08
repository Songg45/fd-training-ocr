"""Idempotent, privacy-conscious batch records and normalized exports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


SIGNATURE_TOKENS = ("signature", "signed")


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _assert_signature_free(value: Any, trail: str = "record") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if any(token in str(key).casefold() for token in SIGNATURE_TOKENS):
                raise ValueError(f"signature data is forbidden at {trail}.{key}")
            _assert_signature_free(item, f"{trail}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_signature_free(item, f"{trail}[{index}]")


@dataclass(frozen=True)
class FormRecord:
    source_file: str
    source_sha256: str
    page: int
    form_type: str
    form_version: str
    status: str
    fields: Mapping[str, Mapping[str, Any]]
    event: Mapping[str, Any]
    attendees: tuple[Mapping[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    review: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "review_required"}:
            raise ValueError("invalid form status")
        _assert_signature_free(asdict(self))


@dataclass(frozen=True)
class ErrorRecord:
    source_file: str
    source_sha256: str | None
    error_type: str
    message: str
    status: str = "failed"


@dataclass(frozen=True)
class BatchSummary:
    discovered: int
    succeeded: int
    review_required: int
    failed: int
    skipped_duplicate: int
    records: tuple[str, ...]
    errors: tuple[ErrorRecord, ...]

    @property
    def exit_code(self) -> int:
        if self.failed: return 2
        if self.review_required: return 3
        return 0


FormProcessor = Callable[[Path, str], FormRecord]


def apply_review(record: FormRecord, correction_document: Mapping[str, Any]) -> FormRecord:
    """Apply a separate review document while retaining every machine value."""
    if correction_document.get("schema_version") != 1 or not isinstance(correction_document.get("corrections"), list):
        raise ValueError("invalid corrections document")
    fields = {name: dict(value) for name, value in record.fields.items()}
    for correction in correction_document["corrections"]:
        if not isinstance(correction, Mapping) or set(correction) != {"field_name", "reviewed_value", "status", "reviewed_at"}:
            raise ValueError("invalid correction record")
        name = correction["field_name"]
        if not isinstance(name, str) or any(token in name.casefold() for token in SIGNATURE_TOKENS):
            raise ValueError("signature review data is forbidden")
        if name not in fields: raise ValueError(f"unknown reviewed field: {name}")
        if correction["status"] not in {"approved", "corrected", "unresolved"} or not isinstance(correction["reviewed_at"], str):
            raise ValueError("invalid review provenance")
        fields[name]["reviewed_value"] = correction["reviewed_value"]
        fields[name]["review"] = {"status": correction["status"], "reviewed_at": correction["reviewed_at"]}
    unresolved = any(item.get("review", {}).get("status") in {"unreviewed", "unresolved"} for item in fields.values())
    review = {"status": "pending" if unresolved else "completed", "corrections_applied": True,
              "reviewed_at": max((item.get("review", {}).get("reviewed_at") or "" for item in fields.values()), default="") or None}
    return FormRecord(record.source_file, record.source_sha256, record.page, record.form_type,
        record.form_version, "review_required" if unresolved else "succeeded", fields,
        record.event, record.attendees, record.warnings, review)


def discover_pdfs(source: Path) -> tuple[Path, ...]:
    if source.is_file():
        if source.suffix.casefold() != ".pdf": raise ValueError("source file must be a PDF")
        return (source,)
    if source.is_dir(): return tuple(sorted((p for p in source.iterdir() if p.is_file() and p.suffix.casefold() == ".pdf"), key=lambda p: p.name.casefold()))
    raise FileNotFoundError(f"source does not exist: {source}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _existing_hashes(records_dir: Path) -> set[str]:
    hashes = set()
    for path in records_dir.glob("*.json") if records_dir.exists() else ():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload.get("source_sha256"), str): hashes.add(payload["source_sha256"])
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
    return hashes


def _field_value(record: FormRecord, name: str) -> Any:
    item = record.fields.get(name, {})
    for key in ("reviewed_value", "normalized", "raw"):
        if item.get(key) is not None: return item[key]
    return None


def write_csv_exports(records: Iterable[FormRecord], output_dir: Path) -> None:
    items = tuple(records); output_dir.mkdir(parents=True, exist_ok=True)
    event_columns = ("source_sha256", "source_file", "page", "form_type", "form_version", "status", "date", "start_time", "end_time", "total_hours_written", "total_hours_calculated", "location", "instructor", "training_types", "facilities", "trucks_used", "description")
    with (output_dir / "events.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=event_columns); writer.writeheader()
        for record in items:
            row = {column: record.event.get(column) for column in event_columns}
            row.update({"source_sha256": record.source_sha256, "source_file": record.source_file, "page": record.page, "form_type": record.form_type, "form_version": record.form_version, "status": record.status})
            for key in ("date", "start_time", "end_time", "total_hours_written", "location", "instructor", "description"):
                row[key] = row[key] if row[key] is not None else _field_value(record, "total_hours" if key == "total_hours_written" else key)
            for key in ("training_types", "facilities", "trucks_used"):
                if isinstance(row[key], (list, tuple)): row[key] = "|".join(str(x) for x in row[key])
            writer.writerow(row)
    attendee_columns = ("source_sha256", "source_file", "page", "row", "unit_id", "print_name", "status")
    with (output_dir / "attendees.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=attendee_columns); writer.writeheader()
        for record in items:
            for attendee in record.attendees:
                row_number = int(attendee.get("row", 0)); prefix = f"attendee.{row_number:02d}"
                writer.writerow({"source_sha256": record.source_sha256, "source_file": record.source_file, "page": record.page, "row": row_number,
                    "unit_id": _field_value(record, prefix + ".unit_id") or attendee.get("unit_id"),
                    "print_name": _field_value(record, prefix + ".print_name") or attendee.get("print_name"), "status": record.status})


def run_batch(source: Path, output_dir: Path, processor: FormProcessor) -> BatchSummary:
    paths = discover_pdfs(source); records_dir = output_dir / "records"; errors_dir = output_dir / "errors"
    existing = _existing_hashes(records_dir); records: list[FormRecord] = []; errors: list[ErrorRecord] = []; skipped = 0
    for path in paths:
        digest = None
        try:
            digest = source_sha256(path)
            if digest in existing:
                skipped += 1; continue
            record = processor(path, digest)
            if record.source_sha256 != digest: raise ValueError("processor returned a mismatched source hash")
            _write_json(records_dir / f"{digest}.json", asdict(record)); records.append(record); existing.add(digest)
        except Exception as exc:  # batch boundary intentionally isolates each document
            error = ErrorRecord(path.name, digest, type(exc).__name__, str(exc)); errors.append(error)
            key = digest or hashlib.sha256(str(path).encode()).hexdigest()
            _write_json(errors_dir / f"{key}.json", asdict(error))
    all_records = []
    for path in records_dir.glob("*.json") if records_dir.exists() else ():
        payload = json.loads(path.read_text(encoding="utf-8")); payload["attendees"] = tuple(payload.get("attendees", ())); payload["warnings"] = tuple(payload.get("warnings", ())); all_records.append(FormRecord(**payload))
    write_csv_exports(all_records, output_dir)
    summary = BatchSummary(len(paths), sum(r.status == "succeeded" for r in records), sum(r.status == "review_required" for r in records), len(errors), skipped, tuple(str(records_dir / f"{r.source_sha256}.json") for r in records), tuple(errors))
    _write_json(output_dir / "batch-summary.json", {**asdict(summary), "exit_code": summary.exit_code})
    return summary
