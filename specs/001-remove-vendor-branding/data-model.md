# Data Model: Remove Legacy Vendor Branding

## 1. Training System Mapping

Represents department-specific identifiers used to build the external activity request.

### Fields

- `schema_version`: Integer; required; currently `1`.
- `categories`: Array of category records.
  - `name`: Non-empty unique display name.
  - `id`: Positive unique integer among selectable categories.
  - `status`: Non-empty verification status.
- `locations`: Array of location records.
  - `name`: Non-empty unique display name.
  - `id`: Positive unique integer among selectable locations.
  - `upsize_ts`: Non-empty external concurrency/version value.
- `station`: Single station record.
  - `name`: Non-empty station name.
  - `id`: Positive integer.

### Validation

- All four required category names and all three required location names must be present.
- Department numeric values remain external to Git.
- The active mapping file defaults to `C:\Temp\training-system-ids.json`.

## 2. Roster Member

Represents one department member and the external identity needed for activity participation.

### Fields

- `name`: Required canonical local name.
- `unit_ids`: One or more unique department identifiers.
- `aliases`: Zero or more alternate names.
- `training_system_staff_id`: Optional positive integer; unique across members.

### Migration Rule

The external rename map moves the existing integration-specific numeric property to `training_system_staff_id` without changing its value or member association. Conflicting old and new values fail migration.

## 3. Reviewed Training Record

Represents OCR evidence, human corrections, event selections, attendees, and external-submission preparation for one source page.

### Neutral Integration Properties

- `training_system_selection`
  - `category`: Selected neutral category name or empty string.
  - `location`: Selected neutral location name or empty string.
- `training_system_request_review`
  - `payload`: Last valid reviewed request object.
  - `reviewed_at`: UTC timestamp.
- `training_system_request_draft`: Invalid editor text preserved verbatim.
- `training_system_submission`
  - `status`: `submitted`, `rejected`, or `unknown`.
  - `recorded_at`: UTC receipt timestamp when available.
  - `payload_sha256`: Stable digest of the submitted payload.
  - `activity_id`: Positive external identifier or null.
  - `error`: Error detail or null.

### State Transitions

```text
generated request
    -> valid reviewed request
    -> confirmed submission
        -> submitted (locked)
        -> rejected (editable and eligible for corrected retry)
        -> unknown (locked pending reconciliation)

generated/reviewed request
    -> invalid draft (preserved, submission blocked)
```

### Migration Rules

- Preserve payload objects and invalid draft text exactly.
- Rename application-owned wrapper properties only; never alter fields inside the external activity payload.
- Preserve `source_sha256` and `page`, because they form the duplicate-protection identity.
- If both legacy and neutral properties exist with equal values, retain one neutral property.
- If both exist with different values, stop that file with a conflict and do not overwrite it.

## 4. Submission Receipt

Append-only JSONL evidence for one submission attempt.

### Fields

- `schema_version`: Integer.
- `recorded_at`: UTC timestamp.
- `status`: `submitted`, `rejected`, or `unknown`.
- `source_file`: Original source filename.
- `source_sha256`: Required source digest.
- `page`: Source page number or null.
- `payload_sha256`: Stable request digest.
- `payload`: Exact parsed request object sent.
- `activity_id`: External identifier or null.
- `response`: Parsed service response or null.
- `error`: Error detail or null.

### Migration Rules

- Preserve entry order and every existing value.
- The schema does not require branded property renames; only the active ledger path becomes neutral.
- A receipt with `submitted` or `unknown` continues to block the same source identity.

## 5. Migration Rename Map

External operational input that tells the generic migration engine how to convert the current installation without committing prohibited text.

### Fields

- `schema_version`: Required integer, initially `1`.
- `property_renames`: Object mapping exact old property names to neutral property names.
- `file_moves`: Array of move records.
  - `source`: Absolute source path under the approved operational root.
  - `destination`: Absolute neutral destination path under the approved operational root.
- `directory_moves`: Array using the same source/destination rules.
- `excluded_roots`: Array containing historical backup roots that must never be modified.

### Validation

- Paths must resolve below explicitly approved operational roots.
- Source and destination cannot be equal.
- Two sources cannot target the same destination.
- Destination conflicts must be byte-identical or migration stops.
- The map is external and must not be copied into tracked files or timestamped source-control artifacts.

## 6. Migration Report

Durable record of one dry run or committed migration.

### Fields

- `schema_version`: Integer.
- `started_at` and `completed_at`: UTC timestamps.
- `mode`: `what_if` or `commit`.
- `backup_path`: Timestamped recovery snapshot or null during a dry run.
- `files_examined`: Non-negative integer.
- `files_changed`: Non-negative integer.
- `files_moved`: Non-negative integer.
- `properties_renamed`: Non-negative integer.
- `conflicts`: Array of actionable conflict records.
- `errors`: Array of failure records.
- `status`: `ready`, `completed`, `conflict`, or `failed`.

### State Transitions

```text
created -> preflight
preflight -> ready | conflict | failed
ready -> backed_up -> completed | failed
```

No active file is changed before `ready` and `backed_up` are established.
