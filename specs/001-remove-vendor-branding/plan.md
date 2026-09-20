# Implementation Plan: Remove Legacy Vendor Branding

**Branch**: `main` | **Date**: 2026-09-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-remove-vendor-branding/spec.md`

## Summary

Replace all application-owned vendor-specific integration terminology with the neutral concept **Training System**, while preserving the existing external activity contract and safety behavior. The implementation renames UI text, accessibility metadata, modules, classes, functions, command-line options, persisted properties, tests, documentation, external operational paths, and backup labels. Existing active data is converted before deployment through a generic, auditable migration tool driven by an external rename map; immutable historical backups are not rewritten. A repository audit supplied the prohibited token at runtime must return zero matches before release.

## Technical Context

**Language/Version**: Python 3.11+; deployment target uses Python 3.12

**Primary Dependencies**: Python standard library, PySide6 6.7+, NumPy 1.26+, Pillow 10.2+; no new runtime dependency planned

**Storage**: External JSON roster, JSON GUI state, exported JSON records, JSON mapping table, append-only JSONL receipt ledger, and timestamped filesystem backups under `C:\Temp`

**Testing**: `unittest` discovery with the existing 150-test offline suite, AST syntax parsing, repository text audit, synthetic migration fixtures, and mocked HTTP transport

**Target Platform**: Windows 11 desktop station; local-first OCR with explicit outbound access only during connection validation and confirmed submission

**Project Type**: Single Python package providing a CLI and a PySide6 desktop GUI

**Performance Goals**: Neutral naming must add no observable GUI latency; migration of 1,000 active JSON/JSONL records should complete within 10 seconds on the station PC, excluding backup copy time

**Constraints**: Zero case-insensitive matches for the prohibited product token in tracked files; no branded compatibility aliases in the final application; no live POST in automated tests; token remains memory-only; exactly one POST per confirmed submission; active data is backed up before mutation; historical backups remain immutable; department mappings remain outside Git

**Scale/Scope**: One station installation, dozens of roster members, hundreds of reviewed training records, one active queue state, one mapping table, one receipt ledger, and up to 20 retained startup snapshots

## Constitution Check

*GATE: Passed before Phase 0 and re-checked after Phase 1.*

The generated project constitution is still an unratified template and defines no enforceable project principles. The feature therefore adopts the following specification-derived gates:

- **G1 — Data safety**: Timestamped recovery snapshot exists before active operational data changes. **PASS**: migration contract requires preflight and backup before commit.
- **G2 — Submission safety**: Existing exact-visible-payload, single-POST, no-retry, uncertain-outcome lock, and duplicate protection remain unchanged. **PASS**: external submission and persistence contracts preserve them.
- **G3 — Secret safety**: No bearer token is persisted, backed up, logged, or added to the repository. **PASS**: token lifecycle remains memory-only.
- **G4 — Repository privacy**: Department-specific numeric mappings remain in the external mapping file. **PASS**: only mapping schema and neutral paths are documented in Git.
- **G5 — Complete removal**: Final tracked content has zero case-insensitive matches for the prohibited product token. **PASS**: audit is a release gate and new planning artifacts use neutral terminology.
- **G6 — Offline validation**: Automated tests use mocked transport and perform no live activity POST. **PASS**: test and quickstart contracts explicitly require offline execution.
- **G7 — Historical integrity**: Existing timestamped backups are never rewritten. **PASS**: migration scope is limited to active operational data.

### Post-Design Re-check

Phase 1 design introduces no gate violation. The generic migration engine avoids permanent branded aliases, the external rename map keeps legacy spelling outside tracked application content, conflict handling prevents partial overwrite, and the external submission contract remains unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/001-remove-vendor-branding/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   ├── cli-ui.md
│   ├── external-submission.md
│   ├── migration.md
│   └── persistence.md
└── tasks.md
```

### Source Code (repository root)

```text
src/fd_training_ocr/
├── training_system.py           # Neutral payload, mapping, and validation domain
├── training_system_client.py    # Neutral HTTP client and receipt ledger
├── gui.py                       # Neutral controls, messages, paths, and CLI options
├── gui_controller.py            # Backup and roster import helpers
├── validation.py                # Neutral roster staff-ID property
└── ...                          # Existing OCR pipeline remains unchanged

scripts/
├── Install-FDTrainingOCR.ps1
└── Migrate-FDTrainingOCRData.ps1

tests/
├── test_training_system.py
├── test_training_system_client.py
├── test_gui_controller.py
├── test_installer_assets.py
├── test_branding_audit.py
└── ...                          # Existing OCR tests remain unchanged
```

**Structure Decision**: Keep the existing single-package layout. Rename the two integration-specific modules and their tests to neutral destination paths, update cross-module imports in place, add one generic migration script, and add a dedicated branding-audit test. No new package or service is necessary.

## Implementation Approach

### 1. Establish a Neutral Vocabulary Map

Create one implementation checklist mapping every application-owned integration concept to its neutral replacement. The permanent vocabulary is:

- User-facing product reference: **Training System**
- Internal prefix: `training_system`
- Class prefix: `TrainingSystem`
- Roster identifier: `training_system_staff_id`
- CLI options: `--training-system-ids`, `--training-system-ledger`, and `--training-system-api-base`
- Default mapping file: `C:\Temp\training-system-ids.json`
- Default ledger: `C:\Temp\FDTrainingOCR-Training-System\submissions.jsonl`
- Backup subdirectory: `Training System`

The inventory must include case variants, module and test filenames, docstrings, comments, errors, accessibility metadata, record properties, README content, installer content, Spec Kit artifacts, and local default paths.

### 2. Rename the Permanent Application Surface

Rename integration modules, tests, symbols, record properties, roster fields, GUI controls, messages, accessibility names, parser options, backup parameters, and documentation. Use semantic edits for Python identifiers and imports; do not perform an unchecked global replacement. Preserve external request keys and endpoint paths exactly.

### 3. Migrate Active Operational Data Safely

Add a neutral generic migration script that consumes a rename map supplied from `C:\Temp` and therefore does not permanently encode the prohibited token. The script supports `-WhatIf`, validates source and destination paths, creates a timestamped pre-migration backup, rewrites active JSON structurally, copies or moves external files only after validation, preserves JSONL entry order, detects conflicts, writes a migration report, and is idempotent. The external rename map is operational input and is not committed.

Deployment order is: stop GUI, snapshot, dry-run migration, resolve conflicts, run migration, validate report and neutral files, deploy neutral application, run offline smoke tests, then reopen the GUI. Historical backup directories are excluded from migration.

### 4. Preserve the External Contract

Keep the existing service hostname, read-only validation lookup, activity endpoint, headers, body schema, category/location/station numeric values, visible JSON editor, staff resolution, confirmation, response interpretation, ledger digest, and duplicate-protection rules. Only application-owned names change.

### 5. Make Reintroduction Detectable

Add an offline test that reads the prohibited token from an environment variable or external test input and scans tracked text files. It must fail with file and line locations when a match appears. The token itself must not be committed. Combine this with semantic payload-equivalence tests, migration round trips, mocked connection/submission tests, AST parsing, and the complete existing suite.

## Release Strategy

1. **MVP — Neutral visible workflow**: Neutral UI, accessibility text, formatted-request controls, errors, and README language with unchanged payload behavior.
2. **Compatibility release preparation**: Generic migration tool, external rename map procedure, active-data backup, conflict detection, and recovery instructions.
3. **Permanent neutral internals**: Rename modules, symbols, CLI options, persisted properties, paths, tests, and installer assets; remove all permanent aliases.
4. **Release gate**: Run migration tests, payload equivalence, complete offline suite, AST parsing, and zero-match audit before push/deployment.

## Complexity Tracking

No constitution violations or additional architectural layers are introduced. The generic externally driven migration tool is necessary because permanent compatibility aliases would retain the prohibited token, while omitting migration would risk roster, review, and duplicate-protection data.
