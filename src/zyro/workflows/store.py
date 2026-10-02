"""Versioned SQLite workflow state and execution history."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from zyro.workflows.contracts import (
    StepStatus,
    TriggerKind,
    WorkflowDefinition,
    WorkflowSnapshot,
    WorkflowStatus,
    WorkflowStep,
)


def _now() -> datetime:
    return datetime.now(UTC)


class WorkflowStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                correlation_id TEXT NOT NULL,
                owner_principal_id TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                waiting_reason TEXT,
                error_code TEXT,
                next_run_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workflow_steps (
                workflow_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                result_json TEXT,
                error_code TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(workflow_id, step_id),
                FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
            );
            CREATE TABLE IF NOT EXISTS workflow_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workflow_status_next
                ON workflows(status, next_run_at);
            CREATE INDEX IF NOT EXISTS idx_workflow_history
                ON workflow_history(workflow_id, history_id);
            """
        )
        self._connection.execute("PRAGMA user_version=1")
        self._connection.commit()
        self.recover_running()

    def create(self, definition: WorkflowDefinition) -> WorkflowSnapshot:
        now = definition.created_at or _now()
        next_run = definition.scheduled_for
        status = (
            WorkflowStatus.SCHEDULED
            if definition.trigger in {TriggerKind.SCHEDULED, TriggerKind.RECURRING}
            else WorkflowStatus.PENDING
        )
        encoded = self._encode_definition(definition)
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO workflows VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        definition.workflow_id,
                        definition.request_id,
                        definition.correlation_id,
                        definition.owner_principal_id,
                        encoded,
                        status.value,
                        1,
                        None,
                        None,
                        None if next_run is None else next_run.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                for step in definition.steps:
                    self._connection.execute(
                        "INSERT INTO workflow_steps VALUES(?,?,?,?,NULL,NULL,?)",
                        (
                            definition.workflow_id,
                            step.step_id,
                            StepStatus.PENDING.value,
                            0,
                            now.isoformat(),
                        ),
                    )
                self._history(
                    definition.workflow_id, "WORKFLOW_CREATED", status.value, definition.goal, now
                )
        except sqlite3.IntegrityError:
            existing = self.by_request(definition.request_id)
            if existing is not None and existing.definition == definition:
                return existing
            raise ValueError("workflow identity or request conflicts") from None
        return self.get(definition.workflow_id)

    def get(self, workflow_id: str) -> WorkflowSnapshot:
        row = self._connection.execute(
            "SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"workflow not found: {workflow_id}")
        step_rows = self._connection.execute(
            "SELECT * FROM workflow_steps WHERE workflow_id=? ORDER BY step_id", (workflow_id,)
        ).fetchall()
        history_rows = self._connection.execute(
            "SELECT event_type,status,detail,recorded_at FROM workflow_history "
            "WHERE workflow_id=? ORDER BY history_id",
            (workflow_id,),
        ).fetchall()
        return WorkflowSnapshot(
            self._decode_definition(row["definition_json"]),
            WorkflowStatus(row["status"]),
            int(row["revision"]),
            {item["step_id"]: StepStatus(item["status"]) for item in step_rows},
            {item["step_id"]: int(item["attempts"]) for item in step_rows},
            datetime.fromisoformat(row["created_at"]),
            datetime.fromisoformat(row["updated_at"]),
            row["waiting_reason"],
            row["error_code"],
            None if row["next_run_at"] is None else datetime.fromisoformat(row["next_run_at"]),
            tuple(dict(item) for item in history_rows),
        )

    def by_request(self, request_id: str) -> WorkflowSnapshot | None:
        row = self._connection.execute(
            "SELECT workflow_id FROM workflows WHERE request_id=?", (request_id,)
        ).fetchone()
        return None if row is None else self.get(row["workflow_id"])

    def list(self, *, limit: int = 100) -> tuple[WorkflowSnapshot, ...]:
        rows = self._connection.execute(
            "SELECT workflow_id FROM workflows ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return tuple(self.get(row["workflow_id"]) for row in rows)

    def transition(
        self,
        workflow_id: str,
        expected_revision: int,
        status: WorkflowStatus,
        *,
        waiting_reason: str | None = None,
        error_code: str | None = None,
        event_type: str = "WORKFLOW_STATUS_CHANGED",
        detail: str = "",
    ) -> WorkflowSnapshot:
        now = _now()
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE workflows SET status=?,revision=revision+1,waiting_reason=?,error_code=?,"
                "updated_at=? WHERE workflow_id=? AND revision=?",
                (
                    status.value,
                    waiting_reason,
                    error_code,
                    now.isoformat(),
                    workflow_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("stale workflow revision")
            self._history(workflow_id, event_type, status.value, detail, now)
        return self.get(workflow_id)

    def update_step(
        self,
        workflow_id: str,
        step_id: str,
        status: StepStatus,
        *,
        result: object | None = None,
        error_code: str | None = None,
        increment_attempt: bool = False,
    ) -> WorkflowSnapshot:
        now = _now()
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE workflow_steps SET status=?,attempts=attempts+?,result_json=?,error_code=?,"
                "updated_at=? WHERE workflow_id=? AND step_id=?",
                (
                    status.value,
                    int(increment_attempt),
                    None if result is None else json.dumps(result, sort_keys=True, default=str),
                    error_code,
                    now.isoformat(),
                    workflow_id,
                    step_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"workflow step not found: {step_id}")
            self._connection.execute(
                "UPDATE workflows SET revision=revision+1,updated_at=? WHERE workflow_id=?",
                (now.isoformat(), workflow_id),
            )
            self._history(workflow_id, "STEP_STATUS_CHANGED", status.value, step_id, now)
        return self.get(workflow_id)

    def recover_running(self) -> int:
        now = _now()
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE workflows SET status=?,waiting_reason=?,revision=revision+1,updated_at=? "
                "WHERE status IN (?,?)",
                (
                    WorkflowStatus.RECOVERING.value,
                    "process restarted during workflow execution",
                    now.isoformat(),
                    WorkflowStatus.RUNNING.value,
                    WorkflowStatus.RECOVERING.value,
                ),
            )
            self._connection.execute(
                "UPDATE workflow_steps SET status=?,updated_at=? WHERE status IN (?,?)",
                (
                    StepStatus.RETRYING.value,
                    now.isoformat(),
                    StepStatus.RUNNING.value,
                    StepStatus.VERIFYING.value,
                ),
            )
        return cursor.rowcount

    def _history(
        self, workflow_id: str, event_type: str, status: str, detail: str, at: datetime
    ) -> None:
        self._connection.execute(
            "INSERT INTO workflow_history(workflow_id,event_type,status,detail,recorded_at) "
            "VALUES(?,?,?,?,?)",
            (workflow_id, event_type, status, detail[:2_000], at.isoformat()),
        )

    @staticmethod
    def _encode_definition(item: WorkflowDefinition) -> str:
        return json.dumps(
            {
                "workflow_id": item.workflow_id,
                "request_id": item.request_id,
                "correlation_id": item.correlation_id,
                "owner_principal_id": item.owner_principal_id,
                "goal": item.goal,
                "trigger": item.trigger.value,
                "scheduled_for": None
                if item.scheduled_for is None
                else item.scheduled_for.isoformat(),
                "recurrence_seconds": item.recurrence_seconds,
                "event_type": item.event_type,
                "created_at": None if item.created_at is None else item.created_at.isoformat(),
                "steps": [
                    {
                        "step_id": step.step_id,
                        "name": step.name,
                        "capability": step.capability,
                        "agent_id": step.agent_id,
                        "dependencies": step.dependencies,
                        "max_attempts": step.max_attempts,
                        "requires_approval": step.requires_approval,
                    }
                    for step in item.steps
                ],
            },
            sort_keys=True,
        )

    @staticmethod
    def _decode_definition(encoded: str) -> WorkflowDefinition:
        item = json.loads(encoded)
        return WorkflowDefinition(
            item["workflow_id"],
            item["request_id"],
            item["correlation_id"],
            item["owner_principal_id"],
            item["goal"],
            tuple(WorkflowStep(**step) for step in item["steps"]),
            TriggerKind(item["trigger"]),
            None
            if item["scheduled_for"] is None
            else datetime.fromisoformat(item["scheduled_for"]),
            item["recurrence_seconds"],
            item["event_type"],
            None if item["created_at"] is None else datetime.fromisoformat(item["created_at"]),
        )

    def close(self) -> None:
        self._connection.close()


__all__ = ["WorkflowStore"]
