import json
from pathlib import Path
import tempfile
import unittest

from fd_training_ocr.config import AppConfig
from fd_training_ocr.gui_controller import (GuiPaths, accept_stage3_suggestion, add_attendee,
                                             apply_event_selection,
                                             apply_facilities_edit, apply_gui_edit,
                                             apply_roster_linked_unit_edit,
                                             populate_name_from_roster_unit,
                                             populate_unit_from_roster_name,
                                             automatic_export, automatic_export_stem,
                                             build_processor, display_value, effective_event_selection,
                                             effective_facilities,
                                             discover_pdfs, export_record, index_after_removal, structured_rows,
                                             load_gui_state, save_gui_state, unprocessed_sources,
                                             attendee_row_from_field, remove_attendee,
                                             first_available_attendee_row,
                                             stage3_suggestion,
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
        self.assertIn(("Training type", "New Driver", "Double-click to select training types", False), rows)
        self.assertIn(("Truck", "Brush 54", "Double-click to select trucks", False), rows)
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

    def test_gui_date_edit_is_saved_as_mm_dd_yy(self):
        record = {"fields":{"date":{"raw":"12/1/2025", "reviewed_value":None}},
                  "review":{"corrections_applied":False, "reviewed_at":None}}
        apply_gui_edit(record, "date", "12/1/2025", "2026-08-08T12:00:00Z")
        self.assertEqual(record["fields"]["date"]["reviewed_value"], "12/01/25")

    def test_unit_id_edit_updates_attendee_name_from_exact_roster_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"; repository.mkdir()
            roster_path = root / "roster.json"
            roster_path.write_text(json.dumps({
                "schema_version": 1,
                "members": [{"name": "Diane Brown", "unit_ids": ["6854"], "aliases": []}],
            }), encoding="utf-8")
            record = {
                "fields": {
                    "attendee.03.unit_id": {"raw": "G854", "reviewed_value": None},
                    "attendee.03.print_name": {"raw": "Vigne Starnos", "reviewed_value": None},
                },
                "attendees": [{"row": 3, "unit_id": "G854", "print_name": "Vigne Starnos"}],
                "review": {"corrections_applied": False, "reviewed_at": None},
            }
            matched = apply_roster_linked_unit_edit(
                record, "attendee.03.unit_id", "6854", roster_path, repository,
                "2026-08-08T12:00:00Z")
            self.assertEqual(matched, "Diane Brown")
            self.assertEqual(record["fields"]["attendee.03.unit_id"]["reviewed_value"], "6854")
            self.assertEqual(record["fields"]["attendee.03.print_name"]["reviewed_value"],
                             "Diane Brown")
            self.assertEqual(record["attendees"][0],
                             {"row": 3, "unit_id": "6854", "print_name": "Diane Brown"})

    def test_roster_name_populates_single_unit_id_after_stage3_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"; repository.mkdir()
            roster_path = root / "roster.json"
            roster_path.write_text(json.dumps({
                "schema_version": 1,
                "members": [{"name": "Samantha Gibson", "unit_ids": ["7254"],
                             "aliases": ["Sam Gibson"]}],
            }), encoding="utf-8")
            record = {
                "fields": {
                    "attendee.01.unit_id": {"raw": "7Z54", "reviewed_value": None},
                    "attendee.01.print_name": {"raw": "Samantha C", "reviewed_value": None,
                        "stage_3": "Samantha Gibson", "second_pass_review_required": True},
                },
                "attendees": [{"row": 1, "unit_id": "7Z54", "print_name": "Samantha C"}],
                "review": {"corrections_applied": False, "reviewed_at": None},
            }
            stamp = "2026-08-08T12:00:00Z"
            accept_stage3_suggestion(record, "attendee.01.print_name", stamp)
            unit = populate_unit_from_roster_name(
                record, "attendee.01.print_name", "Samantha Gibson",
                roster_path, repository, stamp)
            self.assertEqual(unit, "7254")
            self.assertEqual(record["fields"]["attendee.01.unit_id"]["reviewed_value"], "7254")
            self.assertEqual(record["attendees"][0],
                             {"row": 1, "unit_id": "7254", "print_name": "Samantha Gibson"})

    def test_roster_unit_populates_name_after_stage3_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"; repository.mkdir()
            roster_path = root / "roster.json"
            roster_path.write_text(json.dumps({
                "schema_version": 1,
                "members": [{"name": "Diane Brown", "unit_ids": ["6854"], "aliases": []}],
            }), encoding="utf-8")
            record = {
                "fields": {
                    "attendee.03.unit_id": {"raw": "G854", "reviewed_value": None,
                        "stage_3": "6854", "second_pass_review_required": True},
                    "attendee.03.print_name": {"raw": "Vigne Starnos", "reviewed_value": None},
                },
                "attendees": [{"row": 3, "unit_id": "G854", "print_name": "Vigne Starnos"}],
                "review": {"corrections_applied": False, "reviewed_at": None},
            }
            stamp = "2026-08-08T12:00:00Z"
            accept_stage3_suggestion(record, "attendee.03.unit_id", stamp)
            name = populate_name_from_roster_unit(
                record, "attendee.03.unit_id", "6854", roster_path, repository, stamp)
            self.assertEqual(name, "Diane Brown")
            self.assertEqual(record["fields"]["attendee.03.unit_id"]["review"]["source"],
                             "stage_3")
            self.assertEqual(record["fields"]["attendee.03.print_name"]["reviewed_value"],
                             "Diane Brown")
            self.assertEqual(record["attendees"][0],
                             {"row": 3, "unit_id": "6854", "print_name": "Diane Brown"})

    def test_attendee_deletion_removes_active_values_and_preserves_audit(self):
        unit = {"raw":"4554", "normalized":"4554"}
        name = {"raw":"Brandon Tucker", "normalized":"Brandon Tucker Sr"}
        record = {"fields":{"attendee.01.unit_id":unit,
                            "attendee.01.print_name":name,
                            "description":{"raw":"Driver training"}},
                  "attendees":({"row":1, "unit_id":"4554",
                                 "print_name":"Brandon Tucker Sr"},),
                  "review":{"corrections_applied":False, "reviewed_at":None}}
        remove_attendee(record, 1, "2026-08-08T12:00:00Z")
        self.assertNotIn("attendee.01.unit_id", record["fields"])
        self.assertNotIn("attendee.01.print_name", record["fields"])
        self.assertEqual(record["attendees"], [])
        removed = record["review"]["removed_attendees"][0]
        self.assertEqual(removed["fields"]["attendee.01.unit_id"], unit)
        self.assertEqual(removed["attendees"][0]["unit_id"], "4554")
        self.assertTrue(record["review"]["corrections_applied"])

    def test_attendee_row_is_derived_only_from_attendee_fields(self):
        self.assertEqual(attendee_row_from_field("attendee.09.print_name"), 9)
        self.assertEqual(attendee_row_from_field("attendee.19.unit_id"), 19)
        self.assertIsNone(attendee_row_from_field("description"))

    def test_attendee_addition_uses_open_row_and_preserves_manual_audit(self):
        record = {"fields":{"attendee.01.unit_id":{"raw":"4554"},
                            "attendee.01.print_name":{"raw":"Brandon Tucker"}},
                  "attendees":[{"row":1, "unit_id":"4554",
                                "print_name":"Brandon Tucker Sr"}],
                  "review":{"corrections_applied":False, "reviewed_at":None}}
        self.assertEqual(first_available_attendee_row(record), 2)
        add_attendee(record, 2, "4354", "Nick Sledge", "2026-08-08T12:00:00Z")
        self.assertEqual(record["fields"]["attendee.02.unit_id"]["reviewed_value"], "4354")
        self.assertEqual(record["fields"]["attendee.02.print_name"]["reviewed_value"], "Nick Sledge")
        self.assertEqual(record["attendees"][1],
                         {"row":2, "unit_id":"4354", "print_name":"Nick Sledge"})
        self.assertEqual(record["review"]["added_attendees"][0]["row"], 2)
        self.assertTrue(record["review"]["corrections_applied"])

    def test_attendee_addition_rejects_occupied_or_incomplete_rows(self):
        record = {"fields":{}, "attendees":[{"row":1, "unit_id":"4554",
                                               "print_name":"Brandon Tucker Sr"}]}
        with self.assertRaisesRegex(ValueError, "occupied"):
            add_attendee(record, 1, "4354", "Nick Sledge")
        with self.assertRaisesRegex(ValueError, "requires both"):
            add_attendee(record, 2, "", "Nick Sledge")

    def test_unresolved_stage3_suggestion_is_shown_in_warnings(self):
        rows = structured_rows({"fields":{"description":{"raw":"system", "normalized":"system",
            "warnings":["recognizer supplied alternatives"], "stage_3":"Fire/Res Safety",
            "second_pass_review_required":True}}, "event":{}})
        self.assertEqual(rows[0], ("description", "system",
            "recognizer supplied alternatives; Stage 3 suggests: Fire/Res Safety", True))

    def test_stage3_suggestion_acceptance_preserves_evidence_and_resolves_field(self):
        record = {"fields":{"description":{"raw":"system", "normalized":"system",
            "reviewed_value":None, "stage_3":"Fire/Res Safety",
            "second_pass_review_required":True, "warnings":["check description"]}},
            "review":{"corrections_applied":False, "reviewed_at":None}}
        self.assertEqual(stage3_suggestion(record, "description"), "Fire/Res Safety")
        accept_stage3_suggestion(record, "description", "2026-08-08T12:00:00Z")
        field = record["fields"]["description"]
        self.assertEqual(field["raw"], "system")
        self.assertEqual(field["stage_3"], "Fire/Res Safety")
        self.assertEqual(field["reviewed_value"], "Fire/Res Safety")
        self.assertFalse(field["second_pass_review_required"])
        self.assertEqual(field["review"]["source"], "stage_3")
        self.assertIsNone(stage3_suggestion(record, "description"))
        self.assertEqual(record["review"]["accepted_stage3"][0]["field_name"],
                         "description")

    def test_facilities_edit_preserves_machine_result(self):
        record = {"event":{"facilities":[]},
                  "review":{"corrections_applied":False, "reviewed_at":None}}
        apply_facilities_edit(record, ["classroom", "outside_area"], "2026-08-08T12:00:00Z")
        self.assertEqual(record["event"]["facilities"], [])
        self.assertEqual(record["event"]["reviewed_facilities"], ["classroom", "outside_area"])
        self.assertEqual(effective_facilities(record["event"]), ["classroom", "outside_area"])
        self.assertTrue(record["review"]["corrections_applied"])

    def test_training_type_and_truck_edits_preserve_machine_results(self):
        record = {"event":{"training_types":[], "trucks_used":["Engine 54"]},
                  "review":{"corrections_applied":False, "reviewed_at":None}}
        apply_event_selection(record, "Training type", ["new_driver", "driver"],
                              "2026-08-08T12:00:00Z")
        apply_event_selection(record, "Truck", ["Brush 54"],
                              "2026-08-08T12:01:00Z")
        self.assertEqual(record["event"]["training_types"], [])
        self.assertEqual(record["event"]["trucks_used"], ["Engine 54"])
        self.assertEqual(effective_event_selection(record["event"], "Training type"),
                         ["new_driver", "driver"])
        self.assertEqual(effective_event_selection(record["event"], "Truck"), ["Brush 54"])

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

    def test_date_correction_renames_automatic_export_without_leaving_undated_file(self):
        record = {"source_file":"Scan 17.pdf", "source_sha256":"same-source",
                  "fields":{"date":{"raw":"unknown", "reviewed_value":None}}}
        with tempfile.TemporaryDirectory() as directory:
            export_dir = Path(directory)
            undated = automatic_export(record, export_dir)
            self.assertEqual(undated.name, "undated-Scan-17.json")
            record["fields"]["date"]["reviewed_value"] = "12/17/2025"
            dated = automatic_export(record, export_dir)
            self.assertEqual(dated.name, "2025-12-17.json")
            self.assertTrue(dated.exists())
            self.assertFalse(undated.exists())
            self.assertEqual(list(export_dir.glob("*.json")), [dated])

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
