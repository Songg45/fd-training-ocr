import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from fd_training_ocr.export import run_batch
from fd_training_ocr.pipeline import load_optional_roster, processor_factory
from fd_training_ocr.recognition import MockRecognitionProvider, RecognitionError
from fd_training_ocr.template import Region, TemplateDefinition


class PipelineTests(unittest.TestCase):
    def test_missing_roster_is_generic_and_requires_downstream_review(self):
        with tempfile.TemporaryDirectory() as temp:
            roster, warning = load_optional_roster(Path(temp).resolve() / "missing-private-roster.json", Path.cwd())
            self.assertIsNone(roster)
            self.assertEqual(warning, "configured roster unavailable or invalid; roster matching was not applied")
            self.assertNotIn("missing-private-roster", warning)

    def test_failed_blank_attendee_cell_exports_rest_of_document_for_review(self):
        class BlankCellProvider(MockRecognitionProvider):
            def recognize(self, request):
                if request.field_name == "attendee.02.unit_id":
                    self.requests.append(request)
                    raise RecognitionError("HTTP 500: prediction aborted, token repeat limit reached")
                return super().recognize(request)
        regions = [Region("date", "text", (.1, .1, .2, .1), {}),
                   Region("description", "text", (.1, .75, .7, .1), {})]
        for row, top in ((1, .3), (2, .5)):
            for name, kind, left, width in (("unit_id", "attendee_cell", .1, .2),
                                           ("print_name", "attendee_cell", .3, .3),
                                           ("signature", "signature", .6, .3)):
                regions.append(Region(f"attendee.{row:02d}.{name}", kind,
                                      (left, top, width, .1), {"row": row}))
        definition = TemplateDefinition("test", "v1", "normalized_xywh", (1000, 1000),
                                        frozenset({"signature"}), {}, tuple(regions))
        responses = {name: {"value": value, "confidence": .99, "alternatives": []}
                     for name, value in (("date", "01/02/26"), ("description", "Ladder training"),
                        ("attendee.01.unit_id", "4354"), ("attendee.01.print_name", "Example Person"))}
        provider = BlankCellProvider(responses)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.pdf"
            source.write_bytes(b"synthetic source, rendering mocked")
            master, aligned, rendered = [root / name for name in ("master.png", "aligned.png", "rendered.png")]
            for path in (master, aligned, rendered):
                Image.new("L", (1000, 1000), 255).save(path)
            with patch.multiple("fd_training_ocr.pipeline",
                    load_template=Mock(return_value=definition),
                    render_pdf=Mock(return_value=[SimpleNamespace(path=rendered)]),
                    align_image=Mock(return_value=SimpleNamespace(
                        aligned_path=aligned, overlay_path=root / "overlay.png")),
                    detect_options=Mock(return_value=()), detect_populated_rows=Mock(return_value=()),
                    contiguous_populated_rows=Mock(return_value=(1, 2))):
                processor = processor_factory(work_dir=root / "work", master_path=master,
                    template_path=root / "template.json", provider=provider, repository_root=root)
                summary = run_batch(source, root / "output", processor)
            self.assertEqual((summary.discovered, summary.succeeded, summary.review_required,
                              summary.failed), (1, 0, 1, 0))
            record = json.loads(Path(summary.records[0]).read_text(encoding="utf-8"))
            self.assertEqual(record["fields"]["description"]["normalized"], "Ladder training")
            self.assertEqual(record["fields"]["date"]["normalized"], "2026-01-02")
            self.assertEqual(record["attendees"][0]["unit_id"], "4354")
            self.assertIsNone(record["attendees"][1]["unit_id"])
            failed = record["fields"]["attendee.02.unit_id"]
            self.assertIsNone(failed["raw"])
            self.assertTrue(failed["second_pass_review_required"])
            self.assertTrue(any("token repeat limit" in warning for warning in failed["warnings"]))
            self.assertEqual(len(failed["stage_1"]["retry_attempts"]), 3)
            self.assertEqual(failed["stage_2"]["stage"], 2)
            self.assertEqual(len(failed["stage_3_attempts"]), 1)
            self.assertEqual(source.read_bytes(), b"synthetic source, rendering mocked")
