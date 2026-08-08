"""Versioned, normalized form-template definitions."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


class TemplateError(ValueError):
    """Raised when a template definition is incomplete or inconsistent."""


@dataclass(frozen=True)
class Region:
    name: str
    kind: str
    box: tuple[float, float, float, float]
    metadata: dict[str, Any]

    def pixel_box(self, width: int, height: int) -> tuple[int, int, int, int]:
        x, y, w, h = self.box
        return (round(x * width), round(y * height), round((x + w) * width),
                round((y + h) * height))


@dataclass(frozen=True)
class TemplateDefinition:
    form_type: str
    form_version: str
    coordinate_system: str
    master_size: tuple[int, int]
    excluded_region_kinds: frozenset[str]
    alignment: dict[str, Any]
    regions: tuple[Region, ...]

    def region(self, name: str) -> Region:
        try:
            return next(region for region in self.regions if region.name == name)
        except StopIteration as exc:
            raise KeyError(name) from exc


def _box(value: object, context: str) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4 or not all(isinstance(v, (int, float)) for v in value):
        raise TemplateError(f"{context} must be [x, y, width, height]")
    result = tuple(float(v) for v in value)
    x, y, width, height = result
    if min(result) < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise TemplateError(f"{context} must be positive normalized coordinates within the page")
    return result  # type: ignore[return-value]


def load_template(path: Path) -> TemplateDefinition:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateError(f"Could not load template {path}: {exc}") from exc
    required = {"schema_version", "form_type", "form_version", "coordinate_system",
                "master", "alignment", "regions"}
    missing = required - payload.keys()
    if missing:
        raise TemplateError(f"Template is missing keys: {sorted(missing)}")
    if payload["schema_version"] != 1 or payload["coordinate_system"] != "normalized_xywh":
        raise TemplateError("Only schema version 1 normalized_xywh templates are supported")
    master = payload["master"]
    size = master.get("size_pixels") if isinstance(master, dict) else None
    if not isinstance(size, list) or len(size) != 2 or not all(isinstance(v, int) and v > 0 for v in size):
        raise TemplateError("master.size_pixels must contain two positive integers")
    items = payload["regions"]
    if not isinstance(items, list) or not items:
        raise TemplateError("regions must be a non-empty list")
    regions: list[Region] = []
    names: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("kind"), str):
            raise TemplateError(f"regions[{index}] requires string name and kind")
        if item["name"] in names:
            raise TemplateError(f"Duplicate region name: {item['name']}")
        names.add(item["name"])
        regions.append(Region(item["name"], item["kind"], _box(item.get("box"), f"regions[{index}].box"),
                              {k: v for k, v in item.items() if k not in {"name", "kind", "box"}}))
    alignment = payload["alignment"]
    if not isinstance(alignment, dict) or not isinstance(alignment.get("quality_thresholds"), dict):
        raise TemplateError("alignment.quality_thresholds is required")
    processing = payload.get("processing", {})
    excluded = processing.get("excluded_region_kinds", []) if isinstance(processing, dict) else None
    if not isinstance(excluded, list) or not all(isinstance(kind, str) for kind in excluded):
        raise TemplateError("processing.excluded_region_kinds must be a list of strings")
    return TemplateDefinition(payload["form_type"], payload["form_version"],
                              payload["coordinate_system"], (size[0], size[1]),
                              frozenset(excluded), alignment,
                              tuple(regions))
