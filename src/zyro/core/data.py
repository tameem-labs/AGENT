"""Shared bounded, non-secret structured-data validation."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast

MAX_RECORD_BYTES = 65_536
_SENSITIVE_NAMES = {
    "secret",
    "password",
    "credential",
    "api_key",
    "access_token",
    "private_key",
    "refresh_token",
}
_SECRET_TEXT = re.compile(
    r"(?i)\b(password|api[_-]?key|access[_-]?token|private[_-]?key|credential)\b\s*[:=]"
)


def plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def validate_text(value: str, field_name: str, *, max_chars: int = MAX_RECORD_BYTES) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > max_chars:
        raise ValueError(f"{field_name} exceeds its bounded size")
    if _SECRET_TEXT.search(cleaned):
        raise ValueError(f"{field_name} cannot contain secret-shaped assignments")
    return cleaned


def validate_record(
    value: Mapping[str, Any],
    field_name: str = "record",
    *,
    max_bytes: int = MAX_RECORD_BYTES,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")

    def inspect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str) or not key.strip():
                    raise ValueError(f"{field_name} keys must be non-empty strings")
                if key.lower().replace("-", "_") in _SENSITIVE_NAMES:
                    raise ValueError(f"{field_name} cannot contain secret fields")
                inspect(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                inspect(child)
        elif isinstance(item, str):
            if _SECRET_TEXT.search(item):
                raise ValueError(f"{field_name} cannot contain secret-shaped assignments")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{field_name} numbers must be finite")
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"{field_name} values must be JSON-compatible")

    inspect(value)
    encoded = json.dumps(plain(value), sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > max_bytes:
        raise ValueError(f"{field_name} exceeds the bounded record size")
    return cast(Mapping[str, Any], freeze(value))


def normalized_terms(query: str) -> tuple[str, ...]:
    return tuple(sorted(set(re.findall(r"[a-z0-9]+", query.lower()))))


def lexical_score(query: str, text: str) -> int:
    terms = normalized_terms(query)
    if not terms:
        return 1
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)


__all__ = [
    "MAX_RECORD_BYTES",
    "freeze",
    "lexical_score",
    "normalized_terms",
    "plain",
    "validate_record",
    "validate_text",
]
