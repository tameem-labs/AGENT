"""Deterministic side-effect-aware Recovery subsystem."""

from zyro.recovery.contracts import (
    FailureClass,
    FailureIdentity,
    FailureRecord,
    RecoverableOperation,
    RecoverableOperationStatus,
    RecoveryAction,
    RecoveryDecision,
    RecoveryRequest,
    SideEffectState,
)
from zyro.recovery.policy import FailureClassifier, RecoveryPolicy
from zyro.recovery.store import RecoveryStoreError, SQLiteRecoveryStore, StartupReconciler
from zyro.recovery.task_adapter import TaskRecoveryAdapter

__all__ = [
    "FailureClass",
    "FailureClassifier",
    "FailureIdentity",
    "FailureRecord",
    "RecoverableOperation",
    "RecoverableOperationStatus",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryPolicy",
    "RecoveryRequest",
    "RecoveryStoreError",
    "SQLiteRecoveryStore",
    "SideEffectState",
    "StartupReconciler",
    "TaskRecoveryAdapter",
]
