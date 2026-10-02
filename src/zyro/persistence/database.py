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


@dataclass(frozen=True, slots=True)
class DatabaseSpec:
    name: str
    filename: str
    description: str


class DatabaseCatalog:
    """Central registry and health manager for all ZYRO SQLite stores."""

    DATABASES: tuple[DatabaseSpec, ...] = (
        DatabaseSpec("auth", "auth.sqlite", "Authentication, users, and tokens"),
        DatabaseSpec("credentials", "credentials.sqlite", "Encrypted OAuth and API credentials"),
        DatabaseSpec(
            "memory", "memory.sqlite", "Versioned episodic, semantic, and preference memory"
        ),
        DatabaseSpec(
            "knowledge",
            "knowledge.sqlite",
            "Document chunks, search indices, and reference knowledge",
        ),
        DatabaseSpec("state", "state.sqlite", "Operational runtime state and checkpoints"),
        DatabaseSpec("tasks", "tasks.sqlite", "Durable task records and execution receipts"),
        DatabaseSpec(
            "workflows", "workflows.sqlite", "Workflow runs, step states, and dependencies"
        ),
        DatabaseSpec(
            "freelancing", "freelancing.sqlite", "Leads, clients, proposals, and CRM state"
        ),
        DatabaseSpec("research", "research.sqlite", "Research plans, dossiers, and evidence"),
    )

    def __init__(self, data_directory: str | Path) -> None:
        self.data_dir = Path(data_directory)

    def check_integrity(self) -> dict[str, bool]:
        """Runs PRAGMA integrity_check on every existing database file."""
        results: dict[str, bool] = {}
        for spec in self.DATABASES:
            db_path = self.data_dir / spec.filename
            if not db_path.exists():
                results[spec.name] = True
                continue
            mgr = MigrationManager(db_path, (Migration(1, "base_schema", lambda conn: None),))
            results[spec.name] = mgr.integrity_check()
        return results

    def backup_all(self, backup_dir: str | Path) -> dict[str, Path]:
        """Creates durable backups of all active databases using SQLite backup API."""
        target_dir = Path(backup_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        backups: dict[str, Path] = {}
        for spec in self.DATABASES:
            db_path = self.data_dir / spec.filename
            if db_path.exists():
                mgr = MigrationManager(db_path, (Migration(1, "base_schema", lambda conn: None),))
                backups[spec.name] = mgr.backup(target_dir / f"{spec.filename}.bak")
        return backups


__all__ = ["DatabaseCatalog", "DatabaseSpec", "Migration", "MigrationManager"]

