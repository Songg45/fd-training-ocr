# Research: Remove Legacy Vendor Branding

## Decision 1: Use One Neutral Vocabulary

**Decision**: Use **Training System** in user-facing text, `training_system` in Python and persisted identifiers, and `TrainingSystem` for class names.

**Rationale**: The wording explains the integration's purpose without substituting another vendor identity. A single vocabulary prevents drift across GUI labels, accessibility text, errors, paths, and code.

**Alternatives considered**:

- **EPR Training**: More specific but still vendor-associated.
- **External System**: Too vague for operators and accessibility tools.
- **Training API**: Describes transport rather than the operator workflow.

## Decision 2: Do Not Keep Permanent Branded Aliases

**Decision**: Rename command-line options, internal symbols, persisted properties, modules, tests, files, paths, and GUI text without permanent legacy aliases.

**Rationale**: Any alias would leave the prohibited literal in application-owned content and fail the primary requirement. A planned migration is safer and more honest than indefinite dual naming.

**Alternatives considered**:

- **Deprecated aliases for one release**: Easier upgrades but directly violates zero-literal removal.
- **User-visible-only rename**: Leaves the product name throughout source, exports, and operational paths, which does not satisfy the request.

## Decision 3: Use a Generic Migration Engine With an External Rename Map

**Decision**: Commit a neutral migration engine but keep the deployment-specific old-to-new rename map external under `C:\Temp`.

**Rationale**: The application repository can reach zero matches while active roster, state, exports, mappings, and receipts are migrated deterministically. The same generic engine can be tested with synthetic legacy names.

**Alternatives considered**:

- **Hard-code old names in the migration script**: Reliable but leaves prohibited text in tracked application code.
- **Manual edits**: Error-prone for hundreds of exports and unsafe for duplicate-protection receipts.
- **Ignore existing data**: Risks losing staff resolution, review edits, and prior submission locks.

## Decision 4: Migrate Active Data, Preserve Historical Backups

**Decision**: Convert current operational files only. Treat existing timestamped backups as immutable recovery evidence.

**Rationale**: Rewriting backups destroys their value as known pre-migration restore points. The user asked to remove branding from the application, not to erase historical evidence.

**Alternatives considered**:

- **Rewrite every backup**: Expensive, destructive, and makes recovery less trustworthy.
- **Delete old backups**: Unacceptable data loss.

## Decision 5: Preserve the External Wire Contract

**Decision**: Change no service hostname, endpoint, authentication header, activity body field, category/location/station value, response interpretation, or one-attempt submission behavior.

**Rationale**: The change is nomenclature and local-data migration, not an external integration redesign. Wire changes would increase risk without helping the removal goal.

**Alternatives considered**:

- **Rebuild the integration client**: Unnecessary and would expand regression risk.
- **Wrap payload fields in a new schema**: Incompatible with the external service.

## Decision 6: Treat Remote Response Text as External Evidence

**Decision**: Do not rewrite response bodies received from the external service, even if future vendor-authored text contains the prohibited token.

**Rationale**: Receipt evidence must represent what the service actually returned. The zero-match release gate applies to tracked application-owned content, not unpredictable remote data in external operational files.

**Alternatives considered**:

- **Sanitize responses**: Weakens the audit trail and could hide useful rejection details.

## Decision 7: Add a Runtime-Supplied Branding Audit

**Decision**: The audit reads the prohibited token from an environment variable or external input and scans tracked text files case-insensitively.

**Rationale**: The test can enforce zero matches without committing the prohibited token in the test itself. Reporting file and line locations makes failures actionable.

**Alternatives considered**:

- **Commit the token in the test**: The test would fail its own requirement.
- **Rely on manual search**: Easy to forget and cannot prevent regression.

## Decision 8: No New Runtime Dependency

**Decision**: Implement migration and auditing with PowerShell, Python standard library, and existing test infrastructure.

**Rationale**: The application is already local-first and the station setup is constrained. The required transformations are small, structured file operations that do not justify another dependency.

**Alternatives considered**:

- **Add a migration framework**: Disproportionate to a one-installation file migration.

## Decision 9: Test Payload Equivalence Semantically

**Decision**: Compare parsed request objects for exact transmitted-field equality, not JSON whitespace or property order.

**Rationale**: The application already parses and serializes visible JSON. Semantic equality proves the external request is unchanged while allowing normal formatting differences.

**Alternatives considered**:

- **Byte-for-byte comparison**: Brittle and unrelated to service behavior.
