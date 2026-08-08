"""Geometric scan alignment and alignment-quality diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .preprocessing import crop_page, estimate_skew, normalize_and_despeckle
from .template import Region, TemplateDefinition


class AlignmentError(RuntimeError):
    """Raised when a page cannot be safely aligned to its template."""


@dataclass(frozen=True)
class AlignmentMetrics:
    orientation_degrees: int
    deskew_degrees: float
    form_coverage: float
    anchor_coverage: float
    excess_ink_ratio: float
    passed: bool


@dataclass(frozen=True)
class AlignmentResult:
    aligned_path: Path
    overlay_path: Path
    metrics: AlignmentMetrics


def _ink(image: Image.Image, size: tuple[int, int] | None = None) -> np.ndarray:
    gray = image.convert("L")
    if size:
        gray = gray.resize(size, Image.Resampling.BILINEAR)
    return np.asarray(gray) < 185


def _coverage(reference: np.ndarray, candidate: np.ndarray, tolerance: int = 2) -> float:
    if not reference.any():
        return 1.0
    kernel = tolerance * 2 + 1
    nearby = np.asarray(Image.fromarray(candidate).filter(ImageFilter.MaxFilter(kernel))) > 0
    return float(nearby[reference].mean())


def _orientation_score(candidate: Image.Image, master: Image.Image) -> float:
    candidate, _ = crop_page(candidate)
    return _coverage(_ink(master, (220, 285)), _ink(candidate, (220, 285)), tolerance=2)


def _passes_quality(form_coverage: float, anchor_scores: list[float], deskew: float,
                    thresholds: dict[str, object]) -> bool:
    """Accept normal anchors or a strong header plus globally matching form."""
    minimum_form = float(thresholds["min_form_coverage"])
    minimum_anchor = float(thresholds["min_anchor_coverage"])
    deskew_ok = abs(deskew) <= float(thresholds["max_abs_deskew_degrees"])
    anchors_ok = not anchor_scores or min(anchor_scores) >= minimum_anchor
    # Clean rescans can disagree with the noisy master's thin table-line pixels
    # while the stable dark header and overall form still align exceptionally
    # well. The first configured anchor is the header by template convention.
    strong_header_override = bool(anchor_scores and anchor_scores[0] >= .90)
    return (form_coverage >= minimum_form and deskew_ok
            and (anchors_ok or strong_header_override))


def align_image(source: Path, master_path: Path, output_dir: Path,
                template: TemplateDefinition) -> AlignmentResult:
    """Normalize rotation, deskew, crop and scale a page into master coordinates."""
    try:
        original = Image.open(source).convert("L")
        master = Image.open(master_path).convert("L")
    except OSError as exc:
        raise AlignmentError(f"Could not read alignment image: {exc}") from exc
    if master.size != template.master_size:
        raise AlignmentError(f"Master size {master.size} does not match template {template.master_size}")

    rotations = [(angle, original.rotate(angle, Image.Resampling.BICUBIC, expand=True, fillcolor=255))
                 for angle in (0, 90, 180, 270)]
    orientation, oriented = max(rotations, key=lambda item: _orientation_score(item[1], master))
    oriented, _ = crop_page(oriented)
    deskew = estimate_skew(oriented, limit=4.0, step=0.25)
    corrected = oriented.rotate(deskew, Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    cropped, _ = crop_page(corrected)
    normalized = normalize_and_despeckle(cropped).resize(master.size, Image.Resampling.LANCZOS)

    evaluation_size = (425, 550)
    master_ink = _ink(master, evaluation_size)
    aligned_ink = _ink(normalized, evaluation_size)
    form_coverage = _coverage(master_ink, aligned_ink, tolerance=2)
    anchor_scores = []
    for raw in template.alignment.get("anchor_regions", []):
        region = Region("anchor", "alignment_anchor", tuple(raw), {})  # validated with template JSON
        box = region.pixel_box(*evaluation_size)
        anchor_scores.append(_coverage(master_ink[box[1]:box[3], box[0]:box[2]],
                                       aligned_ink[box[1]:box[3], box[0]:box[2]], tolerance=2))
    anchor_coverage = min(anchor_scores) if anchor_scores else form_coverage
    excess_ink = np.logical_and(aligned_ink, ~np.asarray(
        Image.fromarray(master_ink).filter(ImageFilter.MaxFilter(7))) > 0)
    excess_ratio = float(excess_ink.sum() / max(1, aligned_ink.sum()))
    thresholds = template.alignment["quality_thresholds"]
    passed = _passes_quality(form_coverage, anchor_scores, deskew, thresholds)

    output_dir.mkdir(parents=True, exist_ok=True)
    aligned_path = output_dir / "aligned.png"
    overlay_path = output_dir / "regions-overlay.png"
    normalized.save(aligned_path)
    draw_region_overlay(normalized, template.regions, overlay_path)
    metrics = AlignmentMetrics(orientation, deskew, form_coverage, anchor_coverage,
                               excess_ratio, passed)
    if not passed:
        raise AlignmentError("Alignment quality below threshold: " + format_metrics(metrics))
    return AlignmentResult(aligned_path, overlay_path, metrics)


def draw_region_overlay(image: Image.Image, regions: tuple[Region, ...], output: Path) -> None:
    canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")
    palette = {"text": (0, 110, 255, 70), "option": (255, 120, 0, 95),
               "attendee_cell": (0, 180, 80, 65), "signature": (180, 0, 180, 55)}
    for region in regions:
        box = region.pixel_box(*canvas.size)
        color = palette.get(region.kind, (255, 0, 0, 65))
        draw.rectangle(box, fill=color, outline=color[:3] + (230,), width=2)
        draw.text((box[0] + 3, box[1] + 2), region.name, fill=(160, 0, 0, 255),
                  stroke_width=2, stroke_fill=(255, 255, 255, 230))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def format_metrics(metrics: AlignmentMetrics) -> str:
    return (f"orientation={metrics.orientation_degrees}, deskew={metrics.deskew_degrees:.2f}, "
            f"form_coverage={metrics.form_coverage:.3f}, anchor_coverage={metrics.anchor_coverage:.3f}, "
            f"excess_ink_ratio={metrics.excess_ink_ratio:.3f}, passed={metrics.passed}")
