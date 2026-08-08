"""Generate a local, self-contained review page without signature material."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageDraw

from .template import TemplateDefinition
from .validation import ValidationReport


@dataclass(frozen=True)
class Correction:
    field_name: str
    machine_value: str | None
    reviewed_value: str | None
    status: str
    reviewed_at: str


def save_corrections(path: Path, corrections: Mapping[str, tuple[str | None, str]]) -> None:
    allowed = {"approved", "corrected", "unresolved"}; records = []
    stamp = datetime.now(timezone.utc).isoformat()
    for field, (value, status) in corrections.items():
        if status not in allowed: raise ValueError(f"invalid review status: {status}")
        records.append({"field_name": field, "reviewed_value": value, "status": status, "reviewed_at": stamp})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "corrections": records}, indent=2), encoding="utf-8")


def build_review_artifacts(aligned_page: Image.Image, template: TemplateDefinition,
                           report: ValidationReport, output_dir: Path) -> Path:
    """Write page/crops/HTML locally; signatures are excluded by construction."""
    output_dir.mkdir(parents=True, exist_ok=True); crops = output_dir / "field-crops"; crops.mkdir(exist_ok=True)
    page = aligned_page.convert("RGB")
    page_draw = ImageDraw.Draw(page)
    for region in template.regions:
        if region.kind == "signature" or region.name.endswith(".signature"):
            page_draw.rectangle(region.pixel_box(*page.size), fill="white")
    overlay = page.copy(); draw = ImageDraw.Draw(overlay)
    safe_regions = {r.name: r for r in template.regions if r.kind != "signature" and not r.name.endswith(".signature")}
    overlay.save(output_dir / "aligned-page.png")
    rows = []
    for assessment in report.fields:
        region = safe_regions.get(assessment.field_name)
        crop_name = None
        if region is not None:
            box = region.pixel_box(*page.size); draw.rectangle(box, outline="orange", width=3)
            crop_name = assessment.field_name.replace(".", "_") + ".png"
            page.crop(box).save(crops / crop_name)
        rows.append(f"<section><h2>{escape(assessment.field_name)}</h2>" +
                    (f'<img src="field-crops/{escape(crop_name)}" alt="field crop">' if crop_name else "") +
                    f"<p>Written: {escape(str(assessment.raw))}<br>Proposed: {escape(str(assessment.normalized))}<br>Confidence: {assessment.confidence:.2f}<br>Alternatives: {escape(', '.join(assessment.alternatives) or 'none')}<br>Warnings: {escape('; '.join(assessment.warnings) or 'none')}</p>"+
                    f'<input data-field="{escape(assessment.field_name)}" value="{escape(assessment.normalized or "")}"><select data-status="{escape(assessment.field_name)}"><option>approved</option><option>corrected</option><option>unresolved</option></select></section>')
    overlay.save(output_dir / "review-overlay.png")
    payload = json.dumps({"review_required": report.review_required, "warnings": report.warnings, "total_hours_calculated": report.total_hours_calculated}, ensure_ascii=True).replace("<", "\\u003c")
    html = "<!doctype html><meta charset=utf-8><title>FD OCR local review</title><style>body{font:16px sans-serif;max-width:1100px;margin:auto}img{max-width:100%}section{border-top:1px solid #ccc;padding:1em}input{min-width:25em}</style><h1>Local form review</h1><img src=review-overlay.png alt='aligned page with reviewed fields'><button id=save>Download separate corrections JSON</button>" + "".join(rows) + f"<script>const report={payload};document.querySelector('#save').onclick=()=>{{const at=new Date().toISOString(),corrections=[...document.querySelectorAll('[data-field]')].map(x=>({{field_name:x.dataset.field,reviewed_value:x.value,status:document.querySelector('[data-status=\"'+x.dataset.field+'\"]').value,reviewed_at:at}}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({{schema_version:1,corrections}},null,2)],{{type:'application/json'}}));a.download='corrections.json';a.click()}};</script>"
    target = output_dir / "review.html"; target.write_text(html, encoding="utf-8")
    return target
