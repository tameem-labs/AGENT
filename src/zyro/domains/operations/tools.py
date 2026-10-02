"""Tools for the System / Operations Department."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zyro.domains.operations.contracts import DatabaseIntegrityReport, SystemHealthReport
from zyro.tools.contracts import ToolExecutionContext, ToolHandlerResult


class DatabaseCheckHandler:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or Path(".zyro").resolve()

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        reports: list[DatabaseIntegrityReport] = []
        if not self.data_dir.is_dir():
            return ToolHandlerResult.success(
                {"databases": [], "status": "NO_DATABASES_FOUND"}
            )

        for path in sorted(self.data_dir.glob("*.sqlite")):
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    res = conn.execute("PRAGMA quick_check").fetchone()
                    status = "HEALTHY" if res and res[0] == "ok" else "FAILED"
                    details = res[0] if res else "Unknown"
                finally:
                    conn.close()
            except sqlite3.DatabaseError as err:
                status = "FAILED"
                details = str(err)

            reports.append(DatabaseIntegrityReport(path.name, status, details))

        return ToolHandlerResult.success(
            {
                "databases": [
                    {"name": r.database_name, "status": r.status, "details": r.details}
                    for r in reports
                ],
                "all_healthy": all(r.status == "HEALTHY" for r in reports),
            }
        )


class SystemHealthCheckHandler:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or Path(".zyro").resolve()
        self.db_checker = DatabaseCheckHandler(self.data_dir)

    def execute(
        self, context: ToolExecutionContext, arguments: Mapping[str, Any]
    ) -> ToolHandlerResult:
        db_res = self.db_checker.execute(context, arguments)
        db_data = db_res.output or {}
        raw_dbs = db_data.get("databases", [])
        reports = tuple(
            DatabaseIntegrityReport(d["name"], d["status"], d["details"])
            for d in raw_dbs
        )
        all_ok = db_data.get("all_healthy", True)
        host_status = "HEALTHY" if all_ok else "DEGRADED"

        health = SystemHealthReport(
            host_status=host_status,
            databases=reports,
            active_workflows=0,
            timestamp=datetime.now(UTC),
        )
        return ToolHandlerResult.success(health.to_dict())


__all__ = ["DatabaseCheckHandler", "SystemHealthCheckHandler"]
