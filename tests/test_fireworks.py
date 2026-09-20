import unittest

from fd_training_ocr.fireworks import fireworks_staff_ids, formatted_fireworks_request
from fd_training_ocr.validation import Roster, RosterMember


class FireworksRequestTests(unittest.TestCase):
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
        payload = formatted_fireworks_request(record, (20, 21))
        self.assertEqual(payload["assignTitle"], "Fire & Safety")
        self.assertEqual(payload["assignInst"], "<p>Fire &amp; Safety</p>")
        self.assertEqual(payload["startDt"], "2026-09-19T18:00:00.000Z")
        self.assertEqual(payload["endDt"], "2026-09-19T22:00:00.000Z")
        self.assertEqual(payload["totalHours"], "4")
        self.assertEqual(payload["staff"], [20, 21])

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


if __name__ == "__main__":
    unittest.main()
