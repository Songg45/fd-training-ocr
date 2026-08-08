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


if __name__ == "__main__":
    unittest.main()
