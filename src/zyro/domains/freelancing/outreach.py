"""Approval-bound outreach preparation and idempotent external dispatch."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from zyro.core.data import plain, validate_record, validate_text
from zyro.core.errors import ErrorInfo
from zyro.core.events import Event, EventDelivery, EventPublisher, RetryPolicy
from zyro.core.risk import RiskClass
from zyro.observability import TraceContext, TraceStatus
from zyro.observability.service import Observer
from zyro.recovery import (
    FailureClass,
    FailureIdentity,
    FailureRecord,
    RecoverableOperation,
    RecoverableOperationStatus,
    RecoveryDecision,
    RecoveryPolicy,
    RecoveryRequest,
    SideEffectState,
    SQLiteRecoveryStore,
)
from zyro.resources import (
    AdmissionOutcome,
    ResourceKind,
    SQLiteResourceManager,
    WorkLane,
)
from zyro.tools.contracts import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolHandlerResult,
    ToolInvoker,
    ToolResultStatus,
)

SEND_OUTREACH_CAPABILITY = "send_outreach"
OUTREACH_TOOL_ID = "freelancing.outreach.send"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OutreachChannel(StrEnum):
    EMAIL = "EMAIL"
    CRM = "CRM"
    OTHER_REGISTERED = "OTHER_REGISTERED"


class OutreachStatus(StrEnum):
    PREPARED = "PREPARED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED = "APPROVED"
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"
    ACCEPTED = "ACCEPTED"
    DELIVERED = "DELIVERED"
    VERIFIED = "VERIFIED"
    SUCCEEDED_BUT_UNVERIFIED = "SUCCEEDED_BUT_UNVERIFIED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"


_FINAL_ACTION_STATUSES = frozenset(
    {
        OutreachStatus.ACCEPTED,
        OutreachStatus.DELIVERED,
        OutreachStatus.VERIFIED,
        OutreachStatus.SUCCEEDED_BUT_UNVERIFIED,
        OutreachStatus.FAILED,
        OutreachStatus.UNCERTAIN,
        OutreachStatus.CANCELLED,
        OutreachStatus.REJECTED,
        OutreachStatus.TIMED_OUT,
    }
)


@dataclass(frozen=True, slots=True)
class OutreachPreparation:
    preparation_id: str
    external_action_id: str
    lead_id: str
    lead_revision: int
    qualification_verification_id: str
    recipient_id: str
    recipient: str
    objective: str
    channel: OutreachChannel
    message_body: str
    request_id: str
    task_id: str
    correlation_id: str
    workflow_id: str | None
    agent_id: str
    policy_version: str
    verification_method: str
    personalization_evidence: tuple[str, ...] = ()
    source_references: tuple[str, ...] = ()
    subject: str | None = None
    model_id: str | None = None
    provider_id: str | None = None
    requested_action: str = SEND_OUTREACH_CAPABILITY
    risk_class: RiskClass = RiskClass.STRICT_AUTHORIZATION
    approval_required: bool = True
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        for name in (
            "preparation_id",
            "external_action_id",
            "lead_id",
            "qualification_verification_id",
            "recipient_id",
            "recipient",
            "objective",
            "message_body",
            "request_id",
            "task_id",
            "correlation_id",
            "agent_id",
            "policy_version",
            "verification_method",
            "requested_action",
        ):
            limit = 10_000 if name == "message_body" else 1_024
            object.__setattr__(
                self, name, validate_text(getattr(self, name), name, max_chars=limit)
            )
        for name in ("workflow_id", "subject", "model_id", "provider_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, validate_text(value, name, max_chars=2_000))
        if self.lead_revision < 1:
            raise ValueError("outreach requires a positive qualified lead revision")
        if not isinstance(self.channel, OutreachChannel):
            raise ValueError("channel must be an OutreachChannel")
        if self.risk_class is not RiskClass.STRICT_AUTHORIZATION or not self.approval_required:
            raise ValueError("outbound outreach must require strict action-specific approval")
        if self.requested_action != SEND_OUTREACH_CAPABILITY:
            raise ValueError("outreach requested action must be send_outreach")
        if self.created_at.tzinfo is None:
            raise ValueError("outreach creation time must be timezone-aware")
        for name in ("personalization_evidence", "source_references"):
            values = tuple(
                validate_text(item, name, max_chars=2_000) for item in getattr(self, name)
            )
            object.__setattr__(self, name, values)
        validate_record(self.execution_arguments, "outreach final payload", max_bytes=32_768)

    @property
    def execution_arguments(self) -> Mapping[str, Any]:
        arguments: dict[str, Any] = {
            "external_action_id": self.external_action_id,
            "preparation_id": self.preparation_id,
            "lead_id": self.lead_id,
            "lead_revision": self.lead_revision,
            "qualification_verification_id": self.qualification_verification_id,
            "recipient_id": self.recipient_id,
            "recipient": self.recipient,
            "channel": self.channel.value,
            "message_body": self.message_body,
        }
        if self.subject is not None:
            arguments["subject"] = self.subject
        return arguments

    @property
    def payload_digest(self) -> str:
        encoded = json.dumps(plain(self.execution_arguments), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @property
    def approval_display(self) -> Mapping[str, Any]:
        return {
            "recipient": self.recipient,
            "recipient_id": self.recipient_id,
            "channel": self.channel.value,
            "subject": self.subject,
            "exact_final_message": self.message_body,
            "lead_id": self.lead_id,
            "lead_revision": self.lead_revision,
            "qualification_verification_id": self.qualification_verification_id,
            "objective": self.objective,
            "requested_action": self.requested_action,
            "risk": self.risk_class.value,
            "verification_plan": self.verification_method,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True, slots=True)
class ExternalDispatchResult:
    status: OutreachStatus
    provider_reference: str | None = None
    delivery_confirmed: bool = False
    verification_reference: str | None = None
    simulated: bool = False
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        allowed = {
            OutreachStatus.ACCEPTED,
            OutreachStatus.DELIVERED,
            OutreachStatus.FAILED,
            OutreachStatus.UNCERTAIN,
            OutreachStatus.REJECTED,
            OutreachStatus.TIMED_OUT,
        }
        if self.status not in allowed:
            raise ValueError("adapter returned an unsupported dispatch status")
        if self.delivery_confirmed and self.status is not OutreachStatus.DELIVERED:
            raise ValueError("delivery confirmation requires DELIVERED status")
        if (
            self.status
            in {
                OutreachStatus.FAILED,
                OutreachStatus.UNCERTAIN,
                OutreachStatus.REJECTED,
                OutreachStatus.TIMED_OUT,
            }
            and self.error is None
        ):
            raise ValueError("non-success dispatch requires a structured error")


class OutboundChannelAdapter(Protocol):
    adapter_id: str

    def dispatch(self, preparation: OutreachPreparation) -> ExternalDispatchResult: ...


@dataclass(frozen=True, slots=True)
class ExternalActionRecord:
    external_action_id: str
    preparation_id: str
    payload_digest: str
    status: OutreachStatus
    attempts: int
    provider_reference: str | None
    verification_reference: str | None
    simulated: bool
    updated_at: datetime
    error_code: str | None = None
    error_type: str | None = None


class OutreachStoreError(RuntimeError):
    pass


class SQLiteOutreachStore:
    """Durable preparation/action ledger; DISPATCHING becomes UNCERTAIN after restart."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS outreach_preparations (
                preparation_id TEXT PRIMARY KEY,
                external_action_id TEXT NOT NULL UNIQUE,
                payload_digest TEXT NOT NULL,
                document_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS external_actions (
                external_action_id TEXT PRIMARY KEY,
                preparation_id TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                provider_reference TEXT,
                verification_reference TEXT,
                simulated INTEGER NOT NULL,
                error_code TEXT,
                error_type TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outreach_lead
                ON outreach_preparations(external_action_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_external_action_status
                ON external_actions(status,updated_at);
            """
        )
        with self._connection:
            self.reconciled_uncertain = self._connection.execute(
                "UPDATE external_actions SET status=?,error_code=?,error_type=?,updated_at=? "
                "WHERE status=?",
                (
                    OutreachStatus.UNCERTAIN.value,
                    "process_interrupted_after_dispatch_started",
                    "ExternalSideEffectUncertainty",
                    self._clock().isoformat(),
                    OutreachStatus.DISPATCHING.value,
                ),
            ).rowcount

    def close(self) -> None:
        self._connection.close()

    def save_preparation(self, preparation: OutreachPreparation) -> bool:
        document = _preparation_document(preparation)
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO outreach_preparations(preparation_id,external_action_id,"
                    "payload_digest,document_json,created_at) VALUES(?,?,?,?,?)",
                    (
                        preparation.preparation_id,
                        preparation.external_action_id,
                        preparation.payload_digest,
                        encoded,
                        preparation.created_at.isoformat(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            existing = self.preparation(preparation.preparation_id)
            if existing == preparation:
                return False
            raise OutreachStoreError("outreach preparation identity conflict") from None

    def preparation(self, preparation_id: str) -> OutreachPreparation:
        row = self._connection.execute(
            "SELECT document_json FROM outreach_preparations WHERE preparation_id=?",
            (preparation_id,),
        ).fetchone()
        if row is None:
            raise OutreachStoreError("outreach preparation is not registered")
        return _preparation_from_document(json.loads(row["document_json"]))

    def begin_dispatch(self, preparation: OutreachPreparation) -> tuple[bool, ExternalActionRecord]:
        existing = self.action(preparation.external_action_id)
        if existing is not None:
            if existing.payload_digest != preparation.payload_digest:
                raise OutreachStoreError("external action identity payload conflict")
            return False, existing
        now = self._clock()
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO external_actions(external_action_id,preparation_id,payload_digest,"
                    "status,attempts,simulated,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        preparation.external_action_id,
                        preparation.preparation_id,
                        preparation.payload_digest,
                        OutreachStatus.DISPATCHING.value,
                        1,
                        0,
                        now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self.action(preparation.external_action_id)
            if existing is None:
                raise OutreachStoreError("unable to claim external action") from None
            return False, existing
        return True, cast(ExternalActionRecord, self.action(preparation.external_action_id))

    def finish_dispatch(
        self,
        external_action_id: str,
        result: ExternalDispatchResult,
    ) -> ExternalActionRecord:
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE external_actions SET status=?,provider_reference=?,"
                "verification_reference=?,simulated=?,error_code=?,error_type=?,updated_at=? "
                "WHERE external_action_id=? AND status=?",
                (
                    result.status.value,
                    result.provider_reference,
                    result.verification_reference,
                    int(result.simulated),
                    None if result.error is None else result.error.code,
                    None if result.error is None else result.error.error_type,
                    self._clock().isoformat(),
                    external_action_id,
                    OutreachStatus.DISPATCHING.value,
                ),
            )
        if cursor.rowcount != 1:
            raise OutreachStoreError("external action is not in dispatching state")
        return cast(ExternalActionRecord, self.action(external_action_id))

    def mark_verified(
        self,
        external_action_id: str,
        verification_reference: str,
    ) -> ExternalActionRecord:
        current = self.action_required(external_action_id)
        reference = validate_text(verification_reference, "verification_reference")
        if current.status is OutreachStatus.VERIFIED:
            if current.verification_reference == reference:
                return current
            raise OutreachStoreError("verified action has conflicting verification evidence")
        if current.simulated or current.status is not OutreachStatus.DELIVERED:
            raise OutreachStoreError("only definitively delivered non-simulated actions can verify")
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE external_actions SET status=?,verification_reference=?,updated_at=? "
                "WHERE external_action_id=? AND status=?",
                (
                    OutreachStatus.VERIFIED.value,
                    reference,
                    self._clock().isoformat(),
                    external_action_id,
                    OutreachStatus.DELIVERED.value,
                ),
            )
        if cursor.rowcount != 1:
            raise OutreachStoreError("external action changed during verification")
        return self.action_required(external_action_id)

    def action(self, external_action_id: str) -> ExternalActionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM external_actions WHERE external_action_id=?",
            (external_action_id,),
        ).fetchone()
        return None if row is None else _action_from_row(row)

    def action_required(self, external_action_id: str) -> ExternalActionRecord:
        record = self.action(external_action_id)
        if record is None:
            raise OutreachStoreError("external action is not registered")
        return record


class DeterministicSimulatedChannel:
    """Explicit test adapter. It never represents simulated delivery as real verification."""

    adapter_id = "simulated.outbound"

    def __init__(self, status: OutreachStatus = OutreachStatus.ACCEPTED) -> None:
        if status not in {OutreachStatus.ACCEPTED, OutreachStatus.DELIVERED}:
            raise ValueError("simulated channel supports accepted or delivered outcomes")
        self.status = status
        self.calls: list[str] = []

    def dispatch(self, preparation: OutreachPreparation) -> ExternalDispatchResult:
        self.calls.append(preparation.external_action_id)
        return ExternalDispatchResult(
            self.status,
            provider_reference=f"simulated:{preparation.external_action_id}",
            delivery_confirmed=self.status is OutreachStatus.DELIVERED,
            verification_reference=None,
            simulated=True,
        )


class OutreachToolHandler:
    """Authorized Tool handler with durable idempotency and resource admission."""

    def __init__(
        self,
        store: SQLiteOutreachStore,
        adapters: Mapping[OutreachChannel, OutboundChannelAdapter],
        resources: SQLiteResourceManager,
        *,
        rate_resource_ids: Mapping[OutreachChannel, str] | None = None,
    ) -> None:
        self._store = store
        self._adapters = dict(adapters)
        self._resources = resources
        self._rate_resource_ids = dict(rate_resource_ids or {})

    def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolHandlerResult:
        preparation_id = str(arguments.get("preparation_id", ""))
        try:
            preparation = self._store.preparation(preparation_id)
        except OutreachStoreError:
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "preparation_missing",
                    "Outreach preparation is unavailable.",
                    "ValidationFailure",
                )
            )
        if dict(plain(preparation.execution_arguments)) != dict(plain(arguments)):
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "prepared_payload_mismatch",
                    "Dispatch payload differs from the immutable prepared message.",
                    "ApprovalActionMismatch",
                )
            )
        adapter = self._adapters.get(preparation.channel)
        if adapter is None:
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "channel_unavailable",
                    "No registered outbound channel adapter exists.",
                    "DependencyFailure",
                )
            )
        existing = self._store.action(preparation.external_action_id)
        if existing is not None:
            return _tool_result_for_existing(existing)

        reservation_id = f"external-action:{preparation.external_action_id}"
        admission = self._resources.reserve(
            reservation_id,
            context.instance_id,
            ResourceKind.TOOL_CALL,
            OUTREACH_TOOL_ID,
            WorkLane.BACKGROUND,
            context.task_id,
            preparation.workflow_id,
            queue_if_unavailable=True,
        )
        if admission.outcome is not AdmissionOutcome.ADMITTED:
            return ToolHandlerResult.failure(
                ErrorInfo(
                    "outreach_capacity_unavailable",
                    "Outbound capacity is queued or unavailable.",
                    "ResourceExhaustion",
                    retryable=True,
                )
            )
        try:
            rate_id = self._rate_resource_ids.get(preparation.channel)
            if rate_id is not None:
                rate = self._resources.check_rate_limit(preparation.external_action_id, rate_id)
                if not rate.allowed:
                    return ToolHandlerResult.failure(
                        ErrorInfo(
                            "outreach_rate_limited",
                            "Outbound provider rate capacity is unavailable.",
                            "ResourceExhaustion",
                            retryable=True,
                        )
                    )
            should_dispatch, existing = self._store.begin_dispatch(preparation)
            if not should_dispatch:
                return _tool_result_for_existing(existing)
            try:
                result = adapter.dispatch(preparation)
            except TimeoutError:
                result = ExternalDispatchResult(
                    OutreachStatus.UNCERTAIN,
                    error=ErrorInfo(
                        "dispatch_timeout_uncertain",
                        "Dispatch timed out after the external action may have started.",
                        "ExternalSideEffectUncertainty",
                    ),
                )
            except Exception as error:
                result = ExternalDispatchResult(
                    OutreachStatus.UNCERTAIN,
                    error=ErrorInfo(
                        "dispatch_boundary_uncertain",
                        f"Dispatch boundary raised {type(error).__name__}; outcome is unknown.",
                        "ExternalSideEffectUncertainty",
                    ),
                )
            record = self._store.finish_dispatch(preparation.external_action_id, result)
            return _tool_result_for_existing(record)
        finally:
            self._resources.release(reservation_id, context.instance_id)


def outreach_tool_definition() -> ToolDefinition:
    properties = {
        "external_action_id": {"type": "string"},
        "preparation_id": {"type": "string"},
        "lead_id": {"type": "string"},
        "lead_revision": {"type": "integer"},
        "qualification_verification_id": {"type": "string"},
        "recipient_id": {"type": "string"},
        "recipient": {"type": "string"},
        "channel": {"type": "string"},
        "subject": {"type": "string"},
        "message_body": {"type": "string"},
    }
    required = [name for name in properties if name != "subject"]
    return ToolDefinition(
        OUTREACH_TOOL_ID,
        "Bounded Outreach Sender",
        "1.0.0",
        "Dispatches one exact approved outreach payload through a registered adapter.",
        frozenset({SEND_OUTREACH_CAPABILITY}),
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "external_action_id": {"type": "string"},
                "status": {"type": "string"},
                "simulated": {"type": "boolean"},
            },
            "required": ["external_action_id", "status", "simulated"],
        },
        "freelancing.outreach-handler",
        risk_class=RiskClass.STRICT_AUTHORIZATION,
    )


@dataclass(frozen=True, slots=True)
class OutreachExecution:
    status: OutreachStatus
    external_action_id: str
    approval_id: str | None = None
    permission_decision_id: str | None = None
    provider_reference: str | None = None
    simulated: bool = False
    error: ErrorInfo | None = None


class OutreachService:
    """Prepare and dispatch through ToolExecutor; it owns no permission or approval state."""

    def __init__(
        self,
        store: SQLiteOutreachStore,
        tool_invoker: ToolInvoker,
        publisher: EventPublisher,
        *,
        observer: Observer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._tools = tool_invoker
        self._publisher = publisher
        self._observer = observer
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def prepare(self, preparation: OutreachPreparation) -> bool:
        created = self._store.save_preparation(preparation)
        if created:
            self._emit(preparation, "OUTREACH_PREPARED", OutreachStatus.PREPARED)
        return created

    def dispatch(
        self,
        preparation_id: str,
        *,
        requester_id: str,
        instance_id: str,
        approval_id: str | None = None,
    ) -> OutreachExecution:
        preparation = self._store.preparation(preparation_id)
        result = self._tools.execute(
            ToolCall(
                tool_id=OUTREACH_TOOL_ID,
                arguments=preparation.execution_arguments,
                request_id=preparation.request_id,
                task_id=preparation.task_id,
                workflow_id=preparation.workflow_id,
                agent_id=preparation.agent_id,
                instance_id=instance_id,
                correlation_id=preparation.correlation_id,
                requester_id=requester_id,
                capability=SEND_OUTREACH_CAPABILITY,
                target=f"{preparation.channel.value}:{preparation.recipient}",
                purpose=preparation.objective,
                expected_effect="Send the exact approved message to the named recipient.",
                approval_id=approval_id,
                conditions={"lead_id": preparation.lead_id},
                approval_context=preparation.approval_display,
            )
        )
        status = _outreach_status_for_tool_result(result.status)
        record = self._store.action(preparation.external_action_id)
        if result.status is ToolResultStatus.SUCCESS and record is not None:
            status = record.status
        execution = OutreachExecution(
            status,
            preparation.external_action_id,
            result.approval_id,
            result.permission_decision_id,
            None if record is None else record.provider_reference,
            False if record is None else record.simulated,
            result.error,
        )
        event_type = {
            OutreachStatus.APPROVAL_REQUIRED: "OUTREACH_APPROVAL_REQUIRED",
            OutreachStatus.REJECTED: "OUTREACH_REJECTED",
            OutreachStatus.ACCEPTED: "OUTREACH_ACCEPTED",
            OutreachStatus.DELIVERED: "OUTREACH_DELIVERED",
            OutreachStatus.UNCERTAIN: "OUTREACH_UNCERTAIN",
            OutreachStatus.FAILED: "OUTREACH_FAILED",
        }.get(status, "OUTREACH_DISPATCHED")
        self._emit(preparation, event_type, status, approval_id=result.approval_id)
        return execution

    def verify_delivery(
        self,
        external_action_id: str,
        *,
        verification_reference: str | None,
    ) -> OutreachExecution:
        record = self._store.action_required(external_action_id)
        preparation = self._store.preparation(record.preparation_id)
        if (
            record.status in {OutreachStatus.DELIVERED, OutreachStatus.VERIFIED}
            and not record.simulated
            and verification_reference is not None
        ):
            was_verified = record.status is OutreachStatus.VERIFIED
            record = self._store.mark_verified(external_action_id, verification_reference)
            status = OutreachStatus.VERIFIED
            if not was_verified:
                self._emit(
                    preparation,
                    "OUTREACH_VERIFIED",
                    status,
                    verification_id=record.verification_reference,
                )
        elif record.status in {OutreachStatus.ACCEPTED, OutreachStatus.DELIVERED}:
            status = OutreachStatus.SUCCEEDED_BUT_UNVERIFIED
        else:
            status = record.status
        return OutreachExecution(
            status,
            external_action_id,
            provider_reference=record.provider_reference,
            simulated=record.simulated,
        )

    def _emit(
        self,
        preparation: OutreachPreparation,
        event_type: str,
        status: OutreachStatus,
        *,
        approval_id: str | None = None,
        verification_id: str | None = None,
    ) -> None:
        event = Event(
            self._id_factory(),
            preparation.request_id,
            preparation.task_id,
            preparation.correlation_id,
            event_type,
            "freelancing.outreach",
            {
                "preparation_id": preparation.preparation_id,
                "external_action_id": preparation.external_action_id,
                "lead_id": preparation.lead_id,
                "channel": preparation.channel.value,
                "status": status.value,
                "approval_id": approval_id,
            },
            self._clock(),
            "1.0",
            workflow_id=preparation.workflow_id,
            delivery=EventDelivery(True, True, preparation.external_action_id, RetryPolicy(3)),
        )
        with suppress(Exception):
            self._publisher.publish(
                event,
                idempotency_key=f"{event_type}:{preparation.external_action_id}:{status.value}",
            )
        if self._observer is not None:
            with suppress(Exception):
                self._observer.record(
                    event_type,
                    "freelancing.outreach",
                    "dispatch" if status is not OutreachStatus.PREPARED else "prepare",
                    _trace_status(status),
                    TraceContext(
                        preparation.request_id,
                        preparation.task_id,
                        preparation.correlation_id,
                        preparation.workflow_id,
                        preparation.agent_id,
                    ),
                    event_id=event.event_id,
                    approval_id=approval_id,
                    verification_id=verification_id,
                    tool_id=OUTREACH_TOOL_ID,
                    metadata={
                        "external_action_id": preparation.external_action_id,
                        "lead_id": preparation.lead_id,
                        "status": status.value,
                    },
                )


class OutreachRecoveryBridge:
    """Translate durable uncertainty into Phase 8 Recovery without executing a retry."""

    def __init__(
        self,
        policy: RecoveryPolicy,
        *,
        store: SQLiteRecoveryStore | None = None,
        observer: Observer | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._policy = policy
        self._store = store
        self._observer = observer
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def decide(
        self,
        preparation: OutreachPreparation,
        action: ExternalActionRecord,
        *,
        reconciliation_available: bool,
    ) -> RecoveryDecision:
        if action.status is not OutreachStatus.UNCERTAIN:
            raise ValueError("only uncertain external actions enter uncertainty recovery")
        identity = FailureIdentity(
            preparation.request_id,
            preparation.task_id,
            preparation.correlation_id,
            workflow_id=preparation.workflow_id,
            agent_id=preparation.agent_id,
            tool_id=OUTREACH_TOOL_ID,
            component_id=preparation.external_action_id,
        )
        operation = RecoverableOperation(
            preparation.external_action_id,
            identity,
            RecoverableOperationStatus.UNCERTAIN,
            action.attempts,
            3,
            True,
            SideEffectState.UNCERTAIN,
            1,
            action.updated_at,
        )
        if self._store is not None:
            existing_operation = self._store.operation(preparation.external_action_id)
            if existing_operation is not None and existing_operation != operation:
                raise ValueError("external action conflicts with durable recovery operation")
            existing_decision = self._store.decision_for(preparation.external_action_id, 1)
            if existing_decision is not None:
                return existing_decision
        failure = FailureRecord(
            self._id_factory(),
            FailureClass.EXTERNAL_SIDE_EFFECT_UNCERTAIN,
            identity,
            ErrorInfo(
                action.error_code or "outreach_outcome_uncertain",
                "External outreach may have occurred; blind retry is forbidden.",
                action.error_type or "ExternalSideEffectUncertainty",
            ),
            self._clock(),
            False,
            SideEffectState.UNCERTAIN,
        )
        decision = self._policy.decide(
            RecoveryRequest(
                self._id_factory(),
                preparation.external_action_id,
                failure,
                action.attempts,
                3,
                True,
                True,
                False,
                False,
                reconciliation_available,
                OutreachStatus.UNCERTAIN.value,
            )
        )
        if self._store is not None:
            if self._store.operation(preparation.external_action_id) is None:
                self._store.register_operation(operation)
            self._store.record_failure(preparation.external_action_id, failure)
            decision = self._store.record_decision(decision)
        if self._observer is not None:
            with suppress(Exception):
                self._observer.record(
                    "OUTREACH_RECOVERY_DECIDED",
                    "freelancing.outreach-recovery",
                    "decide",
                    TraceStatus.UNKNOWN,
                    TraceContext(
                        preparation.request_id,
                        preparation.task_id,
                        preparation.correlation_id,
                        preparation.workflow_id,
                        preparation.agent_id,
                    ),
                    tool_id=OUTREACH_TOOL_ID,
                    recovery_id=decision.recovery_id,
                    attempt=action.attempts,
                    error_classification=failure.classification.value,
                    metadata={
                        "external_action_id": preparation.external_action_id,
                        "action": decision.action.value,
                        "requires_reconciliation": decision.requires_reconciliation,
                    },
                )
        return decision


def _tool_result_for_existing(record: ExternalActionRecord) -> ToolHandlerResult:
    output = {
        "external_action_id": record.external_action_id,
        "status": record.status.value,
        "simulated": record.simulated,
        "provider_reference": record.provider_reference,
    }
    if record.status in {
        OutreachStatus.ACCEPTED,
        OutreachStatus.DELIVERED,
        OutreachStatus.VERIFIED,
    }:
        return ToolHandlerResult.success(output)
    error = ErrorInfo(
        record.error_code or "external_action_not_successful",
        "External action did not produce a verified successful outcome.",
        record.error_type or "ExternalActionFailure",
    )
    if record.status in {
        OutreachStatus.DISPATCHING,
        OutreachStatus.UNCERTAIN,
        OutreachStatus.TIMED_OUT,
    }:
        return ToolHandlerResult.unknown_outcome(error, output)
    return ToolHandlerResult.failure(error)


def _outreach_status_for_tool_result(status: ToolResultStatus) -> OutreachStatus:
    if status is ToolResultStatus.APPROVAL_PENDING:
        return OutreachStatus.APPROVAL_REQUIRED
    if status in {
        ToolResultStatus.APPROVAL_DENIED,
        ToolResultStatus.APPROVAL_EXPIRED,
        ToolResultStatus.APPROVAL_INVALID,
        ToolResultStatus.PERMISSION_DENIED,
    }:
        return OutreachStatus.REJECTED
    if status is ToolResultStatus.UNKNOWN:
        return OutreachStatus.UNCERTAIN
    if status is not ToolResultStatus.SUCCESS:
        return OutreachStatus.FAILED
    return OutreachStatus.DISPATCHED


def _trace_status(status: OutreachStatus) -> TraceStatus:
    if status in {OutreachStatus.FAILED, OutreachStatus.REJECTED, OutreachStatus.TIMED_OUT}:
        return TraceStatus.FAILED
    if status is OutreachStatus.UNCERTAIN:
        return TraceStatus.UNKNOWN
    if status is OutreachStatus.APPROVAL_REQUIRED:
        return TraceStatus.WAITING
    if status in {OutreachStatus.PREPARED, OutreachStatus.DISPATCHING, OutreachStatus.DISPATCHED}:
        return TraceStatus.STARTED
    return TraceStatus.SUCCEEDED


def _preparation_document(item: OutreachPreparation) -> dict[str, Any]:
    return {
        "preparation_id": item.preparation_id,
        "external_action_id": item.external_action_id,
        "lead_id": item.lead_id,
        "lead_revision": item.lead_revision,
        "qualification_verification_id": item.qualification_verification_id,
        "recipient_id": item.recipient_id,
        "recipient": item.recipient,
        "objective": item.objective,
        "channel": item.channel.value,
        "message_body": item.message_body,
        "request_id": item.request_id,
        "task_id": item.task_id,
        "correlation_id": item.correlation_id,
        "workflow_id": item.workflow_id,
        "agent_id": item.agent_id,
        "policy_version": item.policy_version,
        "verification_method": item.verification_method,
        "personalization_evidence": list(item.personalization_evidence),
        "source_references": list(item.source_references),
        "subject": item.subject,
        "model_id": item.model_id,
        "provider_id": item.provider_id,
        "requested_action": item.requested_action,
        "created_at": item.created_at.isoformat(),
    }


def _preparation_from_document(document: Mapping[str, Any]) -> OutreachPreparation:
    return OutreachPreparation(
        preparation_id=document["preparation_id"],
        external_action_id=document["external_action_id"],
        lead_id=document["lead_id"],
        lead_revision=int(document["lead_revision"]),
        qualification_verification_id=document["qualification_verification_id"],
        recipient_id=document["recipient_id"],
        recipient=document["recipient"],
        objective=document["objective"],
        channel=OutreachChannel(document["channel"]),
        message_body=document["message_body"],
        request_id=document["request_id"],
        task_id=document["task_id"],
        correlation_id=document["correlation_id"],
        workflow_id=document["workflow_id"],
        agent_id=document["agent_id"],
        policy_version=document["policy_version"],
        verification_method=document["verification_method"],
        personalization_evidence=tuple(document["personalization_evidence"]),
        source_references=tuple(document["source_references"]),
        subject=document["subject"],
        model_id=document["model_id"],
        provider_id=document["provider_id"],
        requested_action=document["requested_action"],
        created_at=datetime.fromisoformat(document["created_at"]),
    )


def _action_from_row(row: sqlite3.Row) -> ExternalActionRecord:
    return ExternalActionRecord(
        row["external_action_id"],
        row["preparation_id"],
        row["payload_digest"],
        OutreachStatus(row["status"]),
        int(row["attempts"]),
        row["provider_reference"],
        row["verification_reference"],
        bool(row["simulated"]),
        datetime.fromisoformat(row["updated_at"]),
        row["error_code"],
        row["error_type"],
    )


__all__ = [
    "OUTREACH_TOOL_ID",
    "SEND_OUTREACH_CAPABILITY",
    "DeterministicSimulatedChannel",
    "ExternalActionRecord",
    "ExternalDispatchResult",
    "OutboundChannelAdapter",
    "OutreachChannel",
    "OutreachExecution",
    "OutreachPreparation",
    "OutreachRecoveryBridge",
    "OutreachService",
    "OutreachStatus",
    "OutreachStoreError",
    "OutreachToolHandler",
    "SQLiteOutreachStore",
    "outreach_tool_definition",
]
