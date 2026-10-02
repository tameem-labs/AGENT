from __future__ import annotations

import ast
from pathlib import Path

from zyro.agents.registry import AgentRegistry
from zyro.domains.freelancing.agents import (
    QUALIFICATION_AGENT_ID,
    SCORING_AGENT_ID,
    StageInputRegistry,
    register_freelancing_agents,
)

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "src" / "zyro" / "domains" / "freelancing"
PHASE_FIVE_FILES = tuple(
    DOMAIN / name
    for name in (
        "agents.py",
        "contracts.py",
        "evaluation.py",
        "pipeline.py",
        "policies.py",
        "state.py",
        "verification.py",
    )
)


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_qualification_and_scoring_agents_are_registered_core_agents() -> None:
    registry = AgentRegistry()
    register_freelancing_agents(registry, StageInputRegistry())

    qualification = registry.get(QUALIFICATION_AGENT_ID).definition
    scoring = registry.get(SCORING_AGENT_ID).definition

    assert qualification.domain == "freelancing"
    assert scoring.domain == "freelancing"
    assert qualification.permissions == ()
    assert scoring.permissions == ()
    assert qualification.agent_id != scoring.agent_id


def test_domain_agents_do_not_choose_providers_self_approve_or_mutate_tasks() -> None:
    agent_imports = imports(DOMAIN / "agents.py")
    source = (DOMAIN / "agents.py").read_text()

    assert "zyro.models.provider" not in agent_imports
    assert "zyro.models.registry" not in agent_imports
    assert "zyro.security.approval" not in agent_imports
    assert "zyro.security.permission" not in agent_imports
    assert "zyro.core.task" not in agent_imports
    assert ".approve(" not in source
    assert ".mark_verified(" not in source
    assert ".complete(" not in source


def test_domain_does_not_redefine_core_task_or_bypass_tool_authorization() -> None:
    for path in PHASE_FIVE_FILES:
        source = path.read_text()
        tree = ast.parse(source)
        class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
        assert "Task" not in class_names
        assert "ToolExecutor" not in source
        assert "registered.handler.execute" not in source


def test_phase_five_has_no_outreach_or_forbidden_future_dependencies() -> None:
    forbidden_imports = {
        "requests",
        "selenium",
        "playwright",
        "smtplib",
        "zyro.memory",
        "zyro.knowledge",
        "zyro.state",
        "zyro.interfaces",
    }
    all_imports: set[str] = set()
    for path in PHASE_FIVE_FILES:
        all_imports.update(imports(path))

    assert forbidden_imports.isdisjoint(all_imports)
    source = "\n".join(path.read_text().lower() for path in PHASE_FIVE_FILES)
    assert "send_email" not in source
    assert "browser" not in source
    assert "crm_client" not in source
    assert "client_reply" not in source


def test_compatibility_event_publisher_remains_non_durable() -> None:
    source = (ROOT / "src" / "zyro" / "core" / "events.py").read_text()
    publisher = source.split("class InProcessEventPublisher", 1)[1].split("def new_event_id", 1)[0]

    assert "Compatibility recorder" in publisher
    assert "def subscribe" not in publisher
    assert "def deliver" not in publisher
    assert "def acknowledge" not in publisher
