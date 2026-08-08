# FD Training OCR

A local-first pipeline for extracting auditable, structured data from standardized fire department training sign-in sheets. Development is divided into explicit checkpoints; each checkpoint must be reviewed before work begins on the next one.

## Current status

Checkpoint 1 provides the project scaffold, configuration loader, CLI shell, and offline tests. It does **not** render PDFs, inspect forms, perform OCR, or send data to external services.

## Requirements

- Python 3.11 or newer (Python 3.12 recommended)
- No runtime dependencies for Checkpoint 1

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

The `process` command intentionally exits with a clear “not implemented” message until later checkpoints. It never reads or modifies the supplied PDF in Checkpoint 1.

## Offline tests

Tests use only Python's standard library and synthetic temporary files:

```powershell
python -m unittest discover -s tests -v
```

## Privacy

Do not commit source forms, completed forms, extracted signatures, personal data, credentials, or generated review artifacts. Source documents will remain unchanged, and network-backed recognition will require separate review and approval.

See [agent-plan.md](agent-plan.md) for the architecture, acceptance criteria, and checkpoint protocol.
