"""SQLite current-State store with owner control and compare-and-set writes."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zyro.core.data import plain, validate_record, validate_text
from zyro.core.errors import ErrorInfo
from zyro.core.scope import ResourceScope, ScopeKind
from zyro.security.resource_authorization import ResourceAuthorizer
from zyro.state.contracts import StateReadResult, StateRecord, StateWriteOutcome, StateWriteResult


def _utc_now() -> datetime:
    return datetime.now(UTC)


class StateStoreError(RuntimeError):
    pass


class SQLiteStateStore:
    """Current snapshots only; historical experience belongs in Memory."""

    def __init__(
        self,
        path: str | Path,
        authorizer: ResourceAuthorizer,
        category_owners: Mapping[str, str],
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._authorizer = authorizer
        self._owners = {
            validate_text(category, "state category", max_chars=256): validate_text(
                owner, "state owner", max_chars=256
            )
            for category, owner in category_owners.items()
        }
        if not self._owners:
            raise ValueError("at least one state category owner is required")
        self._clock = clock
        try:
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS current_state (
                    category TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    scope_kind TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(category,state_key,scope_kind,scope_id)
                );
                CREATE INDEX IF NOT EXISTS idx_state_owner_key
                    ON current_state(owner_id,category,state_key);
                CREATE INDEX IF NOT EXISTS idx_state_scope
                    ON current_state(scope_kind,scope_id,category);
                """
            )
            self._connection.execute("PRAGMA user_version=1")
            self._connection.commit()
        except sqlite3.Error as error:
            raise StateStoreError("unable to initialize state store") from error

    def close(self) -> None:
        self._connection.close()

    def create(
        self,
        caller_id: str,
        category: str,
        state_key: str,
        scope: ResourceScope,
        value: Mapping[str, Any],
    ) -> StateWriteResult:
        owner = self._owner(category)
        if owner is None:
            return StateWriteResult(StateWriteOutcome.UNKNOWN_CATEGORY)
        if caller_id != owner:
            return self._unauthorized_write()
        validate_text(state_key, "state_key", max_chars=256)
        frozen = validate_record(value, "state value")
        now = self._clock()
        record = StateRecord(category, state_key, scope, owner, frozen, 1, now, now)
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO current_state(category,state_key,scope_kind,scope_id,owner_id,"
                    "value_json,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        category,
                        state_key,
                        scope.kind.value,
                        scope.scope_id,
                        owner,
                        json.dumps(plain(frozen), sort_keys=True, separators=(",", ":")),
                        1,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            current = self._get(category, state_key, scope)
            return StateWriteResult(StateWriteOutcome.STALE, current)
        except sqlite3.Error as error:
            raise StateStoreError("unable to create current state") from error
        return StateWriteResult(StateWriteOutcome.CREATED, record)

    def compare_and_set(
        self,
        caller_id: str,
        category: str,
        state_key: str,
        scope: ResourceScope,
        expected_revision: int,
        value: Mapping[str, Any],
    ) -> StateWriteResult:
        owner = self._owner(category)
        if owner is None:
            return StateWriteResult(StateWriteOutcome.UNKNOWN_CATEGORY)
        if caller_id != owner:
            return self._unauthorized_write()
        frozen = validate_record(value, "state value")
        current = self._get(category, state_key, scope)
        if current is None:
            return StateWriteResult(StateWriteOutcome.NOT_FOUND)
        if current.revision != expected_revision:
            return StateWriteResult(
                StateWriteOutcome.STALE,
                current,
                ErrorInfo(
                    "stale_state_revision",
                    "Expected state revision does not match current authoritative state.",
                    "StateConflict",
                ),
            )
        now = self._clock()
        revision = expected_revision + 1
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE current_state SET value_json=?,revision=?,updated_at=? "
                "WHERE category=? AND state_key=? AND scope_kind=? AND scope_id=? AND revision=?",
                (
                    json.dumps(plain(frozen), sort_keys=True, separators=(",", ":")),
                    revision,
                    now.isoformat(),
                    category,
                    state_key,
                    scope.kind.value,
                    scope.scope_id,
                    expected_revision,
                ),
            )
        if cursor.rowcount != 1:
            return StateWriteResult(
                StateWriteOutcome.STALE,
                self._get(category, state_key, scope),
            )
        return StateWriteResult(
            StateWriteOutcome.UPDATED,
            StateRecord(
                category,
                state_key,
                scope,
                owner,
                frozen,
                revision,
                current.created_at,
                now,
            ),
        )

    def read(
        self,
        requester_id: str,
        category: str,
        state_key: str,
        scope: ResourceScope,
    ) -> StateReadResult:
        try:
            decision = self._authorizer.authorize(requester_id, "state.read", scope.target, "read")
        except Exception:
            return StateReadResult(
                None,
                ErrorInfo(
                    "resource_authorization_unavailable",
                    "State authorization failed closed.",
                    "AuthorizationFailure",
                ),
            )
        if not decision.allowed:
            return StateReadResult(None, decision.error, decision.decision_id)
        return StateReadResult(
            self._get(category, state_key, scope),
            permission_decision_id=decision.decision_id,
        )

    def _owner(self, category: str) -> str | None:
        return self._owners.get(category)

    @staticmethod
    def _unauthorized_write() -> StateWriteResult:
        return StateWriteResult(
            StateWriteOutcome.UNAUTHORIZED,
            error=ErrorInfo(
                "state_owner_mismatch",
                "Only the registered subsystem owner may mutate this state category.",
                "StateOwnershipDenied",
            ),
        )

    def _get(self, category: str, state_key: str, scope: ResourceScope) -> StateRecord | None:
        row = self._connection.execute(
            "SELECT * FROM current_state WHERE category=? AND state_key=? "
            "AND scope_kind=? AND scope_id=?",
            (category, state_key, scope.kind.value, scope.scope_id),
        ).fetchone()
        if row is None:
            return None
        return StateRecord(
            row["category"],
            row["state_key"],
            ResourceScope(ScopeKind(row["scope_kind"]), row["scope_id"]),
            row["owner_id"],
            json.loads(row["value_json"]),
            row["revision"],
            datetime.fromisoformat(row["created_at"]),
            datetime.fromisoformat(row["updated_at"]),
        )


__all__ = ["SQLiteStateStore", "StateStoreError"]
