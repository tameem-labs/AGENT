from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from tests.integration.test_freelancing_delivery_runtime import DeliveryHandler
from tests.integration.test_freelancing_pipeline import pipeline
from tests.unit.test_freelancing_evaluation import found_lead
from tests.unit.test_freelancing_outreach import DeliveredAdapter, preparation, system
from zyro.agents.definition import AgentDefinition
from zyro.agents.registry import AgentRegistry
from zyro.core.events import InProcessEventPublisher
from zyro.core.executive import ExecutiveOutcome, UserRequest, ZyroExecutive
from zyro.core.risk import RiskClass
from zyro.domains.freelancing.contracts import PipelineOutcome
from zyro.domains.freelancing.delivery import (
    Deliverable,
    DeliveryCoordinator,
    HandoffCompletion,
    HandoffService,
    OpportunityService,
    ProjectStatus,
    QACriterion,
    QAService,
    SQLiteProjectStore,
)
from zyro.domains.freelancing.outreach import OutreachStatus
from zyro.domains.freelancing.replies import (
    ClientReply,
    ClientReplyIntake,
    DeterministicReplyProcessor,
    SQLiteReplyStore,
)
from zyro.execution.verification import StructuralRuntimeVerifier
from zyro.observability import OperationalObserver, SQLiteObservabilityStore, TraceQuery
from zyro.resources import (
    ReservationStatus,
    ResourcePolicy,
    SQLiteResourceManager,
)
from zyro.runtime.agent_runtime import AgentRuntime

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_qualified_lead_to_approved_outreach_reply_delivery_qa_handoff(
    tmp_path: Path,
) -> None:
    qualification, _, _ = pipeline()
    qualified = qualification.process(found_lead(), owner="owner-1")
    assert qualified.outcome is PipelineOutcome.LEAD_QUALIFIED

    trace_store = SQLiteObservabilityStore(tmp_path / "e2e-traces.sqlite")
    trace_ids = count(1)
    observer = OperationalObserver(
        trace_store,
        clock=lambda: NOW,
        id_factory=lambda: f"e2e-trace-{next(trace_ids)}",
    )
    outreach, approvals, _, outreach_store, outreach_resources, adapter, outreach_events = system(
        tmp_path, adapter=DeliveredAdapter()
    )
    outreach._observer = observer
    prepared = replace(
        preparation(),
        lead_id=qualified.lead.lead_id,
        lead_revision=qualified.lead.revision,
        qualification_verification_id=qualified.lead.provenance[-1].verification_id,
    )
    outreach.prepare(prepared)
    pending = outreach.dispatch(
        prepared.preparation_id,
        requester_id="owner-1",
        instance_id="outreach-instance",
    )
    assert pending.status is OutreachStatus.APPROVAL_REQUIRED
    approvals.approve(pending.approval_id or "", "human-1", "Reviewed exact final message.")
    dispatched = outreach.dispatch(
        prepared.preparation_id,
        requester_id="owner-1",
        instance_id="outreach-instance",
        approval_id=pending.approval_id,
    )
    assert dispatched.status is OutreachStatus.DELIVERED
    assert not dispatched.simulated
    verified_outreach = outreach.verify_delivery(
        prepared.external_action_id,
        verification_reference="independent-outreach-evidence",
    )
    assert verified_outreach.status is OutreachStatus.VERIFIED
    assert adapter.calls == [prepared.external_action_id]

    reply_store = SQLiteReplyStore(tmp_path / "e2e-replies.sqlite")
    reply_publisher = InProcessEventPublisher()
    inbound = ClientReply(
        "reply-e2e",
        "external-reply-e2e",
        qualified.lead.lead_id,
        "client-1",
        "thread-1",
        "EMAIL",
        NOW,
        "I am interested in starting this project.",
        "request-reply",
        "task-reply",
        prepared.correlation_id,
        prepared.workflow_id,
        "Project",
        {"provider": "fixture"},
    )
    assert ClientReplyIntake(
        reply_store,
        reply_publisher,
        observer=observer,
        clock=lambda: NOW,
        id_factory=lambda: "reply-event",
    ).ingest(inbound)
    processed = DeterministicReplyProcessor(
        reply_store,
        policy_version="reply-v1",
        observer=observer,
        clock=lambda: NOW,
        id_factory=lambda: "processing-e2e",
    ).process(inbound.reply_id, processing_task_id="task-processing")
    assert processed.creates_potential_project

    project_store = SQLiteProjectStore(tmp_path / "e2e-project.sqlite", clock=lambda: NOW)
    project = OpportunityService(
        project_store,
        observer=observer,
        clock=lambda: NOW,
        id_factory=lambda: "project-e2e",
    ).create_pending(
        inbound,
        processed,
        scope={"summary": "Produce one bounded reviewed deliverable"},
        owner_agent_id="freelancing.project-management",
        deliverables=(
            Deliverable("deliverable-e2e", "Implementation", "verified Task and passing QA"),
        ),
        verification_requirements=("structural verification",),
        workflow_id="workflow-e2e",
    )
    active = project_store.transition(
        project.project_id, project.revision, ProjectStatus.PROJECT_ACTIVE
    )

    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            "freelancing.project-management",
            "Project Management Agent",
            "1.0.0",
            "Execute bounded delivery",
            "freelancing",
            ("produce deliverable",),
            ("freelancing.delivery.execute",),
            risk_class=RiskClass.AUTOMATIC,
        ),
        DeliveryHandler(),
    )
    ids = count(1)
    executive = ZyroExecutive(
        AgentRuntime(registry, instance_id_factory=lambda: "delivery-instance"),
        StructuralRuntimeVerifier(),
        id_factory=lambda: f"delivery-task-{next(ids)}",
    )
    delivery_resources = SQLiteResourceManager(
        tmp_path / "e2e-delivery-resources.sqlite",
        ResourcePolicy(max_concurrent_tasks=1, max_concurrent_agents=1),
        clock=lambda: NOW,
    )
    delivery_events = InProcessEventPublisher()
    delivery = DeliveryCoordinator(
        project_store,
        delivery_resources,
        executive,
        delivery_events,
        observer=observer,
        clock=lambda: NOW,
        id_factory=lambda: "delivery-event",
    )
    run = delivery.run_task(
        project.project_id,
        UserRequest(
            "Produce the agreed bounded deliverable",
            "owner-1",
            "freelancing.project-management",
            request_id="request-delivery",
            correlation_id=prepared.correlation_id,
        ),
        workflow_id="workflow-e2e",
    )
    assert run.result is not None and run.result.outcome is ExecutiveOutcome.VERIFIED_SUCCESS

    delivering = project_store.transition(
        project.project_id, active.revision, ProjectStatus.DELIVERY
    )
    verified = project_store.verify_deliverable(
        project.project_id,
        delivering.revision,
        "deliverable-e2e",
        "artifact-e2e",
    )
    project_store.transition(project.project_id, verified.revision, ProjectStatus.QA)
    qa = QAService(
        project_store,
        observer=observer,
        clock=lambda: NOW,
        id_factory=lambda: "qa-e2e",
    ).evaluate(
        project.project_id,
        "task-qa",
        (QACriterion("scope", "Deliverable matches scope", True, ("artifact-e2e",)),),
        (run.result.verification.verification_id or "",),
    )
    assert qa.outcome.value == "PASS"
    handoff = HandoffService(
        project_store,
        observer=observer,
        clock=lambda: NOW,
        id_factory=lambda: "handoff-e2e",
    ).create(project.project_id)

    assert handoff.completion is HandoffCompletion.VERIFIED_COMPLETE
    assert project_store.get(project.project_id).status is ProjectStatus.COMPLETED
    assert [event.event_type for event in outreach_events.events()] == [
        "OUTREACH_PREPARED",
        "OUTREACH_APPROVAL_REQUIRED",
        "OUTREACH_DELIVERED",
        "OUTREACH_VERIFIED",
    ]
    assert [event.event_type for event in reply_publisher.events()] == ["CLIENT_REPLY_RECEIVED"]
    assert [event.event_type for event in delivery_events.events()] == ["DELIVERY_TASK_RECORDED"]
    assert (
        outreach_resources.reservation(f"external-action:{prepared.external_action_id}").status
        is ReservationStatus.RELEASED
    )
    delivery_admission_id = f"delivery:{project.project_id}:request-delivery"
    assert (
        delivery_resources.reservation(f"task-slot:{delivery_admission_id}").status
        is ReservationStatus.RELEASED
    )
    assert (
        delivery_resources.reservation(f"agent-slot:{delivery_admission_id}").status
        is ReservationStatus.RELEASED
    )
    traces = trace_store.query(TraceQuery(correlation_id=prepared.correlation_id))
    trace_types = {trace.event_type for trace in traces}
    assert trace_types >= {
        "OUTREACH_PREPARED",
        "OUTREACH_APPROVAL_REQUIRED",
        "OUTREACH_DELIVERED",
        "OUTREACH_VERIFIED",
        "CLIENT_REPLY_RECEIVED",
        "CLIENT_REPLY_PROCESSED",
        "PROJECT_PENDING",
        "DELIVERY_TASK_RECORDED",
        "QA_COMPLETED",
        "HANDOFF_RECORDED",
    }
    assert any(trace.approval_id == pending.approval_id for trace in traces)
    assert any(trace.verification_id == "independent-outreach-evidence" for trace in traces)
    assert any(trace.verification_id == run.result.verification.verification_id for trace in traces)
    outreach_store.close()
    outreach_resources.close()
    reply_store.close()
    project_store.close()
    delivery_resources.close()
    trace_store.close()
