"""Application configuration with dependency-free TOML loading."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
import tomllib


@dataclass(frozen=True)
class AppConfig:
    output_dir: Path = Path("output")
    template_dir: Path = Path("templates")
    log_level: str = "INFO"
    offline: bool = True

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output_dir"] = str(self.output_dir)
        data["template_dir"] = str(self.template_dir)
        return data


def _build_config(values: Mapping[str, Any]) -> AppConfig:
    allowed = {"output_dir", "template_dir", "log_level", "offline"}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"Unknown app configuration key(s): {', '.join(sorted(unknown))}")

    log_level = values.get("log_level", "INFO")
    offline = values.get("offline", True)
    if not isinstance(log_level, str):
        raise ValueError("app.log_level must be a string")
    if not isinstance(offline, bool):
        raise ValueError("app.offline must be a boolean")

    return AppConfig(
        output_dir=Path(values.get("output_dir", "output")),
        template_dir=Path(values.get("template_dir", "templates")),
        log_level=log_level.upper(),
        offline=offline,
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
