from pathlib import Path
import tempfile
import unittest

from fd_training_ocr.config import AppConfig, load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_are_local_and_offline(self) -> None:
        self.assertEqual(load_config(), AppConfig())
        self.assertTrue(load_config().offline)
        self.assertEqual(dict(load_config().location_aliases)["PRO"], "Pilot FD")

    def test_loads_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[app]\noutput_dir = "results"\nlog_level = "debug"\n', encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.output_dir, Path("results"))
        self.assertEqual(config.log_level, "DEBUG")

    def test_loads_local_ollama_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[app]\nollama_model="vision:3b"\nollama_stage3_model="vision:8b"\nollama_timeout_seconds=15\n', encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.ollama_model, "vision:3b")
        self.assertEqual(config.ollama_stage3_model, "vision:8b")
        self.assertEqual(config.ollama_timeout_seconds, 15.0)

    def test_loads_bounded_recognition_crop_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[app]\nrecognition_crop_padding_pixels=20\nrecognition_max_attempts=2\n', encoding="utf-8")
            config = load_config(path)
        self.assertEqual((config.recognition_crop_padding_pixels, config.recognition_max_attempts), (20, 2))

    def test_loads_location_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                '[app]\nvalid_locations=["District", "Pilot Fire Department"]\n'
                'location_aliases={PFD="Pilot Fire Department", "Pilot FD"="Pilot Fire Department"}\n',
                encoding="utf-8")
            config = load_config(path)
        self.assertEqual(dict(config.location_aliases)["PFD"], "Pilot FD")
        self.assertEqual(dict(config.location_aliases)["Pilot Fire Department"], "Pilot FD")
        self.assertIn("Pilot FD", config.valid_locations)
        self.assertNotIn("Pilot Fire Department", config.valid_locations)

    def test_rejects_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text("[app]\napi_key = \"should-not-be-here\"\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "api_key"):
                load_config(path)
