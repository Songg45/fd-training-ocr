"""Application configuration with dependency-free TOML loading."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
import tomllib
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AppConfig:
    output_dir: Path = Path("output")
    template_dir: Path = Path("templates")
    log_level: str = "INFO"
    offline: bool = True
    ollama_endpoint: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5vl:7b"
    ollama_timeout_seconds: float = 90.0

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output_dir"] = str(self.output_dir)
        data["template_dir"] = str(self.template_dir)
        return data


def _build_config(values: Mapping[str, Any]) -> AppConfig:
    allowed = {"output_dir", "template_dir", "log_level", "offline",
               "ollama_endpoint", "ollama_model", "ollama_timeout_seconds"}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"Unknown app configuration key(s): {', '.join(sorted(unknown))}")

    log_level = values.get("log_level", "INFO")
    offline = values.get("offline", True)
    if not isinstance(log_level, str):
        raise ValueError("app.log_level must be a string")
    if not isinstance(offline, bool):
        raise ValueError("app.offline must be a boolean")
    endpoint = values.get("ollama_endpoint", "http://127.0.0.1:11434")
    model = values.get("ollama_model", "qwen2.5vl:7b")
    timeout = values.get("ollama_timeout_seconds", 90.0)
    parsed_endpoint = urlsplit(endpoint) if isinstance(endpoint, str) else None
    if parsed_endpoint is None or parsed_endpoint.scheme != "http" or parsed_endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("app.ollama_endpoint must be a loopback HTTP URL")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("app.ollama_model must be a non-empty string")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("app.ollama_timeout_seconds must be positive")

    return AppConfig(
        output_dir=Path(values.get("output_dir", "output")),
        template_dir=Path(values.get("template_dir", "templates")),
        log_level=log_level.upper(),
        offline=offline,
        ollama_endpoint=endpoint,
        ollama_model=model,
        ollama_timeout_seconds=float(timeout),
    )


def _load_toml(path: Path) -> Mapping[str, Any]:
    with path.open("rb") as config_file:
        return tomllib.load(config_file)


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load optional TOML configuration, returning safe local defaults."""
    if path is None:
        return AppConfig()
    document = _load_toml(path)
    unknown_sections = set(document) - {"app"}
    if unknown_sections:
        raise ValueError(f"Unknown configuration section(s): {', '.join(sorted(unknown_sections))}")
    app = document.get("app", {})
    if not isinstance(app, dict):
        raise ValueError("app must be a TOML table")
    return _build_config(app)
