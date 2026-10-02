from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "src" / "zyro" / "domains" / "freelancing"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def test_outreach_reuses_tool_authority_and_never_imports_approval_or_permission() -> None:
    outreach = imports(DOMAIN / "outreach.py")
    source = (DOMAIN / "outreach.py").read_text()

    assert "zyro.tools.contracts" in outreach
    assert "RiskClass.STRICT_AUTHORIZATION" in source
    assert not any(item.startswith("zyro.security") for item in outreach)
    assert "ApprovalService" not in source
    assert "PermissionEvaluator" not in source


def test_external_reply_is_data_and_has_no_execution_boundary() -> None:
    reply_imports = imports(DOMAIN / "replies.py")
    source = (DOMAIN / "replies.py").read_text()

    assert not any(item.startswith("zyro.tools") for item in reply_imports)
    assert not any(item.startswith("zyro.security") for item in reply_imports)
    assert "execute_tool" not in source
    assert "Approval" not in source


def test_delivery_reuses_executive_resource_and_verification_results() -> None:
    delivery_imports = imports(DOMAIN / "delivery.py")
    source = (DOMAIN / "delivery.py").read_text()

    assert "zyro.core.executive" in delivery_imports
    assert "zyro.resources" in delivery_imports
    assert "class Task" not in source
    assert "class Workflow" not in source
    assert "ExecutiveOutcome.VERIFIED_SUCCESS" in source


def test_no_future_interface_financial_or_distributed_scope() -> None:
    combined: set[str] = set()
    text = ""
    for name in ("outreach.py", "replies.py", "delivery.py", "operations_agents.py"):
        path = DOMAIN / name
        combined.update(imports(path))
        text += path.read_text()
    forbidden = {
        "requests",
        "httpx",
        "selenium",
        "playwright",
        "stripe",
        "celery",
        "kafka",
        "redis",
        "kubernetes",
        "threading",
        "multiprocessing",
    }

    assert forbidden.isdisjoint({item.split(".")[0] for item in combined})
    assert "browser automation" not in text.lower()
    assert "payment system" not in text.lower()
