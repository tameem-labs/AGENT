"""Unit tests for DatabaseCatalog persistence manager."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from zyro.persistence import DatabaseCatalog


def test_database_catalog_integrity_and_backup(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Create dummy database
    auth_db = data_dir / "auth.sqlite"
    conn = sqlite3.connect(auth_db)
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO users VALUES ('u1', 'Alice')")
    conn.commit()
    conn.close()

    catalog = DatabaseCatalog(data_dir)
    integrity = catalog.check_integrity()
    assert integrity["auth"] is True

    backup_dir = tmp_path / "backups"
    backups = catalog.backup_all(backup_dir)
    assert "auth" in backups
    assert backups["auth"].exists()

    # Verify backup contents
    backup_conn = sqlite3.connect(backups["auth"])
    row = backup_conn.execute("SELECT name FROM users WHERE id='u1'").fetchone()
    assert row is not None
    assert row[0] == "Alice"
    backup_conn.close()
