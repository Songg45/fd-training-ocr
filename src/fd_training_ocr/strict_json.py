"""Strict JSON helpers for security-sensitive local configuration and payloads."""

from __future__ import annotations

import json
from typing import Any


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_number(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def parse_json_object(text: str, *, object_name: str = "JSON value") -> dict[str, Any]:
    """Parse an object while rejecting duplicate keys and non-finite numbers."""
    payload = json.loads(
        text, object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite_number)
    if not isinstance(payload, dict):
        raise ValueError(f"{object_name} must be a JSON object")
    return payload
