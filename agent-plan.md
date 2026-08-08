# FD Training Form OCR - Agent Implementation Plan

## Objective

Build a reliable, auditable pipeline that extracts structured data from standardized fire department training sign-in sheets. The system must handle scanned PDFs containing handwriting, checkmarks, table entries, and signatures without treating the page as an unstructured block of text.

The initial form is the "Pilot Fire Department Training Sign-in Sheet." The architecture should support additional form revisions through versioned templates rather than form-specific rewrites.

## Core Principles

- Preserve every source PDF unchanged.
- Align each scan to a clean master template before extraction.
- Extract known fields from fixed regions instead of running whole-page OCR.
- Use deterministic image processing for alignment, checkbox detection, line removal, and blank-row detection.
- Use handwriting-capable vision recognition only for populated text regions.
- Store confidence, alternatives, and source coordinates with every extracted value.
- Route uncertain or contradictory results to human review.
- Preserve signatures as image evidence; do not identify or transcribe signers from signatures.
- Never silently correct conflicting values.

## Initial Output Schema

```json
{
  "source_file": "string",
  "form_type": "pilot_fd_training_sign_in",
  "form_version": "v1",
  "page": 1,
  "date": null,
  "start_time": null,
  "end_time": null,
  "total_hours_written": null,
  "total_hours_calculated": null,
  "location": null,
  "training_types": [],
  "instructor": null,
  "facilities": [],
  "attendees": [
    {
      "unit_id": null,
      "print_name": null,
      "signature_crop": null
    }
  ],
  "trucks_used": [],
  "description": null,
  "warnings": [],
  "review_required": false
}
```

Each recognized field should also have internal metadata containing its bounding box, confidence, recognition method, raw result, normalized result, and optional alternatives. Export a simplified CSV separately for operational use.

## Repository Layout

```text
fd-training-ocr/
  agent-plan.md
  README.md
  pyproject.toml
  src/fd_training_ocr/
    cli.py
    config.py
    pdf_render.py
    alignment.py
    preprocessing.py
    template.py
    checkbox_detection.py
    table_extraction.py
    recognition.py
    normalization.py
    validation.py
    export.py
    review.py
  templates/
    pilot_fd_training_sign_in/v1/
      template.json
      README.md
  tests/
    fixtures/
    test_alignment.py
    test_checkbox_detection.py
    test_normalization.py
    test_validation.py
  output/
    .gitkeep
```

Do not commit source forms, completed forms, extracted signatures, personally identifiable information, API keys, or generated review artifacts. Test fixtures must be synthetic or explicitly approved and redacted.

## Phase 1 - Baseline and Template Preparation

1. Add a command-line interface that accepts one PDF or a directory of PDFs.
2. Render PDF pages at 300 DPI or higher with consistent color handling.
3. Create a cleaned master from the best available blank scan:
   - rotate 180 degrees;
   - deskew;
   - crop page boundaries;
   - normalize contrast;
   - remove scan speckles;
   - mask the stray pen mark in the signature column.
4. Define a versioned `template.json` using normalized page coordinates for:
   - date;
   - start and end time;
   - total hours;
   - location;
   - training-type options;
   - instructor;
   - facility options;
   - attendee table columns and rows;
   - truck options;
   - training description;
   - signature regions.
5. Add a debug output that overlays every region on the aligned page.

### Phase 1 Acceptance Criteria

- Both the blank scan and representative completed scans align to the template.
- Region overlays stay within their printed field boundaries.
- Alignment failures are detected and reported rather than processed silently.
- Input PDFs remain byte-for-byte unchanged.

## Phase 2 - Deterministic Form Analysis

1. Detect rotation before alignment.
2. Use stable printed features and table intersections for geometric alignment.
3. Detect marked training types, facilities, and trucks from localized image differences.
4. Detect populated attendee rows before invoking recognition.
5. Suppress table rules and form underlines inside handwriting crops where doing so improves recognition.
6. Save optional diagnostic images behind a debug flag.

### Phase 2 Acceptance Criteria

- Blank options are not reported as selected.
- Handwritten marks crossing an option line are detected.
- Empty attendee rows are excluded.
- Printed rules do not become OCR characters.

## Phase 3 - Handwriting Recognition

1. Define a provider-neutral recognition interface.
2. Pass tightly cropped fields with field-specific instructions and expected formats.
3. For attendee rows, recognize unit ID and printed name separately.
4. Preserve raw recognition output before normalization.
5. Require structured responses and reject malformed results.
6. Support a local/mock recognizer so tests do not require network access.
7. Treat signatures only as evidence crops linked to their attendee rows.

### Phase 3 Acceptance Criteria

- Recognition can be swapped without changing alignment or validation code.
- Each result records provenance and confidence.
- Uncertain handwriting produces alternatives or a review flag instead of a fabricated value.
- Automated tests run without external API access.

## Phase 4 - Normalization and Validation

Normalize:

- dates to ISO `YYYY-MM-DD` while retaining the written value;
- times to 24-hour `HH:MM`;
- unit IDs using configured department formats;
- apparatus names using an allowlist;
- names against an optional roster without overwriting the raw transcription.

Validate:

- date validity;
- start time before end time, including an explicit policy for overnight training;
- calculated duration against written total hours;
- recognized training types, facilities, and apparatus against configured values;
- attendee row completeness;
- duplicate attendees;
- low-confidence or ambiguous fields.

Every discrepancy becomes a warning. Validation must not silently rewrite source values.

### Phase 4 Acceptance Criteria

- A written total that disagrees with start/end time is flagged.
- Unknown unit IDs and apparatus names are flagged.
- Raw and normalized values remain available.
- Review status is derived reproducibly from configurable thresholds.

## Phase 5 - Human Review

Build a lightweight local review interface that displays:

- the full aligned page;
- the source crop for the active field;
- extracted and normalized values;
- confidence and alternatives;
- validation warnings;
- controls to approve or correct the value.

Record corrections separately from machine output so evaluation can distinguish original predictions from reviewed results.

### Phase 5 Acceptance Criteria

- A reviewer can resolve all flagged fields without editing JSON manually.
- Approved results include review timestamp and status.
- Review history does not expose signature images outside authorized storage.

## Phase 6 - Export and Batch Operation

1. Export one detailed JSON record per form.
2. Export normalized CSV tables for training events and attendees.
3. Create a batch summary with succeeded, review-required, and failed counts.
4. Make processing idempotent using source hashes.
5. Add clear exit codes and machine-readable error records.

### Phase 6 Acceptance Criteria

- Reprocessing the same file does not create duplicate records.
- One bad page does not abort an entire batch.
- Every exported value can be traced to its source file, page, and region.

## Testing Strategy

- Unit tests for coordinate transforms, normalization, checkbox scoring, and validation.
- Synthetic image tests for rotation, skew, blur, contrast, and noise.
- Golden tests for approved, redacted forms.
- Regression tests for every corrected extraction failure.
- Measure field-level exact match separately for dates, times, categories, unit IDs, names, and descriptions.
- Measure checkbox precision/recall and attendee-row detection precision/recall.
- Track automation rate: percentage of forms accepted without human correction.

Do not report one overall OCR accuracy number; it hides failures in operationally important fields.

## Security and Privacy

- Keep source documents and extracted data local by default.
- If a hosted recognizer is used, document retention, training-use, regional processing, and access-control implications before deployment.
- Read credentials from environment variables or an approved secret store.
- Never log document images, signatures, or full recognition payloads by default.
- Add `.gitignore` rules for inputs, outputs, crops, logs, local databases, and environment files.

## Definition of Done for the Initial Prototype

The prototype is complete when it can process a folder of Pilot Fire Department training forms and produce structured JSON plus review-ready diagnostics while:

- reliably aligning the known form;
- detecting selected options and populated attendee rows;
- extracting the principal handwritten fields;
- flagging uncertain or inconsistent values;
- preserving source traceability;
- passing automated tests without network access;
- keeping sensitive documents out of version control.

## Agent Working Rules

- Work in small, reviewable commits organized by phase.
- Add or update tests with every behavioral change.
- Do not introduce a hosted OCR or vision dependency without isolating it behind the provider interface.
- Do not commit real forms or inferred personal data.
- Prefer configuration changes over hard-coded coordinate changes.
- Preserve backward compatibility for existing template versions.
- Document any assumption that affects extracted meaning.
- Stop and request clarification when a form revision changes field semantics rather than guessing.
