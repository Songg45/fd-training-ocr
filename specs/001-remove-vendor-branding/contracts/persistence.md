# Contract: Neutral Persistence and Paths

## Roster

Each member may contain:

```json
{
  "name": "Example Member",
  "unit_ids": ["4554"],
  "aliases": ["E. Member"],
  "training_system_staff_id": 20
}
```

`training_system_staff_id` is optional, positive, numeric, and unique across members.

## Reviewed Record Properties

Application-owned integration metadata uses only:

- `training_system_selection`
- `training_system_request_review`
- `training_system_request_draft`
- `training_system_submission`

The activity body stored below the review property remains unchanged because it is the external contract.

## Default Active Paths

| Artifact | Neutral default |
|----------|-----------------|
| Mapping table | `C:\Temp\training-system-ids.json` |
| Receipt ledger | `C:\Temp\FDTrainingOCR-Training-System\submissions.jsonl` |
| Backup integration subdirectory | `Training System` |

## Startup Backup

- Include active exported JSON, roster, GUI state, configuration, neutral mapping table, and neutral receipt ledger when present.
- Never include a bearer token.
- Preserve the existing date/time snapshot layout and retention count.
- Never edit an older snapshot in place.

## Receipt Ledger

- Remains append-only JSONL.
- Preserves the existing schema and source identity rules.
- A malformed ledger blocks submission rather than bypassing duplicate protection.
- Submitted and unknown identities remain locked after path migration.
