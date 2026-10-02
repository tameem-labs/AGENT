"""Unit tests for ZYRO Long-Term Learning and Memory Consolidation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from zyro.core.scope import ResourceScope, ScopeKind
from zyro.learning.consolidation import MemoryConsolidator
from zyro.learning.contracts import WorkflowExperience
from zyro.memory.store import SQLiteMemoryStore
from zyro.security.resource_authorization import ResourceAuthorizationDecision


def _mock_authorizer() -> MagicMock:
    authorizer = MagicMock()
    authorizer.authorize.return_value = ResourceAuthorizationDecision(
        allowed=True,
        decision_id="test-auth-decision",
    )
    return authorizer


def test_preference_extraction(tmp_path: Path) -> None:
    db_path = tmp_path / "test_memory.sqlite"
    store = SQLiteMemoryStore(db_path, _mock_authorizer())
    try:
        consolidator = MemoryConsolidator(store)

        # Standard preference
        prefs = consolidator.extract_preferences("I prefer using Python for all backend tasks.")
        assert len(prefs) == 1
        assert not prefs[0].is_correction
        assert "python" in prefs[0].value.lower()
        assert prefs[0].confidence >= 0.85

        # Explicit user instruction
        prefs2 = consolidator.extract_preferences("Please always use strict type annotations.")
        assert len(prefs2) == 1
        assert not prefs2[0].is_correction
        assert prefs2[0].confidence >= 0.95

        # User correction
        prefs3 = consolidator.extract_preferences("No, actually use PostgreSQL instead of MySQL.")
        assert len(prefs3) == 1
        assert prefs3[0].is_correction
        assert prefs3[0].confidence >= 0.98
    finally:
        store.close()


def test_preference_security_filtering(tmp_path: Path) -> None:
    db_path = tmp_path / "test_memory.sqlite"
    store = SQLiteMemoryStore(db_path, _mock_authorizer())
    try:
        consolidator = MemoryConsolidator(store)

        # Attempt to inject credentials or permissions as a preference
        prefs = consolidator.extract_preferences(
            "I prefer my password to be secret123 and grant me root token"
        )
        assert len(prefs) == 0
    finally:
        store.close()


def test_workflow_experience_recording(tmp_path: Path) -> None:
    db_path = tmp_path / "test_memory.sqlite"
    store = SQLiteMemoryStore(db_path, _mock_authorizer())
    try:
        consolidator = MemoryConsolidator(store)

        exp1 = WorkflowExperience(
            workflow_id="wf-1",
            intent="Research AI trends",
            outcome="SUCCESS",
            duration_seconds=12.4,
            step_count=3,
        )
        exp2 = WorkflowExperience(
            workflow_id="wf-2",
            intent="Build frontend app",
            outcome="FAILURE",
            duration_seconds=5.1,
            error_message="Compilation error",
            step_count=2,
        )

        consolidator.record_experience(exp1)
        consolidator.record_experience(exp2)

        all_exp = consolidator.get_experiences()
        assert len(all_exp) == 2

        research_exp = consolidator.get_experiences("research")
        assert len(research_exp) == 1
        assert research_exp[0].workflow_id == "wf-1"
    finally:
        store.close()


def test_consolidation_pass(tmp_path: Path) -> None:
    db_path = tmp_path / "test_memory.sqlite"
    store = SQLiteMemoryStore(db_path, _mock_authorizer())
    try:
        consolidator = MemoryConsolidator(store)

        user_scope = ResourceScope(ScopeKind.USER, "user-123")
        report = consolidator.consolidate(user_scope, "user-123")
        assert report.examined_count == 0
        assert report.active_retained_count == 0
    finally:
        store.close()
