from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "zyro"


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text())


def _imports(path: Path) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            result.update(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _classes(path: Path) -> set[str]:
    return {node.name for node in ast.walk(_tree(path)) if isinstance(node, ast.ClassDef)}


def test_domains_do_not_redefine_core_authority_or_transport_contracts() -> None:
    prohibited = {
        "Task",
        "Workflow",
        "ToolExecutor",
        "Permission",
        "PermissionEvaluator",
        "ApprovalService",
        "Event",
        "DurableEventBus",
        "RecoveryPolicy",
        "SQLiteResourceManager",
    }
    for path in (SRC / "domains").rglob("*.py"):
        assert prohibited.isdisjoint(_classes(path)), path


def test_authority_capable_freelancing_paths_use_only_the_canonical_tool_boundary() -> None:
    domain = SRC / "domains" / "freelancing"
    outreach_imports = _imports(domain / "outreach.py")
    reply_imports = _imports(domain / "replies.py")
    delivery_imports = _imports(domain / "delivery.py")

    assert "zyro.tools.contracts" in outreach_imports
    assert not any(name.startswith("zyro.security") for name in outreach_imports)
    assert not any(name.startswith("zyro.tools") for name in reply_imports)
    assert not any(name.startswith("zyro.security") for name in reply_imports)
    assert not any(name.startswith("zyro.security") for name in delivery_imports)
    assert "zyro.core.executive" in delivery_imports

    dispatch_calls = [
        node
        for node in ast.walk(_tree(domain / "outreach.py"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dispatch"
    ]
    assert len(dispatch_calls) == 1


def test_event_recovery_resource_and_observability_remain_non_authoritative() -> None:
    event_imports = _imports(SRC / "communication" / "event_bus.py")
    recovery_imports = set().union(*(_imports(path) for path in (SRC / "recovery").glob("*.py")))
    resource_imports = set().union(*(_imports(path) for path in (SRC / "resources").glob("*.py")))
    observation_imports = set().union(
        *(_imports(path) for path in (SRC / "observability").glob("*.py"))
    )

    for imported in event_imports:
        assert not imported.startswith(("zyro.domains", "zyro.state", "zyro.memory"))
    assert not any(name.startswith("zyro.security.approval") for name in recovery_imports)
    assert not any(name.startswith("zyro.security") for name in resource_imports)
    assert not any(
        name.startswith(("zyro.state", "zyro.memory", "zyro.security"))
        for name in observation_imports
    )


def test_memory_state_knowledge_context_are_separate_storage_authorities() -> None:
    memory_imports = set().union(*(_imports(path) for path in (SRC / "memory").glob("*.py")))
    state_imports = set().union(*(_imports(path) for path in (SRC / "state").glob("*.py")))
    knowledge_imports = set().union(*(_imports(path) for path in (SRC / "knowledge").glob("*.py")))
    context_imports = set().union(*(_imports(path) for path in (SRC / "context").glob("*.py")))

    assert not any(name.startswith(("zyro.state", "zyro.knowledge")) for name in memory_imports)
    assert not any(name.startswith(("zyro.memory", "zyro.knowledge")) for name in state_imports)
    assert not any(name.startswith(("zyro.memory", "zyro.state")) for name in knowledge_imports)
    assert not any(name.startswith("sqlite3") for name in context_imports)
    assert not _classes(SRC / "context" / "assembler.py").intersection(
        {"MemoryStore", "StateStore", "KnowledgeStore"}
    )


def test_agents_request_models_and_tools_through_runtime_boundaries() -> None:
    agent_imports = set().union(*(_imports(path) for path in (SRC / "agents").glob("*.py")))
    runtime_imports = _imports(SRC / "runtime" / "agent_runtime.py")
    handler_imports = _imports(SRC / "agents" / "handler.py")

    assert not any(name.startswith("zyro.models.provider") for name in agent_imports)
    assert "zyro.models.contracts" in runtime_imports
    assert "zyro.tools.contracts" in runtime_imports
    assert "zyro.agents.handler" in runtime_imports
    assert "zyro.context.contracts" in handler_imports
