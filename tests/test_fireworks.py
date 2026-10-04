import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fd_training_ocr.fireworks import (FireworksCategory, FireworksInstructor,
                                       FireworksLocation,
                                       FireworksMappings, displayed_fireworks_request,
                                       backfill_reviewed_instructors,
                                       fireworks_instructor_ids,
                                       fireworks_staff_ids, formatted_fireworks_request,
                                       load_fireworks_mappings, save_fireworks_request_edit,
                                       parse_fireworks_request,
                                       selected_category_name, selected_location_name,
                                       update_payload_selection,
                                       validate_fireworks_payload)
from fd_training_ocr.validation import Roster, RosterMember


class FireworksRequestTests(unittest.TestCase):
    def mappings(self):
        return FireworksMappings(
            (
                FireworksCategory("Company Training", 101, "confirmed"),
                FireworksCategory("Driver/Operator", 102, "confirmed"),
                FireworksCategory("Officer Training", 103, "confirmed"),
                FireworksCategory("Outside Department Training", 104, "inferred"),
            ),
            (
                FireworksLocation("Fire Station", 201, "station-version"),
                FireworksLocation("Classroom", 202, "classroom-version"),
                FireworksLocation("Outside Area", 203, "outside-version"),
            ),
            "Pilot FD", 54,
            (
                FireworksInstructor("Matthew Grice", 2),
                FireworksInstructor("Nicholas Sledge", 9, ("Nick Sledge",)),
            ))

    def test_reviewed_values_build_fireworks_request(self):
        record = {
            "fields": {
                "date": {"reviewed_value": "09/19/26", "raw": "091926"},
                "start_time": {"reviewed_value": "18:00", "raw": "1800"},
                "end_time": {"resolved_value": "22:00"},
                "total_hours": {"reviewed_value": "4"},
                "description": {"reviewed_value": "Fire & Safety"},
            },
            "event": {"total_hours_calculated": 4.0},
        }
        mappings = self.mappings()
        payload = formatted_fireworks_request(
            record, (20, 21),
            category=mappings.category_named("Company Training"),
            location=mappings.location_named("Fire Station"),
            instructor_ids=(9,),
            station_id=mappings.station_id)
        self.assertEqual(payload["assignTitle"], "Fire & Safety")
        self.assertEqual(payload["assignInst"], "<p>Fire &amp; Safety</p>")
        self.assertEqual(payload["startDt"], "2026-09-19T18:00:00.000Z")
        self.assertEqual(payload["endDt"], "2026-09-19T22:00:00.000Z")
        self.assertEqual(payload["totalHours"], "4")
        self.assertEqual(payload["staff"], [20, 21])
        self.assertEqual(payload["attendance"], 2)
        self.assertEqual(payload["instructors"], [9])
        self.assertEqual(payload["assignCat"], 101)
        self.assertEqual(payload["location"], 201)
        self.assertEqual(payload["station"], 54)

    def test_overnight_end_time_advances_to_the_next_date(self):
        record = {"fields": {
            "date": {"reviewed_value": "09/19/26"},
            "start_time": {"reviewed_value": "23:00"},
            "end_time": {"reviewed_value": "01:00"},
            "total_hours": {"reviewed_value": "2"},
            "description": {"reviewed_value": "Night training"},
        }}
        payload = formatted_fireworks_request(record)
        self.assertEqual(payload["startDt"], "2026-09-19T23:00:00.000Z")
        self.assertEqual(payload["endDt"], "2026-09-20T01:00:00.000Z")

    def test_training_type_and_facilities_produce_unambiguous_suggestions(self):
        record = {"event": {
            "reviewed_training_types": ["driver", "new_driver"],
            "reviewed_facilities": ["drill_ground", "outside_area"],
        }}
        self.assertEqual(selected_category_name(record), "Driver/Operator")
        self.assertEqual(selected_location_name(record), "Outside Area")
        record["event"]["reviewed_training_types"].append("officers")
        record["event"]["reviewed_facilities"].append("classroom")
        self.assertIsNone(selected_category_name(record))
        self.assertIsNone(selected_location_name(record))

    def test_attendees_map_through_external_roster_staff_ids(self):
        record = {"fields": {}, "event": {}, "attendees": [
            {"row": 1, "unit_id": "JR7454", "print_name": "Alex Myers"},
            {"row": 2, "unit_id": "4354", "print_name": "Nick Sledge"},
            {"row": 3, "unit_id": "9999", "print_name": "Unknown Person"},
        ]}
        roster = Roster((
            RosterMember("Alex Myers", ("JR7454",), (), 20),
            RosterMember("Nick Sledge", ("4354",), ("Nicholas Sledge",), 26),
        ))
        staff_ids, unresolved = fireworks_staff_ids(record, roster)
        self.assertEqual(staff_ids, (20, 26))
        self.assertEqual(unresolved, ("Unknown Person",))

    def test_instructor_is_not_counted_as_attending_staff(self):
        record = {
            "fields": {"instructor": {"reviewed_value": "Nick Sledge"}},
            "attendees": [
                {"row": 1, "unit_id": "JR7454", "print_name": "Alex Myers"},
            ],
        }
        roster = Roster((
            RosterMember("Alex Myers", ("JR7454",), (), 20),
            RosterMember("Nick Sledge", ("4354",), ("Nicholas Sledge",), 26),
        ))
        staff_ids, unresolved = fireworks_staff_ids(record, roster)
        self.assertEqual(staff_ids, (20,))
        self.assertEqual(unresolved, ())

    def test_instructor_uses_separate_mapping_and_aliases(self):
        record = {
            "fields": {"instructor": {"reviewed_value": "Nick Sledge"}},
            "attendees": [],
        }
        instructor_ids, unresolved = fireworks_instructor_ids(
            record, self.mappings())
        self.assertEqual(instructor_ids, (9,))
        self.assertEqual(unresolved, ())

        record["fields"]["instructor"]["reviewed_value"] = "Unknown Trainer"
        instructor_ids, unresolved = fireworks_instructor_ids(
            record, self.mappings())
        self.assertEqual(instructor_ids, ())
        self.assertEqual(unresolved, ("Instructor: Unknown Trainer",))

    def test_dropdown_update_changes_only_controlled_payload_fields(self):
        mappings = self.mappings()
        payload = {
            "assignTitle": "Keep me",
            "assignCat": 101,
            "location": 201,
            "locationstr": "Fire Station",
            "locationFlds": {"moneln": 201, "desc": "Fire Station",
                              "upsize_ts": "station-version", "custom": "keep"},
            "station": None,
        }
        update_payload_selection(
            payload, mappings, category_name="Officer Training",
            location_name="Classroom")
        self.assertEqual(payload["assignTitle"], "Keep me")
        self.assertEqual(payload["assignCat"], 103)
        self.assertEqual(payload["location"], 202)
        self.assertEqual(payload["locationstr"], "Classroom")
        self.assertEqual(payload["locationFlds"]["upsize_ts"], "classroom-version")
        self.assertEqual(payload["locationFlds"]["custom"], "keep")
        self.assertEqual(payload["station"], 54)

    def test_submission_validation_catches_mismatches_and_accepts_ready_payload(self):
        mappings = self.mappings()
        record = {"fields": {
            "date": {"reviewed_value": "09/19/26"},
            "start_time": {"reviewed_value": "18:00"},
            "end_time": {"reviewed_value": "19:00"},
            "total_hours": {"reviewed_value": "1"},
            "description": {"reviewed_value": "Reviewed training"},
        }}
        payload = formatted_fireworks_request(
            record, (20,), category=mappings.category_named("Company Training"),
            location=mappings.location_named("Fire Station"), station_id=54)
        self.assertEqual(validate_fireworks_payload(payload, mappings), ())
        payload["locationstr"] = "Classroom"
        errors = validate_fireworks_payload(
            payload, mappings, ("Instructor: Unknown",))
        self.assertIn("locationstr does not match location", errors)
        self.assertTrue(any("Instructor: Unknown" in error for error in errors))

    def test_submission_validation_rejects_total_hours_that_disagree_with_interval(self):
        mappings = self.mappings()
        record = {"fields": {
            "date": {"reviewed_value": "09/19/26"},
            "start_time": {"reviewed_value": "16:00"},
            "end_time": {"reviewed_value": "17:00"},
            "total_hours": {"reviewed_value": "8"},
            "description": {"reviewed_value": "Reviewed training"},
        }}
        payload = formatted_fireworks_request(
            record, (20,), category=mappings.category_named("Company Training"),
            location=mappings.location_named("Fire Station"), station_id=54)

        errors = validate_fireworks_payload(
            payload, mappings, expected_staff_ids=(20,))

        self.assertTrue(any(
            "totalHours must match the startDt/endDt duration" in error
            for error in errors))

    def test_submission_validation_accepts_matching_overnight_duration(self):
        mappings = self.mappings()
        record = {"fields": {
            "date": {"reviewed_value": "09/19/26"},
            "start_time": {"reviewed_value": "23:00"},
            "end_time": {"reviewed_value": "01:30"},
            "total_hours": {"reviewed_value": "2.5"},
            "description": {"reviewed_value": "Overnight training"},
        }}
        payload = formatted_fireworks_request(
            record, (20,), category=mappings.category_named("Company Training"),
            location=mappings.location_named("Fire Station"), station_id=54)

        self.assertEqual(
            validate_fireworks_payload(
                payload, mappings, expected_staff_ids=(20,)), ())

    def test_submission_validation_requires_exact_reviewed_staff_ids(self):
        mappings = self.mappings()
        record = {"fields": {
            "date": {"reviewed_value": "09/19/26"},
            "start_time": {"reviewed_value": "18:00"},
            "end_time": {"reviewed_value": "19:00"},
            "total_hours": {"reviewed_value": "1"},
            "description": {"reviewed_value": "Reviewed training"},
        }}
        payload = formatted_fireworks_request(
            record, (20, 26),
            category=mappings.category_named("Company Training"),
            location=mappings.location_named("Fire Station"), station_id=54)
        self.assertEqual(
            validate_fireworks_payload(
                payload, mappings, expected_staff_ids=(20, 26)), ())

        payload["staff"] = [20, 999999]
        errors = validate_fireworks_payload(
            payload, mappings, expected_staff_ids=(20, 26))
        self.assertTrue(any("missing reviewed Staff ID 26" in error for error in errors))
        self.assertTrue(any("unexpected Staff ID 999999" in error for error in errors))

    def test_submission_validation_requires_attendance_to_match_staff_count(self):
        mappings = self.mappings()
        record = {"fields": {
            "date": {"reviewed_value": "09/19/26"},
            "start_time": {"reviewed_value": "18:00"},
            "end_time": {"reviewed_value": "19:00"},
            "total_hours": {"reviewed_value": "1"},
            "description": {"reviewed_value": "Reviewed training"},
        }}
        payload = formatted_fireworks_request(
            record, (20, 26),
            category=mappings.category_named("Company Training"),
            location=mappings.location_named("Fire Station"), station_id=54)
        payload["attendance"] = 1

        errors = validate_fireworks_payload(
            payload, mappings, expected_staff_ids=(20, 26))

        self.assertIn("attendance must equal the number of staff IDs (2)", errors)

    def test_submission_validation_requires_exact_reviewed_instructor_id(self):
        mappings = self.mappings()
        record = {"fields": {
            "date": {"reviewed_value": "09/19/26"},
            "start_time": {"reviewed_value": "18:00"},
            "end_time": {"reviewed_value": "19:00"},
            "total_hours": {"reviewed_value": "1"},
            "description": {"reviewed_value": "Reviewed training"},
        }}
        payload = formatted_fireworks_request(
            record, (20,), category=mappings.category_named("Company Training"),
            location=mappings.location_named("Fire Station"),
            instructor_ids=(9,), station_id=54)

        self.assertEqual(validate_fireworks_payload(
            payload, mappings, expected_staff_ids=(20,),
            expected_instructor_ids=(9,)), ())

        payload["instructors"] = [2]
        errors = validate_fireworks_payload(
            payload, mappings, expected_staff_ids=(20,),
            expected_instructor_ids=(9,))
        self.assertIn("instructors is missing reviewed Instructor ID 9", errors)
        self.assertIn("instructors contains unexpected Instructor ID 2", errors)

        payload["instructors"] = [999]
        errors = validate_fireworks_payload(
            payload, mappings, unresolved_instructors=("Instructor: Unknown",),
            expected_instructor_ids=())
        self.assertIn("instructors contains unconfigured Instructor ID 999", errors)
        self.assertTrue(any("Instructor: Unknown" in error for error in errors))

    def test_external_mapping_file_is_strict_and_selects_only_submission_categories(self):
        with TemporaryDirectory() as name:
            path = Path(name) / "ids.json"
            path.write_text('''{
              "schema_version": 1,
              "categories": [
                {"name":"Activities","id":100,"status":"confirmed"},
                {"name":"Company Training","id":101,"status":"confirmed"},
                {"name":"Driver/Operator","id":102,"status":"confirmed"},
                {"name":"Officer Training","id":103,"status":"confirmed"},
                {"name":"Outside Department Training","id":104,"status":"inferred"}
              ],
              "locations": [
                {"name":"Fire Station","id":201,"upsize_ts":"a"},
                {"name":"Classroom","id":202,"upsize_ts":"b"},
                {"name":"Outside Area","id":203,"upsize_ts":"c"}
              ],
              "instructors": [
                {"name":"Nicholas Sledge","id":9,"aliases":["Nick Sledge"]},
                {"name":"Matthew Grice","id":2,"aliases":[]}
              ],
              "station": {"name":"Pilot FD","id":54}
            }''', encoding="utf-8")
            mappings = load_fireworks_mappings(path)
        self.assertEqual(
            [item.name for item in mappings.categories],
            ["Company Training", "Driver/Operator", "Officer Training",
             "Outside Department Training"])
        self.assertEqual(mappings.station_id, 54)
        self.assertEqual(mappings.instructor_named("Nick Sledge").id, 9)
        self.assertEqual(mappings.instructor_with_id(2).name, "Matthew Grice")

    def test_external_mapping_rejects_duplicate_json_keys(self):
        with TemporaryDirectory() as name:
            path = Path(name) / "ids.json"
            path.write_text(
                '{"schema_version":1,"schema_version":1}',
                encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_fireworks_mappings(path)

    def test_formatted_request_edits_and_invalid_drafts_are_persistent(self):
        record = {}
        valid, error = save_fireworks_request_edit(
            record, '{"assignTitle":"Reviewed","staff":[20],"instructors":[9]}',
            "2026-09-19T12:00:00+00:00")
        self.assertTrue(valid)
        self.assertIsNone(error)
        text, source = displayed_fireworks_request(record, {"staff": []})
        self.assertEqual(source, "reviewed")
        self.assertIn('"assignTitle": "Reviewed"', text)

        valid, error = save_fireworks_request_edit(
            record, '{"assignTitle":', "2026-09-19T12:01:00+00:00")
        self.assertFalse(valid)
        self.assertIn("Invalid JSON", error)
        text, source = displayed_fireworks_request(record, {"staff": []})
        self.assertEqual((text, source), ('{"assignTitle":', "draft"))

    def test_resolved_instructor_backfills_empty_legacy_reviewed_request(self):
        record = {
            "fireworks_request_review": {
                "payload": {"staff": [20], "instructors": []},
                "reviewed_at": "2026-10-04T12:00:00+00:00",
            },
        }

        changed = backfill_reviewed_instructors(
            record, (1,), "2026-10-04T12:05:00+00:00")

        self.assertTrue(changed)
        self.assertEqual(
            record["fireworks_request_review"]["payload"]["instructors"], [1])
        self.assertEqual(
            record["fireworks_request_review"]["instructors_updated_at"],
            "2026-10-04T12:05:00+00:00")

    def test_resolved_instructor_never_overwrites_nonempty_manual_selection(self):
        record = {
            "fireworks_request_review": {
                "payload": {"staff": [20], "instructors": [9]},
            },
        }

        changed = backfill_reviewed_instructors(
            record, (1,), "2026-10-04T12:05:00+00:00")

        self.assertFalse(changed)
        self.assertEqual(
            record["fireworks_request_review"]["payload"]["instructors"], [9])

    def test_unresolved_instructor_does_not_modify_reviewed_request(self):
        record = {
            "fireworks_request_review": {
                "payload": {"staff": [20], "instructors": []},
            },
        }

        self.assertFalse(backfill_reviewed_instructors(
            record, (), "2026-10-04T12:05:00+00:00"))
        self.assertEqual(
            record["fireworks_request_review"]["payload"]["instructors"], [])

    def test_formatted_request_requires_numeric_staff_ids(self):
        record = {}
        valid, error = save_fireworks_request_edit(
            record, '{"staff":["JR7454"]}', "2026-09-19T12:00:00+00:00")
        self.assertFalse(valid)
        self.assertIn("numeric Fireworks IDs", error)

    def test_formatted_request_rejects_duplicate_json_keys(self):
        record = {}
        valid, error = save_fireworks_request_edit(
            record, '{"staff":[20],"staff":[999999]}',
            "2026-09-19T12:00:00+00:00")
        self.assertFalse(valid)
        self.assertIn("duplicate JSON key", error)
        self.assertNotIn("fireworks_request_review", record)

    def test_formatted_request_rejects_non_finite_numbers(self):
        record = {}
        valid, error = save_fireworks_request_edit(
            record, '{"staff":[20],"totalHours":NaN}',
            "2026-09-19T12:00:00+00:00")
        self.assertFalse(valid)
        self.assertIn("non-finite JSON number", error)

    def test_strict_request_parser_returns_the_exact_object(self):
        self.assertEqual(
            parse_fireworks_request('{"staff":[20],"assignTitle":"Reviewed"}'),
            {"staff": [20], "assignTitle": "Reviewed"})


if __name__ == "__main__":
    unittest.main()
