"""Validated client-reply intake and deterministic advisory processing."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from zyro.core.data import plain, validate_record, validate_text
from zyro.core.events import Event, EventDelivery, EventPublisher, RetryPolicy
from zyro.observability import TraceContext, TraceStatus
from zyro.observability.service import Observer


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ReplyCategory(StrEnum):
    INTERESTED = "INTERESTED"
    NOT_INTERESTED = "NOT_INTERESTED"
    QUESTION_REQUEST = "QUESTION_REQUEST"
    REQUIRES_HUMAN_RESPONSE = "REQUIRES_HUMAN_RESPONSE"
    POTENTIAL_PROJECT = "POTENTIAL_PROJECT"
    UNCLEAR = "UNCLEAR"
    SPAM_IRRELEVANT = "SPAM_IRRELEVANT"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class ClientReply:
    reply_id: str
    external_reference_id: str
    lead_id: str
    client_id: str
    thread_id: str
    channel: str
    received_at: datetime
    body: str
    request_id: str
    task_id: str
    correlation_id: str
    workflow_id: str | None = None
    subject: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    source: str = "external_callback"

    def __post_init__(self) -> None:
        for name in (
            "reply_id",
            "external_reference_id",
            "lead_id",
            "client_id",
            "thread_id",
            "channel",
            "body",
            "request_id",
            "task_id",
            "correlation_id",
            "source",
        ):
            limit = 20_000 if name == "body" else 1_024
            object.__setattr__(
                self, name, validate_text(getattr(self, name), name, max_chars=limit)
            )
        for name in ("workflow_id", "subject"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, validate_text(value, name, max_chars=2_000))
        if self.received_at.tzinfo is None:
            raise ValueError("reply received_at must be timezone-aware")
        object.__setattr__(
            self,
            "provider_metadata",
            validate_record(self.provider_metadata, "reply provider metadata", max_bytes=8_192),
        )


@dataclass(frozen=True, slots=True)
class ReplyProcessingResult:
    processing_id: str
    reply_id: str
    category: ReplyCategory
    policy_version: str
    evidence: tuple[str, ...]
    requires_human_response: bool
    creates_potential_project: bool
    task_id: str
    agent_id: str
    processed_at: datetime
    model_id: str | None = None
    advisory_only: bool = True

    def __post_init__(self) -> None:
        for name in ("processing_id", "reply_id", "policy_version", "task_id", "agent_id"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        if self.model_id is not None:
            object.__setattr__(self, "model_id", validate_text(self.model_id, "model_id"))
        if self.processed_at.tzinfo is None:
            raise ValueError("reply processing time must be timezone-aware")
        if not self.advisory_only:
            raise ValueError("reply classification must remain advisory")
        object.__setattr__(
            self,
            "evidence",
            tuple(validate_text(item, "reply evidence", max_chars=1_000) for item in self.evidence),
        )


class SQLiteReplyStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS client_replies (
                reply_id TEXT PRIMARY KEY,
                external_reference_id TEXT NOT NULL UNIQUE,
                document_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reply_processing (
                processing_id TEXT PRIMARY KEY,
                reply_id TEXT NOT NULL UNIQUE,
                document_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reply_external
                ON client_replies(external_reference_id);
            """
        )

    def close(self) -> None:
        self._connection.close()

    def ingest(self, reply: ClientReply) -> bool:
        encoded = json.dumps(_reply_document(reply), sort_keys=True, separators=(",", ":"))
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO client_replies(reply_id,external_reference_id,document_json) "
                    "VALUES(?,?,?)",
                    (reply.reply_id, reply.external_reference_id, encoded),
                )
            return True
        except sqlite3.IntegrityError:
            existing = self.by_external_reference(reply.external_reference_id)
            if existing == reply:
                return False
            raise ValueError("reply identity or external reference conflict") from None

    def by_external_reference(self, external_reference_id: str) -> ClientReply:
        row = self._connection.execute(
            "SELECT document_json FROM client_replies WHERE external_reference_id=?",
            (external_reference_id,),
        ).fetchone()
        if row is None:
            raise KeyError("reply external reference is not registered")
        return _reply_from_document(json.loads(row["document_json"]))

    def get(self, reply_id: str) -> ClientReply:
        row = self._connection.execute(
            "SELECT document_json FROM client_replies WHERE reply_id=?", (reply_id,)
        ).fetchone()
        if row is None:
            raise KeyError("reply is not registered")
        return _reply_from_document(json.loads(row["document_json"]))

    def save_processing(self, result: ReplyProcessingResult) -> bool:
        document = {
            "processing_id": result.processing_id,
            "reply_id": result.reply_id,
            "category": result.category.value,
            "policy_version": result.policy_version,
            "evidence": list(result.evidence),
            "requires_human_response": result.requires_human_response,
            "creates_potential_project": result.creates_potential_project,
            "task_id": result.task_id,
            "agent_id": result.agent_id,
            "processed_at": result.processed_at.isoformat(),
            "model_id": result.model_id,
        }
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO reply_processing(processing_id,reply_id,document_json) "
                    "VALUES(?,?,?)",
                    (result.processing_id, result.reply_id, encoded),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def processing(self, reply_id: str) -> ReplyProcessingResult | None:
        row = self._connection.execute(
            "SELECT document_json FROM reply_processing WHERE reply_id=?", (reply_id,)
        ).fetchone()
        if row is None:
            return None
        document = json.loads(row["document_json"])
        return ReplyProcessingResult(
            document["processing_id"],
            document["reply_id"],
            ReplyCategory(document["category"]),
            document["policy_version"],
            tuple(document["evidence"]),
            bool(document["requires_human_response"]),
            bool(document["creates_potential_project"]),
            document["task_id"],
            document["agent_id"],
            datetime.fromisoformat(document["processed_at"]),
            document["model_id"],
        )


class ClientReplyIntake:
    """External callback boundary: validate, persist, then publish; never mutate agents."""

    def __init__(
        self,
        store: SQLiteReplyStore,
        publisher: EventPublisher,
        *,
        observer: Observer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._observer = observer
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def ingest(self, reply: ClientReply) -> bool:
        created = self._store.ingest(reply)
        event = Event(
            self._id_factory(),
            reply.request_id,
            reply.task_id,
            reply.correlation_id,
            "CLIENT_REPLY_RECEIVED",
            "freelancing.reply-intake",
            {
                "reply_id": reply.reply_id,
                "external_reference_id": reply.external_reference_id,
                "lead_id": reply.lead_id,
                "client_id": reply.client_id,
                "thread_id": reply.thread_id,
                "channel": reply.channel,
                "received_at": reply.received_at.isoformat(),
            },
            self._clock(),
            "1.0",
            workflow_id=reply.workflow_id,
            delivery=EventDelivery(True, True, reply.thread_id, RetryPolicy(3)),
        )
        # Re-ingesting an identical callback safely reconciles a prior publish failure:
        # the canonical Event Bus deduplicates this stable publication identity.
        with suppress(Exception):
            self._publisher.publish(
                event,
                idempotency_key=f"CLIENT_REPLY_RECEIVED:{reply.external_reference_id}",
            )
        if self._observer is not None:
            with suppress(Exception):
                self._observer.record(
                    "CLIENT_REPLY_RECEIVED",
                    "freelancing.reply-intake",
                    "ingest",
                    TraceStatus.SUCCEEDED,
                    TraceContext(
                        reply.request_id,
                        reply.task_id,
                        reply.correlation_id,
                        reply.workflow_id,
                    ),
                    event_id=event.event_id,
                    metadata={"reply_id": reply.reply_id, "lead_id": reply.lead_id},
                )
        return created


class DeterministicReplyProcessor:
    """Bounded classification policy. Text is data and never invokes a tool."""

    def __init__(
        self,
        store: SQLiteReplyStore,
        *,
        policy_version: str,
        agent_id: str = "freelancing.reply-processing",
        observer: Observer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self.policy_version = validate_text(policy_version, "policy_version")
        self.agent_id = validate_text(agent_id, "agent_id")
        self._observer = observer
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def process(self, reply_id: str, *, processing_task_id: str) -> ReplyProcessingResult:
        existing = self._store.processing(reply_id)
        if existing is not None:
            return existing
        reply = self._store.get(reply_id)
        text = f"{reply.subject or ''} {reply.body}".lower()
        category, evidence = _classify(text)
        result = ReplyProcessingResult(
            self._id_factory(),
            reply.reply_id,
            category,
            self.policy_version,
            evidence,
            category
            in {
                ReplyCategory.QUESTION_REQUEST,
                ReplyCategory.REQUIRES_HUMAN_RESPONSE,
                ReplyCategory.UNCLEAR,
            },
            category in {ReplyCategory.INTERESTED, ReplyCategory.POTENTIAL_PROJECT},
            processing_task_id,
            self.agent_id,
            self._clock(),
        )
        self._store.save_processing(result)
        if self._observer is not None:
            with suppress(Exception):
                self._observer.record(
                    "CLIENT_REPLY_PROCESSED",
                    "freelancing.reply-processing",
                    "classify",
                    TraceStatus.SUCCEEDED,
                    TraceContext(
                        reply.request_id,
                        processing_task_id,
                        reply.correlation_id,
                        reply.workflow_id,
                        self.agent_id,
                    ),
                    metadata={
                        "reply_id": reply.reply_id,
                        "lead_id": reply.lead_id,
                        "processing_id": result.processing_id,
                        "category": result.category.value,
                        "advisory_only": True,
                    },
                )
        return result


def _classify(text: str) -> tuple[ReplyCategory, tuple[str, ...]]:
    spam = ("unsubscribe", "crypto giveaway", "buy followers")
    negative = ("not interested", "no thanks", "do not contact")
    project = ("project", "proposal", "scope", "hire", "start work")
    interested = ("interested", "sounds good", "let's talk", "lets talk")
    if any(term in text for term in spam):
        return ReplyCategory.SPAM_IRRELEVANT, ("matched configured irrelevant-content marker",)
    if any(term in text for term in negative):
        return ReplyCategory.NOT_INTERESTED, ("matched configured negative-intent marker",)
    if any(term in text for term in project):
        return ReplyCategory.POTENTIAL_PROJECT, ("matched configured project-intent marker",)
    if any(term in text for term in interested):
        return ReplyCategory.INTERESTED, ("matched configured interest marker",)
    if "?" in text:
        return ReplyCategory.QUESTION_REQUEST, ("message contains a question",)
    if len(text.split()) < 3:
        return ReplyCategory.UNCLEAR, ("message is too short for deterministic classification",)
    return ReplyCategory.OTHER, ("no configured deterministic marker matched",)


def _reply_document(reply: ClientReply) -> dict[str, Any]:
    return {
        "reply_id": reply.reply_id,
        "external_reference_id": reply.external_reference_id,
        "lead_id": reply.lead_id,
        "client_id": reply.client_id,
        "thread_id": reply.thread_id,
        "channel": reply.channel,
        "received_at": reply.received_at.isoformat(),
        "body": reply.body,
        "request_id": reply.request_id,
        "task_id": reply.task_id,
        "correlation_id": reply.correlation_id,
        "workflow_id": reply.workflow_id,
        "subject": reply.subject,
        "provider_metadata": plain(reply.provider_metadata),
        "source": reply.source,
    }


def _reply_from_document(document: Mapping[str, Any]) -> ClientReply:
    return ClientReply(
        document["reply_id"],
        document["external_reference_id"],
        document["lead_id"],
        document["client_id"],
        document["thread_id"],
        document["channel"],
        datetime.fromisoformat(document["received_at"]),
        document["body"],
        document["request_id"],
        document["task_id"],
        document["correlation_id"],
        document["workflow_id"],
        document["subject"],
        document["provider_metadata"],
        document["source"],
    )


__all__ = [
    "ClientReply",
    "ClientReplyIntake",
    "DeterministicReplyProcessor",
    "ReplyCategory",
    "ReplyProcessingResult",
    "SQLiteReplyStore",
]
