"""Command-line shell for the checkpointed extraction pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from .config import load_config
from .alignment import AlignmentError, align_image, format_metrics
from .pdf_render import PdfRenderError, render_pdf
from .preprocessing import prepare_template
from .template import TemplateError, load_template


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
    process = subcommands.add_parser("process", help="Process one PDF or directory (future checkpoint)")
    process.add_argument("source", type=Path)
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
        print(
            "End-to-end processing is not implemented through Checkpoint 3; the source was not read or modified.",
            file=sys.stderr,
        )
        return 2
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
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
