from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from tests.resource_fakes import NOW, MutableClock, resource_authorizer
from zyro.core.scope import ResourceScope, ScopeKind
from zyro.memory import (
    AssertionType,
    MemoryLayer,
    MemoryProvenance,
    MemoryQuery,
    MemorySourceType,
    MemoryStatus,
    MemoryType,
    MemoryWriteOutcome,
    MemoryWriteReason,
    MemoryWriteRequest,
    PrivacyClassification,
    RetentionPolicy,
    SQLiteMemoryStore,
)

SCOPE = ResourceScope(ScopeKind.PROJECT, "project-1")


def write_request(
    memory_id: str = "memory-1",
    *,
    logical_key: str = "database-preference",
    content: dict[str, object] | None = None,
    assertion_type: AssertionType = AssertionType.FACT,
    source_type: MemorySourceType = MemorySourceType.USER_INPUT,
    privacy: PrivacyClassification = PrivacyClassification.PRIVATE,
    scope: ResourceScope = SCOPE,
    retention: RetentionPolicy | None = None,
) -> MemoryWriteRequest:
    return MemoryWriteRequest(
        "owner-1",
        memory_id,
        logical_key,
        scope,
        MemoryLayer.PROJECT,
        MemoryType.PREFERENCE,
        assertion_type,
        content or {"preference": "SQLite"},
        MemoryProvenance(source_type, "request-1", ("evidence-1",)),
        MemoryWriteReason.STABLE_PREFERENCE,
        privacy,
        1.0,
        retention=retention,
    )


def test_memory_creation_retrieval_provenance_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    store = SQLiteMemoryStore(path, resource_authorizer(SCOPE), clock=lambda: NOW)

    created = store.write(write_request())
    retrieved = store.retrieve(MemoryQuery("owner-1", SCOPE, "SQLite"))

    assert created.outcome is MemoryWriteOutcome.CREATED
    assert retrieved.error is None
    assert retrieved.records == (created.record,)
    assert retrieved.records[0].provenance.evidence_ids == ("evidence-1",)
    assert retrieved.records[0].assertion_type is AssertionType.FACT
    assert retrieved.records[0].retention.expires_at is None
    assert retrieved.records[0].retention.owner_controlled
    store.close()

    reopened = SQLiteMemoryStore(path, resource_authorizer(SCOPE), clock=lambda: NOW)
    assert (
        reopened.retrieve(MemoryQuery("owner-1", SCOPE, "SQLite")).records[0].memory_id
        == "memory-1"
    )
    reopened.close()


def test_memory_write_is_selective_validated_and_identity_deduplicated(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(
        tmp_path / "validation.sqlite", resource_authorizer(SCOPE), clock=lambda: NOW
    )
    first = store.write(write_request())
    duplicate = store.write(write_request())
    collision = store.write(write_request(content={"preference": "PostgreSQL"}))

    assert first.outcome is MemoryWriteOutcome.CREATED
    assert duplicate.outcome is MemoryWriteOutcome.DUPLICATE
    assert collision.outcome is MemoryWriteOutcome.CONFLICT
    with pytest.raises(ValueError):
        write_request(content={"api" + "_key": "redacted"})
    with pytest.raises(ValueError):
        MemoryWriteRequest(
            "owner-1",
            "memory-2",
            "key",
            SCOPE,
            MemoryLayer.PROJECT,
            MemoryType.NOTE,
            AssertionType.FACT,
            ["raw conversation"],  # type: ignore[arg-type]
            MemoryProvenance(MemorySourceType.USER_INPUT, "request-2"),
            MemoryWriteReason.USER_INSTRUCTION,
        )
    store.close()


def test_scope_and_permissions_fail_closed(tmp_path: Path) -> None:
    other = ResourceScope(ScopeKind.PROJECT, "project-2")
    store = SQLiteMemoryStore(
        tmp_path / "scope.sqlite", resource_authorizer(SCOPE), clock=lambda: NOW
    )
    assert store.write(write_request()).outcome is MemoryWriteOutcome.CREATED

    wrong_scope = store.retrieve(MemoryQuery("owner-1", other, "SQLite"))
    unknown_requester = store.retrieve(MemoryQuery("intruder", SCOPE, "SQLite"))
    unauthorized_write = store.write(replace(write_request("memory-2"), requester_id="intruder"))

    assert wrong_scope.records == () and wrong_scope.error is not None
    assert unknown_requester.records == () and unknown_requester.error is not None
    assert unauthorized_write.outcome is MemoryWriteOutcome.UNAUTHORIZED
    store.close()


def test_expired_and_temporally_invalid_memory_is_excluded(tmp_path: Path) -> None:
    clock = MutableClock()
    store = SQLiteMemoryStore(
        tmp_path / "retention.sqlite", resource_authorizer(SCOPE), clock=clock
    )
    store.write(
        write_request(
            retention=RetentionPolicy(NOW + timedelta(seconds=5)),
        )
    )
    future = write_request("memory-future", logical_key="future")
    future = MemoryWriteRequest(
        future.requester_id,
        future.memory_id,
        future.logical_key,
        future.scope,
        future.layer,
        future.memory_type,
        future.assertion_type,
        future.content,
        future.provenance,
        future.reason,
        future.privacy,
        future.confidence,
        valid_from=NOW + timedelta(days=1),
    )
    store.write(future)

    assert len(store.retrieve(MemoryQuery("owner-1", SCOPE, "preference")).records) == 1
    clock.advance(seconds=6)
    assert store.retrieve(MemoryQuery("owner-1", SCOPE, "preference")).records == ()
    store.close()


def test_correction_and_contradiction_return_only_current_revision(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(
        tmp_path / "correction.sqlite", resource_authorizer(SCOPE), clock=lambda: NOW
    )
    original = store.write(write_request())
    corrected = store.correct(
        "memory-1",
        1,
        write_request("memory-2", content={"preference": "PostgreSQL"}),
        contradicted=True,
    )
    stale = store.correct(
        "memory-1",
        1,
        write_request("memory-3", content={"preference": "Other"}),
    )

    records = store.retrieve(MemoryQuery("owner-1", SCOPE, "preference"))
    assert original.record is not None
    assert corrected.outcome is MemoryWriteOutcome.CORRECTED
    assert corrected.record is not None and corrected.record.revision == 2
    assert corrected.record.supersedes_memory_id == "memory-1"
    assert stale.outcome is MemoryWriteOutcome.CONFLICT
    assert [item.memory_id for item in records.records] == ["memory-2"]
    store.close()


def test_forgetting_scrubs_revision_chain_and_prevents_resurrection(tmp_path: Path) -> None:
    path = tmp_path / "forget.sqlite"
    store = SQLiteMemoryStore(path, resource_authorizer(SCOPE), clock=lambda: NOW)
    store.write(write_request())
    store.correct("memory-1", 1, write_request("memory-2", content={"preference": "PostgreSQL"}))

    forgotten = store.forget("owner-1", SCOPE, "memory-2")

    assert forgotten.outcome is MemoryWriteOutcome.FORGOTTEN
    assert forgotten.record is not None
    assert forgotten.record.status is MemoryStatus.FORGOTTEN
    assert forgotten.record.content == {}
    assert store.retrieve(MemoryQuery("owner-1", SCOPE, "preference")).records == ()
    store.close()
    reopened = SQLiteMemoryStore(path, resource_authorizer(SCOPE), clock=lambda: NOW)
    assert reopened.retrieve(MemoryQuery("owner-1", SCOPE, "preference")).records == ()
    reopened.close()


def test_fact_precedes_inference_and_retrieval_is_bounded(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(
        tmp_path / "rank.sqlite", resource_authorizer(SCOPE), clock=lambda: NOW
    )
    store.write(
        write_request(
            "inference",
            logical_key="inferred-db",
            content={"database": "inferred choice"},
            assertion_type=AssertionType.INFERENCE,
            source_type=MemorySourceType.AGENT_OUTPUT,
        )
    )
    store.write(
        write_request(
            "fact",
            logical_key="verified-db",
            content={"database": "verified choice"},
            source_type=MemorySourceType.VERIFIED_OUTCOME,
        )
    )

    result = store.retrieve(MemoryQuery("owner-1", SCOPE, "database choice", limit=1))

    assert [record.memory_id for record in result.records] == ["fact"]
    store.close()


def test_retrieval_remains_bounded_over_meaningful_fixture(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(
        tmp_path / "fixture-size.sqlite", resource_authorizer(SCOPE), clock=lambda: NOW
    )
    for index in range(120):
        store.write(
            write_request(
                f"memory-{index}",
                logical_key=f"fixture-{index}",
                content={"fixture": f"database item {index}"},
            )
        )

    result = store.retrieve(
        MemoryQuery("owner-1", SCOPE, "database item", limit=10, max_characters=1_000)
    )

    assert len(result.records) == 10
    assert all("fixture" in record.content for record in result.records)
    store.close()


def test_restricted_memory_requires_additional_permission(tmp_path: Path) -> None:
    path = tmp_path / "privacy.sqlite"
    basic = SQLiteMemoryStore(path, resource_authorizer(SCOPE), clock=lambda: NOW)
    basic.write(write_request(privacy=PrivacyClassification.RESTRICTED))
    assert basic.retrieve(MemoryQuery("owner-1", SCOPE, "SQLite")).records == ()
    basic.close()

    privileged = SQLiteMemoryStore(
        path,
        resource_authorizer(SCOPE, restricted=True),
        clock=lambda: NOW,
    )
    assert len(privileged.retrieve(MemoryQuery("owner-1", SCOPE, "SQLite")).records) == 1
    privileged.close()
