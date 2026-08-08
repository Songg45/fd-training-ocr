"""Command-line shell for the checkpointed extraction pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from PIL import Image

from .config import load_config
from .alignment import AlignmentError, align_image, format_metrics
from .pdf_render import PdfRenderError, render_pdf
from .preprocessing import prepare_template
from .template import TemplateError, load_template
from .checkbox_detection import detect_options, draw_option_diagnostics
from .table_extraction import detect_populated_rows, draw_row_diagnostics
from .export import run_batch
from .pipeline import processor_factory
from .recognition import MockRecognitionProvider, OllamaVisionProvider
from .validation import ValidationPolicy
from .evaluation import evaluate, write_report


def _normalized_rectangle(value: str) -> tuple[float, float, float, float]:
    try:
        rectangle = tuple(float(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected x,y,width,height") from exc
    if len(rectangle) != 4:
        raise argparse.ArgumentTypeError("expected x,y,width,height")
    return rectangle  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fd-training-ocr")
    parser.add_argument("--config", type=Path, help="Path to a local TOML configuration file")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("inspect-config", help="Print effective non-secret configuration")
    process = subcommands.add_parser("process", help="Process one PDF or a directory idempotently")
    process.add_argument("source", type=Path)
    process.add_argument("--master", type=Path, required=True)
    process.add_argument("--template", type=Path, required=True)
    process.add_argument("--output-dir", type=Path)
    process.add_argument("--pdftoppm", type=Path)
    process.add_argument("--provider", choices=("mock", "ollama"), default="mock")
    evaluation = subcommands.add_parser("evaluate", help="Report exact match separately by field type")
    evaluation.add_argument("predictions", type=Path, help="JSON array of record_id, field_name, value objects")
    evaluation.add_argument("truth", type=Path, help="JSON array of record_id, field_name, value objects")
    evaluation.add_argument("--output", type=Path, required=True)
    prepare = subcommands.add_parser("prepare-template", help="Render and clean page 1 of a blank form")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("--output-dir", type=Path)
    prepare.add_argument("--pdftoppm", type=Path)
    prepare.add_argument("--dpi", type=int, default=300)
    prepare.add_argument("--stray-mark", type=_normalized_rectangle, action="append", default=[],
                         help="Normalized x,y,width,height region containing a pen mark")
    align = subcommands.add_parser("align", help="Align page 1 and draw the versioned field map")
    align.add_argument("source", type=Path)
    align.add_argument("--master", type=Path, required=True)
    align.add_argument("--template", type=Path, required=True)
    align.add_argument("--output-dir", type=Path)
    align.add_argument("--pdftoppm", type=Path)
    align.add_argument("--dpi", type=int, default=300)
    detect = subcommands.add_parser("detect", help="Run deterministic detection on an aligned page")
    detect.add_argument("aligned", type=Path)
    detect.add_argument("--master", type=Path, required=True)
    detect.add_argument("--template", type=Path, required=True)
    detect.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if args.command == "inspect-config":
        print(json.dumps(config.as_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "process":
        destination = args.output_dir or config.output_dir / "batch"
        provider = (OllamaVisionProvider(config.ollama_model, config.ollama_endpoint, config.ollama_timeout_seconds)
                    if args.provider == "ollama" else MockRecognitionProvider())
        try:
            processor = processor_factory(work_dir=destination / "work", master_path=args.master,
                template_path=args.template, provider=provider, repository_root=Path.cwd(),
                roster_path=config.roster_path, pdftoppm=args.pdftoppm,
                recognition_crop_padding=config.recognition_crop_padding_pixels,
                recognition_max_attempts=config.recognition_max_attempts,
                policy=ValidationPolicy(apparatus=config.valid_apparatus, locations=config.valid_locations))
            summary = run_batch(args.source, destination, processor)
        except (OSError, ValueError, TemplateError) as exc:
            print(json.dumps({"status":"failed", "error_type":type(exc).__name__, "message":str(exc)}), file=sys.stderr)
            return 2
        print(json.dumps({"discovered": summary.discovered, "succeeded": summary.succeeded,
            "review_required": summary.review_required, "failed": summary.failed,
            "skipped_duplicate": summary.skipped_duplicate, "exit_code": summary.exit_code}, indent=2))
        return summary.exit_code
    if args.command == "evaluate":
        try:
            predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
            truth = json.loads(args.truth.read_text(encoding="utf-8"))
            if not isinstance(predictions, list) or not isinstance(truth, list): raise ValueError("evaluation inputs must be JSON arrays")
            metrics = evaluate(predictions, truth); write_report(args.output, metrics)
        except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
            print(json.dumps({"status":"failed", "error_type":type(exc).__name__, "message":str(exc)}), file=sys.stderr); return 2
        print(json.dumps({"metrics_by_field_type":[{"field_type":x.field_type,"correct":x.correct,"total":x.total,"exact_match":x.exact_match} for x in metrics]}, indent=2))
        return 0
    if args.command == "prepare-template":
        destination = args.output_dir or config.output_dir / "template-preparation"
        try:
            rendered = render_pdf(args.source, destination / "rendered", dpi=args.dpi,
                                  pdftoppm=args.pdftoppm)
            result = prepare_template(rendered[0].path, destination,
                                      stray_mark_regions=args.stray_mark)
        except (OSError, ValueError, PdfRenderError) as exc:
            print(f"Template preparation failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"cleaned_master": str(result.cleaned_path),
                          "diagnostics": str(result.diagnostics_path),
                          "deskew_angle_degrees": round(result.angle_degrees, 3),
                          "crop_box": result.crop_box}, indent=2))
        return 0
    if args.command == "align":
        destination = args.output_dir or config.output_dir / "alignment"
        try:
            definition = load_template(args.template)
            rendered = render_pdf(args.source, destination / "rendered", dpi=args.dpi,
                                  pdftoppm=args.pdftoppm)
            result = align_image(rendered[0].path, args.master, destination, definition)
        except (OSError, ValueError, PdfRenderError, TemplateError, AlignmentError) as exc:
            print(f"Alignment failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"aligned_page": str(result.aligned_path),
                          "regions_overlay": str(result.overlay_path),
                          "quality": format_metrics(result.metrics)}, indent=2))
        return 0
    if args.command == "detect":
        destination = args.output_dir or config.output_dir / "detection"
        try:
            definition = load_template(args.template)
            master = Image.open(args.master).convert("L")
            completed = Image.open(args.aligned).convert("L")
            options = detect_options(master, completed, definition)
            rows = detect_populated_rows(master, completed, definition)
            destination.mkdir(parents=True, exist_ok=True)
            draw_option_diagnostics(completed, definition, options, destination / "options.png")
            draw_row_diagnostics(completed, definition, rows, destination / "rows.png")
        except (OSError, ValueError, TemplateError) as exc:
            print(f"Detection failed: {exc}", file=sys.stderr)
            return 1
        payload = {
            "selected_options": [score.name for score in options if score.selected],
            "option_scores": {score.name: {"difference": round(score.difference_ratio, 5),
                "added_ink": round(score.added_ink_ratio, 5)} for score in options},
            "populated_rows": [score.row for score in rows if score.populated],
            "row_scores": {str(score.row): {"unit_id": round(score.unit_id.added_ink_ratio, 5),
                "print_name": round(score.print_name.added_ink_ratio, 5)} for score in rows},
            "diagnostics": [str(destination / "options.png"), str(destination / "rows.png")],
        }
        (destination / "scores.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
