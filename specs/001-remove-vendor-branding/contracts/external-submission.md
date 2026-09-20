# Contract: External Training Activity Submission

## Connection Validation

- Input: bearer token supplied through a masked GUI field.
- Token lifecycle: memory only; cleared on replacement, connection failure, and application close.
- Request count: exactly one read-only lookup per Connect action.
- Expected success: HTTP success, response code value `0`, and an array result.
- Failure: connection remains disconnected and a neutral actionable error is shown.

## Activity Submission

- Endpoint: existing EPR Systems `addActivity` endpoint.
- Method: one HTTP POST after explicit reviewer confirmation.
- Body: the exact visible JSON parsed as one object and serialized normally.
- Retry policy: no automatic retry.
- Four-hundred-level response: definite rejection.
- Network loss, timeout, unreadable response, or server error: unknown outcome; record locks until reconciliation.
- Successful or unknown receipt: prevents another POST for the same `source_sha256` and page.

## Payload Invariants

The neutralization work does not change:

- External body property names.
- Category, location, station, or staff numeric values.
- Date/time format.
- Total-hours representation.
- HTML instruction content.
- Location subobject values.
- Staff inclusion and deduplication rules.
- Payload digest calculation.

## Test Boundary

Automated tests use a recording mock transport. No automated test may connect to the live service or submit an activity.
