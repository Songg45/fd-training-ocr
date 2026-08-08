import contextlib
import io
import json
import unittest

from fd_training_ocr.cli import main


class CliTests(unittest.TestCase):
    def test_inspect_config_is_offline(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output): result = main(["inspect-config"])
        self.assertEqual(result, 0); self.assertTrue(json.loads(output.getvalue())["offline"])

    def test_process_requires_pipeline_inputs(self) -> None:
        with self.assertRaises(SystemExit): main(["process", "private-form.pdf"])
