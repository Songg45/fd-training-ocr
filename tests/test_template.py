import json
import tempfile
from pathlib import Path
import unittest

from fd_training_ocr.template import TemplateError, load_template


class TemplateTests(unittest.TestCase):
    def test_loads_normalized_field_map(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        definition = load_template(repository / "templates" / "pilot_fd_training_sign_in" / "v1" / "template.json")
        self.assertEqual(definition.master_size, (2614, 3554))
        self.assertEqual(definition.region("date").kind, "text")
        self.assertEqual(len([r for r in definition.regions if r.kind == "signature"]), 19)
        self.assertEqual(definition.excluded_region_kinds, frozenset({"signature"}))

    def test_rejects_out_of_bounds_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps({
                "schema_version": 1, "form_type": "x", "form_version": "v1",
                "coordinate_system": "normalized_xywh", "master": {"size_pixels": [10, 10]},
                "alignment": {"quality_thresholds": {}},
                "regions": [{"name": "bad", "kind": "text", "box": [0.9, 0.9, 0.2, 0.2]}]
            }), encoding="utf-8")
            with self.assertRaises(TemplateError):
                load_template(path)

    def test_v1_writable_regions_exclude_known_printed_label_columns(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        definition = load_template(repository / "templates" / "pilot_fd_training_sign_in" / "v1" / "template.json")
        start = definition.region("start_time").box
        end = definition.region("end_time").box
        self.assertEqual(start, (.635, .153, .115, .03))
        self.assertLessEqual(start[0] + start[2], .75)  # stops before printed "To"
        self.assertEqual(end, (.785, .153, .132, .03))
        self.assertGreaterEqual(definition.region("end_time").box[0], .78)
        self.assertGreaterEqual(definition.region("total_hours").box[0], .74)
        self.assertGreaterEqual(definition.region("instructor").box[0], .23)
        for row in range(1, 20):
            prefix = f"attendee.{row:02d}"
            self.assertEqual(definition.region(prefix + ".unit_id").box[0], .165)
            self.assertEqual(definition.region(prefix + ".print_name").box[0], .282)
        description = definition.region("description").box
        self.assertLessEqual(description[1], .825)
        self.assertGreaterEqual(description[3], .095)


if __name__ == "__main__":
    unittest.main()
