from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "Test-NeutralBranding.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL and shutil.which("git"), "PowerShell and Git are required")
class BrandingAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="fd-ocr-audit-")
        self.repo = Path(self.temp_dir.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write(self, relative: str, text: str, *, tracked: bool = True) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if tracked:
            subprocess.run(["git", "-C", str(self.repo), "add", "--", relative], check=True)
        return path

    def run_audit(self, token: str | None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("FD_OCR_PROHIBITED_TOKEN", None)
        if token is not None:
            environment["FD_OCR_PROHIBITED_TOKEN"] = token
        return subprocess.run(
            [
                POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-RepositoryRoot",
                str(self.repo),
            ],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    def test_reports_case_insensitive_content_matches_with_line_numbers(self) -> None:
        self.write("src/dirty.txt", "clean line\nOLD_VENDOR appears here\n")

        result = self.run_audit("old_vendor")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CONTENT src/dirty.txt:2:", result.stdout)
        self.assertIn("OLD_VENDOR appears here", result.stdout)

    def test_reports_tracked_filename_matches(self) -> None:
        self.write("docs/OLD_VENDOR-notes.txt", "content is otherwise clean\n")

        result = self.run_audit("old_vendor")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("PATH docs/OLD_VENDOR-notes.txt", result.stdout)

    def test_utf16_tracked_text_is_scanned_instead_of_treated_as_binary(self) -> None:
        path = self.repo / "docs" / "utf16.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("clean line\nOlD_VeNdOr appears here\n", encoding="utf-16")
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "--", "docs/utf16.txt"],
            check=True,
        )

        result = self.run_audit("old_vendor")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("CONTENT docs/utf16.txt:2:", result.stdout)

    def test_untracked_files_are_not_scanned(self) -> None:
        self.write("tracked.txt", "clean\n")
        self.write("untracked-old_vendor.txt", "old_vendor\n", tracked=False)

        result = self.run_audit("old_vendor")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No prohibited token matches", result.stdout)

    def test_missing_runtime_token_is_a_configuration_error(self) -> None:
        self.write("tracked.txt", "clean\n")

        result = self.run_audit(None)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("FD_OCR_PROHIBITED_TOKEN", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
