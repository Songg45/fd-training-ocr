"""Conservative field normalization which always preserves written OCR text."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Callable, Mapping


@dataclass(frozen=True)
class NormalizedValue:
    raw: str | None
    normalized: str | None
    valid: bool
    reason: str | None = None


def _empty(raw: str | None) -> NormalizedValue | None:
    if raw is None or not raw.strip():
        return NormalizedValue(raw, None, False, "value is blank")
    return None


def normalize_date(raw: str | None) -> NormalizedValue:
    if result := _empty(raw): return result
    text = raw.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y",
                "%m%d%Y", "%m%d%y", "%Y-%m-%d"):
        try:
            return NormalizedValue(raw, datetime.strptime(text, fmt).date().isoformat(), True)
        except ValueError:
            pass
    return NormalizedValue(raw, None, False, "unrecognized or invalid date")


def canonical_date(raw: str | None) -> str | None:
    """Return a valid date in the application's canonical MM/DD/YY format."""
    normalized = normalize_date(raw)
    if not normalized.valid or normalized.normalized is None:
        return None
    return datetime.fromisoformat(normalized.normalized).strftime("%m/%d/%y")


def normalize_time(raw: str | None) -> NormalizedValue:
    if result := _empty(raw): return result
    text = re.sub(r"\s+", "", raw).upper().replace(".", "")
    for fmt in ("%H:%M", "%H%M", "%I:%M%p", "%I%p"):
        try:
            return NormalizedValue(raw, datetime.strptime(text, fmt).strftime("%H:%M"), True)
        except ValueError:
            pass
    return NormalizedValue(raw, None, False, "unrecognized or invalid time")


def normalize_hours(raw: str | None) -> NormalizedValue:
    if result := _empty(raw): return result
    try:
        value = float(raw.strip())
    except ValueError:
        return NormalizedValue(raw, None, False, "hours is not numeric")
    if value < 0 or value > 24:
        return NormalizedValue(raw, None, False, "hours is outside 0-24")
    return NormalizedValue(raw, f"{value:g}", True)


def normalize_allowlisted(raw: str | None, allowed: tuple[str, ...]) -> NormalizedValue:
    if result := _empty(raw): return result
    matches = {item.casefold(): item for item in allowed}
    normalized = matches.get(raw.strip().casefold())
    return (NormalizedValue(raw, normalized, True) if normalized is not None else
            NormalizedValue(raw, raw.strip(), False, "value is not in configured allowlist"))


def normalize_aliased_allowlisted(raw: str | None, allowed: tuple[str, ...],
                                  aliases: Mapping[str, str] | tuple[tuple[str, str], ...]) -> NormalizedValue:
    """Normalize configured aliases to a canonical allowlisted value."""
    if result := _empty(raw): return result
    canonical = {item.casefold(): item for item in allowed}
    alias_items = aliases.items() if isinstance(aliases, Mapping) else aliases
    for alias, target in alias_items:
        canonical[alias.casefold()] = target
    normalized = canonical.get(raw.strip().casefold())
    return (NormalizedValue(raw, normalized, True) if normalized is not None else
            NormalizedValue(raw, raw.strip(), False, "value is not in configured allowlist or aliases"))


Normalizer = Callable[[str | None], NormalizedValue]
