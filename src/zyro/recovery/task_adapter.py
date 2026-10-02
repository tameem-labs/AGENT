"""Narrow Task lifecycle adapter; Recovery never assigns Task status directly."""

from __future__ import annotations

from zyro.core.task import Task
from zyro.recovery.contracts import RecoveryAction, RecoveryDecision


class TaskRecoveryAdapter:
    @staticmethod
    def prepare_retry(task: Task, decision: RecoveryDecision) -> bool:
        if decision.action is not RecoveryAction.RETRY or not task.can_retry:
            return False
        task.prepare_retry()
        return True


__all__ = ["TaskRecoveryAdapter"]
