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

For the optional Windows desktop GUI, install PySide6 through the GUI extra:

```powershell
python -m pip install -e ".[gui]"
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
ollama_stage3_model = "qwen3-vl:8b-instruct"
ollama_timeout_seconds = 90
roster_path = "C:\\Temp\\fd-training-ocr-roster.json"
valid_apparatus = ["Engine 54", "Tanker 54", "Brush 54", "Engine 254", "Tanker 854", "Brush 254"]
valid_locations = ["District", "Pilot Fire Department"]
location_aliases = { PFD = "Pilot Fire Department", "Pilot FD" = "Pilot Fire Department", "Pilot Fire Department" = "Pilot Fire Department" }
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

## Local desktop GUI

The optional PySide6 desktop front end can hold multiple one-page PDFs while retaining the
tested CLI pipeline. Add PDFs with multi-select, then use Previous/Next or Go To and the
position indicator to navigate forms; processed results and reviewer edits remain attached
to each PDF while navigating. It shows a zoomable and pannable page preview on the left and
editable structured field results plus the complete JSON record on the right. The structured
view uses labeled standard Windows-accessible inputs with a predictable tab order so Windows
11 Voice Access can target fields by name and dictate corrections. Training type, truck, and
facilities use labeled selection buttons; calculated duration and Stage-3 resolution remain
read-only. Dedicated Add Attendee and Delete Attendee buttons are voice-targetable. The Add
Attendee dialog links exact roster Unit IDs to canonical names and unique roster names or
aliases back to Unit IDs. OCR runs in a background executor while all Qt work remains on
the main thread, and a prominent banner identifies records requiring human review. Edits
are stored as separate reviewed values without replacing machine evidence. Results are
automatically written to the configured export folder after processing, after edits, when
moving with Previous/Next, and when closing the GUI; no separate export action is required.

Launch it with the same local master, template, configuration, and Poppler executable used
by the CLI:

```powershell
fd-training-ocr-gui `
  --config C:\Temp\fd-training-ocr-config.toml `
  --master C:\Github\OCR\fd-training-ocr\output\template-preparation\cleaned-master.png `
  --template C:\Github\OCR\fd-training-ocr\templates\pilot_fd_training_sign_in\v1\template.json `
  --pdftoppm C:\path\to\pdftoppm.exe
```

Stages 1 and 2 use `ollama_model` (`qwen2.5vl:7b` by default); exception-only Stage 3 uses
`ollama_stage3_model` (`qwen3-vl:8b-instruct` by default). Both endpoints remain restricted
to loopback. The preview is rendered into a temporary local directory and deleted when the
window closes. The existing pipeline masks signature regions before retaining aligned
artifacts or sending crops to Ollama. This checkpoint does not yet highlight source
regions, process multi-page PDFs as forms, run an optimized unattended batch, or provide
packaging automation.

## Station PC installer

The idempotent PowerShell installer provisions a Windows 10 22H2-or-newer station with
Git, Python 3.12, Poppler, Ollama, the GUI environment, both local OCR models, an external
configuration and roster, and a desktop shortcut. WinGet verifies the package manifests
and installer hashes. Existing configuration and roster files are preserved.

Copy the repository or just `scripts\Install-FDTrainingOCR.ps1` to the station, open
PowerShell, and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\Install-FDTrainingOCR.ps1 -RunTests
```

Defaults install the repository at `C:\Github\fd-training-ocr` and local operational data
under `C:\Temp`. Use `-InstallRoot` or `-DataRoot` to change them. Model downloads require
about 12 GB; the installer requires at least 25 GB free for the complete environment.
Use `-SkipModels` only when models will be transferred or pulled separately. The script
does not install GPU drivers; install a current NVIDIA driver if `nvidia-smi` is absent.

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
