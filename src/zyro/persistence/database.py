"""Non-destructive SQLite schema migration, integrity, and backup primitives."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


class MigrationManager:
    """Apply ordered forward-only migrations with a durable history."""

    def __init__(self, path: str | Path, migrations: tuple[Migration, ...]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrations = tuple(sorted(migrations, key=lambda item: item.version))
        versions = [item.version for item in self._migrations]
        if versions != list(range(1, len(versions) + 1)):
            raise ValueError("migration versions must be contiguous and start at one")

    def migrate(self) -> int:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL "
                "DEFAULT (datetime('now')))"
            )
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > len(self._migrations):
                raise RuntimeError("database schema is newer than this ZYRO runtime")
            for migration in self._migrations[current:]:
                with connection:
                    migration.apply(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version,name) VALUES(?,?)",
                        (migration.version, migration.name),
                    )
                    connection.execute(f"PRAGMA user_version={migration.version}")
            return len(self._migrations)
        finally:
            connection.close()

    def integrity_check(self) -> bool:
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(result is not None and result[0] == "ok")
        finally:
            connection.close()

    def backup(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        source_connection = sqlite3.connect(self.path)
        destination_connection = sqlite3.connect(target)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()
        return target


__all__ = ["Migration", "MigrationManager"]
