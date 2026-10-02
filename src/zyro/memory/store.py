"""SQLite-backed selective Memory storage, correction, retrieval, and forgetting."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from zyro.core.data import lexical_score, plain
from zyro.core.errors import ErrorInfo
from zyro.core.scope import ResourceScope, ScopeKind
from zyro.memory.contracts import (
    AssertionType,
    MemoryLayer,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryRetrievalResult,
    MemorySourceType,
    MemoryStatus,
    MemoryType,
    MemoryWriteOutcome,
    MemoryWriteRequest,
    MemoryWriteResult,
    PrivacyClassification,
    RetentionPolicy,
)
from zyro.security.resource_authorization import ResourceAuthorizer


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _time(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class MemoryStoreError(RuntimeError):
    pass


class SQLiteMemoryStore:
    """Durable local Memory repository; ordinary reads expose active effective records only."""

    def __init__(
        self,
        path: str | Path,
        authorizer: ResourceAuthorizer,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._authorizer = authorizer
        self._clock = clock
        try:
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    logical_key TEXT NOT NULL,
                    scope_kind TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    assertion_type TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_from TEXT,
                    valid_until TEXT,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT,
                    owner_controlled INTEGER NOT NULL,
                    privacy TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    supersedes_memory_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memory_scope_status
                    ON memories(scope_kind,scope_id,status,updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_type_layer
                    ON memories(memory_type,layer);
                CREATE INDEX IF NOT EXISTS idx_memory_logical
                    ON memories(scope_kind,scope_id,logical_key,revision DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_retention ON memories(expires_at);
                """
            )
            self._connection.execute("PRAGMA user_version=1")
            self._connection.commit()
        except sqlite3.Error as error:
            raise MemoryStoreError("unable to initialize memory store") from error

    def close(self) -> None:
        self._connection.close()

    def write(self, request: MemoryWriteRequest) -> MemoryWriteResult:
        decision = self._authorize(request.requester_id, "memory.write", request.scope, "write")
        if decision.error is not None:
            return MemoryWriteResult(
                MemoryWriteOutcome.UNAUTHORIZED,
                error=decision.error,
                permission_decision_id=decision.decision_id,
            )
        now = self._clock()
        retention = request.retention or self._default_retention(
            request.scope.kind, request.memory_type, now
        )
        record = MemoryRecord(
            request.memory_id,
            request.logical_key,
            request.scope,
            request.layer,
            request.memory_type,
            request.assertion_type,
            request.content,
            request.provenance,
            now,
            now,
            request.valid_from,
            request.valid_until,
            request.confidence,
            MemoryStatus.ACTIVE,
            retention,
            request.privacy,
            1,
        )
        existing_id = self._row_by_id(record.memory_id)
        if existing_id is not None:
            existing = self._record(existing_id)
            same_identity = (
                existing.logical_key == request.logical_key
                and existing.scope == request.scope
                and existing.layer is request.layer
                and existing.memory_type is request.memory_type
                and existing.assertion_type is request.assertion_type
                and existing.content == request.content
                and existing.provenance == request.provenance
                and existing.valid_from == request.valid_from
                and existing.valid_until == request.valid_until
                and existing.confidence == request.confidence
                and existing.privacy is request.privacy
            )
            outcome = MemoryWriteOutcome.DUPLICATE if same_identity else MemoryWriteOutcome.CONFLICT
            return MemoryWriteResult(outcome, existing, permission_decision_id=decision.decision_id)
        current = self._current_row(record.scope, record.logical_key)
        if current is not None:
            return MemoryWriteResult(
                MemoryWriteOutcome.CONFLICT,
                self._record(current),
                ErrorInfo(
                    "memory_correction_required",
                    "An active assertion already owns this logical key; use correction.",
                    "MemoryConflict",
                ),
                decision.decision_id,
            )
        self._insert(record)
        return MemoryWriteResult(
            MemoryWriteOutcome.CREATED,
            record,
            permission_decision_id=decision.decision_id,
        )

    def correct(
        self,
        current_memory_id: str,
        expected_revision: int,
        replacement: MemoryWriteRequest,
        *,
        contradicted: bool = False,
    ) -> MemoryWriteResult:
        decision = self._authorize(
            replacement.requester_id, "memory.write", replacement.scope, "correct"
        )
        if decision.error is not None:
            return MemoryWriteResult(
                MemoryWriteOutcome.UNAUTHORIZED,
                error=decision.error,
                permission_decision_id=decision.decision_id,
            )
        row = self._row_by_id(current_memory_id)
        if row is None:
            return MemoryWriteResult(
                MemoryWriteOutcome.NOT_FOUND, permission_decision_id=decision.decision_id
            )
        current = self._record(row)
        if (
            current.scope != replacement.scope
            or current.logical_key != replacement.logical_key
            or current.status is not MemoryStatus.ACTIVE
            or current.revision != expected_revision
        ):
            return MemoryWriteResult(
                MemoryWriteOutcome.CONFLICT,
                current,
                ErrorInfo(
                    "memory_revision_conflict",
                    "Memory correction does not match the active revision and scope.",
                    "MemoryConflict",
                ),
                decision.decision_id,
            )
        if self._row_by_id(replacement.memory_id) is not None:
            return MemoryWriteResult(
                MemoryWriteOutcome.CONFLICT, permission_decision_id=decision.decision_id
            )
        now = self._clock()
        retention = replacement.retention or self._default_retention(
            replacement.scope.kind, replacement.memory_type, now
        )
        updated = MemoryRecord(
            replacement.memory_id,
            replacement.logical_key,
            replacement.scope,
            replacement.layer,
            replacement.memory_type,
            replacement.assertion_type,
            replacement.content,
            replacement.provenance,
            now,
            now,
            replacement.valid_from,
            replacement.valid_until,
            replacement.confidence,
            MemoryStatus.ACTIVE,
            retention,
            replacement.privacy,
            current.revision + 1,
            current.memory_id,
        )
        old_status = MemoryStatus.CONTRADICTED if contradicted else MemoryStatus.SUPERSEDED
        try:
            with self._connection:
                self._connection.execute(
                    "UPDATE memories SET status=?,updated_at=? WHERE memory_id=? AND revision=?",
                    (old_status.value, _iso(now), current.memory_id, expected_revision),
                )
                self._insert(updated, transactional=True)
        except sqlite3.Error as error:
            raise MemoryStoreError("unable to correct memory") from error
        return MemoryWriteResult(
            MemoryWriteOutcome.CORRECTED,
            updated,
            permission_decision_id=decision.decision_id,
        )

    def forget(
        self,
        requester_id: str,
        scope: ResourceScope,
        memory_id: str,
    ) -> MemoryWriteResult:
        decision = self._authorize(requester_id, "memory.write", scope, "forget")
        if decision.error is not None:
            return MemoryWriteResult(
                MemoryWriteOutcome.UNAUTHORIZED,
                error=decision.error,
                permission_decision_id=decision.decision_id,
            )
        row = self._row_by_id(memory_id)
        if row is None:
            return MemoryWriteResult(
                MemoryWriteOutcome.NOT_FOUND, permission_decision_id=decision.decision_id
            )
        record = self._record(row)
        if record.scope != scope:
            return MemoryWriteResult(
                MemoryWriteOutcome.NOT_FOUND, permission_decision_id=decision.decision_id
            )
        now = self._clock()
        with self._connection:
            self._connection.execute(
                "UPDATE memories SET content_json='{}',status=?,updated_at=? "
                "WHERE scope_kind=? AND scope_id=? AND logical_key=?",
                (
                    MemoryStatus.FORGOTTEN.value,
                    _iso(now),
                    scope.kind.value,
                    scope.scope_id,
                    record.logical_key,
                ),
            )
        forgotten_row = self._row_by_id(memory_id)
        assert forgotten_row is not None
        return MemoryWriteResult(
            MemoryWriteOutcome.FORGOTTEN,
            self._record(forgotten_row),
            permission_decision_id=decision.decision_id,
        )

    def retrieve(self, query: MemoryQuery) -> MemoryRetrievalResult:
        decision = self._authorize(query.requester_id, "memory.read", query.scope, "read")
        if decision.error is not None:
            return MemoryRetrievalResult((), decision.error, (decision.decision_id,))
        parameters: list[Any] = [
            query.scope.kind.value,
            query.scope.scope_id,
            MemoryStatus.ACTIVE.value,
        ]
        where = ["scope_kind=?", "scope_id=?", "status=?"]
        if query.since is not None:
            where.append("updated_at>=?")
            parameters.append(_iso(query.since))
        if query.layers:
            where.append(f"layer IN ({','.join('?' for _ in query.layers)})")
            parameters.extend(item.value for item in query.layers)
        if query.memory_types:
            where.append(f"memory_type IN ({','.join('?' for _ in query.memory_types)})")
            parameters.extend(item.value for item in query.memory_types)
        rows = self._connection.execute(
            f"SELECT * FROM memories WHERE {' AND '.join(where)} "
            "ORDER BY updated_at DESC,memory_id LIMIT 500",
            parameters,
        ).fetchall()
        now = self._clock()
        scored: list[tuple[int, int, float, str, MemoryRecord]] = []
        permission_ids = [decision.decision_id]
        for row in rows:
            record = self._record(row)
            if not record.effective(now):
                continue
            if record.privacy is PrivacyClassification.RESTRICTED:
                restricted = self._authorize(
                    query.requester_id,
                    "memory.read.restricted",
                    query.scope,
                    "read",
                )
                permission_ids.append(restricted.decision_id)
                if restricted.error is not None:
                    continue
            encoded = json.dumps(plain(record.content), sort_keys=True)
            relevance = lexical_score(query.query, encoded)
            if query.query.strip() and relevance == 0:
                continue
            authority = 2 if record.assertion_type is AssertionType.FACT else 1
            scored.append(
                (relevance, authority, record.confidence, record.updated_at.isoformat(), record)
            )
        scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4].memory_id))
        selected: list[MemoryRecord] = []
        used = 0
        for *_, record in scored:
            size = len(json.dumps(plain(record.content), sort_keys=True))
            if selected and used + size > query.max_characters:
                continue
            if size > query.max_characters:
                continue
            selected.append(record)
            used += size
            if len(selected) >= query.limit:
                break
        return MemoryRetrievalResult(tuple(selected), permission_decision_ids=tuple(permission_ids))

    def _authorize(
        self, requester_id: str, capability: str, scope: ResourceScope, action: str
    ) -> Any:
        try:
            return self._authorizer.authorize(requester_id, capability, scope.target, action)
        except Exception:
            from zyro.security.resource_authorization import ResourceAuthorizationDecision

            return ResourceAuthorizationDecision(
                False,
                "authorization-unavailable",
                ErrorInfo(
                    "resource_authorization_unavailable",
                    "Resource authorization failed closed.",
                    "AuthorizationFailure",
                ),
            )

    @staticmethod
    def _default_retention(
        scope: ScopeKind, memory_type: MemoryType, now: datetime
    ) -> RetentionPolicy:
        if scope in {ScopeKind.USER} or memory_type in {
            MemoryType.PREFERENCE,
            MemoryType.DECISION,
            MemoryType.RELATIONSHIP,
        }:
            return RetentionPolicy(owner_controlled=True)
        days = 7 if scope in {ScopeKind.TASK, ScopeKind.WORKFLOW, ScopeKind.AGENT} else 90
        return RetentionPolicy(now + timedelta(days=days))

    def _insert(self, record: MemoryRecord, *, transactional: bool = False) -> None:
        values = (
            record.memory_id,
            record.logical_key,
            record.scope.kind.value,
            record.scope.scope_id,
            record.layer.value,
            record.memory_type.value,
            record.assertion_type.value,
            json.dumps(plain(record.content), sort_keys=True, separators=(",", ":")),
            record.provenance.source_type.value,
            record.provenance.source_id,
            json.dumps(record.provenance.evidence_ids),
            _iso(record.created_at),
            _iso(record.updated_at),
            _iso(record.valid_from),
            _iso(record.valid_until),
            record.confidence,
            record.status.value,
            _iso(record.retention.expires_at),
            int(record.retention.owner_controlled),
            record.privacy.value,
            record.revision,
            record.supersedes_memory_id,
        )
        statement = """INSERT INTO memories(
            memory_id,logical_key,scope_kind,scope_id,layer,memory_type,assertion_type,
            content_json,source_type,source_id,evidence_json,created_at,updated_at,
            valid_from,valid_until,confidence,status,expires_at,owner_controlled,privacy,
            revision,supersedes_memory_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        try:
            if transactional:
                self._connection.execute(statement, values)
            else:
                with self._connection:
                    self._connection.execute(statement, values)
        except sqlite3.Error as error:
            raise MemoryStoreError("unable to persist memory") from error

    def _row_by_id(self, memory_id: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._connection.execute(
                "SELECT * FROM memories WHERE memory_id=?", (memory_id,)
            ).fetchone(),
        )

    def _current_row(self, scope: ResourceScope, logical_key: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._connection.execute(
                "SELECT * FROM memories WHERE scope_kind=? AND scope_id=? AND logical_key=? "
                "AND status=? ORDER BY revision DESC LIMIT 1",
                (scope.kind.value, scope.scope_id, logical_key, MemoryStatus.ACTIVE.value),
            ).fetchone(),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            row["memory_id"],
            row["logical_key"],
            ResourceScope(ScopeKind(row["scope_kind"]), row["scope_id"]),
            MemoryLayer(row["layer"]),
            MemoryType(row["memory_type"]),
            AssertionType(row["assertion_type"]),
            json.loads(row["content_json"]),
            MemoryProvenance(
                MemorySourceType(row["source_type"]),
                row["source_id"],
                tuple(json.loads(row["evidence_json"])),
            ),
            _time(row["created_at"]),  # type: ignore[arg-type]
            _time(row["updated_at"]),  # type: ignore[arg-type]
            _time(row["valid_from"]),
            _time(row["valid_until"]),
            row["confidence"],
            MemoryStatus(row["status"]),
            RetentionPolicy(_time(row["expires_at"]), bool(row["owner_controlled"])),
            PrivacyClassification(row["privacy"]),
            row["revision"],
            row["supersedes_memory_id"],
        )


__all__ = ["MemoryStoreError", "SQLiteMemoryStore"]
