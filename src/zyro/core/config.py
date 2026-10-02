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
_TASK_TOKEN_LIMIT_VARIABLE = "ZYRO_TASK_TOKEN_LIMIT"
_WORKFLOW_TOKEN_LIMIT_VARIABLE = "ZYRO_WORKFLOW_TOKEN_LIMIT"
_MAX_CONCURRENT_TASKS_VARIABLE = "ZYRO_MAX_CONCURRENT_TASKS"
_MAX_CONCURRENT_AGENTS_VARIABLE = "ZYRO_MAX_CONCURRENT_AGENTS"
_MAX_CONCURRENT_TOOL_CALLS_VARIABLE = "ZYRO_MAX_CONCURRENT_TOOL_CALLS"
_ALLOWED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigurationError(ValueError):
    """Raised when runtime configuration is invalid."""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Non-secret settings required to initialize the foundation runtime."""

    environment: str = "development"
    log_level: str = "INFO"
    task_token_limit: int = 50_000
    workflow_token_limit: int = 300_000
    max_concurrent_tasks: int = 8
    max_concurrent_agents: int = 8
    max_concurrent_tool_calls: int = 4

    def __post_init__(self) -> None:
        if not isinstance(self.environment, str) or not self.environment.strip():
            raise ConfigurationError("environment must be a non-empty string")
        if not isinstance(self.log_level, str):
            raise ConfigurationError("log_level must be a string")
        normalized_level = self.log_level.upper()
        if normalized_level not in _ALLOWED_LOG_LEVELS:
            allowed = ", ".join(sorted(_ALLOWED_LOG_LEVELS))
            raise ConfigurationError(f"log_level must be one of: {allowed}")
        for name in (
            "task_token_limit",
            "workflow_token_limit",
            "max_concurrent_tasks",
            "max_concurrent_agents",
            "max_concurrent_tool_calls",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ConfigurationError(f"{name} must be a positive integer")
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
    integer_environment = {
        _TASK_TOKEN_LIMIT_VARIABLE: "task_token_limit",
        _WORKFLOW_TOKEN_LIMIT_VARIABLE: "workflow_token_limit",
        _MAX_CONCURRENT_TASKS_VARIABLE: "max_concurrent_tasks",
        _MAX_CONCURRENT_AGENTS_VARIABLE: "max_concurrent_agents",
        _MAX_CONCURRENT_TOOL_CALLS_VARIABLE: "max_concurrent_tool_calls",
    }
    for variable, setting in integer_environment.items():
        if variable in source:
            try:
                values[setting] = int(source[variable])
            except ValueError as error:
                raise ConfigurationError(f"{variable} must be an integer") from error

    known_keys = {
        "environment",
        "log_level",
        "task_token_limit",
        "workflow_token_limit",
        "max_concurrent_tasks",
        "max_concurrent_agents",
        "max_concurrent_tool_calls",
    }
    unknown_keys = values.keys() - known_keys
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ConfigurationError(f"unknown configuration setting(s): {unknown}")

    try:
        return AppConfig(**values)
    except TypeError as error:
        raise ConfigurationError("configuration values have invalid types") from error
