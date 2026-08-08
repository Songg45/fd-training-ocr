from pathlib import Path
import tempfile
import unittest

from fd_training_ocr.pipeline import load_optional_roster


class PipelineTests(unittest.TestCase):
    def test_missing_roster_is_generic_and_requires_downstream_review(self):
        with tempfile.TemporaryDirectory() as temp:
            roster, warning = load_optional_roster(Path(temp).resolve() / "missing-private-roster.json", Path.cwd())
            self.assertIsNone(roster)
            self.assertEqual(warning, "configured roster unavailable or invalid; roster matching was not applied")
            self.assertNotIn("missing-private-roster", warning)
