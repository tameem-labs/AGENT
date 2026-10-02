"""SQLite operational trace persistence with bounded filtered queries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from zyro.core.data import plain
from zyro.observability.contracts import TraceQuery, TraceRecord, TraceStatus


class ObservabilityStoreError(RuntimeError):
    pass


class SQLiteObservabilityStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self._connection = sqlite3.connect(self.path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    component TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    workflow_id TEXT,
                    agent_id TEXT,
                    instance_id TEXT,
                    correlation_id TEXT NOT NULL,
                    message_id TEXT,
                    event_id TEXT,
                    tool_id TEXT,
                    model_id TEXT,
                    approval_id TEXT,
                    verification_id TEXT,
                    recovery_id TEXT,
                    duration_ms REAL,
                    attempt INTEGER,
                    error_classification TEXT,
                    resource_usage_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trace_correlation
                    ON traces(correlation_id,sequence);
                CREATE INDEX IF NOT EXISTS idx_trace_request_task
                    ON traces(request_id,task_id,sequence);
                CREATE INDEX IF NOT EXISTS idx_trace_workflow
                    ON traces(workflow_id,sequence);
                CREATE INDEX IF NOT EXISTS idx_trace_recovery
                    ON traces(recovery_id,sequence);
                CREATE INDEX IF NOT EXISTS idx_trace_event
                    ON traces(event_type,timestamp);
                """
            )
            self._connection.commit()
        except sqlite3.Error as error:
            raise ObservabilityStoreError("unable to initialize observability store") from error

    def close(self) -> None:
        self._connection.close()

    def append(self, record: TraceRecord) -> bool:
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO traces(trace_id,event_type,timestamp,component,operation,status,"
                    "request_id,task_id,workflow_id,agent_id,instance_id,correlation_id,message_id,"
                    "event_id,tool_id,model_id,approval_id,verification_id,recovery_id,duration_ms,"
                    "attempt,error_classification,resource_usage_json,metadata_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.trace_id,
                        record.event_type,
                        record.timestamp.isoformat(),
                        record.component,
                        record.operation,
                        record.status.value,
                        record.request_id,
                        record.task_id,
                        record.workflow_id,
                        record.agent_id,
                        record.instance_id,
                        record.correlation_id,
                        record.message_id,
                        record.event_id,
                        record.tool_id,
                        record.model_id,
                        record.approval_id,
                        record.verification_id,
                        record.recovery_id,
                        record.duration_ms,
                        record.attempt,
                        record.error_classification,
                        json.dumps(
                            plain(record.resource_usage), sort_keys=True, separators=(",", ":")
                        ),
                        json.dumps(plain(record.metadata), sort_keys=True, separators=(",", ":")),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            existing = self._connection.execute(
                "SELECT * FROM traces WHERE trace_id=?", (record.trace_id,)
            ).fetchone()
            if existing is not None and self._record(existing) == record:
                return False
            raise ObservabilityStoreError(
                "trace identity conflicts with existing telemetry"
            ) from None
        except sqlite3.Error as error:
            raise ObservabilityStoreError("unable to persist trace record") from error

    def query(self, query: TraceQuery) -> tuple[TraceRecord, ...]:
        where: list[str] = []
        parameters: list[object] = []
        for field in (
            "correlation_id",
            "request_id",
            "task_id",
            "workflow_id",
            "recovery_id",
            "event_type",
        ):
            value = getattr(query, field)
            if value is not None:
                where.append(f"{field}=?")
                parameters.append(value)
        parameters.append(query.limit)
        rows = self._connection.execute(
            f"SELECT * FROM traces WHERE {' AND '.join(where)} ORDER BY sequence LIMIT ?",
            parameters,
        ).fetchall()
        return tuple(self._record(row) for row in rows)

    @staticmethod
    def _record(row: sqlite3.Row) -> TraceRecord:
        from datetime import datetime

        return TraceRecord(
            trace_id=row["trace_id"],
            event_type=row["event_type"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            component=row["component"],
            operation=row["operation"],
            status=TraceStatus(row["status"]),
            request_id=row["request_id"],
            task_id=row["task_id"],
            workflow_id=row["workflow_id"],
            agent_id=row["agent_id"],
            instance_id=row["instance_id"],
            correlation_id=row["correlation_id"],
            message_id=row["message_id"],
            event_id=row["event_id"],
            tool_id=row["tool_id"],
            model_id=row["model_id"],
            approval_id=row["approval_id"],
            verification_id=row["verification_id"],
            recovery_id=row["recovery_id"],
            duration_ms=row["duration_ms"],
            attempt=row["attempt"],
            error_classification=row["error_classification"],
            resource_usage=json.loads(row["resource_usage_json"]),
            metadata=json.loads(row["metadata_json"]),
        )


__all__ = ["ObservabilityStoreError", "SQLiteObservabilityStore"]
