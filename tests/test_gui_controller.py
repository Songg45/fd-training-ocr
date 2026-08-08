import json
from pathlib import Path
import tempfile
import unittest

from fd_training_ocr.config import AppConfig
from fd_training_ocr.gui_controller import (GuiPaths, build_processor, display_value,
                                             export_record, structured_rows, validate_pdf)


class GuiControllerTests(unittest.TestCase):
    def test_pdf_validation_rejects_non_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "form.txt"; path.write_text("x")
            with self.assertRaisesRegex(ValueError, "readable PDF"):
                validate_pdf(path)

    def test_display_value_uses_review_resolution_order(self):
        self.assertEqual(display_value({"raw":"a", "normalized":"b", "resolved_value":"c", "reviewed_value":"d"}), "d")
        self.assertEqual(display_value({"raw":"a", "normalized":"b", "resolved_value":None}), "b")

    def test_structured_rows_include_field_warnings(self):
        rows = structured_rows({"fields":{"date":{"normalized":"12/17/25", "warnings":["check date"]}}})
        self.assertEqual(rows, (("date", "12/17/25", "check date"),))

    def test_export_writes_exact_json_record(self):
        record = {"status":"review_required", "warnings":["check"]}
        with tempfile.TemporaryDirectory() as directory:
            destination = export_record(record, Path(directory) / "result.json")
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), record)

    def test_processor_routes_two_models(self):
        calls = []
        def providers(model, endpoint, timeout): calls.append(model); return object()
        import fd_training_ocr.gui_controller as controller
        original = controller.processor_factory
        try:
            controller.processor_factory = lambda **kwargs: kwargs
            result = build_processor(AppConfig(), GuiPaths(Path("master"), Path("template"), Path("output")), providers)
        finally:
            controller.processor_factory = original
        self.assertEqual(calls, ["qwen2.5vl:7b", "qwen3-vl:8b-instruct"])
        self.assertIsNot(result["provider"], result["stage3_provider"])


if __name__ == "__main__": unittest.main()
