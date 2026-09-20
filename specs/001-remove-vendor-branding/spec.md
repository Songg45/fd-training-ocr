# Feature Specification: Remove Legacy Vendor Branding

**Feature Branch**: `main`

**Created**: 2026-09-19

**Status**: Ready for Planning

**Input**: User description: "Remove the legacy vendor product name from the application and produce a complete implementation plan and task list."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operate With Neutral Terminology (Priority: P1)

An operator reviews OCR results, selects the external training category and location, connects, and submits a record without seeing the legacy vendor product name anywhere in the application.

**Why this priority**: Removing the product-specific identity from the active workflow is the primary requested outcome and provides immediate value independently of deeper data cleanup.

**Independent Test**: Launch the application with a processed record and complete the review-through-submission workflow in a non-production test environment. Every label, message, dialog, accessibility name, path shown to the operator, and exported active record uses neutral training-system terminology.

**Acceptance Scenarios**:

1. **Given** a processed OCR record, **When** the operator opens the formatted request tab, **Then** all controls and explanatory text use neutral training-system terminology while the exact outgoing JSON remains visible and editable.
2. **Given** valid connection details, **When** the operator connects and submits a reviewed record, **Then** confirmation, progress, success, rejection, and uncertain-outcome messages contain no legacy vendor branding.
3. **Given** a field or connection error, **When** the application reports the problem, **Then** the error uses neutral terminology and preserves the existing safety guidance.

---

### User Story 2 - Retain Existing Operational Data (Priority: P2)

An operator upgrades an existing installation and continues working with the current roster, queue state, reviewed exports, mappings, and submission receipts without losing staff identifiers, corrections, duplicate protection, or audit history.

**Why this priority**: A terminology change must not invalidate reviewed work or create a risk of duplicate external submissions.

**Independent Test**: Start with a copy of representative pre-change operational data, run the documented migration, open the upgraded application, and verify that every roster association, reviewed payload, queue entry, and protected submission outcome is retained under neutral names.

**Acceptance Scenarios**:

1. **Given** an existing roster containing external staff identifiers, **When** the migration completes, **Then** every member retains the same numeric identifier and remains resolvable during payload generation.
2. **Given** existing reviewed request data and queue state, **When** the upgraded application opens them, **Then** reviewed values and current queue position remain available.
3. **Given** a prior submitted or uncertain receipt, **When** the upgraded application evaluates the same source record, **Then** duplicate-submission protection remains active.
4. **Given** a migration interruption, **When** the operator follows the recovery guide, **Then** the pre-migration snapshot can restore the active data without modifying historical backups.

---

### User Story 3 - Verify Complete Removal and No Regression (Priority: P3)

A maintainer can prove that the tracked application contains no occurrence of the legacy vendor product name and that the neutralized integration retains the same payload, safety, backup, and review behavior.

**Why this priority**: The removal is incomplete if hidden identifiers, documentation, tests, accessibility text, or generated paths still expose the old name; automated proof prevents reintroduction.

**Independent Test**: Run the repository-wide branding audit and the complete automated test suite. The audit reports zero case-insensitive matches in tracked content, all offline tests pass, and no live external submission is made.

**Acceptance Scenarios**:

1. **Given** the completed change, **When** all tracked source, tests, documentation, templates, installer assets, and configuration examples are scanned case-insensitively, **Then** the legacy vendor product name has zero matches.
2. **Given** the pre-change and post-change generated activity payloads for the same reviewed record, **When** their external-system fields are compared, **Then** they are equivalent except for local metadata that is not transmitted.
3. **Given** a timeout or indeterminate submission result, **When** the operator attempts another submission, **Then** the record remains locked pending reconciliation.
4. **Given** the automated suite, **When** it runs during validation, **Then** it performs no live external POST and all required tests pass.

### Edge Cases

- Active data contains both legacy-prefixed and neutral-prefixed properties because a previous migration was interrupted.
- The neutral destination file already exists and differs from the legacy source file.
- The external mapping file or receipt ledger is absent, empty, malformed, or read-only.
- A case-variant of the legacy product name appears in an accessibility label, exception string, comment, test fixture, or generated documentation.
- A submitted or uncertain receipt exists only in the ledger, only in an exported record, or in both locations.
- An invalid formatted-request draft must survive migration without being parsed or normalized.
- Historical timestamped backups contain old terminology; they must remain immutable and recoverable rather than being rewritten.
- The external service changes its response text to include its own product branding; remote response content is not application-owned and must not be rewritten before being recorded.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The application MUST use the neutral user-facing term **Training System** for the external training integration.
- **FR-002**: All tracked application-owned source, tests, documentation, configuration examples, installer assets, templates, accessibility metadata, comments, and local path defaults MUST contain zero case-insensitive occurrences of the legacy vendor product name.
- **FR-003**: All application-owned identifiers, filenames, command-line options, persisted property names, backup subdirectories, mapping filenames, and receipt-ledger paths associated with the integration MUST use neutral terminology.
- **FR-004**: The activity payload sent to the external training service MUST preserve the existing field names, values, validation rules, staff identifiers, category and location mappings, station value, and exact-visible-JSON review behavior.
- **FR-005**: The connection token MUST remain masked, memory-only, and discarded when the application closes.
- **FR-006**: Connection validation MUST remain read-only, and each approved activity submission MUST remain a single POST with no automatic retry.
- **FR-007**: Submitted and uncertain outcomes MUST remain protected against duplicate submission after terminology and data migration.
- **FR-008**: The migration process MUST preserve all active roster staff identifiers, reviewed request payloads, invalid drafts, queue state, external mappings, submission receipts, and source-record identity values.
- **FR-009**: The migration process MUST create a timestamped recovery snapshot before changing active operational data and MUST be safe to run more than once.
- **FR-010**: Historical timestamped backups MUST NOT be rewritten or deleted by the migration.
- **FR-011**: The application MUST reject ambiguous conflicts when both legacy and neutral active values exist and differ, leaving the source data recoverable and providing an actionable message.
- **FR-012**: Deprecated branded command-line options and filenames MUST NOT remain as aliases in the final application because aliases would retain the prohibited literal; the upgrade guide MUST identify the neutral replacements.
- **FR-013**: The installer and upgrade documentation MUST place new operational data in neutral external paths and MUST preserve existing data until migration validation succeeds.
- **FR-014**: Automated validation MUST cover fresh installations, upgraded active data, formatted request editing, category and location selection, connection validation, one-attempt submission behavior, receipt recording, duplicate protection, startup backup coverage, and token disposal.
- **FR-015**: Automated validation MUST NOT perform a live activity submission.
- **FR-016**: A repeatable repository audit MUST fail when any tracked application-owned file reintroduces the legacy vendor product name.

### Key Entities

- **Integration Vocabulary**: The approved neutral names used in user-facing text, internal identifiers, command-line options, persisted properties, files, and directories.
- **Active Operational Data**: The roster, GUI state, exported records, mapping table, and receipt ledger used by the current installation.
- **Submission Receipt**: The durable record of a submitted, rejected, or uncertain request, including source identity, payload digest, activity identifier, response, and error state.
- **Migration Report**: The outcome of a migration attempt, including backup location, migrated artifacts, skipped artifacts, conflicts, validation results, and completion status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A case-insensitive audit of all tracked application-owned files returns exactly zero matches for the legacy vendor product name.
- **SC-002**: One hundred percent of external staff identifiers in the pre-migration active roster are present and associated with the same members after migration.
- **SC-003**: One hundred percent of submitted and uncertain receipt identities continue to block duplicate submission after migration.
- **SC-004**: For a representative set of reviewed records, the external activity payload before and after neutralization is equivalent for every transmitted field.
- **SC-005**: An operator can complete connection, review, and confirmed submission using only neutral terminology, with no additional workflow steps beyond the existing explicit confirmation.
- **SC-006**: All automated offline tests pass, the branding audit passes, and the validation run performs zero live activity POSTs.
- **SC-007**: A failed or interrupted migration leaves a usable timestamped recovery snapshot and reports the failure without partially overwriting a conflicting destination.

## Assumptions

- **Training System** is the approved neutral replacement term for user-facing language.
- Neutral internal names use the concept **training system** rather than another vendor or product identity.
- The external service hostname, endpoint paths, authentication behavior, and request/response field names remain unchanged because they are operational contracts and do not contain the prohibited product name.
- Only active operational data is migrated. Existing timestamped backups are immutable historical recovery artifacts and remain unchanged.
- Remote response text is external evidence and may contain vendor-authored language; the application does not manufacture or rewrite that content.
- Backward-compatible branded aliases are intentionally out of scope because retaining them would violate the zero-literal requirement. Migration occurs before the neutral-only application is put into service.
- This feature is planning-only in the current change; implementation and production submission testing occur in later, explicitly approved work.
