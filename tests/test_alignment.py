import json
import tempfile
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from fd_training_ocr.alignment import AlignmentError, _passes_quality, align_image
from fd_training_ocr.template import load_template


def synthetic_form() -> Image.Image:
    image = Image.new("L", (600, 800), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 50, 530, 130), fill=20)
    draw.rectangle((75, 270, 525, 620), outline=0, width=3)
    for y in range(300, 621, 32):
        draw.line((75, y, 525, y), fill=0, width=2)
    for x in (100, 210, 390):
        draw.line((x, 270, x, 620), fill=0, width=2)
    for y in (690, 725, 760):
        draw.line((80, y, 520, y), fill=0, width=2)
    return image


def write_template(path: Path, *, minimum: float = 0.60) -> None:
    path.write_text(json.dumps({
        "schema_version": 1, "form_type": "synthetic", "form_version": "v1",
        "coordinate_system": "normalized_xywh",
        "master": {"size_pixels": [600, 800]},
        "alignment": {
            "anchor_regions": [[0.1, 0.05, 0.8, 0.15], [0.1, 0.32, 0.8, 0.5]],
            "quality_thresholds": {"min_form_coverage": minimum,
                                   "min_anchor_coverage": minimum,
                                   "max_abs_deskew_degrees": 4.0}},
        "regions": [{"name": "field", "kind": "text", "box": [0.2, 0.2, 0.4, 0.1]}]
    }), encoding="utf-8")


class AlignmentTests(unittest.TestCase):
    def test_strong_header_and_passing_form_override_weak_table_anchor(self) -> None:
        thresholds = {"min_form_coverage": .70, "min_anchor_coverage": .67,
                      "max_abs_deskew_degrees": 4.0}
        self.assertTrue(_passes_quality(.706, [.973, .490], .50, thresholds))
        self.assertFalse(_passes_quality(.699, [.973, .490], .50, thresholds))
        self.assertFalse(_passes_quality(.706, [.89, .490], .50, thresholds))

    def test_rotated_skewed_form_aligns_and_writes_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master = synthetic_form()
            master_path = root / "master.png"
            master.save(master_path)
            transformed = master.rotate(180, expand=True, fillcolor=255)
            transformed = transformed.rotate(2.0, expand=False, fillcolor=255)
            canvas = Image.new("L", (transformed.width + 60, transformed.height + 80), 40)
            canvas.paste(transformed, (25, 35))
            source = root / "source.png"
            canvas.save(source)
            definition_path = root / "template.json"
            write_template(definition_path)

            result = align_image(source, master_path, root / "out", load_template(definition_path))

            self.assertTrue(result.metrics.passed)
            self.assertEqual(result.metrics.orientation_degrees, 180)
            self.assertGreater(result.metrics.form_coverage, 0.75)
            self.assertTrue(result.overlay_path.is_file())

    def test_unrelated_page_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.png"
            synthetic_form().save(master_path)
            unrelated = root / "unrelated.png"
            Image.new("L", (600, 800), 255).save(unrelated)
            definition_path = root / "template.json"
            write_template(definition_path, minimum=0.80)
            with self.assertRaisesRegex(AlignmentError, "below threshold"):
                align_image(unrelated, master_path, root / "out", load_template(definition_path))


if __name__ == "__main__":
    unittest.main()
