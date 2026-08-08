"""Deterministic attendee-row occupancy and form-rule suppression."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .checkbox_detection import difference_mask
from .template import Region, TemplateDefinition


@dataclass(frozen=True)
class CellScore:
    name: str
    difference_ratio: float
    added_ink_ratio: float


@dataclass(frozen=True)
class RowScore:
    row: int
    unit_id: CellScore
    print_name: CellScore
    populated: bool


def detect_populated_rows(master: Image.Image, completed: Image.Image,
                          template: TemplateDefinition, threshold: float = 0.030,
                          difference_threshold: float = 0.060,
                          fallback_added_ink_floor: float = 0.005
                          ) -> tuple[RowScore, ...]:
    """Score rows using only unit ID and print-name cells; signatures are ignored."""
    if master.size != completed.size:
        raise ValueError("master and completed pages must have identical dimensions")
    by_row: dict[int, dict[str, CellScore]] = {}
    for region in template.regions:
        if region.kind != "attendee_cell":
            continue
        column = region.name.rsplit(".", 1)[-1]
        if column not in {"unit_id", "print_name"}:
            continue
        box = region.pixel_box(*master.size)
        master_crop, completed_crop = master.crop(box), completed.crop(box)
        ratio = float(difference_mask(master_crop, completed_crop).mean())
        added = float((np.asarray(completed_crop) < 190).mean() -
                      (np.asarray(master_crop) < 190).mean())
        by_row.setdefault(int(region.metadata["row"]), {})[column] = CellScore(region.name, ratio, added)
    results = []
    for row in sorted(by_row):
        cells = by_row[row]
        if set(cells) != {"unit_id", "print_name"}:
            raise ValueError(f"attendee row {row} lacks unit_id or print_name")
        max_added = max(cells["unit_id"].added_ink_ratio,
                        cells["print_name"].added_ink_ratio)
        max_difference = max(cells["unit_id"].difference_ratio,
                             cells["print_name"].difference_ratio)
        results.append(RowScore(row, cells["unit_id"], cells["print_name"],
                                max_added >= threshold
                                or (max_difference >= difference_threshold
                                    and max_added >= fallback_added_ink_floor)))
    return tuple(results)


def suppress_printed_rules(master_crop: Image.Image, completed_crop: Image.Image,
                           tolerance: int = 2) -> Image.Image:
    """Remove master-printed rules/underlines while retaining new handwriting ink."""
    changed = difference_mask(master_crop, completed_crop, tolerance)
    # Close tiny gaps introduced where handwriting crosses a printed rule.
    restored = Image.fromarray(changed).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    return Image.fromarray(np.where(np.asarray(restored), 0, 255).astype(np.uint8), mode="L")


def draw_row_diagnostics(completed: Image.Image, template: TemplateDefinition,
                         scores: tuple[RowScore, ...], output: Path) -> None:
    canvas = completed.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    lookup = {score.row: score for score in scores}
    for region in template.regions:
        if region.kind != "attendee_cell" or region.name.endswith("signature"):
            continue
        row = int(region.metadata["row"])
        score = lookup[row]
        color = (0, 180, 50) if score.populated else (220, 40, 40)
        draw.rectangle(region.pixel_box(*canvas.size), outline=color, width=3)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
