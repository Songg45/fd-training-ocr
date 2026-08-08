# FD Training OCR

A local-first pipeline for extracting auditable, structured data from standardized fire department training sign-in sheets. Development is divided into explicit checkpoints; each checkpoint must be reviewed before work begins on the next one.

## Current status

Checkpoint 2 adds local first-page PDF rendering and deterministic blank-template preparation. It does **not** align completed forms, define fields, perform OCR, or send data to external services.

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
```

Pass a file with `--config config.local.toml`. Local configuration, PDFs, outputs, logs, databases, crops, and signatures are ignored by Git.

## CLI shell

```powershell
fd-training-ocr --help
fd-training-ocr --config config.local.toml inspect-config
fd-training-ocr process path\to\form.pdf
```

The `process` command intentionally exits with a clear “not implemented” message until later checkpoints. Template preparation is available only through the explicit command below.

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

## Offline tests

Tests run offline with NumPy, Pillow, and synthetic temporary files:

```powershell
python -m unittest discover -s tests -v
```

## Privacy

Do not commit source forms, completed forms, extracted signatures, personal data, credentials, or generated review artifacts. Source documents will remain unchanged, and network-backed recognition will require separate review and approval.

See [agent-plan.md](agent-plan.md) for the architecture, acceptance criteria, and checkpoint protocol.
