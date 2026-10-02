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


def test_observability_has_no_task_state_memory_or_recovery_authority() -> None:
    observed = subsystem_imports("observability")
    forbidden = (
        "zyro.core.task",
        "zyro.state",
        "zyro.memory",
        "zyro.recovery",
        "zyro.security",
    )

    assert not any(item.startswith(forbidden) for item in observed)


def test_resource_manager_has_no_scheduler_task_or_permission_authority() -> None:
    manager = imports(SOURCE / "resources" / "manager.py")
    configuration = imports(SOURCE / "resources" / "configuration.py")

    assert not any(item.startswith("zyro.core.task") for item in manager)
    assert not any(item.startswith("zyro.security") for item in manager | configuration)
    assert not any("scheduler" in item for item in manager | configuration)
    assert not any(item.startswith("zyro.recovery") for item in manager | configuration)


def test_recovery_policy_does_not_execute_or_approve_actions() -> None:
    policy_source = (SOURCE / "recovery" / "policy.py").read_text()
    recovery_imports = subsystem_imports("recovery")

    assert not any(item.startswith("zyro.execution") for item in recovery_imports)
    assert not any(item.startswith("zyro.security.approval") for item in recovery_imports)
    assert "ModelRouter" not in policy_source
    assert "ToolExecutor" not in policy_source


def test_phase_eight_uses_only_local_bounded_infrastructure() -> None:
    combined = set().union(
        subsystem_imports("recovery"),
        subsystem_imports("observability"),
        subsystem_imports("resources"),
    )
    forbidden = {
        "celery",
        "kafka",
        "redis",
        "ray",
        "kubernetes",
        "threading",
        "multiprocessing",
    }

    assert forbidden.isdisjoint(combined)
