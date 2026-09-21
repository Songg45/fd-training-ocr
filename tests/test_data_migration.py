from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "Migrate-FDTrainingOCRData.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL, "PowerShell is required")
class DataMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="fd-ocr-migration-")
        self.root = Path(self.temp_dir.name).resolve()
        self.backup_root = self.root / "backups"
        self.report_path = self.root / "reports" / "report.json"
        self.map_path = self.root / "rename-map.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_json(self, relative: str, value: object) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return path

    def write_map(
        self,
        *,
        targets: list[dict[str, object]] | None = None,
        file_moves: list[dict[str, object]] | None = None,
        directory_moves: list[dict[str, object]] | None = None,
        excluded_roots: list[str] | None = None,
        schema_version: int = 1,
    ) -> None:
        value = {
            "schema_version": schema_version,
            "property_renames": {"old_vendor_key": "training_system_key"},
            "json_targets": targets or [],
            "file_moves": file_moves or [],
            "directory_moves": directory_moves or [],
            "excluded_roots": excluded_roots or [str(self.backup_root)],
        }
        self.map_path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    def target(self, path: Path, *, format_: str = "json") -> dict[str, object]:
        return {
            "path": str(path),
            "kind": "file",
            "format": format_,
            "required": True,
        }

    def run_migration(self, *extra: str) -> subprocess.CompletedProcess[str]:
        command = [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-RenameMap",
            str(self.map_path),
            "-OperationalRoot",
            str(self.root),
            "-BackupRoot",
            str(self.backup_root),
            "-ReportPath",
            str(self.report_path),
            *extra,
        ]
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def read_report(self) -> dict[str, object]:
        return json.loads(self.report_path.read_text(encoding="utf-8-sig"))

    def test_rejects_unsupported_rename_map_schema(self) -> None:
        self.write_map(schema_version=99)

        result = self.run_migration("-WhatIf")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self.read_report()["status"], "failed")

    def test_rejects_target_outside_operational_root(self) -> None:
        outside_dir = Path(tempfile.mkdtemp(prefix="fd-ocr-outside-"))
        self.addCleanup(lambda: shutil.rmtree(outside_dir, ignore_errors=True))
        outside = outside_dir / "record.json"
        outside.write_text('{"old_vendor_key": 1}', encoding="utf-8")
        original = outside.read_bytes()
        self.write_map(targets=[self.target(outside)])

        result = self.run_migration()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(outside.read_bytes(), original)
        self.assertFalse(self.backup_root.exists())

    def test_commit_creates_backup_before_structural_json_write(self) -> None:
        record = self.write_json(
            "active/record.json",
            {
                "old_vendor_key": {"answer": 42},
                "note": "old_vendor_key remains text, not a global replacement",
                "members": [
                    {
                        "name": "Example One",
                        "unit_ids": ["EX54"],
                        "aliases": [],
                        "old_vendor_key": 501,
                    },
                    {
                        "name": "Example Two",
                        "unit_ids": ["EX55"],
                        "aliases": ["E. Two"],
                        "old_vendor_key": 502,
                    },
                ],
            },
        )
        original = record.read_bytes()
        self.write_map(targets=[self.target(record)])

        result = self.run_migration()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        migrated = json.loads(record.read_text(encoding="utf-8-sig"))
        self.assertNotIn("old_vendor_key", migrated)
        self.assertEqual(migrated["training_system_key"], {"answer": 42})
        self.assertEqual(
            migrated["note"],
            "old_vendor_key remains text, not a global replacement",
        )
        self.assertEqual(
            migrated["members"],
            [
                {
                    "name": "Example One",
                    "unit_ids": ["EX54"],
                    "aliases": [],
                    "training_system_key": 501,
                },
                {
                    "name": "Example Two",
                    "unit_ids": ["EX55"],
                    "aliases": ["E. Two"],
                    "training_system_key": 502,
                },
            ],
        )
        report = self.read_report()
        backup_path = Path(str(report["backup_path"]))
        self.assertTrue(backup_path.is_dir())
        backup_record = backup_path / "active" / "record.json"
        self.assertEqual(backup_record.read_bytes(), original)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["properties_renamed"], 3)

    def test_what_if_reports_ready_without_mutation_or_backup(self) -> None:
        record = self.write_json("active/record.json", {"old_vendor_key": "value"})
        original = record.read_bytes()
        self.write_map(targets=[self.target(record)])

        result = self.run_migration("-WhatIf")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(record.read_bytes(), original)
        self.assertFalse(self.backup_root.exists())
        report = self.read_report()
        self.assertEqual(report["status"], "ready")
        self.assertIsNone(report["backup_path"])

    def test_jsonl_entry_order_and_values_are_preserved(self) -> None:
        ledger = self.root / "active" / "submissions.jsonl"
        ledger.parent.mkdir(parents=True)
        entries = [
            {"sequence": 1, "old_vendor_key": "first", "payload": {"value": "A"}},
            {"sequence": 2, "old_vendor_key": "second", "payload": {"value": "B"}},
        ]
        ledger.write_text(
            "".join(json.dumps(entry) + "\n" for entry in entries),
            encoding="utf-8",
        )
        self.write_map(targets=[self.target(ledger, format_="jsonl")])

        result = self.run_migration()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        migrated = [json.loads(line) for line in ledger.read_text(encoding="utf-8-sig").splitlines()]
        self.assertEqual([entry["sequence"] for entry in migrated], [1, 2])
        self.assertEqual([entry["training_system_key"] for entry in migrated], ["first", "second"])
        self.assertEqual([entry["payload"] for entry in migrated], [{"value": "A"}, {"value": "B"}])

    def test_differing_existing_move_destination_is_a_conflict(self) -> None:
        source = self.root / "legacy-map.json"
        destination = self.root / "neutral-map.json"
        source.write_text('{"value": 1}', encoding="utf-8")
        destination.write_text('{"value": 2}', encoding="utf-8")
        self.write_map(
            file_moves=[
                {"source": str(source), "destination": str(destination), "required": True}
            ]
        )

        result = self.run_migration()

        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(source.read_text(encoding="utf-8"), '{"value": 1}')
        self.assertEqual(destination.read_text(encoding="utf-8"), '{"value": 2}')
        self.assertFalse(self.backup_root.exists())
        self.assertEqual(self.read_report()["status"], "conflict")

    def test_second_run_is_idempotent(self) -> None:
        record = self.write_json("active/record.json", {"old_vendor_key": "value"})
        source = self.root / "legacy-map.json"
        destination = self.root / "neutral-map.json"
        source.write_text('{"mapping": true}', encoding="utf-8")
        self.write_map(
            targets=[self.target(record)],
            file_moves=[
                {"source": str(source), "destination": str(destination), "required": False}
            ],
        )
        first = self.run_migration()
        first_bytes = record.read_bytes()
        backup_count = len([path for path in self.backup_root.rglob("*") if path.is_file()])

        second = self.run_migration()

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(record.read_bytes(), first_bytes)
        self.assertEqual(
            len([path for path in self.backup_root.rglob("*") if path.is_file()]),
            backup_count,
        )
        self.assertEqual(self.read_report()["status"], "already_migrated")

    def test_excluded_backup_root_cannot_be_a_json_target(self) -> None:
        protected = self.write_json("backups/old/record.json", {"old_vendor_key": 1})
        original = protected.read_bytes()
        self.write_map(targets=[self.target(protected)])

        result = self.run_migration()

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(protected.read_bytes(), original)

    def test_bare_operational_root_directory_scan_is_rejected(self) -> None:
        self.write_map(
            targets=[
                {
                    "path": str(self.root),
                    "kind": "directory",
                    "pattern": "*.json",
                    "recursive": False,
                    "format": "json",
                    "required": True,
                }
            ]
        )

        result = self.run_migration("-WhatIf")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("bare operational root", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
