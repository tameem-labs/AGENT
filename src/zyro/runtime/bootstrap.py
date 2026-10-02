"""Foundation runtime initialization; no Executive or agent behavior lives here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zyro.core.config import AppConfig, load_config
from zyro.core.logging import configure_logging, get_logger


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Initialized process-level foundation services."""

    config: AppConfig


def initialize_runtime(config_path: str | Path | None = None) -> RuntimeContext:
    """Load safe configuration and initialize package logging."""
    config = load_config(config_path)
    configure_logging(config.log_level)
    get_logger("runtime").debug("foundation runtime initialized")
    return RuntimeContext(config=config)
