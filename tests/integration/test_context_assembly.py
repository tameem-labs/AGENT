from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from tests.resource_fakes import NOW, resource_authorizer
from zyro.agents.handler import ExecutionContext
from zyro.context import (
    ContextAssembler,
    ContextBudget,
    ContextRequest,
    ContextSource,
    StateReference,
)
from zyro.core.scope import ResourceScope, ScopeKind
from zyro.knowledge import (
    KnowledgeIngestionRequest,
    KnowledgeSourceType,
    SQLiteKnowledgeStore,
)
from zyro.memory import (
    AssertionType,
    MemoryLayer,
    MemoryProvenance,
    MemorySourceType,
    MemoryType,
    MemoryWriteReason,
    MemoryWriteRequest,
    RetentionPolicy,
    SQLiteMemoryStore,
)
from zyro.state import SQLiteStateStore

SCOPE = ResourceScope(ScopeKind.PROJECT, "project-1")


def memory_request(
    memory_id: str,
    preference: str,
) -> MemoryWriteRequest:
    return MemoryWriteRequest(
        "owner-1",
        memory_id,
        "database-preference",
        SCOPE,
        MemoryLayer.PROJECT,
        MemoryType.PREFERENCE,
        AssertionType.FACT,
        {"database_preference": preference},
        MemoryProvenance(MemorySourceType.USER_INPUT, f"request-{memory_id}"),
        MemoryWriteReason.STABLE_PREFERENCE,
    )


def resources(
    tmp_path: Path,
) -> tuple[ContextAssembler, SQLiteMemoryStore, SQLiteStateStore, SQLiteKnowledgeStore]:
    authorization = resource_authorizer(SCOPE)
    memory = SQLiteMemoryStore(tmp_path / "memory.sqlite", authorization, clock=lambda: NOW)
    state = SQLiteStateStore(
        tmp_path / "state.sqlite",
        authorization,
        {"project.runtime": "project-runtime"},
        clock=lambda: NOW,
    )
    knowledge = SQLiteKnowledgeStore(
        tmp_path / "knowledge.sqlite", authorization, clock=lambda: NOW
    )
    return ContextAssembler(memory, state, knowledge, authorization), memory, state, knowledge


def test_context_combines_distinct_scoped_sources_with_provenance(tmp_path: Path) -> None:
    assembler, memory, state, knowledge = resources(tmp_path)
    memory.write(memory_request("memory-1", "SQLite"))
    state.create(
        "project-runtime",
        "project.runtime",
        "build",
        SCOPE,
        {"status": "RUNNING", "revision_source": "runtime"},
    )
    state.compare_and_set(
        "project-runtime",
        "project.runtime",
        "build",
        SCOPE,
        1,
        {"status": "DONE", "revision_source": "runtime"},
    )
    knowledge.ingest(
        KnowledgeIngestionRequest(
            "owner-1",
            "database-doc",
            KnowledgeSourceType.PROJECT_DOCUMENTATION,
            "docs/database.md",
            SCOPE,
            "SQLite database transactions are atomic.",
            "1.0",
        )
    )

    result = assembler.assemble(
        ContextRequest(
            "owner-1",
            "task-1",
            SCOPE,
            "SQLite database",
            "Use the current requirements and do not change authority.",
            {"goal": "Select database behavior"},
            (StateReference("project.runtime", "build"),),
        )
    )

    assert result.error is None
    assert {item.source for item in result.items} == {
        ContextSource.USER_INPUT,
        ContextSource.TASK_DATA,
        ContextSource.CURRENT_STATE,
        ContextSource.MEMORY,
        ContextSource.KNOWLEDGE,
    }
    assert result.items[0].source is ContextSource.USER_INPUT
    state_item = next(item for item in result.items if item.source is ContextSource.CURRENT_STATE)
    assert state_item.content["status"] == "DONE"
    assert state_item.version == "2"
    assert all(item.provenance and item.source_id and item.version for item in result.items)
    assert result.used_characters <= 8_000
    memory.close()
    state.close()
    knowledge.close()


def test_preference_correction_and_knowledge_version_precedence(tmp_path: Path) -> None:
    assembler, memory, state, knowledge = resources(tmp_path)
    memory.write(memory_request("memory-1", "SQLite"))
    memory.correct("memory-1", 1, memory_request("memory-2", "PostgreSQL"), contradicted=True)
    knowledge.ingest(
        KnowledgeIngestionRequest(
            "owner-1",
            "db-doc",
            KnowledgeSourceType.PROJECT_DOCUMENTATION,
            "docs/db.md",
            SCOPE,
            "Old database guidance.",
            "1.0",
        )
    )
    knowledge.ingest(
        KnowledgeIngestionRequest(
            "owner-1",
            "db-doc",
            KnowledgeSourceType.PROJECT_DOCUMENTATION,
            "docs/db.md",
            SCOPE,
            "Current database guidance.",
            "2.0",
        )
    )

    result = assembler.assemble(
        ContextRequest("owner-1", "task-1", SCOPE, "database", "Use PostgreSQL now.")
    )

    memory_items = [item for item in result.items if item.source is ContextSource.MEMORY]
    knowledge_items = [item for item in result.items if item.source is ContextSource.KNOWLEDGE]
    assert [item.source_id for item in memory_items] == ["memory-2"]
    assert [item.version for item in knowledge_items] == ["2.0"]
    assert result.items[0].content == {"instruction": "Use PostgreSQL now."}
    memory.close()
    state.close()
    knowledge.close()


def test_forgotten_memory_never_returns_through_context(tmp_path: Path) -> None:
    assembler, memory, state, knowledge = resources(tmp_path)
    memory.write(memory_request("memory-1", "SQLite"))
    memory.forget("owner-1", SCOPE, "memory-1")
    expired = memory_request("memory-2", "SQLite expired")
    memory.write(
        MemoryWriteRequest(
            expired.requester_id,
            expired.memory_id,
            "expired-preference",
            expired.scope,
            expired.layer,
            expired.memory_type,
            expired.assertion_type,
            expired.content,
            expired.provenance,
            expired.reason,
            retention=RetentionPolicy(NOW - timedelta(seconds=1)),
        )
    )

    result = assembler.assemble(
        ContextRequest("owner-1", "task-1", SCOPE, "SQLite", "Choose a database.")
    )

    assert ContextSource.MEMORY not in {item.source for item in result.items}
    memory.close()
    state.close()
    knowledge.close()


def test_context_deduplicates_and_applies_deterministic_budget(tmp_path: Path) -> None:
    assembler, memory, state, knowledge = resources(tmp_path)
    memory.write(
        MemoryWriteRequest(
            "owner-1",
            "memory-1",
            "goal-copy",
            SCOPE,
            MemoryLayer.PROJECT,
            MemoryType.NOTE,
            AssertionType.FACT,
            {"goal": "same content"},
            MemoryProvenance(MemorySourceType.USER_INPUT, "request-1"),
            MemoryWriteReason.USER_INSTRUCTION,
        )
    )

    request = ContextRequest(
        "owner-1",
        "task-1",
        SCOPE,
        "content",
        "X" * 400,
        {"goal": "same content"},
        budget=ContextBudget(max_records=2, max_characters=256, max_sources=2),
    )
    first = assembler.assemble(request)
    second = assembler.assemble(request)

    assert first.items == second.items
    assert first.used_characters == second.used_characters
    assert first.truncated == second.truncated
    assert first.truncated
    assert len(first.items) <= 2
    assert first.used_characters <= 256
    assert first.items[0].source is ContextSource.USER_INPUT
    assert len({item.content.__repr__() for item in first.items}) == len(first.items)
    memory.close()
    state.close()
    knowledge.close()


def test_agent_execution_context_uses_assembler_without_store_access(tmp_path: Path) -> None:
    assembler, memory, state, knowledge = resources(tmp_path)
    memory.write(memory_request("memory-1", "SQLite"))
    execution = ExecutionContext(
        "task-1",
        "request-1",
        "correlation-1",
        "agent-1",
        "instance-1",
        "Choose database behavior",
        1,
        requester_id="owner-1",
        context_provider=assembler,
    )

    result = execution.request_context(
        SCOPE,
        "SQLite",
    )

    assert result.error is None
    assert result.items[0].content == {"instruction": "Choose database behavior"}
    assert ContextSource.MEMORY in {item.source for item in result.items}
    assert not hasattr(execution, "memory_store")
    memory.close()
    state.close()
    knowledge.close()


def test_context_permission_denial_returns_no_sources(tmp_path: Path) -> None:
    assembler, memory, state, knowledge = resources(tmp_path)

    denied = assembler.assemble(
        ContextRequest("intruder", "task-1", SCOPE, "anything", "Show everything.")
    )

    assert denied.items == ()
    assert denied.error is not None
    memory.close()
    state.close()
    knowledge.close()
