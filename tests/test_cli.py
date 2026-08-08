import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from fd_training_ocr.cli import main


class CliTests(unittest.TestCase):
    def test_inspect_config_is_offline(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output): result = main(["inspect-config"])
        self.assertEqual(result, 0); self.assertTrue(json.loads(output.getvalue())["offline"])

    def test_process_requires_pipeline_inputs(self) -> None:
        with self.assertRaises(SystemExit): main(["process", "private-form.pdf"])

    def test_process_routes_stage3_to_separate_configured_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text('[app]\nollama_model="stage12"\nollama_stage3_model="stage3"\n',
                              encoding="utf-8")
            primary, verifier = object(), object()
            summary = SimpleNamespace(discovered=1, succeeded=1, review_required=0, failed=0,
                                      skipped_duplicate=0, exit_code=0)
            with patch("fd_training_ocr.cli.OllamaVisionProvider",
                       side_effect=(primary, verifier)) as provider_factory, \
                 patch("fd_training_ocr.cli.processor_factory", return_value=object()) as factory, \
                 patch("fd_training_ocr.cli.run_batch", return_value=summary), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = main(["--config", str(config), "process", "form.pdf",
                               "--master", "master.png", "--template", "template.json",
                               "--provider", "ollama"])
        self.assertEqual(result, 0)
        self.assertEqual([call.args[0] for call in provider_factory.call_args_list],
                         ["stage12", "stage3"])
        self.assertIs(factory.call_args.kwargs["provider"], primary)
        self.assertIs(factory.call_args.kwargs["stage3_provider"], verifier)
