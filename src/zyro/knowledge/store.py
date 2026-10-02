"""Controlled deterministic SQLite Knowledge ingestion and retrieval."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zyro.core.data import lexical_score, plain
from zyro.core.errors import ErrorInfo
from zyro.core.scope import ResourceScope, ScopeKind
from zyro.knowledge.contracts import (
    KnowledgeIngestionOutcome,
    KnowledgeIngestionRequest,
    KnowledgeIngestionResult,
    KnowledgeQuery,
    KnowledgeRecord,
    KnowledgeRetrievalResult,
    KnowledgeSourceType,
    KnowledgeStatus,
)
from zyro.security.resource_authorization import ResourceAuthorizer


def _utc_now() -> datetime:
    return datetime.now(UTC)


class KnowledgeStoreError(RuntimeError):
    pass


def _chunks(content: str, size: int = 2_000) -> tuple[str, ...]:
    paragraphs = [item.strip() for item in content.split("\n\n") if item.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs or [content.strip()]:
        parts = [paragraph[index : index + size] for index in range(0, len(paragraph), size)]
        for part in parts:
            candidate = part if not current else f"{current}\n\n{part}"
            if len(candidate) <= size:
                current = candidate
            else:
                chunks.append(current)
                current = part
    if current:
        chunks.append(current)
    return tuple(chunks)


class SQLiteKnowledgeStore:
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
                CREATE TABLE IF NOT EXISTS knowledge (
                    knowledge_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_reference TEXT NOT NULL,
                    scope_kind TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    ingested_at TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    UNIQUE(source_id,scope_kind,scope_id,version,chunk_index)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_scope_status
                    ON knowledge(scope_kind,scope_id,status,ingested_at DESC);
                CREATE INDEX IF NOT EXISTS idx_knowledge_source_version
                    ON knowledge(source_id,version,status,chunk_index);
                """
            )
            self._connection.execute("PRAGMA user_version=1")
            self._connection.commit()
        except sqlite3.Error as error:
            raise KnowledgeStoreError("unable to initialize knowledge store") from error

    def close(self) -> None:
        self._connection.close()

    def ingest(self, request: KnowledgeIngestionRequest) -> KnowledgeIngestionResult:
        decision = self._authorize(request.requester_id, "knowledge.write", request.scope, "ingest")
        if decision.error is not None:
            return KnowledgeIngestionResult(
                KnowledgeIngestionOutcome.UNAUTHORIZED,
                error=decision.error,
                permission_decision_id=decision.decision_id,
            )
        chunks = _chunks(request.content)
        digest = hashlib.sha256(request.content.encode()).hexdigest()
        existing = self._connection.execute(
            "SELECT DISTINCT content_digest FROM knowledge WHERE source_id=? AND scope_kind=? "
            "AND scope_id=? AND version=?",
            (
                request.source_id,
                request.scope.kind.value,
                request.scope.scope_id,
                request.version,
            ),
        ).fetchall()
        if existing:
            outcome = (
                KnowledgeIngestionOutcome.DUPLICATE
                if {row["content_digest"] for row in existing} == {digest}
                else KnowledgeIngestionOutcome.CONFLICT
            )
            return KnowledgeIngestionResult(
                outcome,
                self._version_records(request.source_id, request.scope, request.version),
                permission_decision_id=decision.decision_id,
            )
        now = self._clock()
        records = tuple(
            KnowledgeRecord(
                self._knowledge_id(request, index),
                request.source_id,
                request.source_type,
                request.source_reference,
                request.scope,
                chunk,
                index,
                now,
                request.version,
                KnowledgeStatus.CURRENT,
                request.metadata,
            )
            for index, chunk in enumerate(chunks)
        )
        try:
            with self._connection:
                self._connection.execute(
                    "UPDATE knowledge SET status=? WHERE source_id=? AND scope_kind=? "
                    "AND scope_id=? AND status=?",
                    (
                        KnowledgeStatus.SUPERSEDED.value,
                        request.source_id,
                        request.scope.kind.value,
                        request.scope.scope_id,
                        KnowledgeStatus.CURRENT.value,
                    ),
                )
                for record in records:
                    self._connection.execute(
                        "INSERT INTO knowledge(knowledge_id,source_id,source_type,source_reference,"
                        "scope_kind,scope_id,content,content_digest,chunk_index,ingested_at,version,"
                        "status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            record.knowledge_id,
                            record.source_id,
                            record.source_type.value,
                            record.source_reference,
                            record.scope.kind.value,
                            record.scope.scope_id,
                            record.content,
                            digest,
                            record.chunk_index,
                            record.ingested_at.isoformat(),
                            record.version,
                            record.status.value,
                            json.dumps(
                                plain(record.metadata), sort_keys=True, separators=(",", ":")
                            ),
                        ),
                    )
        except sqlite3.Error as error:
            raise KnowledgeStoreError("unable to ingest knowledge") from error
        return KnowledgeIngestionResult(
            KnowledgeIngestionOutcome.INGESTED,
            records,
            permission_decision_id=decision.decision_id,
        )

    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult:
        decision = self._authorize(query.requester_id, "knowledge.read", query.scope, "read")
        if decision.error is not None:
            return KnowledgeRetrievalResult((), decision.error, decision.decision_id)
        where = ["scope_kind=?", "scope_id=?"]
        parameters: list[Any] = [query.scope.kind.value, query.scope.scope_id]
        if query.current_only:
            where.append("status=?")
            parameters.append(KnowledgeStatus.CURRENT.value)
        if query.source_id is not None:
            where.append("source_id=?")
            parameters.append(query.source_id)
        rows = self._connection.execute(
            f"SELECT * FROM knowledge WHERE {' AND '.join(where)} "
            "ORDER BY ingested_at DESC,source_id,chunk_index LIMIT 500",
            parameters,
        ).fetchall()
        scored: list[tuple[int, str, int, KnowledgeRecord]] = []
        for row in rows:
            record = self._record(row)
            score = lexical_score(query.query, record.content)
            if query.query.strip() and score == 0:
                continue
            scored.append((score, record.ingested_at.isoformat(), record.chunk_index, record))
        scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3].knowledge_id))
        selected: list[KnowledgeRecord] = []
        used = 0
        for *_, record in scored:
            if used + len(record.content) > query.max_characters:
                continue
            selected.append(record)
            used += len(record.content)
            if len(selected) >= query.limit:
                break
        return KnowledgeRetrievalResult(
            tuple(selected), permission_decision_id=decision.decision_id
        )

    def _version_records(
        self, source_id: str, scope: ResourceScope, version: str
    ) -> tuple[KnowledgeRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM knowledge WHERE source_id=? AND scope_kind=? AND scope_id=? "
            "AND version=? ORDER BY chunk_index",
            (source_id, scope.kind.value, scope.scope_id, version),
        ).fetchall()
        return tuple(self._record(row) for row in rows)

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
                    "Knowledge authorization failed closed.",
                    "AuthorizationFailure",
                ),
            )

    @staticmethod
    def _knowledge_id(request: KnowledgeIngestionRequest, chunk_index: int) -> str:
        identity = f"{request.scope.target}:{request.source_id}:{request.version}:{chunk_index}"
        return f"knowledge-{hashlib.sha256(identity.encode()).hexdigest()}"

    @staticmethod
    def _record(row: sqlite3.Row) -> KnowledgeRecord:
        return KnowledgeRecord(
            row["knowledge_id"],
            row["source_id"],
            KnowledgeSourceType(row["source_type"]),
            row["source_reference"],
            ResourceScope(ScopeKind(row["scope_kind"]), row["scope_id"]),
            row["content"],
            row["chunk_index"],
            datetime.fromisoformat(row["ingested_at"]),
            row["version"],
            KnowledgeStatus(row["status"]),
            json.loads(row["metadata_json"]),
        )


__all__ = ["KnowledgeStoreError", "SQLiteKnowledgeStore"]
