"""Qt-independent controller helpers for the local desktop GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping

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


def display_value(field: Mapping[str, Any]) -> Any:
    for key in ("reviewed_value", "resolved_value", "normalized", "raw"):
        if field.get(key) is not None:
            return field[key]
    return None


def structured_rows(record: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    rows = []
    for name, field in record.get("fields", {}).items():
        warnings = field.get("warnings", ())
        rows.append((str(name), "" if display_value(field) is None else str(display_value(field)),
                     "; ".join(str(item) for item in warnings)))
    return tuple(rows)


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
