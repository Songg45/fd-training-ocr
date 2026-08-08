import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from fd_training_ocr.pdf_render import PdfRenderError, render_pdf


class PdfRenderTests(unittest.TestCase):
    def test_rejects_low_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            with self.assertRaisesRegex(ValueError, "at least 300"):
                render_pdf(source, Path(directory) / "out", dpi=299, pdftoppm=source)

    def test_invokes_renderer_without_modifying_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.pdf"
            source.write_bytes(b"%PDF synthetic")
            renderer = root / "pdftoppm.exe"
            renderer.write_bytes(b"fixture")
            before = source.read_bytes()
            def fake_run(command, **_kwargs):
                Path(command[-1] + ".png").write_bytes(b"png")
                return type("Result", (), {"returncode": 0, "stderr": ""})()
            with patch("fd_training_ocr.pdf_render.subprocess.run", side_effect=fake_run):
                pages = render_pdf(source, root / "out", pdftoppm=renderer)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(pages[0].page_number, 1)

    def test_missing_renderer_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.pdf"
            source.write_bytes(b"%PDF synthetic")
            with self.assertRaises(PdfRenderError):
                render_pdf(source, Path(directory) / "out", pdftoppm=Path(directory) / "missing")


if __name__ == "__main__":
    unittest.main()
