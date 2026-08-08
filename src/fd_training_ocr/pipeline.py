"""Single-form orchestration used by the Checkpoint 7 batch boundary."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Callable

from PIL import Image

from .alignment import AlignmentError, align_image, draw_region_overlay
from .checkbox_detection import detect_options
from .export import FormRecord
from .pdf_render import render_pdf
from .recognition import RecognitionProvider, recognize_fields
from .review import mask_signature_column
from .table_extraction import detect_populated_rows
from .template import load_template
from .validation import RosterError, ValidationPolicy, load_roster, validate


def load_optional_roster(path: Path | None, repository_root: Path):
    """Degrade safely to review-required without revealing roster contents/errors."""
    if path is None: return None, None
    try: return load_roster(path, repository_root), None
    except RosterError: return None, "configured roster unavailable or invalid; roster matching was not applied"


def processor_factory(*, work_dir: Path, master_path: Path, template_path: Path,
                      provider: RecognitionProvider, repository_root: Path,
                      roster_path: Path | None = None, pdftoppm: Path | None = None,
                      policy: ValidationPolicy = ValidationPolicy()) -> Callable[[Path, str], FormRecord]:
    definition = load_template(template_path)
    master = Image.open(master_path).convert("L")
    roster, roster_warning = load_optional_roster(roster_path, repository_root)

    def process(path: Path, digest: str) -> FormRecord:
        artifact_dir = work_dir / digest
        rendered = render_pdf(path, artifact_dir / "rendered", pdftoppm=pdftoppm)
        if len(rendered) != 1: raise ValueError("only one-page training forms are supported")
        try:
            aligned = align_image(rendered[0].path, master_path, artifact_dir / "alignment", definition)
        except AlignmentError:
            for name in ("aligned.png", "regions-overlay.png"):
                (artifact_dir / "alignment" / name).unlink(missing_ok=True)
            raise
        finally:
            for rendered_page in rendered:
                rendered_page.path.unlink(missing_ok=True)
        page = Image.open(aligned.aligned_path).convert("L")
        # Derived whole-page artifacts are retained only after signature boxes are masked.
        # The source PDF remains unchanged and is the sole authoritative original.
        page = mask_signature_column(page, definition)
        page.save(aligned.aligned_path)
        safe_regions = tuple(r for r in definition.regions if r.kind != "signature" and not r.name.endswith(".signature"))
        draw_region_overlay(page, safe_regions, aligned.overlay_path)
        option_scores = detect_options(master, page, definition)
        selected = [x.name for x in option_scores if x.selected]
        populated = [x.row for x in detect_populated_rows(master, page, definition) if x.populated]
        recognized = recognize_fields(page, master, definition, provider, populated)
        apparatus_map = {"truck.engine54":"Engine 54", "truck.tanker54":"Tanker 54", "truck.engine254":"Engine 254", "truck.brush54":"Brush 54", "truck.tanker854":"Tanker 854"}
        apparatus = [apparatus_map[x] for x in selected if x in apparatus_map]
        report = validate(recognized, roster=roster, selected_apparatus=apparatus, policy=policy)
        fields = {}
        by_assessment = {x.field_name: x for x in report.fields}
        for item in recognized:
            assessment = by_assessment[item.field_name]
            fields[item.field_name] = {"raw": item.value, "normalized": assessment.normalized,
                "reviewed_value": None, "confidence": item.confidence, "alternatives": list(item.alternatives),
                "provider": item.provider, "model": item.model, "source_region": list(item.source_region),
                "warnings": list(assessment.warnings), "review": {"status": "unreviewed", "reviewed_at": None}}
        attendees = []
        for row in populated:
            prefix = f"attendee.{row:02d}"
            attendees.append({"row": row, "unit_id": fields.get(prefix + ".unit_id", {}).get("normalized"), "print_name": fields.get(prefix + ".print_name", {}).get("normalized")})
        warnings = list(report.warnings)
        if roster_warning: warnings.append(roster_warning)
        review_required = report.review_required or bool(roster_warning)
        event = {"total_hours_calculated": report.total_hours_calculated,
            "training_types": [x.removeprefix("training_type.") for x in selected if x.startswith("training_type.")],
            "facilities": [x.removeprefix("facility.") for x in selected if x.startswith("facility.")],
            "trucks_used": apparatus}
        return FormRecord(path.name, digest, 1, definition.form_type, definition.form_version,
            "review_required" if review_required else "succeeded", fields, event, tuple(attendees), tuple(warnings),
            {"status": "pending" if review_required else "not_required", "corrections_applied": False, "reviewed_at": None})
    return process
