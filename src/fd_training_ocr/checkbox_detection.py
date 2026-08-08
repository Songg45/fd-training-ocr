"""Localized blank-versus-completed option detection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .template import Region, TemplateDefinition


@dataclass(frozen=True)
class OptionScore:
    name: str
    changed_pixels: int
    inspected_pixels: int
    difference_ratio: float
    added_ink_ratio: float
    selected: bool


def _ink(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L")) < 190


def difference_mask(master_crop: Image.Image, completed_crop: Image.Image,
                    tolerance: int = 2) -> np.ndarray:
    """Return candidate ink not explained by nearby printed master ink."""
    if master_crop.size != completed_crop.size:
        raise ValueError("master and completed crops must have identical dimensions")
    master = _ink(master_crop)
    candidate = _ink(completed_crop)
    nearby_master = np.asarray(Image.fromarray(master).filter(
        ImageFilter.MaxFilter(tolerance * 2 + 1))) > 0
    return candidate & ~nearby_master


def score_option(master: Image.Image, completed: Image.Image, region: Region,
                 threshold: float = 0.0225) -> OptionScore:
    if region.kind != "option":
        raise ValueError(f"{region.name} is not an option region")
    box = region.pixel_box(*master.size)
    master_crop, completed_crop = master.crop(box), completed.crop(box)
    changed = difference_mask(master_crop, completed_crop)
    ratio = float(changed.mean())
    added = float(_ink(completed_crop).mean() - _ink(master_crop).mean())
    # Apparatus blanks sit among heavy printed labels and rules. Requiring a
    # localized difference as well as net added ink prevents a globally darker
    # scan from becoming a truck selection.
    selected = added >= threshold
    if region.name.startswith("truck."):
        selected = selected and ratio >= .045
    return OptionScore(region.name, int(changed.sum()), int(changed.size), ratio, added,
                       selected)


def detect_options(master: Image.Image, completed: Image.Image,
                   template: TemplateDefinition, threshold: float = 0.0225
                   ) -> tuple[OptionScore, ...]:
    if master.size != completed.size:
        raise ValueError("master and completed pages must have identical dimensions")
    scores = tuple(score_option(master, completed, region, threshold)
                   for region in template.regions if region.kind == "option")
    for prefix in ("training_type.", "facility."):
        group = [score for score in scores if score.name.startswith(prefix)]
        if not group or any(score.selected for score in group):
            continue
        ranked = sorted(group, key=lambda score: score.added_ink_ratio, reverse=True)
        best = ranked[0]
        runner_up = ranked[1].added_ink_ratio if len(ranked) > 1 else float("-inf")
        if (best.difference_ratio >= .060 and best.added_ink_ratio >= .002
                and best.added_ink_ratio - runner_up >= .003):
            scores = tuple(replace(score, selected=True) if score.name == best.name else score
                           for score in scores)
    return scores


def draw_option_diagnostics(completed: Image.Image, template: TemplateDefinition,
                            scores: tuple[OptionScore, ...], output: Path) -> None:
    canvas = completed.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    lookup = {score.name: score for score in scores}
    for region in template.regions:
        if region.kind != "option":
            continue
        score = lookup[region.name]
        box = region.pixel_box(*canvas.size)
        color = (0, 180, 50) if score.selected else (220, 40, 40)
        draw.rectangle(box, outline=color, width=4)
        draw.text((box[0], max(0, box[1] - 18)),
                  f"{score.name} diff={score.difference_ratio:.3f} add={score.added_ink_ratio:.3f}", fill=color)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
