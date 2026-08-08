# FD Training OCR

A local-first pipeline for extracting auditable, structured data from standardized fire department training sign-in sheets. Development is divided into explicit checkpoints; each checkpoint must be reviewed before work begins on the next one.

## Current status

Checkpoint 7 is a release candidate with idempotent file/folder processing, detailed JSON,
normalized events/attendees CSV files, isolated machine-readable failures, batch summaries,
and field-type evaluation. Deployment and production-archive processing remain unapproved.

## Requirements

- Python 3.11 or newer (Python 3.12 recommended)
- Poppler (`pdftoppm`), supplied by the bundled Codex workspace runtime or installed locally

## Install for development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## Configuration

Configuration is optional and uses TOML. Unknown keys are rejected to catch mistakes early.

```toml
[app]
output_dir = "output"
template_dir = "templates"
log_level = "INFO"
offline = true
ollama_endpoint = "http://127.0.0.1:11434"
ollama_model = "qwen2.5vl:7b"
ollama_timeout_seconds = 90
roster_path = "C:\\Temp\\fd-training-ocr-roster.json"
valid_apparatus = ["Engine 54", "Tanker 54", "Brush 54", "Engine 254", "Tanker 854", "Brush 254"]
valid_locations = ["District"]
recognition_crop_padding_pixels = 12
recognition_max_attempts = 3
```

Pass a file with `--config config.local.toml`. Local configuration, PDFs, outputs, logs, databases, crops, and signatures are ignored by Git.

The optional roster must use an absolute path outside this Git repository. Its schema is
`{"schema_version":1,"members":[{"name":"...","unit_ids":["..."],"aliases":["..."]}]}`.
Use `C:\Temp\fd-training-ocr-roster.json` for deployment. The loader rejects repository-local,
missing, unreadable, malformed, or unexpected roster content. Never commit roster data.

Validation preserves every written value and records normalized proposals separately. A
field requires review when syntax/allowlists/roster checks fail, confidence is below its
field threshold, alternatives exist, or cross-field checks conflict. Model confidence is
never sufficient to bypass these checks. Review artifacts are local and ignored; the HTML
shows only non-signature crops and downloads corrections as a separate timestamped record.

## CLI shell

```powershell
fd-training-ocr --help
fd-training-ocr --config config.local.toml inspect-config
fd-training-ocr process path\to\form.pdf --master path\to\cleaned-master.png `
  --template templates\pilot_fd_training_sign_in\v1\template.json
```

`process` accepts one PDF or a non-recursive directory of PDFs. It hashes source bytes with
SHA-256 and stores one record per hash, so identical input is skipped even if renamed. One
bad form creates an error JSON without stopping the rest. Exit codes are `0` for success,
`3` when at least one new record requires review, and `2` for processing/configuration
failure. The default mock provider is offline and intentionally yields review-required
records; pass `--provider ollama` only for an approved local recognition run.

Each batch output contains `records/*.json`, `errors/*.json`, `events.csv`,
`attendees.csv`, and `batch-summary.json`. Detailed fields retain raw, normalized, and
reviewed values plus confidence, alternatives, provider/model, source bounding box,
warnings, and review provenance. Signature fields are rejected at the export boundary.
The optional external roster remains outside Git; if configured but missing or invalid,
processing continues conservatively and marks the record for review without logging roster
contents or its path.

Prepare a local master (the output directory is ignored by Git):

```powershell
fd-training-ocr prepare-template path\to\blank.pdf --pdftoppm path\to\pdftoppm.exe
```

The command renders page 1 at 300 DPI, rotates it 180 degrees, estimates and corrects
small skew, crops the page, normalizes contrast, removes isolated speckles, and writes
both `cleaned-master.png` and `preparation-diagnostics.png`. The input PDF is never modified.
For a known stray pen stroke, repeat `--stray-mark x,y,width,height` with normalized
page coordinates. Only short strokes inside that local region are cleared; long table
rules are retained. These form-specific values stay in local invocation/configuration.

Align a completed or blank form to the local cleaned master and render a region overlay:

```powershell
fd-training-ocr align path\to\form.pdf `
  --master output\template-preparation\cleaned-master.png `
  --template templates\pilot_fd_training_sign_in\v1\template.json `
  --pdftoppm path\to\pdftoppm.exe
```

The command reports cardinal orientation, fine deskew, printed-form coverage, anchor
coverage, and excess ink. It exits with status 1 when the configured form/anchor coverage
or deskew limits fail. Outputs remain under ignored `output/` paths.

Run deterministic detection after alignment:

```powershell
fd-training-ocr detect output\alignment\aligned.png `
  --master output\template-preparation\cleaned-master.png `
  --template templates\pilot_fd_training_sign_in\v1\template.json
```

The command writes per-region scores plus ignored option and row diagnostic overlays.
Signature regions are never cropped, scored, detected, validated, exported, or processed.

## Optional local Ollama recognition

Ollama is not required for installation or tests. To try it, install Ollama separately,
start its local service, and pull a compact vision model suitable for the deployment PC's
8 GB VRAM ceiling. The default adapter model is `qwen2.5vl:7b`, which uses nearly the full
8 GB budget; `qwen2.5vl:3b` remains the lower-memory fallback. Model availability and
handwriting quality must be verified locally before operational use. The endpoint, model,
and timeout are constructor-configurable. Only loopback endpoints are accepted, and only
tightly cropped non-signature fields are serialized to the API. Requests are sequential;
the adapter uses deterministic temperature and strict JSON parsing.

Each eligible field produces raw-grayscale and printed-rule-suppressed candidates.
Suppression is selected only when a measured ink-preservation threshold passes. Crops
receive a white border, and invalid or low-confidence structured results are retried
sequentially with raw and then wider raw context (three attempts maximum). Detailed JSON
records preserve the chosen variant and every attempt. Roster matching is non-destructive:
raw OCR remains machine output while a canonical suggestion, ambiguity flag, and reason
are exposed for human review.

Recognition uses three stages. Every field is first read from the preferred tight crop,
then independently read from a wider raw crop with different prompt wording and no access
to the first result. Only exceptions receive a third contextual request. The verifier
groups labeled start/end/total-hours fields, checks the instructor against only an
unambiguous nearby roster candidate, and reads each populated attendee's unit-ID/name
cells together. Attendee context stops at the excluded-column boundary. Every stage,
crop variant, prompt, response, provider/model, candidate, resolved value, and reason
remain in detailed JSON. Automatic resolution requires two independent agreeing signals
plus deterministic validation; contradictions or malformed output remain review-required.

An opt-in smoke test is available with `FD_OCR_LIVE_OLLAMA=1`; optionally set
`FD_OCR_OLLAMA_MODEL`. It skips with setup guidance if the service/model is unavailable.

## Offline tests

Tests run offline with NumPy, Pillow, and synthetic temporary files:

```powershell
python -m unittest discover -s tests -v
```

## Privacy

Do not commit source forms, completed forms, extracted signatures, personal data, credentials, or generated review artifacts. Source documents will remain unchanged, and network-backed recognition will require separate review and approval.

See [agent-plan.md](agent-plan.md) for the architecture, acceptance criteria, and checkpoint protocol.
