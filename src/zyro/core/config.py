"""Safe, dependency-free configuration for the ZYRO foundation."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ENVIRONMENT_VARIABLE = "ZYRO_ENV"
_LOG_LEVEL_VARIABLE = "ZYRO_LOG_LEVEL"
_ALLOWED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigurationError(ValueError):
    """Raised when runtime configuration is invalid."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Non-secret settings required to initialize the foundation runtime."""

    environment: str = "development"
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not isinstance(self.environment, str) or not self.environment.strip():
            raise ConfigurationError("environment must be a non-empty string")
        if not isinstance(self.log_level, str):
            raise ConfigurationError("log_level must be a string")
        normalized_level = self.log_level.upper()
        if normalized_level not in _ALLOWED_LOG_LEVELS:
            allowed = ", ".join(sorted(_ALLOWED_LOG_LEVELS))
            raise ConfigurationError(f"log_level must be one of: {allowed}")
        object.__setattr__(self, "environment", self.environment.strip())
        object.__setattr__(self, "log_level", normalized_level)


def load_config(
    path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load non-secret TOML settings, overridden by process environment values.

    The runtime never requires an API key. Secret files are deliberately outside
    this configuration contract and are ignored by version control.
    """
    values: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        try:
            with config_path.open("rb") as config_file:
                document = tomllib.load(config_file)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ConfigurationError(f"unable to load configuration: {config_path}") from error

        section = document.get("zyro", {})
        if not isinstance(section, dict):
            raise ConfigurationError("the [zyro] configuration section must be a table")
        values.update(section)

    source = os.environ if environ is None else environ
    if _ENVIRONMENT_VARIABLE in source:
        values["environment"] = source[_ENVIRONMENT_VARIABLE]
    if _LOG_LEVEL_VARIABLE in source:
        values["log_level"] = source[_LOG_LEVEL_VARIABLE]

    known_keys = {"environment", "log_level"}
    unknown_keys = values.keys() - known_keys
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ConfigurationError(f"unknown configuration setting(s): {unknown}")

    try:
        return AppConfig(**values)
    except TypeError as error:
        raise ConfigurationError("configuration values have invalid types") from error
