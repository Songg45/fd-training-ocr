import unittest

from fd_training_ocr.normalization import (normalize_aliased_allowlisted, normalize_allowlisted,
                                           normalize_date, normalize_hours, normalize_time)


class NormalizationTests(unittest.TestCase):
    def test_preserves_written_date_and_normalizes(self):
        value = normalize_date("12/17/25")
        self.assertEqual((value.raw, value.normalized, value.valid), ("12/17/25", "2025-12-17", True))
        self.assertEqual(normalize_date("12-17-25").normalized, "2025-12-17")

    def test_invalid_date_is_not_rewritten(self):
        value = normalize_date("LZ//WOES")
        self.assertEqual(value.raw, "LZ//WOES"); self.assertIsNone(value.normalized); self.assertFalse(value.valid)

    def test_time_hours_and_allowlist(self):
        self.assertEqual(normalize_time("4:00 PM").normalized, "16:00")
        self.assertEqual(normalize_hours("2.0").normalized, "2")
        self.assertEqual(normalize_allowlisted("district", ("District",)).normalized, "District")

    def test_location_aliases_normalize_to_canonical_value(self):
        aliases = {value:"Pilot FD" for value in
                   ("PFD", "Pilot", "Pilot Fire", "Pilot Fire Dep", "Pilot Fire Dept",
                    "Pilot Fire Department", "Pilot FD")}
        for raw in ("PFD", "pfd", "Pilot", "Pilot Fire", "Pilot Fire Dep",
                    "Pilot Fire Dept", "Pilot Fire Department", "Pilot FD"):
            value = normalize_aliased_allowlisted(
                raw, ("District", "Pilot FD"), aliases)
            self.assertTrue(value.valid)
            self.assertEqual(value.normalized, "Pilot FD")
