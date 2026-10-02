"""Contracts for the System / Operations Department."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from zyro.core.data import validate_text


@dataclass(frozen=True, slots=True)
class DatabaseIntegrityReport:
    database_name: str
    status: str
    details: str

    def __post_init__(self) -> None:
        for name in ("database_name", "status", "details"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class SystemHealthReport:
    host_status: str
    databases: tuple[DatabaseIntegrityReport, ...]
    active_workflows: int
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_status": self.host_status,
            "databases": [
                {"name": db.database_name, "status": db.status, "details": db.details}
                for db in self.databases
            ],
            "active_workflows": self.active_workflows,
            "timestamp": self.timestamp.astimezone(UTC).isoformat(),
        }


__all__ = ["DatabaseIntegrityReport", "SystemHealthReport"]
