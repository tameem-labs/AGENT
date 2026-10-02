"""Bounded synchronous workflow runner with explicit control transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from zyro.workflows.contracts import StepStatus, WorkflowSnapshot, WorkflowStatus, WorkflowStep
from zyro.workflows.store import WorkflowStore


@dataclass(frozen=True, slots=True)
class StepExecution:
    succeeded: bool
    result: Any | None = None
    error_code: str | None = None
    waiting_for_approval: bool = False
    retryable: bool = False


class StepRunner(Protocol):
    def __call__(self, workflow: WorkflowSnapshot, step: WorkflowStep) -> StepExecution: ...


class WorkflowEngine:
    """Small local runner; it does not infer authority from plans or events."""

    def __init__(self, store: WorkflowStore, runners: dict[str, StepRunner] | None = None) -> None:
        self._store = store
        self._runners = dict(runners or {})

    def register(self, capability: str, runner: StepRunner) -> None:
        if capability in self._runners:
            raise ValueError(f"workflow capability already registered: {capability}")
        self._runners[capability] = runner

    def run(self, workflow_id: str) -> WorkflowSnapshot:
        workflow = self._store.get(workflow_id)
        if workflow.status in {WorkflowStatus.CANCELLED, WorkflowStatus.COMPLETED}:
            return workflow
        if workflow.status is WorkflowStatus.PAUSED:
            return workflow
        workflow = self._store.transition(
            workflow_id,
            workflow.revision,
            WorkflowStatus.RUNNING,
            event_type="WORKFLOW_STARTED",
        )
        while True:
            progressed = False
            by_id = {step.step_id: step for step in workflow.definition.steps}
            for step_id, status in workflow.step_statuses.items():
                if status in {StepStatus.COMPLETED, StepStatus.CANCELLED}:
                    continue
                step = by_id[step_id]
                dependencies = [workflow.step_statuses[item] for item in step.dependencies]
                if any(item in {StepStatus.FAILED, StepStatus.CANCELLED} for item in dependencies):
                    workflow = self._store.update_step(
                        workflow_id, step_id, StepStatus.CANCELLED, error_code="dependency_failed"
                    )
                    progressed = True
                    continue
                if not all(item is StepStatus.COMPLETED for item in dependencies):
                    continue
                if step.requires_approval and status is not StepStatus.WAITING_FOR_APPROVAL:
                    workflow = self._store.update_step(
                        workflow_id, step_id, StepStatus.WAITING_FOR_APPROVAL
                    )
                    return self._store.transition(
                        workflow_id,
                        workflow.revision,
                        WorkflowStatus.WAITING_FOR_APPROVAL,
                        waiting_reason=f"step {step_id} requires approval",
                        event_type="WORKFLOW_APPROVAL_REQUIRED",
                    )
                runner = self._runners.get(step.capability)
                if runner is None:
                    workflow = self._store.update_step(
                        workflow_id,
                        step_id,
                        StepStatus.FAILED,
                        error_code="workflow_capability_unavailable",
                    )
                    return self._store.transition(
                        workflow_id,
                        workflow.revision,
                        WorkflowStatus.FAILED,
                        error_code="workflow_capability_unavailable",
                    )
                workflow = self._store.update_step(
                    workflow_id,
                    step_id,
                    StepStatus.RUNNING,
                    increment_attempt=True,
                )
                result = runner(workflow, step)
                if result.waiting_for_approval:
                    workflow = self._store.update_step(
                        workflow_id, step_id, StepStatus.WAITING_FOR_APPROVAL
                    )
                    return self._store.transition(
                        workflow_id,
                        workflow.revision,
                        WorkflowStatus.WAITING_FOR_APPROVAL,
                        waiting_reason=f"step {step_id} requires approval",
                    )
                if result.succeeded:
                    workflow = self._store.update_step(
                        workflow_id, step_id, StepStatus.COMPLETED, result=result.result
                    )
                    progressed = True
                    continue
                attempts = workflow.step_attempts[step_id]
                if result.retryable and attempts < step.max_attempts:
                    workflow = self._store.update_step(
                        workflow_id, step_id, StepStatus.RETRYING, error_code=result.error_code
                    )
                    progressed = True
                    continue
                workflow = self._store.update_step(
                    workflow_id, step_id, StepStatus.FAILED, error_code=result.error_code
                )
                return self._store.transition(
                    workflow_id,
                    workflow.revision,
                    WorkflowStatus.FAILED,
                    error_code=result.error_code or "workflow_step_failed",
                )
            if all(item is StepStatus.COMPLETED for item in workflow.step_statuses.values()):
                return self._store.transition(
                    workflow_id,
                    workflow.revision,
                    WorkflowStatus.COMPLETED,
                    event_type="WORKFLOW_COMPLETED",
                )
            if not progressed:
                return self._store.transition(
                    workflow_id,
                    workflow.revision,
                    WorkflowStatus.WAITING,
                    waiting_reason="workflow dependencies or external condition are pending",
                )

    def approve_step(self, workflow_id: str, step_id: str) -> WorkflowSnapshot:
        workflow = self._store.get(workflow_id)
        if workflow.step_statuses.get(step_id) is not StepStatus.WAITING_FOR_APPROVAL:
            raise ValueError("workflow step is not waiting for approval")
        workflow = self._store.update_step(workflow_id, step_id, StepStatus.READY)
        return self.run(workflow_id)

    def pause(self, workflow_id: str) -> WorkflowSnapshot:
        current = self._store.get(workflow_id)
        if current.status not in {WorkflowStatus.RUNNING, WorkflowStatus.WAITING}:
            raise ValueError("workflow cannot be paused from current state")
        return self._store.transition(workflow_id, current.revision, WorkflowStatus.PAUSED)

    def resume(self, workflow_id: str) -> WorkflowSnapshot:
        current = self._store.get(workflow_id)
        if current.status not in {
            WorkflowStatus.PAUSED,
            WorkflowStatus.WAITING,
            WorkflowStatus.RECOVERING,
        }:
            raise ValueError("workflow cannot be resumed from current state")
        return self.run(workflow_id)

    def cancel(self, workflow_id: str) -> WorkflowSnapshot:
        current = self._store.get(workflow_id)
        if current.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            return current
        for step_id, status in current.step_statuses.items():
            if status not in {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.CANCELLED}:
                current = self._store.update_step(workflow_id, step_id, StepStatus.CANCELLED)
        return self._store.transition(
            workflow_id, current.revision, WorkflowStatus.CANCELLED, event_type="WORKFLOW_CANCELLED"
        )

    def retry(self, workflow_id: str) -> WorkflowSnapshot:
        current = self._store.get(workflow_id)
        if current.status is not WorkflowStatus.FAILED:
            raise ValueError("only a failed workflow can retry")
        for step_id, status in current.step_statuses.items():
            if status is StepStatus.FAILED:
                current = self._store.update_step(workflow_id, step_id, StepStatus.RETRYING)
        return self.run(workflow_id)


__all__ = ["StepExecution", "StepRunner", "WorkflowEngine"]
