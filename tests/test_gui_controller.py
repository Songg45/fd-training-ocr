import json
from pathlib import Path
import tempfile
import unittest

from fd_training_ocr.config import AppConfig
from fd_training_ocr.gui_controller import (GuiPaths, apply_facilities_edit, apply_gui_edit,
                                             build_processor, display_value, effective_facilities,
                                             export_record, structured_rows, validate_pdf, validate_pdfs)


class GuiControllerTests(unittest.TestCase):
    def test_pdf_validation_rejects_non_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "form.txt"; path.write_text("x")
            with self.assertRaisesRegex(ValueError, "readable PDF"):
                validate_pdf(path)

    def test_pdf_selection_preserves_order_and_removes_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.pdf"; first.write_bytes(b"%PDF")
            second = Path(directory) / "second.pdf"; second.write_bytes(b"%PDF")
            self.assertEqual(validate_pdfs([first, second, first]),
                             (first.resolve(), second.resolve()))

    def test_display_value_uses_review_resolution_order(self):
        self.assertEqual(display_value({"raw":"a", "normalized":"b", "resolved_value":"c", "reviewed_value":"d"}), "d")
        self.assertEqual(display_value({"raw":"a", "normalized":"b", "resolved_value":None}), "b")

    def test_structured_rows_include_field_warnings(self):
        rows = structured_rows({"fields":{"date":{"normalized":"12/17/25", "warnings":["check date"],
                                                           "stage_3": "12/17/25",
                                                           "second_pass_review_required": False}},
                                "event":{"training_types":["new_driver"], "facilities":[],
                                         "trucks_used":["Brush 54"], "total_hours_calculated":1.0,
                                         "second_pass_call_count":1}})
        self.assertEqual(rows[0], ("date", "12/17/25", "check date", True))
        self.assertIn(("Training type", "New Driver", "", False), rows)
        self.assertIn(("Truck", "Brush 54", "", False), rows)
        self.assertIn(("Facilities", "None selected", "Double-click to select facilities", False), rows)
        self.assertIn(("Calculated duration", "1.0 hour", "", False), rows)
        self.assertIn(("Stage 3 resolution", "1 call; 1 field resolved; 0 unresolved", "", False), rows)

    def test_gui_edit_preserves_machine_values_and_records_review(self):
        record = {"fields":{"instructor":{"raw":"Nick Sleder", "resolved_value":"Nick Sledge",
                                                "reviewed_value":None}},
                  "review":{"status":"pending", "corrections_applied":False, "reviewed_at":None}}
        apply_gui_edit(record, "instructor", "Nicholas Sledge", "2026-08-08T12:00:00Z")
        self.assertEqual(record["fields"]["instructor"]["raw"], "Nick Sleder")
        self.assertEqual(record["fields"]["instructor"]["resolved_value"], "Nick Sledge")
        self.assertEqual(record["fields"]["instructor"]["reviewed_value"], "Nicholas Sledge")
        self.assertEqual(record["fields"]["instructor"]["review"],
                         {"status":"corrected", "reviewed_at":"2026-08-08T12:00:00Z"})
        self.assertTrue(record["review"]["corrections_applied"])

    def test_unresolved_stage3_suggestion_is_shown_in_warnings(self):
        rows = structured_rows({"fields":{"description":{"raw":"system", "normalized":"system",
            "warnings":["recognizer supplied alternatives"], "stage_3":"Fire/Res Safety",
            "second_pass_review_required":True}}, "event":{}})
        self.assertEqual(rows[0], ("description", "system",
            "recognizer supplied alternatives; Stage 3 suggests: Fire/Res Safety", True))

    def test_facilities_edit_preserves_machine_result(self):
        record = {"event":{"facilities":[]},
                  "review":{"corrections_applied":False, "reviewed_at":None}}
        apply_facilities_edit(record, ["classroom", "outside_area"], "2026-08-08T12:00:00Z")
        self.assertEqual(record["event"]["facilities"], [])
        self.assertEqual(record["event"]["reviewed_facilities"], ["classroom", "outside_area"])
        self.assertEqual(effective_facilities(record["event"]), ["classroom", "outside_area"])
        self.assertTrue(record["review"]["corrections_applied"])

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
