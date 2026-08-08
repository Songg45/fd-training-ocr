import json
from pathlib import Path
import tempfile
import unittest

from fd_training_ocr.config import AppConfig
from fd_training_ocr.gui_controller import (GuiPaths, apply_facilities_edit, apply_gui_edit,
                                             automatic_export, automatic_export_stem,
                                             build_processor, display_value, effective_facilities,
                                             discover_pdfs, export_record, index_after_removal, structured_rows,
                                             load_gui_state, save_gui_state, unprocessed_sources,
                                             roster_table_rows, save_roster_table,
                                             validate_pdf, validate_pdfs)


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

    def test_folder_discovery_is_non_recursive_case_insensitive_and_sorted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "z.PDF").write_bytes(b"%PDF")
            (root / "A.pdf").write_bytes(b"%PDF")
            (root / "notes.txt").write_text("ignore")
            nested = root / "nested"; nested.mkdir()
            (nested / "hidden.pdf").write_bytes(b"%PDF")
            self.assertEqual(discover_pdfs(root),
                             ((root / "A.pdf").resolve(), (root / "z.PDF").resolve()))

    def test_queue_index_after_removing_current_pdf(self):
        self.assertEqual(index_after_removal(1, 2), 1)
        self.assertEqual(index_after_removal(2, 2), 1)
        self.assertEqual(index_after_removal(0, 0), -1)

    def test_batch_selection_skips_completed_records_and_preserves_order(self):
        first, second, third = Path("1.pdf"), Path("2.pdf"), Path("3.pdf")
        self.assertEqual(unprocessed_sources([first, second, third], {second: {}}),
                         (first, third))

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

    def test_automatic_export_uses_iso_date_and_avoids_collisions(self):
        first = {"source_file":"one.pdf", "source_sha256":"aaa",
                 "fields":{"date":{"normalized":"12/17/25"}}}
        second = {"source_file":"two.pdf", "source_sha256":"bbb",
                  "fields":{"date":{"reviewed_value":"12/17/2025"}}}
        self.assertEqual(automatic_export_stem(first), "2025-12-17")
        with tempfile.TemporaryDirectory() as directory:
            first_path = automatic_export(first, Path(directory))
            retry_path = automatic_export(first, Path(directory))
            second_path = automatic_export(second, Path(directory))
            self.assertEqual(first_path.name, "2025-12-17.json")
            self.assertEqual(retry_path, first_path)
            self.assertEqual(second_path.name, "2025-12-17-2.json")

    def test_automatic_export_uses_source_name_when_date_is_invalid(self):
        record = {"source_file":"Scan 17 (copy).pdf", "fields":{"date":{"raw":"unknown"}}}
        self.assertEqual(automatic_export_stem(record), "undated-Scan-17-copy")

    def test_gui_state_round_trip_restores_results_failures_and_position(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.pdf"; first.write_bytes(b"%PDF")
            second = root / "second.pdf"; second.write_bytes(b"%PDF")
            state_file = root / "state.json"
            record = {"source_file":"first.pdf", "status":"succeeded"}
            save_gui_state(state_file, [first, second], 1, {first:record}, {second:"bad scan"})
            sources, index, records, failures = load_gui_state(state_file)
            self.assertEqual(sources, [first.resolve(), second.resolve()])
            self.assertEqual(index, 1)
            self.assertEqual(records[first.resolve()], record)
            self.assertEqual(failures[second.resolve()], "bad scan")

    def test_gui_state_ignores_missing_pdfs_and_clamps_position(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing.pdf"; existing.write_bytes(b"%PDF")
            missing = root / "missing.pdf"
            state_file = root / "state.json"
            save_gui_state(state_file, [missing, existing], 1, {}, {})
            sources, index, records, failures = load_gui_state(state_file)
            self.assertEqual(sources, [existing.resolve()])
            self.assertEqual(index, 0)
            self.assertEqual(records, {})
            self.assertEqual(failures, {})

    def test_editable_roster_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"; repository.mkdir()
            roster_path = root / "roster.json"
            rows = [("Nick Sledge", "4354", "Nicholas Sledge, N. Sledge"),
                    ("Alex Myers", "JR7454", "")]
            save_roster_table(roster_path, repository, rows)
            self.assertEqual(roster_table_rows(roster_path, repository), rows)

    def test_editable_roster_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"; repository.mkdir()
            with self.assertRaisesRegex(ValueError, "duplicated"):
                save_roster_table(root / "roster.json", repository,
                                  [("One", "4554", ""), ("Two", "4554", "")])

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
