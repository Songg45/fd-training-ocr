"""Deterministic preparation of a scanned blank form template."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps


@dataclass(frozen=True)
class PreparationResult:
    cleaned_path: Path
    diagnostics_path: Path
    angle_degrees: float
    crop_box: tuple[int, int, int, int]


def estimate_skew(gray: Image.Image, limit: float = 5.0, step: float = 0.25) -> float:
    """Estimate small skew by maximizing horizontal ink projection variance."""
    sample = gray.copy()
    sample.thumbnail((1000, 1000))
    candidates = np.arange(-limit, limit + step / 2, step)
    scores = []
    for angle in candidates:
        rotated = sample.rotate(float(angle), Image.Resampling.BILINEAR, expand=False, fillcolor=255)
        ink = np.asarray(rotated, dtype=np.uint8) < 180
        projection = ink.sum(axis=1)
        scores.append(float(np.var(projection)))
    return float(candidates[int(np.argmax(scores))])


def crop_page(gray: Image.Image, margin: int = 8) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Crop dark scanner margins while retaining the whole light paper rectangle."""
    array = np.asarray(gray.filter(ImageFilter.GaussianBlur(3)))
    light = array > 180
    rows = np.flatnonzero(light.mean(axis=1) > 0.55)
    columns = np.flatnonzero(light.mean(axis=0) > 0.55)
    if not rows.size or not columns.size:
        return gray, (0, 0, gray.width, gray.height)
    box = (max(0, int(columns[0]) - margin), max(0, int(rows[0]) - margin),
           min(gray.width, int(columns[-1]) + margin + 1),
           min(gray.height, int(rows[-1]) + margin + 1))
    return gray.crop(box), box


def normalize_and_despeckle(gray: Image.Image, speck_size: int = 3) -> Image.Image:
    """Flatten tone, increase contrast, and remove isolated scanner pinpricks."""
    normalized = ImageOps.autocontrast(gray, cutoff=(1, 1))
    normalized = ImageEnhance.Contrast(normalized).enhance(1.25)
    # A size-three median removes isolated single-pixel noise while preserving form rules.
    if speck_size >= 3:
        normalized = normalized.filter(ImageFilter.MedianFilter(size=speck_size))
    return normalized.point(lambda value: 255 if value >= 205 else (0 if value <= 80 else value))


def mask_normalized_rectangles(image: Image.Image,
                               masks: list[tuple[float, float, float, float]]) -> Image.Image:
    result = image.copy()
    draw = ImageDraw.Draw(result)
    for x, y, width, height in masks:
        if min(x, y, width, height) < 0 or x + width > 1 or y + height > 1:
            raise ValueError("mask rectangles must use normalized coordinates within the page")
        draw.rectangle((round(x * image.width), round(y * image.height),
                        round((x + width) * image.width), round((y + height) * image.height)), fill=255)
    return result


def remove_stray_marks(image: Image.Image,
                       regions: list[tuple[float, float, float, float]]) -> Image.Image:
    """Remove diagonal pen marks in local regions while preserving long form rules."""
    result = np.asarray(image).copy()
    page_height, page_width = result.shape
    for x, y, width, height in regions:
        if min(x, y, width, height) < 0 or x + width > 1 or y + height > 1:
            raise ValueError("stray-mark regions must use normalized coordinates within the page")
        x0, y0 = round(x * page_width), round(y * page_height)
        x1, y1 = round((x + width) * page_width), round((y + height) * page_height)
        region = result[y0:y1, x0:x1]
        # Include faint anti-aliased remnants as well as solid ink.
        ink = region < 250
        # Printed table rules span most of this deliberately tight region.
        horizontal_rows = ink.mean(axis=1) >= 0.60
        keep = np.broadcast_to(horizontal_rows[:, None], ink.shape)
        region[ink & ~keep] = 255
    return Image.fromarray(result)


def prepare_template(rendered_page: Path, output_dir: Path, *, rotate_degrees: int = 180,
                     masks: list[tuple[float, float, float, float]] | None = None,
                     stray_mark_regions: list[tuple[float, float, float, float]] | None = None) -> PreparationResult:
    try:
        original = Image.open(rendered_page).convert("L")
    except OSError as exc:
        raise ValueError(f"Could not read rendered page: {rendered_page}") from exc
    rotated = original.rotate(rotate_degrees, Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    correction = estimate_skew(rotated)
    deskewed = rotated.rotate(correction, Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    cropped, crop_box = crop_page(deskewed)
    cleaned = normalize_and_despeckle(cropped)
    cleaned = remove_stray_marks(cleaned, stray_mark_regions or [])
    cleaned = mask_normalized_rectangles(cleaned, masks or [])
    output_dir.mkdir(parents=True, exist_ok=True)
    cleaned_path = output_dir / "cleaned-master.png"
    diagnostics_path = output_dir / "preparation-diagnostics.png"
    cleaned.save(cleaned_path)
    panels = []
    for label, panel in (("ORIGINAL", original), ("ROTATED + DESKEWED", deskewed),
                         ("CROPPED", cropped), ("CLEANED", cleaned)):
        preview = ImageOps.contain(panel.convert("RGB"), (900, 1100), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (900, 1100), "white")
        canvas.paste(preview, ((900 - preview.width) // 2, (1100 - preview.height) // 2))
        ImageDraw.Draw(canvas).text((24, 24), label, fill="red", stroke_width=1, stroke_fill="white")
        panels.append(canvas)
    diagnostics = Image.new("RGB", (1800, 2200), "white")
    for index, panel in enumerate(panels):
        diagnostics.paste(panel, ((index % 2) * 900, (index // 2) * 1100))
    diagnostics.save(diagnostics_path)
    return PreparationResult(cleaned_path, diagnostics_path, correction, crop_box)
