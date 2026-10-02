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
from typing import Any
from uuid import uuid4

from zyro.core.data import plain, validate_record, validate_text
from zyro.core.events import Event, EventDelivery, EventPublisher, RetryPolicy
from zyro.core.executive import ExecutiveOutcome, ExecutiveResult, UserRequest, ZyroExecutive
from zyro.domains.freelancing.replies import ClientReply, ReplyProcessingResult
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

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self.path = Path(path)
        self._clock = clock
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
            CREATE INDEX IF NOT EXISTS idx_project_status ON projects(status,revision);
            CREATE INDEX IF NOT EXISTS idx_delivery_project ON delivery_tasks(project_id);
            """
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
            existing = self.get(project.project_id)
            if existing == project:
                return False
            raise ValueError("project identity or reply conflict") from None

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
        reference: str,
    ) -> ProjectRecord:
        current = self.get(project_id)
        if current.revision != expected_revision:
            raise ValueError("stale project revision")
        if current.status not in {ProjectStatus.DELIVERY, ProjectStatus.QA}:
            raise ValueError("deliverables can only verify during delivery or QA")
        if deliverable_id not in {item.deliverable_id for item in current.deliverables}:
            raise ValueError("deliverable is not registered")
        updated = replace(
            current,
            deliverables=tuple(
                replace(item, verified=True, reference=reference)
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
            return False

    def delivery_tasks(self, project_id: str) -> tuple[DeliveryTaskRecord, ...]:
        rows = self._connection.execute(
            "SELECT document_json FROM delivery_tasks WHERE project_id=? ORDER BY task_id",
            (project_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = json.loads(row["document_json"])
            result.append(
                DeliveryTaskRecord(
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
            )
        return tuple(result)

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
            return False

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
            return False


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
        verification_references: tuple[str, ...] = (),
    ) -> QAResult:
        existing = self._store.qa(project_id)
        if existing is not None:
            return existing
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
        admission_task_id = f"delivery:{project_id}:{request.request_id or self._id_factory()}"
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
        try:
            result = self._executive.handle(request)
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
        self._store.save_delivery_task(record)
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
        project = self._store.get(project_id)
        qa = self._store.qa(project_id)
        if qa is None:
            raise ValueError("handoff requires a QA result")
        tasks = self._store.delivery_tasks(project_id)
        all_deliverables_verified = bool(project.deliverables) and all(
            item.verified for item in project.deliverables
        )
        all_tasks_verified = bool(tasks) and all(item.verified for item in tasks)
        issues = tuple(validate_text(item, "outstanding issue") for item in outstanding_issues)
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
        if completion is HandoffCompletion.VERIFIED_COMPLETE:
            handoff_state = self._store.transition(
                project.project_id, project.revision, ProjectStatus.HANDOFF
            )
            self._store.transition(
                project.project_id, handoff_state.revision, ProjectStatus.COMPLETED
            )
        return handoff


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
