import tempfile
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from fd_training_ocr.preprocessing import estimate_skew, mask_normalized_rectangles, normalize_and_despeckle, prepare_template, remove_stray_marks


class PreprocessingTests(unittest.TestCase):
    def test_estimates_synthetic_horizontal_skew(self) -> None:
        image = Image.new("L", (600, 800), 255)
        draw = ImageDraw.Draw(image)
        for y in range(120, 700, 90):
            draw.line((50, y, 550, y), fill=0, width=3)
        skewed = image.rotate(3.0, Image.Resampling.BICUBIC, fillcolor=255)
        self.assertAlmostEqual(abs(estimate_skew(skewed)), 3.0, delta=0.5)

    def test_despeckle_preserves_rules_and_removes_dot(self) -> None:
        image = Image.new("L", (300, 200), 245)
        draw = ImageDraw.Draw(image)
        draw.line((20, 100, 280, 100), fill=20, width=3)
        image.putpixel((20, 20), 0)
        cleaned = normalize_and_despeckle(image)
        self.assertEqual(cleaned.getpixel((20, 20)), 255)
        self.assertLess(cleaned.getpixel((150, 100)), 128)

    def test_mask_uses_normalized_coordinates(self) -> None:
        image = Image.new("L", (200, 100), 0)
        masked = mask_normalized_rectangles(image, [(0.5, 0.5, 0.25, 0.25)])
        self.assertEqual(masked.getpixel((120, 60)), 255)
        self.assertEqual(masked.getpixel((10, 10)), 0)

    def test_stray_mark_removal_preserves_long_rule(self) -> None:
        image = Image.new("L", (200, 100), 255)
        draw = ImageDraw.Draw(image)
        draw.line((20, 50, 180, 50), fill=0, width=2)
        draw.line((90, 30, 110, 70), fill=0, width=2)
        cleaned = remove_stray_marks(image, [(0.35, 0.2, 0.3, 0.6)])
        self.assertLess(cleaned.getpixel((75, 50)), 128)
        self.assertEqual(cleaned.getpixel((100, 30)), 255)

    def test_prepare_writes_cleaned_and_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = Image.new("L", (500, 700), 255)
            draw = ImageDraw.Draw(page)
            draw.rectangle((30, 30, 470, 670), outline=0, width=3)
            draw.line((60, 200, 440, 200), fill=0, width=2)
            source = root / "synthetic.png"
            page.save(source)
            result = prepare_template(source, root / "out", rotate_degrees=0)
            self.assertTrue(result.cleaned_path.is_file())
            self.assertTrue(result.diagnostics_path.is_file())


if __name__ == "__main__":
    unittest.main()
