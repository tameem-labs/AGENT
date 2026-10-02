"""Durable idempotent Recovery records and startup reconciliation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from zyro.core.errors import ErrorInfo
from zyro.core.events import Event, EventDelivery, EventPublisher, RetryPolicy
from zyro.recovery.contracts import (
    FailureClass,
    FailureIdentity,
    FailureRecord,
    RecoverableOperation,
    RecoverableOperationStatus,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
    SideEffectState,
)
from zyro.recovery.policy import RecoveryPolicy


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RecoveryStoreError(RuntimeError):
    pass


class SQLiteRecoveryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self._connection = sqlite3.connect(self.path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS recovery_operations (
                    operation_id TEXT PRIMARY KEY,
                    identity_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    idempotent INTEGER NOT NULL,
                    side_effect TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_failures (
                    failure_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    retryable INTEGER NOT NULL,
                    side_effect TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    identity_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_decisions (
                    recovery_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    operation_revision INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    next_attempt INTEGER NOT NULL,
                    backoff_seconds REAL NOT NULL,
                    requires_reconciliation INTEGER NOT NULL,
                    UNIQUE(operation_id,operation_revision)
                );
                CREATE INDEX IF NOT EXISTS idx_recovery_operation_status
                    ON recovery_operations(status,updated_at);
                CREATE INDEX IF NOT EXISTS idx_recovery_failure_operation
                    ON recovery_failures(operation_id,occurred_at);
                CREATE INDEX IF NOT EXISTS idx_recovery_decision_operation
                    ON recovery_decisions(operation_id,decided_at);
                """
            )
            self._connection.commit()
        except sqlite3.Error as error:
            raise RecoveryStoreError("unable to initialize recovery store") from error

    def close(self) -> None:
        self._connection.close()

    def register_operation(self, operation: RecoverableOperation) -> bool:
        values = (
            operation.operation_id,
            json.dumps(asdict(operation.identity), sort_keys=True, separators=(",", ":")),
            operation.status.value,
            operation.attempt_count,
            operation.max_attempts,
            int(operation.idempotent),
            operation.side_effect.value,
            operation.revision,
            operation.updated_at.isoformat(),
        )
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO recovery_operations(operation_id,identity_json,status,"
                    "attempt_count,max_attempts,idempotent,side_effect,revision,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    values,
                )
            return True
        except sqlite3.IntegrityError:
            existing = self.operation(operation.operation_id)
            if existing == operation:
                return False
            raise RecoveryStoreError("recovery operation identity conflict") from None

    def update_operation(
        self,
        operation_id: str,
        expected_revision: int,
        status: RecoverableOperationStatus,
        attempt_count: int,
        side_effect: SideEffectState,
        now: datetime,
    ) -> RecoverableOperation:
        current = self.operation(operation_id)
        if current is None or current.revision != expected_revision:
            raise RecoveryStoreError("stale recovery operation update")
        revision = expected_revision + 1
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE recovery_operations SET status=?,attempt_count=?,side_effect=?,"
                "revision=?,updated_at=? WHERE operation_id=? AND revision=?",
                (
                    status.value,
                    attempt_count,
                    side_effect.value,
                    revision,
                    now.isoformat(),
                    operation_id,
                    expected_revision,
                ),
            )
        if cursor.rowcount != 1:
            raise RecoveryStoreError("stale recovery operation update")
        return RecoverableOperation(
            operation_id,
            current.identity,
            status,
            attempt_count,
            current.max_attempts,
            current.idempotent,
            side_effect,
            revision,
            now,
        )

    def operation(self, operation_id: str) -> RecoverableOperation | None:
        row = self._connection.execute(
            "SELECT * FROM recovery_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        return None if row is None else self._operation(row)

    def nonfinal_operations(self, limit: int = 500) -> tuple[RecoverableOperation, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("recovery operation limit must be between 1 and 500")
        terminal = tuple(status.value for status in RecoverableOperationStatus if status.final)
        placeholders = ",".join("?" for _ in terminal)
        rows = self._connection.execute(
            f"SELECT * FROM recovery_operations WHERE status NOT IN ({placeholders}) "
            "ORDER BY updated_at,operation_id LIMIT ?",
            (*terminal, limit),
        ).fetchall()
        return tuple(self._operation(row) for row in rows)

    def record_failure(self, operation_id: str, failure: FailureRecord) -> bool:
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO recovery_failures("
                    "failure_id,operation_id,classification,error_code,error_type,retryable,"
                    "side_effect,occurred_at,identity_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        failure.failure_id,
                        operation_id,
                        failure.classification.value,
                        failure.error.code,
                        failure.error.error_type,
                        int(failure.retryable),
                        failure.side_effect.value,
                        failure.occurred_at.isoformat(),
                        json.dumps(asdict(failure.identity), sort_keys=True, separators=(",", ":")),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False
        except sqlite3.Error as error:
            raise RecoveryStoreError("unable to persist recovery failure") from error

    def record_decision(self, decision: RecoveryDecision) -> RecoveryDecision:
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO recovery_decisions(recovery_id,operation_id,operation_revision,"
                    "action,decided_at,reason_code,reason,next_attempt,backoff_seconds,"
                    "requires_reconciliation) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        decision.recovery_id,
                        decision.operation_id,
                        decision.operation_revision,
                        decision.action.value,
                        decision.decided_at.isoformat(),
                        decision.reason_code,
                        decision.reason,
                        decision.next_attempt,
                        decision.backoff_seconds,
                        int(decision.requires_reconciliation),
                    ),
                )
            return decision
        except sqlite3.IntegrityError:
            existing = self.decision_for(decision.operation_id, decision.operation_revision)
            if existing is None:
                raise RecoveryStoreError("recovery decision identity conflict") from None
            return existing
        except sqlite3.Error as error:
            raise RecoveryStoreError("unable to persist recovery decision") from error

    def decision_for(self, operation_id: str, operation_revision: int) -> RecoveryDecision | None:
        row = self._connection.execute(
            "SELECT * FROM recovery_decisions WHERE operation_id=? AND operation_revision=?",
            (operation_id, operation_revision),
        ).fetchone()
        if row is None:
            return None
        return RecoveryDecision(
            row["recovery_id"],
            row["operation_id"],
            row["operation_revision"],
            RecoveryAction(row["action"]),
            datetime.fromisoformat(row["decided_at"]),
            row["reason_code"],
            row["reason"],
            row["next_attempt"],
            row["backoff_seconds"],
            bool(row["requires_reconciliation"]),
        )

    @staticmethod
    def _operation(row: sqlite3.Row) -> RecoverableOperation:
        identity = FailureIdentity(**json.loads(row["identity_json"]))
        return RecoverableOperation(
            row["operation_id"],
            identity,
            RecoverableOperationStatus(row["status"]),
            row["attempt_count"],
            row["max_attempts"],
            bool(row["idempotent"]),
            SideEffectState(row["side_effect"]),
            row["revision"],
            datetime.fromisoformat(row["updated_at"]),
        )


class StartupReconciler:
    """Classify persisted non-final operations without executing their side effects."""

    def __init__(
        self,
        store: SQLiteRecoveryStore,
        policy: RecoveryPolicy,
        *,
        publisher: EventPublisher | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._publisher = publisher
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def reconcile(self) -> tuple[RecoveryDecision, ...]:
        decisions: list[RecoveryDecision] = []
        for operation in self._store.nonfinal_operations():
            existing = self._store.decision_for(operation.operation_id, operation.revision)
            if existing is not None:
                decisions.append(existing)
                continue
            decision = self._reconcile_operation(operation)
            persisted = self._store.record_decision(decision)
            self._terminalize(operation, persisted)
            self._publish(operation, persisted)
            decisions.append(persisted)
        return tuple(decisions)

    def _reconcile_operation(self, operation: RecoverableOperation) -> RecoveryDecision:
        if operation.status is RecoverableOperationStatus.VERIFYING:
            classification = FailureClass.VERIFICATION_FAILURE
            side_effect = SideEffectState.CONFIRMED
            reconciliation = True
            retryable = True
        elif operation.side_effect is SideEffectState.UNCERTAIN:
            classification = FailureClass.EXTERNAL_SIDE_EFFECT_UNCERTAIN
            side_effect = SideEffectState.UNCERTAIN
            reconciliation = False
            retryable = False
        else:
            classification = FailureClass.PROCESS_CRASH
            side_effect = operation.side_effect
            reconciliation = False
            retryable = operation.status is not RecoverableOperationStatus.WAITING
        failure = FailureRecord(
            f"failure:{operation.operation_id}:{operation.revision}",
            classification,
            operation.identity,
            ErrorInfo(
                "process_interrupted",
                "Process stopped while durable operation was non-final.",
                "ProcessCrash",
                retryable=retryable,
            ),
            self._clock(),
            retryable,
            side_effect,
        )
        self._store.record_failure(operation.operation_id, failure)
        return self._policy.decide(
            RecoveryRequest(
                recovery_id=f"recovery:{operation.operation_id}:{operation.revision}",
                operation_id=operation.operation_id,
                failure=failure,
                attempt_count=operation.attempt_count,
                max_attempts=operation.max_attempts,
                idempotent=operation.idempotent,
                resource_available=operation.status is not RecoverableOperationStatus.WAITING,
                hard_resource_limit=False,
                fallback_available=False,
                reconciliation_available=reconciliation,
                current_task_state=operation.status.value,
                operation_revision=operation.revision,
            )
        )

    def _terminalize(self, operation: RecoverableOperation, decision: RecoveryDecision) -> None:
        target = {
            RecoveryAction.MARK_UNCERTAIN: RecoverableOperationStatus.UNCERTAIN,
            RecoveryAction.STOP: RecoverableOperationStatus.STOPPED,
            RecoveryAction.ESCALATE: RecoverableOperationStatus.STOPPED,
        }.get(decision.action)
        if target is not None:
            self._store.update_operation(
                operation.operation_id,
                operation.revision,
                target,
                operation.attempt_count,
                operation.side_effect,
                self._clock(),
            )

    def _publish(self, operation: RecoverableOperation, decision: RecoveryDecision) -> None:
        if self._publisher is None:
            return
        event_type = {
            RecoveryAction.RETRY: "RECOVERY_RETRYING",
            RecoveryAction.ESCALATE: "RECOVERY_ESCALATED",
            RecoveryAction.MARK_UNCERTAIN: "EXECUTION_UNCERTAIN",
            RecoveryAction.STOP: "RECOVERY_EXHAUSTED",
        }.get(decision.action, "RECOVERY_STARTED")
        event = Event(
            event_id=self._id_factory(),
            request_id=operation.identity.request_id,
            task_id=operation.identity.task_id,
            workflow_id=operation.identity.workflow_id,
            correlation_id=operation.identity.correlation_id,
            event_type=event_type,
            publisher="runtime.recovery",
            payload={
                "recovery_id": decision.recovery_id,
                "operation_id": operation.operation_id,
                "action": decision.action.value,
                "reason_code": decision.reason_code,
            },
            timestamp=self._clock(),
            version="1.0",
            delivery=EventDelivery(
                durable=True,
                ack_required=True,
                ordering_key=operation.identity.task_id,
                retry_policy=RetryPolicy(max_attempts=3),
            ),
        )
        self._publisher.publish(
            event,
            idempotency_key=f"recovery-event:{decision.recovery_id}",
        )


__all__ = ["RecoveryStoreError", "SQLiteRecoveryStore", "StartupReconciler"]
