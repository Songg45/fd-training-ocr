---

description: "Dependency-ordered task list for neutralizing the external training integration"
---

# Tasks: Remove Legacy Vendor Branding

**Input**: Design documents from `specs/001-remove-vendor-branding/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Required by FR-014 through FR-016. Test tasks precede the implementation they validate and must fail for the intended reason before production changes begin.

**Organization**: Tasks are grouped by user story so visible neutralization, active-data retention, and final zero-match verification can be reviewed at separate checkpoints.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files and does not depend on another incomplete task.
- **[Story]**: Maps the task to User Story 1, 2, or 3.
- Every task names its destination file or external operational path.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Capture a complete baseline outside Git and prepare neutral test inputs before renaming any permanent application file.

- [ ] T001 Generate a case-insensitive baseline inventory of prohibited-token matches, including tracked file paths, line numbers, filenames, CLI names, persisted keys, and default local paths, and save it only to `C:\Temp\fd-training-ocr-branding-inventory.txt`
- [ ] T002 Build the deployment-specific old-to-new property/file/directory rename map conforming to `specs/001-remove-vendor-branding/data-model.md` at `C:\Temp\fd-training-ocr-integration-rename-map.json`, validate that it is outside Git, and include every active roster, state, export, mapping, and ledger rename
- [ ] T003 [P] Add synthetic neutral mapping, reviewed-record, roster, and receipt fixtures with no department data in `tests/fixtures/training-system-mapping.json` and `tests/fixtures/training-system-records.json`

**Checkpoint**: Baseline and rename map exist outside Git; no application file or active operational file has changed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build safety mechanisms that must exist before any permanent rename or active-data migration.

**⚠️ CRITICAL**: No user-story implementation begins until migration and audit safety tests exist.

- [ ] T004 [P] Write failing unit tests for rename-map validation, path containment, backup-before-write, structural JSON transformation, JSONL preservation, destination conflicts, idempotence, and excluded backup roots in `tests/test_data_migration.py`
- [ ] T005 [P] Write failing tests for a runtime-supplied prohibited-token audit, including case variants, filenames, line reporting, missing environment input, and tracked-file-only behavior in `tests/test_branding_audit.py`
- [ ] T006 Implement the generic `-WhatIf`-first migration engine, atomic file replacement, timestamped recovery snapshot, conflict exit codes, idempotence, and machine-readable report contract in `scripts/Migrate-FDTrainingOCRData.ps1` until T004 passes with synthetic names
- [ ] T007 Implement the runtime-supplied tracked-content and tracked-filename audit with actionable file/line reporting in `scripts/Test-NeutralBranding.ps1` until T005 passes without committing the prohibited token
- [ ] T008 Run only `tests/test_data_migration.py` and `tests/test_branding_audit.py`, record their offline pass results in `specs/001-remove-vendor-branding/validation-foundation.md`, and verify that neither test modifies `C:\Temp` active data

**Checkpoint**: Generic migration and audit foundations are independently tested; active operational data remains untouched.

---

## Phase 3: User Story 1 — Operate With Neutral Terminology (Priority: P1) 🎯 MVP

**Goal**: Deliver a complete review, connection, and submission workflow whose application-owned surface uses only neutral **Training System** terminology.

**Independent Test**: Launch with synthetic mapping and record data, inspect every integration control and accessibility name, edit dropdowns and JSON, open and cancel the masked connection prompt, and exercise mocked success/rejection/unknown outcomes without any live request or prohibited branding.

### Tests for User Story 1

- [ ] T009 [P] [US1] Rename and update the payload-domain test module to `tests/test_training_system.py`, importing neutral symbols and asserting neutral persisted properties, unchanged external body fields, location-field preservation, instructor staff inclusion, and strict validation; run it once to confirm expected import/name failures before implementation
- [ ] T010 [P] [US1] Rename and update the client/ledger test module to `tests/test_training_system_client.py`, asserting neutral classes and errors, one read-only GET, one exact visible-payload POST, no retry, token clearing, receipt durability, and duplicate locks; run it once to confirm expected import/name failures before implementation
- [ ] T011 [P] [US1] Add parser defaults, accessibility names, GUI labels, neutral status/error text, and exact-visible-JSON behavior assertions to `tests/test_gui_controller.py` and a new `tests/test_gui_vocabulary.py`

### Implementation for User Story 1

- [ ] T012 [P] [US1] Move the payload/mapping/validation domain into `src/fd_training_ocr/training_system.py`, rename public classes and helpers to the `TrainingSystem`/`training_system` vocabulary, rename record wrapper properties, and preserve all external request fields and values
- [ ] T013 [P] [US1] Move the HTTP client and submission ledger into `src/fd_training_ocr/training_system_client.py`, rename all application-owned classes/errors/messages, and preserve endpoint URLs, headers, one-request behavior, response handling, and digest logic
- [ ] T014 [P] [US1] Rename the roster model property, allowed-key validation, uniqueness checks, and error messages to `training_system_staff_id` in `src/fd_training_ocr/validation.py`
- [ ] T015 [US1] Update roster table serialization/import helpers and startup-backup parameter/subdirectory names to neutral vocabulary in `src/fd_training_ocr/gui_controller.py`, depending on T014
- [ ] T016 [US1] Update imports, parser options, default paths, instance properties, controls, accessibility metadata, dialogs, statuses, persisted wrapper keys, and exact-visible-payload workflow to neutral vocabulary in `src/fd_training_ocr/gui.py`, depending on T012 through T015
- [ ] T017 [P] [US1] Update GUI launch examples, roster schema, operational paths, connection/submission terminology, and installer/upgrade guidance in `README.md` and `scripts/Install-FDTrainingOCR.ps1`
- [ ] T018 [US1] Remove superseded integration-specific source/test paths after all imports resolve, run `tests/test_training_system.py`, `tests/test_training_system_client.py`, `tests/test_gui_controller.py`, and `tests/test_gui_vocabulary.py`, then perform the offline GUI smoke test in `specs/001-remove-vendor-branding/quickstart.md`

**Checkpoint**: User Story 1 is demonstrable as an MVP. Stop for operator review of visible wording and workflow before any active-data migration.

---

## Phase 4: User Story 2 — Retain Existing Operational Data (Priority: P2)

**Goal**: Convert active roster, state, exports, mapping, and ledger data without losing identifiers, reviews, drafts, source identity, receipts, or duplicate protection.

**Independent Test**: Migrate a disposable pre-change operational tree twice; the first run preserves every value under neutral names, the second makes no changes, conflicting destinations are left untouched, and historical backup fixtures remain byte-identical.

### Tests for User Story 2

- [ ] T019 [P] [US2] Extend `tests/test_data_migration.py` with representative roster, GUI state, exported reviewed request, invalid draft, mapping-file move, ledger-path move, submitted lock, unknown lock, equal dual-key, conflicting dual-key, interruption, and recovery fixtures
- [ ] T020 [P] [US2] Update startup-backup tests in `tests/test_gui_controller.py` to require the neutral mapping and ledger names, verify token absence, preserve the existing date/time layout, and prove older snapshots remain unchanged
- [ ] T021 [P] [US2] Add end-to-end migration assertions to `tests/test_installer_assets.py` for the neutral installer defaults, explicit pre-upgrade migration sequence, external rename-map requirement, and absence of any automatic live submission

### Implementation for User Story 2

- [ ] T022 [US2] Complete structural property migration, file and directory moves, equal-value collapse, conflict quarantine/reporting, JSONL order preservation, recovery instructions, and idempotent already-migrated detection in `scripts/Migrate-FDTrainingOCRData.ps1` until T019 and T021 pass
- [ ] T023 [US2] Update neutral startup-backup inputs and restore guidance in `src/fd_training_ocr/gui_controller.py`, `src/fd_training_ocr/gui.py`, and `README.md` until T020 passes
- [ ] T024 [US2] Execute `-WhatIf` and then commit mode against a disposable operational tree under `output/migration-validation/`, compare pre/post staff associations, reviewed payloads, invalid drafts, receipt identities, and backup hashes, and save the non-sensitive results to `specs/001-remove-vendor-branding/validation-migration.md`
- [ ] T025 [US2] Re-run the disposable migration from T024 and verify zero additional changes, identical neutral outputs, an `already migrated` result, and unchanged historical-backup hashes in `specs/001-remove-vendor-branding/validation-migration.md`
- [ ] T026 [US2] **STOP CHECKPOINT**: With the GUI closed, run migration `-WhatIf` against active `C:\Temp` data using `C:\Temp\fd-training-ocr-integration-rename-map.json`, present the report, conflicts, planned backup path, roster-ID count, and protected-receipt count for explicit operator approval; do not run commit mode in this task
- [ ] T027 [US2] After explicit operator approval of T026, run commit mode once, record the actual backup/report paths in `C:\Temp\FDTrainingOCR-Migration-Reports`, and validate the neutral roster, queue state, exports, mapping table, ledger, reviewed payloads, and duplicate locks before reopening the GUI

**Checkpoint**: User Story 2 is complete only after the operator approves the active-data dry run and the committed migration passes all count/hash/lock checks.

---

## Phase 5: User Story 3 — Verify Complete Removal and No Regression (Priority: P3)

**Goal**: Prove zero prohibited branding remains in tracked application-owned content and that payload, safety, backup, and review behavior did not regress.

**Independent Test**: Supply the prohibited token externally, run the tracked-content audit and complete offline suite, and compare representative pre/post external request objects. The audit reports zero matches, all required tests pass, and no live POST occurs.

### Tests for User Story 3

- [ ] T028 [P] [US3] Add semantic pre/post transmitted-payload equivalence cases for all four categories, all three locations, station, overnight time, total hours, instructor deduplication, and manually edited visible JSON in `tests/test_training_system.py`
- [ ] T029 [P] [US3] Add explicit no-live-network, one-attempt-only, unknown-outcome lock, malformed-ledger block, and token-non-persistence regression cases in `tests/test_training_system_client.py`
- [ ] T030 [P] [US3] Extend `tests/test_branding_audit.py` to scan tracked file contents and tracked path names, ignore `.git` and external operational data, and fail with deterministic diagnostics for injected case variants
- [ ] T031 [P] [US3] Update installer asset assertions for neutral options, paths, shortcut behavior, and migration prerequisites in `tests/test_installer_assets.py`

### Implementation and Validation for User Story 3

- [ ] T032 [US3] Run `scripts/Test-NeutralBranding.ps1` with `FD_OCR_PROHIBITED_TOKEN` supplied outside Git, remove every reported content/path match from tracked source, tests, docs, templates, Spec Kit artifacts, installer assets, comments, and accessibility text, and repeat until the report is empty
- [ ] T033 [US3] Run the complete offline suite with `python -B -m unittest discover -s tests -v`, confirm the opt-in live model test is the only allowed skip, and record test totals and zero live POSTs in `specs/001-remove-vendor-branding/validation-final.md`
- [ ] T034 [US3] Parse every Python file under `src/` and `tests/`, run `git diff --check`, verify no superseded integration-specific filenames remain tracked, and append the results to `specs/001-remove-vendor-branding/validation-final.md`
- [ ] T035 [US3] Benchmark migration of 1,000 synthetic JSON/JSONL records, require completion within 10 seconds excluding backup copy time, and record hardware, timing, counts, and outcome in `specs/001-remove-vendor-branding/validation-final.md`

**Checkpoint**: All three stories are independently validated. No live production submission has been attempted.

---

## Phase 6: Polish & Cross-Cutting Release Review

**Purpose**: Close documentation, safety, and delivery gaps after all selected user stories pass.

- [ ] T036 [P] Reconcile implemented commands, paths, exit codes, and expected results with `specs/001-remove-vendor-branding/quickstart.md`, `specs/001-remove-vendor-branding/contracts/`, and `README.md`
- [ ] T037 [P] Review `src/fd_training_ocr/training_system.py`, `src/fd_training_ocr/training_system_client.py`, `src/fd_training_ocr/gui.py`, and `scripts/Migrate-FDTrainingOCRData.ps1` for token exposure, response-evidence preservation, path safety, destructive operations, and one-POST/no-retry compliance
- [ ] T038 Run the requirements checklist against the final implementation, mark implementation evidence separately in `specs/001-remove-vendor-branding/validation-final.md`, commit the reviewed change, push it, and stop before any live external end-to-end submission

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 — Setup**: Starts immediately and performs no repository or active-data mutation beyond synthetic fixtures.
- **Phase 2 — Foundational**: Depends on T001–T003 and blocks every user story.
- **Phase 3 — US1**: Depends on T004–T008. Delivers the visible neutral MVP.
- **Phase 4 — US2**: Depends on the neutral schemas and paths from US1 plus the migration foundation. T027 additionally requires explicit operator approval after T026.
- **Phase 5 — US3**: Depends on the application and data changes selected for release; it can begin test authoring after Phase 2 but final validation waits for US1 and US2.
- **Phase 6 — Polish**: Depends on all selected stories and their checkpoints.

### User Story Dependencies

- **US1 (P1)**: Starts after foundational safety and has no dependency on active-data migration. It is the recommended MVP.
- **US2 (P2)**: Depends on US1's neutral property/path contract so the migration has stable destinations.
- **US3 (P3)**: Verification tests can be authored in parallel, but final zero-match and regression proof depend on US1 and US2 completion.

### Within Each User Story

- Write or rename tests first and confirm expected failure before implementation.
- Establish neutral domain/client symbols before updating GUI consumers.
- Update schema validators before roster serialization/import consumers.
- Complete disposable migration validation before touching active `C:\Temp` data.
- Stop for operator review at every explicit checkpoint.
- Run the zero-match audit only with the prohibited token supplied outside Git.

## Parallel Opportunities

- T003 can run while T001 and T002 inventory operational data.
- T004 and T005 can run in parallel.
- T009, T010, and T011 can run in parallel before US1 implementation.
- T012, T013, and T014 touch separate modules and can run in parallel.
- T017 can run alongside T012–T016 after neutral vocabulary is fixed.
- T019, T020, and T021 can run in parallel.
- T028, T029, T030, and T031 can run in parallel.
- T036 and T037 can run in parallel after final validation.

## Parallel Example: User Story 1

```text
Task T009: Prepare neutral payload-domain tests in tests/test_training_system.py
Task T010: Prepare neutral client/ledger tests in tests/test_training_system_client.py
Task T011: Prepare GUI vocabulary tests in tests/test_gui_vocabulary.py

Then, after those tests fail for the intended naming reasons:

Task T012: Create the neutral domain module in src/fd_training_ocr/training_system.py
Task T013: Create the neutral client module in src/fd_training_ocr/training_system_client.py
Task T014: Update roster schema naming in src/fd_training_ocr/validation.py
```

## Parallel Example: User Story 3

```text
Task T028: Payload equivalence tests
Task T029: HTTP and ledger safety tests
Task T030: Repository branding-audit tests
Task T031: Installer contract tests
```

## Implementation Strategy

### MVP First — User Story 1

1. Complete Setup and Foundational phases.
2. Complete T009–T018.
3. Stop and validate the visible workflow with the operator.
4. Do not migrate active data or submit a live activity.

### Incremental Delivery

1. **Foundation**: External inventory, rename map, generic migration safety, and audit safety.
2. **US1**: Neutral application experience and internals with synthetic data.
3. **US2**: Disposable migration, operator-reviewed active dry run, then approved active migration.
4. **US3**: Zero-match proof, payload equivalence, complete offline regression, and performance evidence.
5. **Polish**: Contract/document reconciliation, security review, commit, and push.

### Checkpoint Policy

- Stop after T008 for migration/audit foundation review.
- Stop after T018 for neutral GUI review.
- Stop after T026 and require explicit approval before T027 mutates active data.
- Stop after T035 for final evidence review.
- T038 explicitly excludes a live external submission; that remains a separate user-controlled test.

## Notes

- `[P]` tasks operate on different files or independent test areas.
- Tests are intentionally required because the specification mandates migration, payload, safety, and audit proof.
- The external rename map and prohibited token must never be committed.
- Historical backups are immutable and excluded from migration.
- Remote response bodies are audit evidence and are not sanitized.
- Commit after each checkpoint or cohesive task group so rollback remains simple.
