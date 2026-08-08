import json
from pathlib import Path
import tempfile
import unittest

from fd_training_ocr.recognition import RecognitionResult
from fd_training_ocr.validation import RosterError, ValidationPolicy, load_roster, validate


def result(name, value, confidence=.99, alternatives=()):
    return RecognitionResult(name, value, value, confidence, tuple(alternatives), "synthetic", "mock", "fixture", (0, 0, 1, 1))


class RosterTests(unittest.TestCase):
    def test_external_roster_matches_alias_without_changing_raw(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"; root.mkdir(); roster_path = Path(temp) / "roster.json"
            roster_path.write_text(json.dumps({"schema_version": 1, "members": [{"name": "Synthetic Member", "aliases": ["S. Member"], "unit_ids": ["X001"]}]}))
            roster = load_roster(roster_path, root)
            report = validate([result("attendee.01.print_name", "S. Member"), result("attendee.01.unit_id", "X001")], roster=roster)
            field = report.fields[0]
            self.assertEqual((field.raw, field.normalized), ("S. Member", "S. Member"))
            self.assertEqual(field.suggested_canonical, "Synthetic Member")

    def test_fuzzy_roster_suggestions_never_replace_raw_and_report_ambiguity(self):
        from fd_training_ocr.validation import Roster, RosterMember
        roster = Roster((RosterMember("Synthetic Member", ("4554",)),
                         RosterMember("Synthetica Member", ("4354",))))
        report = validate([result("attendee.01.print_name", "Syntheti Member"),
                           result("attendee.01.unit_id", "455")], roster=roster)
        name, unit = report.fields
        self.assertEqual(name.normalized, "Syntheti Member")
        self.assertTrue(name.suggestion_ambiguous)
        self.assertIsNotNone(name.suggested_canonical)
        self.assertEqual(unit.normalized, "455")
        self.assertEqual(unit.suggested_canonical, "4554")

    def test_rejects_repo_local_and_malformed_rosters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); inside = root / "roster.json"; inside.write_text("{}")
            with self.assertRaisesRegex(RosterError, "outside"): load_roster(inside, root)
            bad = root.parent / (root.name + "-bad.json"); bad.write_text("not json")
            try:
                with self.assertRaisesRegex(RosterError, "valid roster JSON"): load_roster(bad, root)
            finally: bad.unlink()


class ValidationTests(unittest.TestCase):
    def test_duration_disagreement_requires_review(self):
        report = validate([result("start_time", "16:00"), result("end_time", "17:00"), result("total_hours", "2")])
        self.assertEqual(report.total_hours_calculated, 1)
        self.assertTrue(report.review_required)
        self.assertIn("differs", report.warnings[0])

    def test_high_model_confidence_does_not_bypass_invalid_or_ambiguous_value(self):
        report = validate([result("date", "LZ//WOES", 1.0), result("instructor", "Maybe", 1.0, ("May Bee",))])
        self.assertTrue(report.review_required)
        self.assertTrue(report.fields[0].review_required); self.assertTrue(report.fields[1].review_required)

    def test_unknown_apparatus_and_incomplete_attendee(self):
        report = validate([result("attendee.01.unit_id", "X")], selected_apparatus=["Flying Truck"])
        self.assertTrue(any("unknown apparatus" in x for x in report.warnings))
        self.assertTrue(any("incomplete" in x for x in report.warnings))
