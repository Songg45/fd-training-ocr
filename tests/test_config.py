from pathlib import Path
import tempfile
import unittest

from fd_training_ocr.config import AppConfig, load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_are_local_and_offline(self) -> None:
        self.assertEqual(load_config(), AppConfig())
        self.assertTrue(load_config().offline)

    def test_loads_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[app]\noutput_dir = "results"\nlog_level = "debug"\n', encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.output_dir, Path("results"))
        self.assertEqual(config.log_level, "DEBUG")

    def test_rejects_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("[app]\napi_key = \"should-not-be-here\"\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "api_key"):
                load_config(path)
