from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "zyro"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def subsystem_imports(name: str) -> set[str]:
    result: set[str] = set()
    for path in (SOURCE / name).glob("*.py"):
        result.update(imports(path))
    return result


def test_memory_state_and_knowledge_do_not_own_each_other() -> None:
    memory = subsystem_imports("memory")
    state = subsystem_imports("state")
    knowledge = subsystem_imports("knowledge")

    assert not any(item.startswith("zyro.state") for item in memory)
    assert not any(item.startswith("zyro.knowledge") for item in memory)
    assert not any(item.startswith("zyro.memory") for item in state)
    assert not any(item.startswith("zyro.knowledge") for item in state)
    assert not any(item.startswith("zyro.memory") for item in knowledge)
    assert not any(item.startswith("zyro.state") for item in knowledge)


def test_context_is_transient_and_owns_no_storage_or_authority() -> None:
    context_imports = subsystem_imports("context")
    source = "\n".join(path.read_text() for path in (SOURCE / "context").glob("*.py"))

    assert "sqlite3" not in context_imports
    assert "PermissionStore" not in source
    assert "Permission(" not in source
    assert "CREATE TABLE" not in source
    assert "def write(" not in source
    assert "def ingest(" not in source
    assert "def compare_and_set(" not in source


def test_resources_reuse_phase_four_permissions_and_never_approval() -> None:
    authorization = (SOURCE / "security" / "resource_authorization.py").read_text()
    combined = set().union(
        subsystem_imports("memory"),
        subsystem_imports("state"),
        subsystem_imports("knowledge"),
        subsystem_imports("context"),
    )

    assert "PermissionEvaluator" in authorization
    assert "zyro.security.permission" in imports(SOURCE / "security" / "resource_authorization.py")
    assert not any(item.startswith("zyro.security.approval") for item in combined)


def test_existing_authorities_remain_separate() -> None:
    event_imports = subsystem_imports("communication")
    task_source = (SOURCE / "core" / "task.py").read_text()
    freelancing_source = (SOURCE / "domains" / "freelancing" / "state.py").read_text()

    assert not any(item.startswith("zyro.state") for item in event_imports)
    assert "class Task:" in task_source
    assert "class InProcessLeadStore:" in freelancing_source
    assert "SQLiteStateStore" not in task_source
    assert "SQLiteStateStore" not in freelancing_source


def test_no_phase_eight_or_external_retrieval_infrastructure() -> None:
    combined: set[str] = set()
    for subsystem in ("memory", "state", "knowledge", "context"):
        combined.update(subsystem_imports(subsystem))
    forbidden = {
        "redis",
        "elasticsearch",
        "faiss",
        "chromadb",
        "celery",
        "kafka",
        "requests",
        "threading",
        "asyncio",
    }
    assert forbidden.isdisjoint(combined)
