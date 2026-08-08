"""Local PDF-to-image rendering through Poppler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess


class PdfRenderError(RuntimeError):
    """Raised when a PDF cannot be rendered safely."""


@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    path: Path


def find_pdftoppm(explicit: Path | None = None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if not candidate.is_file():
            raise PdfRenderError(f"pdftoppm was not found at: {candidate}")
        return candidate
    found = shutil.which("pdftoppm")
    if not found:
        raise PdfRenderError("pdftoppm is required; pass --pdftoppm or add bundled Poppler to PATH")
    return Path(found)


def render_pdf(source: Path, output_dir: Path, *, dpi: int = 300,
               pdftoppm: Path | None = None) -> list[RenderedPage]:
    """Render page 1 as PNG while leaving the source byte-for-byte untouched."""
    source = source.expanduser().resolve()
    if source.suffix.lower() != ".pdf" or not source.is_file():
        raise PdfRenderError(f"Input is not a readable PDF: {source}")
    if dpi < 300:
        raise ValueError("dpi must be at least 300")
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / "page"
    command = [str(find_pdftoppm(pdftoppm)), "-r", str(dpi), "-png", "-singlefile",
               "-f", "1", "-l", "1", str(source), str(prefix)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise PdfRenderError(f"PDF rendering failed: {completed.stderr.strip() or 'unknown Poppler error'}")
    page = prefix.with_suffix(".png")
    if not page.is_file():
        raise PdfRenderError("PDF renderer succeeded but produced no PNG")
    return [RenderedPage(1, page)]
