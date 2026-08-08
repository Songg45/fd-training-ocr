from pathlib import Path
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class InstallerAssetTests(unittest.TestCase):
    def test_station_installer_contains_required_local_components(self):
        script = (ROOT / "scripts" / "Install-FDTrainingOCR.ps1").read_text(encoding="utf-8")
        for token in ("Python.Python.3.12", "oschwartz10612.Poppler", "Ollama.Ollama",
                      "qwen2.5vl:7b", "qwen3-vl:8b-instruct", "fd-training-ocr-gui.exe"):
            self.assertIn(token, script)
        self.assertNotIn(".cache\\codex-runtimes", script)
        self.assertIn("C:\\Temp", script)

    def test_versioned_clean_master_is_a_readable_blank_template_asset(self):
        master = ROOT / "templates" / "pilot_fd_training_sign_in" / "v1" / "cleaned-master.png"
        with Image.open(master) as image:
            self.assertGreaterEqual(image.width, 2000)
            self.assertGreaterEqual(image.height, 3000)
            self.assertEqual(image.format, "PNG")


if __name__ == "__main__":
    unittest.main()
