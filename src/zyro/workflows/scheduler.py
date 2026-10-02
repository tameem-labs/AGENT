"""Cooperative local scheduler for due and event-triggered workflows."""

from __future__ import annotations

from datetime import UTC, datetime

from zyro.workflows.contracts import TriggerKind, WorkflowStatus
from zyro.workflows.engine import WorkflowEngine
from zyro.workflows.store import WorkflowStore


class LocalWorkflowScheduler:
    """Runs only when polled by the local application; no hidden worker is claimed."""

    def __init__(self, store: WorkflowStore, engine: WorkflowEngine) -> None:
        self._store = store
        self._engine = engine

    def run_due(self, now: datetime | None = None) -> tuple[str, ...]:
        current = now or datetime.now(UTC)
        executed: list[str] = []
        for workflow in self._store.list():
            if workflow.status is not WorkflowStatus.SCHEDULED:
                continue
            if workflow.next_run_at is None or workflow.next_run_at > current:
                continue
            result = self._engine.run(workflow.definition.workflow_id)
            executed.append(result.definition.workflow_id)
        return tuple(executed)

    def trigger_event(self, event_type: str) -> tuple[str, ...]:
        executed: list[str] = []
        for workflow in self._store.list():
            if (
                workflow.definition.trigger is TriggerKind.EVENT
                and workflow.definition.event_type == event_type
                and workflow.status in {WorkflowStatus.PENDING, WorkflowStatus.WAITING}
            ):
                result = self._engine.run(workflow.definition.workflow_id)
                executed.append(result.definition.workflow_id)
        return tuple(executed)


__all__ = ["LocalWorkflowScheduler"]
