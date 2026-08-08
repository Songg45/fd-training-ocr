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
    ollama_stage3_model: str = "qwen3-vl:8b-instruct"
    ollama_timeout_seconds: float = 90.0
    roster_path: Path | None = None
    valid_apparatus: tuple[str, ...] = ("Engine 54", "Tanker 54", "Brush 54", "Engine 254", "Tanker 854", "Brush 254")
    valid_locations: tuple[str, ...] = ("District", "Pilot Fire Department")
    location_aliases: tuple[tuple[str, str], ...] = (
        ("PFD", "Pilot Fire Department"),
        ("Pilot FD", "Pilot Fire Department"),
        ("Pilot Fire Department", "Pilot Fire Department"),
    )
    recognition_crop_padding_pixels: int = 12
    recognition_max_attempts: int = 3

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output_dir"] = str(self.output_dir)
        data["template_dir"] = str(self.template_dir)
        data["roster_path"] = str(self.roster_path) if self.roster_path else None
        return data


def _build_config(values: Mapping[str, Any]) -> AppConfig:
    allowed = {"output_dir", "template_dir", "log_level", "offline",
               "ollama_endpoint", "ollama_model", "ollama_stage3_model", "ollama_timeout_seconds", "roster_path",
               "valid_apparatus", "valid_locations", "location_aliases", "recognition_crop_padding_pixels",
               "recognition_max_attempts"}
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
    stage3_model = values.get("ollama_stage3_model", "qwen3-vl:8b-instruct")
    timeout = values.get("ollama_timeout_seconds", 90.0)
    parsed_endpoint = urlsplit(endpoint) if isinstance(endpoint, str) else None
    if parsed_endpoint is None or parsed_endpoint.scheme != "http" or parsed_endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("app.ollama_endpoint must be a loopback HTTP URL")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("app.ollama_model must be a non-empty string")
    if not isinstance(stage3_model, str) or not stage3_model.strip():
        raise ValueError("app.ollama_stage3_model must be a non-empty string")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("app.ollama_timeout_seconds must be positive")
    roster_path = values.get("roster_path")
    if roster_path is not None and (not isinstance(roster_path, str) or not Path(roster_path).is_absolute()):
        raise ValueError("app.roster_path must be an absolute path")
    apparatus = values.get("valid_apparatus", list(AppConfig.valid_apparatus))
    locations = values.get("valid_locations", list(AppConfig.valid_locations))
    location_aliases = values.get("location_aliases", dict(AppConfig.location_aliases))
    if not isinstance(apparatus, list) or not apparatus or not all(isinstance(x, str) and x.strip() for x in apparatus):
        raise ValueError("app.valid_apparatus must be a nonempty array of strings")
    if not isinstance(locations, list) or not locations or not all(isinstance(x, str) and x.strip() for x in locations):
        raise ValueError("app.valid_locations must be a nonempty array of strings")
    if (not isinstance(location_aliases, dict)
            or not all(isinstance(alias, str) and alias.strip()
                       and isinstance(target, str) and target.strip()
                       for alias, target in location_aliases.items())):
        raise ValueError("app.location_aliases must be a table of string aliases to canonical strings")
    crop_padding = values.get("recognition_crop_padding_pixels", 12)
    max_attempts = values.get("recognition_max_attempts", 3)
    if isinstance(crop_padding, bool) or not isinstance(crop_padding, int) or not 0 <= crop_padding <= 100:
        raise ValueError("app.recognition_crop_padding_pixels must be an integer from 0 to 100")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 3:
        raise ValueError("app.recognition_max_attempts must be an integer from 1 to 3")

    return AppConfig(
        output_dir=Path(values.get("output_dir", "output")),
        template_dir=Path(values.get("template_dir", "templates")),
        log_level=log_level.upper(),
        offline=offline,
        ollama_endpoint=endpoint,
        ollama_model=model,
        ollama_stage3_model=stage3_model,
        ollama_timeout_seconds=float(timeout),
        roster_path=Path(roster_path) if roster_path else None,
        valid_apparatus=tuple(apparatus),
        valid_locations=tuple(locations),
        location_aliases=tuple(location_aliases.items()),
        recognition_crop_padding_pixels=crop_padding,
        recognition_max_attempts=max_attempts,
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
