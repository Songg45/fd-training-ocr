# Contract: Active Data Migration

## Command

```powershell
.\scripts\Migrate-FDTrainingOCRData.ps1 `
  -RenameMap C:\Temp\fd-training-ocr-integration-rename-map.json `
  -OperationalRoot C:\Temp `
  -BackupRoot C:\Temp\FDTrainingOCR-Backups `
  -WhatIf
```

Remove `-WhatIf` only after the dry-run report has no conflicts or errors.

## Inputs

- External rename-map JSON conforming to `data-model.md`.
- Explicit operational root.
- Explicit historical backup root to exclude.
- Current active roster, GUI state, exported records, mapping file, and receipt ledger when present.

## Guarantees

- Resolve and validate every path before mutation.
- Reject any target outside the approved operational root.
- Never traverse or rewrite the excluded historical backup root.
- Dry run performs no mutation.
- Commit mode creates a timestamped recovery snapshot before the first active-data write or move.
- JSON transformations are structural; arbitrary string values and embedded external payloads are not globally replaced.
- JSONL order and entry values are preserved.
- Writes use temporary siblings followed by atomic replacement where supported.
- Existing differing destinations cause a conflict; they are never overwritten silently.
- Re-running a completed migration makes no further changes and succeeds as already migrated.
- A machine-readable report is written for dry-run and commit modes.

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Ready in dry-run mode, completed in commit mode, or already migrated |
| `2` | Rename-map, path, or data validation failed before mutation |
| `3` | One or more source/destination conflicts require operator action |
| `4` | Backup creation failed; no mutation started |
| `5` | Migration failed after backup; recovery instructions are in the report |

## Deployment Sequence

1. Close the GUI and verify no OCR or submission operation is active.
2. Run `-WhatIf` and inspect the report.
3. Resolve every conflict.
4. Run commit mode and record the backup path.
5. Validate neutral files and identifier counts.
6. Install or update the neutral-only application.
7. Run offline smoke tests before reconnecting to the external service.
