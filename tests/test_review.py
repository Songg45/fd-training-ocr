import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from fd_training_ocr.review import build_review_artifacts, save_corrections
from fd_training_ocr.template import load_template
from fd_training_ocr.validation import FieldAssessment, ValidationReport


ROOT = Path(__file__).parents[1]


class ReviewTests(unittest.TestCase):
    def test_review_artifacts_never_contain_signature_crop_or_field(self):
        template = load_template(ROOT / "templates/pilot_fd_training_sign_in/v1/template.json")
        report = ValidationReport((FieldAssessment("date", "12/17/25", "2025-12-17", .9, (), (), False),), (), None, False)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp); html = build_review_artifacts(Image.new("L", template.master_size, "white"), template, report, target)
            names = [p.name for p in target.rglob("*")]
            self.assertFalse(any("signature" in name.casefold() for name in names))
            self.assertNotIn("signature", html.read_text().casefold())

    def test_displayed_full_page_masks_signature_regions(self):
        template = load_template(ROOT / "templates/pilot_fd_training_sign_in/v1/template.json")
        image = Image.new("L", template.master_size, "white")
        signature = next(r for r in template.regions if r.kind == "signature")
        from PIL import ImageDraw
        ImageDraw.Draw(image).rectangle(signature.pixel_box(*image.size), fill="black")
        report = ValidationReport((), (), None, False)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp); build_review_artifacts(image, template, report, target)
            displayed = Image.open(target / "aligned-page.png").convert("L")
            self.assertEqual(displayed.crop(signature.pixel_box(*displayed.size)).getextrema(), (255, 255))

    def test_corrections_are_separate_and_timestamped(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "corrections.json"
            save_corrections(path, {"date": ("2025-12-17", "corrected")})
            record = json.loads(path.read_text())["corrections"][0]
            self.assertEqual(record["status"], "corrected"); self.assertIn("reviewed_at", record)
