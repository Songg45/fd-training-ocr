"""Field-type evaluation; intentionally never collapses results to one OCR score."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class FieldMetric:
    field_type: str
    correct: int
    total: int
    exact_match: float


def field_type(name: str) -> str:
    leaf = name.rsplit(".", 1)[-1]
    if leaf in {"start_time", "end_time"}: return "time"
    if leaf == "total_hours": return "hours"
    return leaf


def evaluate(predictions: Iterable[Mapping[str, str | None]], truth: Iterable[Mapping[str, str | None]]) -> tuple[FieldMetric, ...]:
    predicted = {(str(x["record_id"]), str(x["field_name"])): x.get("value") for x in predictions}
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item in truth:
        name = str(item["field_name"]); kind = field_type(name); key = (str(item["record_id"]), name)
        counts[kind][1] += 1
        if predicted.get(key) == item.get("value"): counts[kind][0] += 1
    return tuple(FieldMetric(kind, correct, total, correct / total if total else 0.0) for kind, (correct, total) in sorted(counts.items()))


def write_report(path: Path, metrics: Iterable[FieldMetric]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"metrics_by_field_type": [asdict(x) for x in metrics]}, indent=2), encoding="utf-8")
