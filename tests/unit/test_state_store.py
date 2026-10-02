from __future__ import annotations

from pathlib import Path

from tests.resource_fakes import NOW, MutableClock, resource_authorizer
from zyro.core.scope import ResourceScope, ScopeKind
from zyro.state import SQLiteStateStore, StateWriteOutcome

SCOPE = ResourceScope(ScopeKind.TASK, "task-1")
OWNERS = {
    "task.runtime": "task-subsystem",
    "agent.runtime": "agent-runtime",
    "system.status": "system-runtime",
}


def test_owner_creates_reads_and_persists_current_state(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    store = SQLiteStateStore(path, resource_authorizer(SCOPE), OWNERS, clock=lambda: NOW)

    created = store.create("task-subsystem", "task.runtime", "task-1", SCOPE, {"status": "RUNNING"})
    current = store.read("owner-1", "task.runtime", "task-1", SCOPE)

    assert created.outcome is StateWriteOutcome.CREATED
    assert current.record == created.record
    assert current.record is not None and current.record.revision == 1
    store.close()

    reopened = SQLiteStateStore(path, resource_authorizer(SCOPE), OWNERS, clock=lambda: NOW)
    assert reopened.read("owner-1", "task.runtime", "task-1", SCOPE).record == created.record
    reopened.close()


def test_wrong_owner_and_unknown_category_fail_closed(tmp_path: Path) -> None:
    store = SQLiteStateStore(
        tmp_path / "owners.sqlite", resource_authorizer(SCOPE), OWNERS, clock=lambda: NOW
    )

    denied = store.create("agent-runtime", "task.runtime", "task-1", SCOPE, {"status": "RUNNING"})
    unknown = store.create("anything", "arbitrary", "key", SCOPE, {"status": "X"})

    assert denied.outcome is StateWriteOutcome.UNAUTHORIZED
    assert denied.error is not None
    assert unknown.outcome is StateWriteOutcome.UNKNOWN_CATEGORY
    assert store.read("owner-1", "task.runtime", "task-1", SCOPE).record is None
    store.close()


def test_compare_and_set_rejects_stale_writes(tmp_path: Path) -> None:
    clock = MutableClock()
    store = SQLiteStateStore(
        tmp_path / "cas.sqlite", resource_authorizer(SCOPE), OWNERS, clock=clock
    )
    store.create("task-subsystem", "task.runtime", "task-1", SCOPE, {"status": "PENDING"})
    clock.advance(seconds=1)

    updated = store.compare_and_set(
        "task-subsystem", "task.runtime", "task-1", SCOPE, 1, {"status": "RUNNING"}
    )
    stale = store.compare_and_set(
        "task-subsystem", "task.runtime", "task-1", SCOPE, 1, {"status": "FAILED"}
    )

    assert updated.outcome is StateWriteOutcome.UPDATED
    assert updated.record is not None and updated.record.revision == 2
    assert stale.outcome is StateWriteOutcome.STALE
    current = store.read("owner-1", "task.runtime", "task-1", SCOPE).record
    assert current is not None and current.value == {"status": "RUNNING"}
    assert current.updated_at > current.created_at
    store.close()


def test_state_reads_are_scoped_and_authorized(tmp_path: Path) -> None:
    store = SQLiteStateStore(
        tmp_path / "read.sqlite", resource_authorizer(SCOPE), OWNERS, clock=lambda: NOW
    )
    store.create("task-subsystem", "task.runtime", "task-1", SCOPE, {"status": "RUNNING"})
    other = ResourceScope(ScopeKind.TASK, "task-2")

    unauthorized = store.read("intruder", "task.runtime", "task-1", SCOPE)
    wrong_scope = store.read("owner-1", "task.runtime", "task-1", other)

    assert unauthorized.record is None and unauthorized.error is not None
    assert wrong_scope.record is None and wrong_scope.error is not None
    store.close()
