# Quickstart Validation: Remove Legacy Vendor Branding

This guide validates the planned change without contacting the live external service.

## Prerequisites

- Windows PowerShell.
- Repository dependencies installed.
- A copy of representative active operational data, never the only copy.
- The prohibited product token supplied outside Git through `FD_OCR_PROHIBITED_TOKEN`.
- The deployment-specific rename map stored at `C:\Temp\fd-training-ocr-integration-rename-map.json`.

## 1. Run Baseline Offline Tests

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m unittest discover -s tests -v
```

Expected: all offline tests pass; the explicitly opt-in live model test may remain skipped.

## 2. Dry-Run Active Data Migration

```powershell
.\scripts\Migrate-FDTrainingOCRData.ps1 `
  -RenameMap C:\Temp\fd-training-ocr-integration-rename-map.json `
  -OperationalRoot C:\Temp `
  -BackupRoot C:\Temp\FDTrainingOCR-Backups `
  -WhatIf
```

Expected: exit code `0`, no changed files, and a report with status `ready` or `already migrated`.

## 3. Test Migration With Disposable Fixtures

Run the migration test module using synthetic old/new integration names:

```powershell
$env:PYTHONPATH = 'src'
python -B -m unittest tests.test_data_migration -v
```

Expected: backup, idempotence, conflict, JSON, JSONL, path-boundary, and recovery cases pass.

## 4. Run the Branding Audit

```powershell
if ([string]::IsNullOrWhiteSpace($env:FD_OCR_PROHIBITED_TOKEN)) {
  throw 'Set FD_OCR_PROHIBITED_TOKEN outside the repository before running the audit.'
}

git grep -in -- "$env:FD_OCR_PROHIBITED_TOKEN"
```

Expected: no output and exit code `1`, meaning the token was not found in tracked content. The automated audit test interprets that result as success.

## 5. Verify Payload Equivalence

```powershell
$env:PYTHONPATH = 'src'
python -B -m unittest tests.test_training_system tests.test_training_system_client -v
```

Expected: identical transmitted-field objects for representative reviewed records, exactly one mocked POST, no retry, and unchanged duplicate protection.

## 6. Validate Python Syntax

```powershell
python -B -c "import ast,pathlib; files=list(pathlib.Path('src').rglob('*.py'))+list(pathlib.Path('tests').rglob('*.py')); [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print(f'AST parsed {len(files)} Python files')"
```

Expected: every Python file parses successfully.

## 7. Fresh-Install GUI Smoke Test

1. Start the GUI with the neutral mapping and ledger options.
2. Load a disposable processed record.
3. Confirm all integration controls, dialogs, accessibility labels, and paths use **Training System** terminology.
4. Change category and location and verify the exact visible JSON updates without losing unrelated edits.
5. Open the masked connection prompt, then cancel it. Do not provide a production token during offline validation.

Expected: the review workflow is usable, no live request is made, and no prohibited branding is visible.

## 8. Upgrade Validation Before Deployment

After the dry run is clean and the GUI is closed:

1. Run the migration without `-WhatIf`.
2. Record the timestamped backup and migration report paths.
3. Compare roster staff-ID count and member associations before and after.
4. Confirm all submitted and unknown receipt identities still block duplicates.
5. Confirm current exports retain reviewed payloads and invalid drafts exactly.
6. Re-run Steps 1, 4, 5, 6, and 7.

Only after these checks pass should the upgraded station installation reconnect to the external service.
