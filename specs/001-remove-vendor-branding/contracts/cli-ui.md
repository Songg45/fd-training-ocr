# Contract: Neutral CLI and Desktop UI

## Command-Line Options

The GUI accepts these neutral integration options:

| Option | Value | Default |
|--------|-------|---------|
| `--training-system-ids` | Path to external mapping JSON | `C:\Temp\training-system-ids.json` |
| `--training-system-ledger` | Path to append-only receipt JSONL | `C:\Temp\FDTrainingOCR-Training-System\submissions.jsonl` |
| `--training-system-api-base` | External service API base URL | Existing EPR Systems API URL |

Requirements:

- Branded aliases are not accepted by the final application.
- Missing or invalid mappings disable external submission without disabling OCR review.
- No option, help text, parser error authored by the application, or documentation contains the prohibited product token.

## Desktop Labels

The formatted request tab exposes:

- **Training Category** dropdown.
- **Class Location** dropdown.
- **Station** read-only value.
- **Exact JSON payload that will be sent** label and editable JSON.
- **Connect to Training System** button.
- **Submit to Training System** button.
- Neutral connection and submission status text.

Accessibility names and descriptions use the same neutral concepts.

## Behavior

- Dropdown changes update the exact visible JSON immediately.
- Unrelated manual JSON edits remain intact.
- Submission parses and validates the exact visible JSON.
- The confirmation identifies the title, dates, category, location, station, and participant count.
- Error, success, rejection, and uncertain-outcome dialogs use neutral terminology while retaining existing safety instructions.
