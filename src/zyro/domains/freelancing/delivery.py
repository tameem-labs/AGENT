"""Bounded project, delivery, QA, and handoff lifecycle for Freelancing."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from zyro.core.data import plain, validate_record, validate_text
from zyro.core.events import Event, EventDelivery, EventPublisher, RetryPolicy
from zyro.core.executive import ExecutiveOutcome, ExecutiveResult, UserRequest, ZyroExecutive
from zyro.domains.freelancing.replies import ClientReply, ReplyProcessingResult
from zyro.execution.evidence import (
    TrustedVerificationEvidence,
    VerificationAuthority,
    VerificationSubject,
)
from zyro.observability import TraceContext, TraceStatus
from zyro.observability.service import Observer
from zyro.resources import AdmissionOutcome, ResourceKind, SQLiteResourceManager, WorkLane


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProjectStatus(StrEnum):
    PROJECT_PENDING = "PROJECT_PENDING"
    PROJECT_ACTIVE = "PROJECT_ACTIVE"
    DELIVERY = "DELIVERY"
    QA = "QA"
    HANDOFF = "HANDOFF"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


_ALLOWED_PROJECT_TRANSITIONS: dict[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.PROJECT_PENDING: frozenset(
        {ProjectStatus.PROJECT_ACTIVE, ProjectStatus.REJECTED, ProjectStatus.CANCELLED}
    ),
    ProjectStatus.PROJECT_ACTIVE: frozenset(
        {ProjectStatus.DELIVERY, ProjectStatus.CANCELLED, ProjectStatus.CLOSED}
    ),
    ProjectStatus.DELIVERY: frozenset({ProjectStatus.QA, ProjectStatus.CANCELLED}),
    ProjectStatus.QA: frozenset(
        {ProjectStatus.DELIVERY, ProjectStatus.HANDOFF, ProjectStatus.CANCELLED}
    ),
    ProjectStatus.HANDOFF: frozenset({ProjectStatus.COMPLETED, ProjectStatus.CANCELLED}),
    ProjectStatus.COMPLETED: frozenset({ProjectStatus.CLOSED}),
    ProjectStatus.CLOSED: frozenset(),
    ProjectStatus.REJECTED: frozenset(),
    ProjectStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Deliverable:
    deliverable_id: str
    description: str
    verification_requirement: str
    reference: str | None = None
    verified: bool = False

    def __post_init__(self) -> None:
        for name in ("deliverable_id", "description", "verification_requirement"):
            object.__setattr__(
                self, name, validate_text(getattr(self, name), name, max_chars=4_000)
            )
        if self.reference is not None:
            object.__setattr__(self, "reference", validate_text(self.reference, "reference"))
        if self.verified and self.reference is None:
            raise ValueError("verified deliverable requires a reference")


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    lead_id: str
    client_id: str
    reply_id: str
    workflow_id: str
    request_id: str
    task_id: str
    correlation_id: str
    scope: Mapping[str, Any]
    owner_agent_id: str
    verification_requirements: tuple[str, ...]
    deliverables: tuple[Deliverable, ...]
    dependencies: tuple[str, ...] = ()
    deadline: datetime | None = None
    status: ProjectStatus = ProjectStatus.PROJECT_PENDING
    revision: int = 1
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "lead_id",
            "client_id",
            "reply_id",
            "workflow_id",
            "request_id",
            "task_id",
            "correlation_id",
            "owner_agent_id",
        ):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        object.__setattr__(
            self, "scope", validate_record(self.scope, "project scope", max_bytes=16_384)
        )
        if self.revision < 1:
            raise ValueError("project revision must be positive")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("project timestamps must be timezone-aware")
        if self.deadline is not None and self.deadline.tzinfo is None:
            raise ValueError("project deadline must be timezone-aware")
        for name in ("verification_requirements", "dependencies"):
            object.__setattr__(
                self,
                name,
                tuple(validate_text(item, name) for item in getattr(self, name)),
            )
        if len({item.deliverable_id for item in self.deliverables}) != len(self.deliverables):
            raise ValueError("project deliverables must have unique identities")


class QACheckOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INCONCLUSIVE = "INCONCLUSIVE"


class FindingSeverity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class QACriterion:
    criterion_id: str
    description: str
    passed: bool | None
    evidence: tuple[str, ...]
    severity: FindingSeverity = FindingSeverity.MEDIUM

    def __post_init__(self) -> None:
        object.__setattr__(self, "criterion_id", validate_text(self.criterion_id, "criterion_id"))
        object.__setattr__(self, "description", validate_text(self.description, "description"))
        object.__setattr__(
            self,
            "evidence",
            tuple(validate_text(item, "qa evidence", max_chars=2_000) for item in self.evidence),
        )


@dataclass(frozen=True, slots=True)
class QAResult:
    qa_id: str
    project_id: str
    task_id: str
    qa_agent_id: str
    qa_agent_version: str
    outcome: QACheckOutcome
    criteria: tuple[QACriterion, ...]
    verification_references: tuple[str, ...]
    checked_at: datetime

    def __post_init__(self) -> None:
        for name in ("qa_id", "project_id", "task_id", "qa_agent_id", "qa_agent_version"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        if not self.criteria:
            raise ValueError("QA requires at least one criterion")
        if self.checked_at.tzinfo is None:
            raise ValueError("QA timestamp must be timezone-aware")


class HandoffCompletion(StrEnum):
    VERIFIED_COMPLETE = "VERIFIED_COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class HandoffRecord:
    handoff_id: str
    project_id: str
    client_id: str
    deliverable_ids: tuple[str, ...]
    delivery_status: ProjectStatus
    qa_id: str
    qa_status: QACheckOutcome
    verification_references: tuple[str, ...]
    outstanding_issues: tuple[str, ...]
    completion: HandoffCompletion
    created_at: datetime

    def __post_init__(self) -> None:
        for name in ("handoff_id", "project_id", "client_id", "qa_id"):
            object.__setattr__(self, name, validate_text(getattr(self, name), name))
        if self.created_at.tzinfo is None:
            raise ValueError("handoff time must be timezone-aware")
        if self.completion is HandoffCompletion.VERIFIED_COMPLETE and (
            self.qa_status is not QACheckOutcome.PASS or self.outstanding_issues
        ):
            raise ValueError("verified handoff requires passing QA and no outstanding issues")


@dataclass(frozen=True, slots=True)
class DeliveryTaskRecord:
    project_id: str
    workflow_id: str
    request_id: str
    task_id: str
    correlation_id: str
    agent_id: str
    instance_id: str | None
    attempts: int
    outcome: ExecutiveOutcome
    verification_id: str | None
    recorded_at: datetime

    @property
    def verified(self) -> bool:
        return (
            self.outcome is ExecutiveOutcome.VERIFIED_SUCCESS and self.verification_id is not None
        )


class SQLiteProjectStore:
    """Authoritative durable project aggregate with compare-and-set transitions."""

    def __init__(
        self,
        path: str | Path,
        *,
        verification_authority: VerificationAuthority | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._verification_authority = verification_authority
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                reply_id TEXT NOT NULL UNIQUE,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                document_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_tasks (
                task_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                document_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS qa_results (
                qa_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL UNIQUE,
                document_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS handoffs (
                handoff_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL UNIQUE,
                document_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_operations (
                operation_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                task_id TEXT,
                result_json TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, request_id)
            );
            CREATE TABLE IF NOT EXISTS handoff_operations (
                project_id TEXT PRIMARY KEY,
                handoff_id TEXT NOT NULL,
                target_status TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_project_status ON projects(status,revision);
            CREATE INDEX IF NOT EXISTS idx_delivery_project ON delivery_tasks(project_id);
            """
        )
        with self._connection:
            self._connection.execute(
                "UPDATE delivery_operations SET status=?,updated_at=? WHERE status=?",
                ("UNCERTAIN", self._clock().isoformat(), "RUNNING"),
            )

    def close(self) -> None:
        self._connection.close()

    def create(self, project: ProjectRecord) -> bool:
        encoded = json.dumps(_project_document(project), sort_keys=True, separators=(",", ":"))
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO projects(project_id,reply_id,revision,status,document_json) "
                    "VALUES(?,?,?,?,?)",
                    (
                        project.project_id,
                        project.reply_id,
                        project.revision,
                        project.status.value,
                        encoded,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            existing = self.by_reply_id(project.reply_id)
            if existing == project:
                return False
            raise ValueError("project identity or reply conflict") from None

    def by_reply_id(self, reply_id: str) -> ProjectRecord | None:
        row = self._connection.execute(
            "SELECT document_json FROM projects WHERE reply_id=?", (reply_id,)
        ).fetchone()
        return None if row is None else _project_from_document(json.loads(row["document_json"]))

    def get(self, project_id: str) -> ProjectRecord:
        row = self._connection.execute(
            "SELECT document_json FROM projects WHERE project_id=?", (project_id,)
        ).fetchone()
        if row is None:
            raise KeyError("project is not registered")
        return _project_from_document(json.loads(row["document_json"]))

    def transition(
        self,
        project_id: str,
        expected_revision: int,
        target: ProjectStatus,
    ) -> ProjectRecord:
        current = self.get(project_id)
        if current.revision != expected_revision:
            raise ValueError("stale project revision")
        if target not in _ALLOWED_PROJECT_TRANSITIONS[current.status]:
            raise ValueError(f"invalid project transition: {current.status} -> {target}")
        updated = replace(
            current,
            status=target,
            revision=current.revision + 1,
            updated_at=self._clock(),
        )
        encoded = json.dumps(_project_document(updated), sort_keys=True, separators=(",", ":"))
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE projects SET revision=?,status=?,document_json=? "
                "WHERE project_id=? AND revision=?",
                (updated.revision, target.value, encoded, project_id, expected_revision),
            )
        if cursor.rowcount != 1:
            raise ValueError("stale project revision")
        return updated

    def verify_deliverable(
        self,
        project_id: str,
        expected_revision: int,
        deliverable_id: str,
        evidence: TrustedVerificationEvidence,
    ) -> ProjectRecord:
        current = self.get(project_id)
        if current.revision != expected_revision:
            raise ValueError("stale project revision")
        if current.status not in {ProjectStatus.DELIVERY, ProjectStatus.QA}:
            raise ValueError("deliverables can only verify during delivery or QA")
        if deliverable_id not in {item.deliverable_id for item in current.deliverables}:
            raise ValueError("deliverable is not registered")
        expected = VerificationSubject(
            "project_deliverable",
            deliverable_id,
            task_id=current.task_id,
            workflow_id=current.workflow_id,
        )
        if self._verification_authority is None or not self._verification_authority.validate(
            evidence, expected
        ):
            raise ValueError("trusted deliverable verification evidence is invalid")
        updated = replace(
            current,
            deliverables=tuple(
                replace(item, verified=True, reference=evidence.fingerprint)
                if item.deliverable_id == deliverable_id
                else item
                for item in current.deliverables
            ),
            revision=current.revision + 1,
            updated_at=self._clock(),
        )
        encoded = json.dumps(_project_document(updated), sort_keys=True, separators=(",", ":"))
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE projects SET revision=?,document_json=? WHERE project_id=? AND revision=?",
                (updated.revision, encoded, project_id, expected_revision),
            )
        if cursor.rowcount != 1:
            raise ValueError("stale project revision")
        return updated

    def delivery_operation(self, project_id: str, request_id: str) -> Mapping[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM delivery_operations WHERE project_id=? AND request_id=?",
            (project_id, request_id),
        ).fetchone()
        return None if row is None else dict(row)

    def begin_delivery_operation(
        self, project_id: str, request_id: str, workflow_id: str, operation_id: str
    ) -> Mapping[str, Any]:
        existing = self.delivery_operation(project_id, request_id)
        if existing is not None:
            return existing
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO delivery_operations VALUES(?,?,?,?,?,NULL,NULL,?)",
                    (
                        operation_id,
                        project_id,
                        request_id,
                        workflow_id,
                        "RUNNING",
                        self._clock().isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            pass
        return cast(Mapping[str, Any], self.delivery_operation(project_id, request_id))

    def complete_delivery_operation(
        self, operation_id: str, record: DeliveryTaskRecord, result: ExecutiveResult
    ) -> None:
        document = {
            "project_id": record.project_id,
            "workflow_id": record.workflow_id,
            "request_id": record.request_id,
            "task_id": record.task_id,
            "correlation_id": record.correlation_id,
            "agent_id": record.agent_id,
            "instance_id": record.instance_id,
            "attempts": record.attempts,
            "outcome": record.outcome.value,
            "verification_id": record.verification_id,
            "recorded_at": record.recorded_at.isoformat(),
        }
        result_document = {
            "outcome": result.outcome.value,
            "task_status": result.task_status.value,
            "result": plain(result.result),
            "error_code": None if result.error is None else result.error.code,
            "verification_id": result.verification.verification_id,
        }
        with self._connection:
            self._connection.execute(
                "INSERT INTO delivery_tasks(task_id,project_id,document_json) VALUES(?,?,?)",
                (record.task_id, record.project_id, json.dumps(document, sort_keys=True)),
            )
            cursor = self._connection.execute(
                "UPDATE delivery_operations SET status=?,task_id=?,result_json=?,updated_at=? "
                "WHERE operation_id=? AND status=?",
                (
                    "COMPLETED",
                    record.task_id,
                    json.dumps(result_document, sort_keys=True),
                    self._clock().isoformat(),
                    operation_id,
                    "RUNNING",
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("delivery operation is not running")

    def save_delivery_task(self, record: DeliveryTaskRecord) -> bool:
        document = {
            "project_id": record.project_id,
            "workflow_id": record.workflow_id,
            "request_id": record.request_id,
            "task_id": record.task_id,
            "correlation_id": record.correlation_id,
            "agent_id": record.agent_id,
            "instance_id": record.instance_id,
            "attempts": record.attempts,
            "outcome": record.outcome.value,
            "verification_id": record.verification_id,
            "recorded_at": record.recorded_at.isoformat(),
        }
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO delivery_tasks(task_id,project_id,document_json) VALUES(?,?,?)",
                    (record.task_id, record.project_id, json.dumps(document, sort_keys=True)),
                )
            return True
        except sqlite3.IntegrityError:
            existing = self.delivery_task(record.task_id)
            if existing == record:
                return False
            raise ValueError("delivery task identity conflict") from None

    def delivery_task(self, task_id: str) -> DeliveryTaskRecord | None:
        row = self._connection.execute(
            "SELECT document_json FROM delivery_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return _delivery_task_from_document(json.loads(row["document_json"]))

    def delivery_tasks(self, project_id: str) -> tuple[DeliveryTaskRecord, ...]:
        rows = self._connection.execute(
            "SELECT document_json FROM delivery_tasks WHERE project_id=? ORDER BY task_id",
            (project_id,),
        ).fetchall()
        return tuple(_delivery_task_from_document(json.loads(row["document_json"])) for row in rows)

    def save_qa(self, result: QAResult) -> bool:
        document = _qa_document(result)
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO qa_results(qa_id,project_id,document_json) VALUES(?,?,?)",
                    (result.qa_id, result.project_id, json.dumps(document, sort_keys=True)),
                )
            return True
        except sqlite3.IntegrityError:
            existing = self.qa(result.project_id)
            if existing == result:
                return False
            raise ValueError("QA result identity or project conflict") from None

    def qa(self, project_id: str) -> QAResult | None:
        row = self._connection.execute(
            "SELECT document_json FROM qa_results WHERE project_id=?", (project_id,)
        ).fetchone()
        return None if row is None else _qa_from_document(json.loads(row["document_json"]))

    def save_handoff(self, handoff: HandoffRecord) -> bool:
        document = {
            "handoff_id": handoff.handoff_id,
            "project_id": handoff.project_id,
            "client_id": handoff.client_id,
            "deliverable_ids": list(handoff.deliverable_ids),
            "delivery_status": handoff.delivery_status.value,
            "qa_id": handoff.qa_id,
            "qa_status": handoff.qa_status.value,
            "verification_references": list(handoff.verification_references),
            "outstanding_issues": list(handoff.outstanding_issues),
            "completion": handoff.completion.value,
            "created_at": handoff.created_at.isoformat(),
        }
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO handoffs(handoff_id,project_id,document_json) VALUES(?,?,?)",
                    (handoff.handoff_id, handoff.project_id, json.dumps(document, sort_keys=True)),
                )
            return True
        except sqlite3.IntegrityError:
            existing = self.handoff(handoff.project_id)
            if existing == handoff:
                return False
            raise ValueError("handoff identity or project conflict") from None

    def complete_verified_handoff(
        self, handoff: HandoffRecord, project: ProjectRecord
    ) -> ProjectRecord:
        """Atomically persist the handoff operation and final project state."""
        if handoff.completion is not HandoffCompletion.VERIFIED_COMPLETE:
            raise ValueError("only a verified handoff can complete a project")
        current = self.get(project.project_id)
        existing = self.handoff(project.project_id)
        if existing is not None and existing != handoff:
            raise ValueError("handoff identity or project conflict")
        if current.status is ProjectStatus.COMPLETED:
            return current
        if current.status not in {ProjectStatus.QA, ProjectStatus.HANDOFF}:
            raise ValueError("verified handoff completion requires QA or handoff state")
        revision_increment = 2 if current.status is ProjectStatus.QA else 1
        completed = replace(
            current,
            status=ProjectStatus.COMPLETED,
            revision=current.revision + revision_increment,
            updated_at=self._clock(),
        )
        document = {
            "handoff_id": handoff.handoff_id,
            "project_id": handoff.project_id,
            "client_id": handoff.client_id,
            "deliverable_ids": list(handoff.deliverable_ids),
            "delivery_status": handoff.delivery_status.value,
            "qa_id": handoff.qa_id,
            "qa_status": handoff.qa_status.value,
            "verification_references": list(handoff.verification_references),
            "outstanding_issues": list(handoff.outstanding_issues),
            "completion": handoff.completion.value,
            "created_at": handoff.created_at.isoformat(),
        }
        encoded_project = json.dumps(
            _project_document(completed), sort_keys=True, separators=(",", ":")
        )
        with self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO handoffs(handoff_id,project_id,document_json) VALUES(?,?,?)",
                (handoff.handoff_id, handoff.project_id, json.dumps(document, sort_keys=True)),
            )
            self._connection.execute(
                "INSERT INTO handoff_operations("
                "project_id,handoff_id,target_status,status,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET status=excluded.status,"
                "updated_at=excluded.updated_at",
                (
                    project.project_id,
                    handoff.handoff_id,
                    ProjectStatus.COMPLETED.value,
                    "COMPLETING",
                    self._clock().isoformat(),
                ),
            )
            cursor = self._connection.execute(
                "UPDATE projects SET revision=?,status=?,document_json=? "
                "WHERE project_id=? AND revision=?",
                (
                    completed.revision,
                    ProjectStatus.COMPLETED.value,
                    encoded_project,
                    project.project_id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("stale project revision during handoff completion")
            self._connection.execute(
                "UPDATE handoff_operations SET status=?,updated_at=? WHERE project_id=?",
                ("COMPLETED", self._clock().isoformat(), project.project_id),
            )
        return completed

    def handoff(self, project_id: str) -> HandoffRecord | None:
        row = self._connection.execute(
            "SELECT document_json FROM handoffs WHERE project_id=?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        item = json.loads(row["document_json"])
        return HandoffRecord(
            item["handoff_id"],
            item["project_id"],
            item["client_id"],
            tuple(item["deliverable_ids"]),
            ProjectStatus(item["delivery_status"]),
            item["qa_id"],
            QACheckOutcome(item["qa_status"]),
            tuple(item["verification_references"]),
            tuple(item["outstanding_issues"]),
            HandoffCompletion(item["completion"]),
            datetime.fromisoformat(item["created_at"]),
        )


class OpportunityService:
    """Create only a pending opportunity; activation remains an explicit state transition."""

    def __init__(
        self,
        store: SQLiteProjectStore,
        *,
        observer: Observer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._observer = observer
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def create_pending(
        self,
        reply: ClientReply,
        processing: ReplyProcessingResult,
        *,
        scope: Mapping[str, Any],
        owner_agent_id: str,
        deliverables: tuple[Deliverable, ...],
        verification_requirements: tuple[str, ...],
        workflow_id: str,
    ) -> ProjectRecord:
        if processing.reply_id != reply.reply_id or not processing.creates_potential_project:
            raise ValueError("only matching potential-project processing may create an opportunity")
        existing = self._store.by_reply_id(reply.reply_id)
        if existing is not None:
            requested_identity = (
                reply.lead_id,
                reply.client_id,
                reply.reply_id,
                workflow_id,
                reply.request_id,
                reply.task_id,
                reply.correlation_id,
                plain(validate_record(scope, "project scope", max_bytes=16_384)),
                owner_agent_id,
                verification_requirements,
                deliverables,
            )
            existing_identity = (
                existing.lead_id,
                existing.client_id,
                existing.reply_id,
                existing.workflow_id,
                existing.request_id,
                existing.task_id,
                existing.correlation_id,
                plain(existing.scope),
                existing.owner_agent_id,
                existing.verification_requirements,
                existing.deliverables,
            )
            if requested_identity == existing_identity:
                return existing
            raise ValueError("reply already owns a materially different project")
        project = ProjectRecord(
            self._id_factory(),
            reply.lead_id,
            reply.client_id,
            reply.reply_id,
            workflow_id,
            reply.request_id,
            reply.task_id,
            reply.correlation_id,
            scope,
            owner_agent_id,
            verification_requirements,
            deliverables,
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        self._store.create(project)
        if self._observer is not None:
            with suppress(Exception):
                self._observer.record(
                    "PROJECT_PENDING",
                    "freelancing.opportunity",
                    "create_pending",
                    TraceStatus.SUCCEEDED,
                    TraceContext(
                        project.request_id,
                        project.task_id,
                        project.correlation_id,
                        project.workflow_id,
                        project.owner_agent_id,
                    ),
                    metadata={
                        "project_id": project.project_id,
                        "lead_id": project.lead_id,
                        "reply_id": project.reply_id,
                    },
                )
        return project


class QAService:
    def __init__(
        self,
        store: SQLiteProjectStore,
        *,
        agent_id: str = "freelancing.qa",
        agent_version: str = "1.0.0",
        observer: Observer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self.agent_id = agent_id
        self.agent_version = agent_version
        self._observer = observer
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def evaluate(
        self,
        project_id: str,
        task_id: str,
        criteria: tuple[QACriterion, ...],
        evidence: TrustedVerificationEvidence,
    ) -> QAResult:
        project = self._store.get(project_id)
        verification_references = (evidence.fingerprint,)
        existing = self._store.qa(project_id)
        if existing is not None:
            if (
                existing.task_id == task_id
                and existing.criteria == criteria
                and existing.verification_references == verification_references
                and existing.qa_agent_id == self.agent_id
                and existing.qa_agent_version == self.agent_version
            ):
                return existing
            raise ValueError("project already has a materially different QA result")
        if project.status is not ProjectStatus.QA:
            raise ValueError("QA evaluation requires project QA state")
        expected = VerificationSubject(
            "project_qa",
            project_id,
            task_id=task_id,
            workflow_id=project.workflow_id,
        )
        authority = self._store._verification_authority
        if authority is None or not authority.validate(evidence, expected):
            raise ValueError("trusted QA verification evidence is invalid")
        claimed_criteria = evidence.claims.get("criteria")
        if claimed_criteria != tuple(item.criterion_id for item in criteria):
            raise ValueError("QA evidence does not bind the supplied criteria")
        if any(item.passed is False for item in criteria):
            outcome = QACheckOutcome.FAIL
        elif any(item.passed is None for item in criteria):
            outcome = QACheckOutcome.INCONCLUSIVE
        elif all(item.passed is True and item.evidence for item in criteria):
            outcome = QACheckOutcome.PASS
        else:
            outcome = QACheckOutcome.NEEDS_REVIEW
        result = QAResult(
            self._id_factory(),
            project_id,
            task_id,
            self.agent_id,
            self.agent_version,
            outcome,
            criteria,
            verification_references,
            self._clock(),
        )
        self._store.save_qa(result)
        if self._observer is not None:
            project = self._store.get(project_id)
            with suppress(Exception):
                self._observer.record(
                    "QA_COMPLETED",
                    "freelancing.qa",
                    "evaluate",
                    TraceStatus.SUCCEEDED
                    if result.outcome is QACheckOutcome.PASS
                    else TraceStatus.FAILED,
                    TraceContext(
                        project.request_id,
                        result.task_id,
                        project.correlation_id,
                        project.workflow_id,
                        self.agent_id,
                    ),
                    metadata={
                        "project_id": project_id,
                        "qa_id": result.qa_id,
                        "qa_outcome": result.outcome.value,
                    },
                )
        return result


@dataclass(frozen=True, slots=True)
class DeliveryRun:
    admitted: bool
    result: ExecutiveResult | None
    reason: str


class DeliveryCoordinator:
    """Resource-gated canonical Task/Agent execution with durable project evidence."""

    def __init__(
        self,
        store: SQLiteProjectStore,
        resources: SQLiteResourceManager,
        executive: ZyroExecutive,
        publisher: EventPublisher,
        *,
        observer: Observer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._resources = resources
        self._executive = executive
        self._publisher = publisher
        self._observer = observer
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def run_task(
        self,
        project_id: str,
        request: UserRequest,
        *,
        workflow_id: str,
        lane: WorkLane = WorkLane.BACKGROUND,
    ) -> DeliveryRun:
        project = self._store.get(project_id)
        if project.status not in {ProjectStatus.PROJECT_ACTIVE, ProjectStatus.DELIVERY}:
            return DeliveryRun(False, None, "project is not active for delivery")
        stable_request_id = request.request_id or self._id_factory()
        existing_operation = self._store.delivery_operation(project_id, stable_request_id)
        if existing_operation is not None:
            status = str(existing_operation["status"])
            if status == "COMPLETED":
                return DeliveryRun(
                    False,
                    None,
                    f"delivery already completed as task {existing_operation['task_id']}",
                )
            return DeliveryRun(
                False,
                None,
                "delivery outcome requires reconciliation; execution was not repeated",
            )
        admission_task_id = f"delivery:{project_id}:{stable_request_id}"
        task_reservation = f"task-slot:{admission_task_id}"
        task_admission = self._resources.reserve(
            task_reservation,
            project.owner_agent_id,
            ResourceKind.TASK_SLOT,
            "tasks",
            lane,
            admission_task_id,
            workflow_id,
        )
        if task_admission.outcome is not AdmissionOutcome.ADMITTED:
            return DeliveryRun(False, None, "task capacity is queued")
        agent_reservation = f"agent-slot:{admission_task_id}"
        agent_admission = self._resources.reserve(
            agent_reservation,
            project.owner_agent_id,
            ResourceKind.AGENT_SLOT,
            "agents",
            lane,
            admission_task_id,
            workflow_id,
        )
        if agent_admission.outcome is not AdmissionOutcome.ADMITTED:
            self._resources.release(task_reservation, project.owner_agent_id)
            return DeliveryRun(False, None, "agent capacity is queued")
        operation_id = f"delivery-operation:{project_id}:{stable_request_id}"
        operation = self._store.begin_delivery_operation(
            project_id, stable_request_id, workflow_id, operation_id
        )
        if operation["status"] != "RUNNING":
            self._resources.release(agent_reservation, project.owner_agent_id)
            self._resources.release(task_reservation, project.owner_agent_id)
            return DeliveryRun(
                False, None, "delivery operation could not be claimed for safe execution"
            )
        canonical_request = (
            request
            if request.request_id is not None and request.workflow_id == workflow_id
            else replace(request, request_id=stable_request_id, workflow_id=workflow_id)
        )
        try:
            result = self._executive.handle(canonical_request)
        finally:
            self._resources.release(agent_reservation, project.owner_agent_id)
            self._resources.release(task_reservation, project.owner_agent_id)
        record = DeliveryTaskRecord(
            project_id,
            workflow_id,
            result.request_id,
            result.task_id,
            result.correlation_id,
            request.agent_id,
            result.instance_id,
            result.attempts,
            result.outcome,
            result.verification.verification_id,
            self._clock(),
        )
        self._store.complete_delivery_operation(operation_id, record, result)
        self._emit(project, record)
        return DeliveryRun(True, result, "delivery task executed through canonical Executive")

    def _emit(self, project: ProjectRecord, record: DeliveryTaskRecord) -> None:
        event = Event(
            self._id_factory(),
            record.request_id,
            record.task_id,
            record.correlation_id,
            "DELIVERY_TASK_RECORDED",
            "freelancing.delivery",
            {
                "project_id": project.project_id,
                "task_outcome": record.outcome.value,
                "verified": record.verified,
                "verification_id": record.verification_id,
            },
            self._clock(),
            "1.0",
            workflow_id=record.workflow_id,
            delivery=EventDelivery(True, True, project.project_id, RetryPolicy(3)),
        )
        with suppress(Exception):
            self._publisher.publish(event, idempotency_key=f"DELIVERY_TASK:{record.task_id}")
        if self._observer is not None:
            with suppress(Exception):
                self._observer.record(
                    "DELIVERY_TASK_RECORDED",
                    "freelancing.delivery",
                    "run_task",
                    TraceStatus.SUCCEEDED if record.verified else TraceStatus.UNKNOWN,
                    TraceContext(
                        record.request_id,
                        record.task_id,
                        record.correlation_id,
                        record.workflow_id,
                        record.agent_id,
                        record.instance_id,
                    ),
                    event_id=event.event_id,
                    verification_id=record.verification_id,
                    attempt=record.attempts,
                    metadata={"project_id": project.project_id},
                )


class HandoffService:
    def __init__(
        self,
        store: SQLiteProjectStore,
        *,
        observer: Observer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._observer = observer
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def create(self, project_id: str, outstanding_issues: tuple[str, ...] = ()) -> HandoffRecord:
        issues = tuple(validate_text(item, "outstanding issue") for item in outstanding_issues)
        existing = self._store.handoff(project_id)
        if existing is not None:
            if existing.outstanding_issues != issues:
                raise ValueError("project already has a handoff with different outstanding issues")
            project = self._store.get(project_id)
            if (
                existing.completion is HandoffCompletion.VERIFIED_COMPLETE
                and project.status is not ProjectStatus.COMPLETED
            ):
                self._store.complete_verified_handoff(existing, project)
            return existing
        project = self._store.get(project_id)
        if project.status is not ProjectStatus.QA:
            raise ValueError("handoff requires project QA state")
        qa = self._store.qa(project_id)
        if qa is None:
            raise ValueError("handoff requires a QA result")
        tasks = self._store.delivery_tasks(project_id)
        all_deliverables_verified = bool(project.deliverables) and all(
            item.verified for item in project.deliverables
        )
        all_tasks_verified = bool(tasks) and all(item.verified for item in tasks)
        if (
            qa.outcome is QACheckOutcome.PASS
            and all_deliverables_verified
            and all_tasks_verified
            and not issues
        ):
            completion = HandoffCompletion.VERIFIED_COMPLETE
        elif qa.outcome in {QACheckOutcome.FAIL, QACheckOutcome.NEEDS_REVIEW} or issues:
            completion = HandoffCompletion.INCOMPLETE
        else:
            completion = HandoffCompletion.UNVERIFIED
        handoff = HandoffRecord(
            self._id_factory(),
            project.project_id,
            project.client_id,
            tuple(item.deliverable_id for item in project.deliverables),
            project.status,
            qa.qa_id,
            qa.outcome,
            qa.verification_references,
            issues,
            completion,
            self._clock(),
        )
        if completion is HandoffCompletion.VERIFIED_COMPLETE:
            self._store.complete_verified_handoff(handoff, project)
        else:
            self._store.save_handoff(handoff)
        if self._observer is not None:
            with suppress(Exception):
                self._observer.record(
                    "HANDOFF_RECORDED",
                    "freelancing.handoff",
                    "create",
                    TraceStatus.SUCCEEDED
                    if completion is HandoffCompletion.VERIFIED_COMPLETE
                    else TraceStatus.UNKNOWN,
                    TraceContext(
                        project.request_id,
                        project.task_id,
                        project.correlation_id,
                        project.workflow_id,
                        project.owner_agent_id,
                    ),
                    verification_id=(
                        qa.verification_references[0] if qa.verification_references else None
                    ),
                    metadata={
                        "project_id": project.project_id,
                        "handoff_id": handoff.handoff_id,
                        "completion": completion.value,
                    },
                )
        return handoff


def _delivery_task_from_document(item: Mapping[str, Any]) -> DeliveryTaskRecord:
    return DeliveryTaskRecord(
        item["project_id"],
        item["workflow_id"],
        item["request_id"],
        item["task_id"],
        item["correlation_id"],
        item["agent_id"],
        item["instance_id"],
        int(item["attempts"]),
        ExecutiveOutcome(item["outcome"]),
        item["verification_id"],
        datetime.fromisoformat(item["recorded_at"]),
    )


def _project_document(project: ProjectRecord) -> dict[str, Any]:
    return {
        "project_id": project.project_id,
        "lead_id": project.lead_id,
        "client_id": project.client_id,
        "reply_id": project.reply_id,
        "workflow_id": project.workflow_id,
        "request_id": project.request_id,
        "task_id": project.task_id,
        "correlation_id": project.correlation_id,
        "scope": plain(project.scope),
        "owner_agent_id": project.owner_agent_id,
        "verification_requirements": list(project.verification_requirements),
        "deliverables": [
            {
                "deliverable_id": item.deliverable_id,
                "description": item.description,
                "verification_requirement": item.verification_requirement,
                "reference": item.reference,
                "verified": item.verified,
            }
            for item in project.deliverables
        ],
        "dependencies": list(project.dependencies),
        "deadline": None if project.deadline is None else project.deadline.isoformat(),
        "status": project.status.value,
        "revision": project.revision,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def _project_from_document(item: Mapping[str, Any]) -> ProjectRecord:
    return ProjectRecord(
        item["project_id"],
        item["lead_id"],
        item["client_id"],
        item["reply_id"],
        item["workflow_id"],
        item["request_id"],
        item["task_id"],
        item["correlation_id"],
        item["scope"],
        item["owner_agent_id"],
        tuple(item["verification_requirements"]),
        tuple(Deliverable(**value) for value in item["deliverables"]),
        tuple(item["dependencies"]),
        None if item["deadline"] is None else datetime.fromisoformat(item["deadline"]),
        ProjectStatus(item["status"]),
        int(item["revision"]),
        datetime.fromisoformat(item["created_at"]),
        datetime.fromisoformat(item["updated_at"]),
    )


def _qa_document(result: QAResult) -> dict[str, Any]:
    return {
        "qa_id": result.qa_id,
        "project_id": result.project_id,
        "task_id": result.task_id,
        "qa_agent_id": result.qa_agent_id,
        "qa_agent_version": result.qa_agent_version,
        "outcome": result.outcome.value,
        "criteria": [
            {
                "criterion_id": item.criterion_id,
                "description": item.description,
                "passed": item.passed,
                "evidence": list(item.evidence),
                "severity": item.severity.value,
            }
            for item in result.criteria
        ],
        "verification_references": list(result.verification_references),
        "checked_at": result.checked_at.isoformat(),
    }


def _qa_from_document(item: Mapping[str, Any]) -> QAResult:
    return QAResult(
        item["qa_id"],
        item["project_id"],
        item["task_id"],
        item["qa_agent_id"],
        item["qa_agent_version"],
        QACheckOutcome(item["outcome"]),
        tuple(
            QACriterion(
                value["criterion_id"],
                value["description"],
                value["passed"],
                tuple(value["evidence"]),
                FindingSeverity(value["severity"]),
            )
            for value in item["criteria"]
        ),
        tuple(item["verification_references"]),
        datetime.fromisoformat(item["checked_at"]),
    )


__all__ = [
    "Deliverable",
    "DeliveryCoordinator",
    "DeliveryRun",
    "DeliveryTaskRecord",
    "FindingSeverity",
    "HandoffCompletion",
    "HandoffRecord",
    "HandoffService",
    "OpportunityService",
    "ProjectRecord",
    "ProjectStatus",
    "QACheckOutcome",
    "QACriterion",
    "QAResult",
    "QAService",
    "SQLiteProjectStore",
]
