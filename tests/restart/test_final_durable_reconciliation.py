from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_freelancing_operations import (
    NOW,
    deliverable_evidence,
    project_setup,
    qa_evidence,
    reply,
)
from zyro.core.executive import ExecutiveOutcome
from zyro.domains.freelancing.delivery import (
    Deliverable,
    DeliveryTaskRecord,
    HandoffCompletion,
    HandoffService,
    OpportunityService,
    ProjectStatus,
    QACriterion,
    QAResult,
    QAService,
    SQLiteProjectStore,
)
from zyro.domains.freelancing.replies import (
    DeterministicReplyProcessor,
    SQLiteReplyStore,
)


def _complete_project(store: SQLiteProjectStore, project_id: str) -> tuple[QAResult, QACriterion]:
    project = store.get(project_id)
    active = store.transition(project_id, project.revision, ProjectStatus.PROJECT_ACTIVE)
    delivery = store.transition(project_id, active.revision, ProjectStatus.DELIVERY)
    qa_state = store.transition(project_id, delivery.revision, ProjectStatus.QA)
    store.verify_deliverable(
        project_id, qa_state.revision, "deliverable-1", deliverable_evidence(store, project)
    )
    task = DeliveryTaskRecord(
        project_id,
        project.workflow_id,
        "request-delivery",
        "task-delivery",
        project.correlation_id,
        "agent-delivery",
        "instance-delivery",
        1,
        ExecutiveOutcome.VERIFIED_SUCCESS,
        "verification-delivery",
        NOW,
    )
    assert store.save_delivery_task(task)
    criterion = QACriterion("criterion-1", "Verified output matches scope", True, ("artifact-1",))
    qa = QAService(store, clock=lambda: NOW, id_factory=lambda: "qa-1").evaluate(
        project_id,
        "task-qa",
        (criterion,),
        qa_evidence(store, project, "task-qa", (criterion,)),
    )
    return qa, criterion


def test_reply_redelivery_after_restart_reuses_the_same_pending_project(tmp_path: Path) -> None:
    reply_store = SQLiteReplyStore(tmp_path / "reply-redelivery.sqlite")
    incoming = reply()
    reply_store.ingest(incoming)
    processing = DeterministicReplyProcessor(
        reply_store,
        policy_version="reply-v1",
        clock=lambda: NOW,
        id_factory=lambda: "processing-redelivery",
    ).process(incoming.reply_id, processing_task_id="processing-task")
    project_path = tmp_path / "project-redelivery.sqlite"
    store = SQLiteProjectStore(project_path, clock=lambda: NOW)
    deliverables = (Deliverable("deliverable-r", "Output", "verified"),)
    first = OpportunityService(
        store, clock=lambda: NOW, id_factory=lambda: "project-original"
    ).create_pending(
        incoming,
        processing,
        scope={"summary": "Bounded redelivery project"},
        owner_agent_id="freelancing.project-management",
        deliverables=deliverables,
        verification_requirements=("canonical verification",),
        workflow_id="workflow-redelivery",
    )
    store.close()

    reopened = SQLiteProjectStore(project_path, clock=lambda: NOW)
    duplicate = OpportunityService(
        reopened, clock=lambda: NOW, id_factory=lambda: "project-must-not-be-created"
    ).create_pending(
        incoming,
        processing,
        scope={"summary": "Bounded redelivery project"},
        owner_agent_id="freelancing.project-management",
        deliverables=deliverables,
        verification_requirements=("canonical verification",),
        workflow_id="workflow-redelivery",
    )

    assert duplicate == first
    assert duplicate.project_id == "project-original"
    assert duplicate.status is ProjectStatus.PROJECT_PENDING
    reopened.close()
    reply_store.close()


def test_qa_and_handoff_restart_are_idempotent_and_terminal_state_does_not_revive(
    tmp_path: Path,
) -> None:
    store, project = project_setup(tmp_path)
    qa, criterion = _complete_project(store, project.project_id)
    handoff = HandoffService(store, clock=lambda: NOW, id_factory=lambda: "handoff-1").create(
        project.project_id
    )
    assert handoff.completion is HandoffCompletion.VERIFIED_COMPLETE
    authority = store._verification_authority
    store.close()

    reopened = SQLiteProjectStore(
        tmp_path / "projects.sqlite",
        verification_authority=authority,
        clock=lambda: NOW,
    )
    repeated_qa = QAService(
        reopened, clock=lambda: NOW, id_factory=lambda: "different-qa-id"
    ).evaluate(
        project.project_id,
        "task-qa",
        (criterion,),
        qa_evidence(reopened, project, "task-qa", (criterion,)),
    )
    repeated_handoff = HandoffService(
        reopened, clock=lambda: NOW, id_factory=lambda: "different-handoff-id"
    ).create(project.project_id)

    assert repeated_qa == qa
    assert repeated_handoff == handoff
    assert reopened.get(project.project_id).status is ProjectStatus.COMPLETED
    with pytest.raises(ValueError, match="invalid project transition"):
        current = reopened.get(project.project_id)
        reopened.transition(project.project_id, current.revision, ProjectStatus.DELIVERY)
    reopened.close()


def test_conflicting_duplicate_qa_delivery_and_handoff_records_fail_closed(
    tmp_path: Path,
) -> None:
    store, project = project_setup(tmp_path)
    qa, criterion = _complete_project(store, project.project_id)

    with pytest.raises(ValueError, match="materially different QA"):
        QAService(store, clock=lambda: NOW, id_factory=lambda: "qa-2").evaluate(
            project.project_id,
            "different-task",
            (criterion,),
            qa_evidence(store, project, "different-task", (criterion,)),
        )

    original_task = store.delivery_task("task-delivery")
    assert original_task is not None
    with pytest.raises(ValueError, match="delivery task identity conflict"):
        store.save_delivery_task(
            DeliveryTaskRecord(
                original_task.project_id,
                original_task.workflow_id,
                original_task.request_id,
                original_task.task_id,
                original_task.correlation_id,
                original_task.agent_id,
                original_task.instance_id,
                2,
                ExecutiveOutcome.FAILED,
                None,
                NOW,
            )
        )

    handoff_service = HandoffService(store, clock=lambda: NOW, id_factory=lambda: "handoff-1")
    handoff = handoff_service.create(project.project_id)
    assert handoff.qa_id == qa.qa_id
    with pytest.raises(ValueError, match="different outstanding issues"):
        handoff_service.create(project.project_id, ("late conflicting issue",))
    store.close()
