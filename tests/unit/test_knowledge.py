from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.resource_fakes import NOW, resource_authorizer
from zyro.core.scope import ResourceScope, ScopeKind
from zyro.knowledge import (
    KnowledgeIngestionOutcome,
    KnowledgeIngestionRequest,
    KnowledgeQuery,
    KnowledgeSourceType,
    KnowledgeStatus,
    SQLiteKnowledgeStore,
)

SCOPE = ResourceScope(ScopeKind.PROJECT, "project-1")


def ingestion(
    *,
    version: str = "1.0",
    content: str = "SQLite uses transactions for atomic database changes.",
    scope: ResourceScope = SCOPE,
) -> KnowledgeIngestionRequest:
    return KnowledgeIngestionRequest(
        "owner-1",
        "sqlite-reference",
        KnowledgeSourceType.PROJECT_DOCUMENTATION,
        "docs/database.md",
        scope,
        content,
        version,
        {"title": "Database Reference"},
    )


def test_ingestion_retrieval_provenance_chunking_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.sqlite"
    store = SQLiteKnowledgeStore(path, resource_authorizer(SCOPE), clock=lambda: NOW)
    request = ingestion(content="A" * 2_100 + "\n\nSQLite transaction reference.")

    result = store.ingest(request)
    retrieved = store.retrieve(KnowledgeQuery("owner-1", SCOPE, "transaction"))

    assert result.outcome is KnowledgeIngestionOutcome.INGESTED
    assert len(result.records) == 2
    assert retrieved.records[0].source_reference == "docs/database.md"
    assert retrieved.records[0].version == "1.0"
    assert retrieved.records[0].status is KnowledgeStatus.CURRENT
    store.close()

    reopened = SQLiteKnowledgeStore(path, resource_authorizer(SCOPE), clock=lambda: NOW)
    assert reopened.retrieve(KnowledgeQuery("owner-1", SCOPE, "transaction")).records
    reopened.close()


def test_duplicate_and_conflicting_source_version_are_deterministic(tmp_path: Path) -> None:
    store = SQLiteKnowledgeStore(
        tmp_path / "duplicate.sqlite", resource_authorizer(SCOPE), clock=lambda: NOW
    )

    assert store.ingest(ingestion()).outcome is KnowledgeIngestionOutcome.INGESTED
    assert store.ingest(ingestion()).outcome is KnowledgeIngestionOutcome.DUPLICATE
    conflict = store.ingest(ingestion(content="Different material under the same version."))

    assert conflict.outcome is KnowledgeIngestionOutcome.CONFLICT
    store.close()


def test_new_version_supersedes_old_for_normal_retrieval(tmp_path: Path) -> None:
    store = SQLiteKnowledgeStore(
        tmp_path / "versions.sqlite", resource_authorizer(SCOPE), clock=lambda: NOW
    )
    store.ingest(ingestion(version="1.0", content="SQLite old transaction guidance."))
    store.ingest(ingestion(version="2.0", content="SQLite current transaction guidance."))

    current = store.retrieve(KnowledgeQuery("owner-1", SCOPE, "transaction"))
    all_versions = store.retrieve(
        KnowledgeQuery("owner-1", SCOPE, "transaction", current_only=False)
    )

    assert {item.version for item in current.records} == {"2.0"}
    assert {item.version for item in all_versions.records} == {"1.0", "2.0"}
    assert {item.status for item in all_versions.records} == {
        KnowledgeStatus.CURRENT,
        KnowledgeStatus.SUPERSEDED,
    }
    store.close()


def test_scope_permissions_and_malformed_or_secret_input_fail_closed(tmp_path: Path) -> None:
    store = SQLiteKnowledgeStore(
        tmp_path / "security.sqlite", resource_authorizer(SCOPE), clock=lambda: NOW
    )
    unauthorized = store.ingest(replace(ingestion(), requester_id="intruder"))
    other = ResourceScope(ScopeKind.PROJECT, "project-2")

    assert unauthorized.outcome is KnowledgeIngestionOutcome.UNAUTHORIZED
    assert store.retrieve(KnowledgeQuery("intruder", SCOPE, "SQLite")).error is not None
    assert store.retrieve(KnowledgeQuery("owner-1", other, "SQLite")).error is not None
    with pytest.raises(ValueError):
        ingestion(content="api" + "_key=redacted")
    with pytest.raises(ValueError):
        ingestion(content=" ")
    store.close()


def test_retrieval_is_relevant_source_filtered_and_bounded(tmp_path: Path) -> None:
    store = SQLiteKnowledgeStore(
        tmp_path / "bounded.sqlite", resource_authorizer(SCOPE), clock=lambda: NOW
    )
    store.ingest(ingestion(content="SQLite transaction details."))
    second = replace(
        ingestion(version="1.0", content="Unrelated rendering reference."),
        source_id="rendering-reference",
        source_reference="docs/rendering.md",
    )
    store.ingest(second)

    result = store.retrieve(
        KnowledgeQuery(
            "owner-1",
            SCOPE,
            "SQLite transaction",
            source_id="sqlite-reference",
            limit=1,
        )
    )

    assert len(result.records) == 1
    assert result.records[0].source_id == "sqlite-reference"
    store.close()
