from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.events import InProcessEventPublisher
from zyro.core.executive import ExecutiveOutcome, UserRequest, ZyroExecutive
from zyro.core.risk import RiskClass
from zyro.domains.freelancing.delivery import (
    Deliverable,
    DeliveryCoordinator,
    ProjectRecord,
    ProjectStatus,
    SQLiteProjectStore,
)
from zyro.execution.verification import StructuralRuntimeVerifier
from zyro.observability import OperationalObserver, SQLiteObservabilityStore, TraceQuery
from zyro.resources import ResourceKind, ResourcePolicy, SQLiteResourceManager, WorkLane
from zyro.runtime.agent_runtime import AgentRuntime

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


class DeliveryHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        return AgentExecution.success(
            {
                "deliverable_id": "deliverable-1",
                "reference": f"artifact:{context.task_id}",
            }
        )


def project() -> ProjectRecord:
    return ProjectRecord(
        "project-1",
        "lead-1",
        "client-1",
        "reply-1",
        "workflow-1",
        "request-project",
        "task-project",
        "correlation-project",
        {"summary": "Bounded implementation"},
        "freelancing.project-management",
        ("structural runtime verification",),
        (Deliverable("deliverable-1", "Implementation", "verified delivery Task"),),
        status=ProjectStatus.PROJECT_PENDING,
        created_at=NOW,
        updated_at=NOW,
    )


def coordinator(
    tmp_path: Path,
) -> tuple[
    DeliveryCoordinator,
    SQLiteProjectStore,
    SQLiteResourceManager,
    SQLiteObservabilityStore,
    InProcessEventPublisher,
]:
    trace_store = SQLiteObservabilityStore(tmp_path / "trace.sqlite")
    traces = count(1)
    observer = OperationalObserver(
        trace_store,
        clock=lambda: NOW,
        id_factory=lambda: f"trace-{next(traces)}",
    )
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            "freelancing.project-management",
            "Project Management Agent",
            "1.0.0",
            "Perform one bounded delivery task",
            "freelancing",
            ("produce one deliverable",),
            ("freelancing.delivery.execute",),
            risk_class=RiskClass.AUTOMATIC,
            verification_requirements=("independent structural verification",),
        ),
        DeliveryHandler(),
    )
    task_ids = count(1)
    executive = ZyroExecutive(
        AgentRuntime(
            registry,
            instance_id_factory=lambda: "instance-delivery",
            observer=observer,
        ),
        StructuralRuntimeVerifier(),
        id_factory=lambda: f"task-{next(task_ids)}",
        observer=observer,
    )
    store = SQLiteProjectStore(tmp_path / "project.sqlite", clock=lambda: NOW)
    item = project()
    store.create(item)
    store.transition(item.project_id, item.revision, ProjectStatus.PROJECT_ACTIVE)
    resources = SQLiteResourceManager(
        tmp_path / "resources.sqlite",
        ResourcePolicy(max_concurrent_tasks=1, max_concurrent_agents=1),
        clock=lambda: NOW,
    )
    publisher = InProcessEventPublisher()
    events = count(1)
    delivery = DeliveryCoordinator(
        store,
        resources,
        executive,
        publisher,
        observer=observer,
        clock=lambda: NOW,
        id_factory=lambda: f"delivery-event-{next(events)}",
    )
    return delivery, store, resources, trace_store, publisher


def test_delivery_uses_canonical_task_runtime_resources_verification_and_trace(
    tmp_path: Path,
) -> None:
    delivery, store, resources, traces, publisher = coordinator(tmp_path)

    run = delivery.run_task(
        "project-1",
        UserRequest(
            "Produce the bounded implementation deliverable",
            "owner-1",
            "freelancing.project-management",
            request_id="request-delivery",
            correlation_id="correlation-delivery",
        ),
        workflow_id="workflow-1",
    )

    assert run.admitted and run.result is not None
    assert run.result.outcome is ExecutiveOutcome.VERIFIED_SUCCESS
    records = store.delivery_tasks("project-1")
    assert len(records) == 1 and records[0].verified
    assert records[0].verification_id is not None
    assert publisher.events("DELIVERY_TASK_RECORDED")[0].payload["verified"] is True
    correlated = traces.query(TraceQuery(correlation_id="correlation-delivery"))
    assert any(item.event_type == "TASK_COMPLETED" for item in correlated)
    assert any(item.event_type == "DELIVERY_TASK_RECORDED" for item in correlated)
    store.close()
    resources.close()
    traces.close()


def test_resource_queue_blocks_delivery_without_bypassing_or_creating_task(tmp_path: Path) -> None:
    delivery, store, resources, traces, publisher = coordinator(tmp_path)
    blocker = resources.reserve(
        "blocker",
        "other-owner",
        ResourceKind.TASK_SLOT,
        "tasks",
        WorkLane.BACKGROUND,
        "other-task",
    )
    assert blocker.outcome.value == "ADMITTED"

    run = delivery.run_task(
        "project-1",
        UserRequest(
            "Must wait for capacity",
            "owner-1",
            "freelancing.project-management",
            request_id="request-blocked",
            correlation_id="correlation-blocked",
        ),
        workflow_id="workflow-1",
    )

    assert not run.admitted and run.result is None
    assert store.delivery_tasks("project-1") == ()
    assert publisher.events("DELIVERY_TASK_RECORDED") == ()
    store.close()
    resources.close()
    traces.close()
