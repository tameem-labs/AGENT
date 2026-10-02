from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from zyro.tools.executor import ToolExecutor
from zyro.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "zyro"


def test_tool_executor_requires_an_explicit_authorizer_dependency() -> None:
    with pytest.raises(TypeError):
        ToolExecutor(ToolRegistry())  # type: ignore[call-arg]

    parameters = inspect.signature(ToolExecutor).parameters
    assert tuple(parameters) == ("registry", "authorizer")


def test_handler_invocation_is_lexically_after_authorization_boundary() -> None:
    source = (SRC / "tools" / "executor.py").read_text()
    tree = ast.parse(source)
    authorize_lines: list[int] = []
    handler_lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "authorize":
            authorize_lines.append(node.lineno)
        if (
            node.func.attr == "execute"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "handler"
        ):
            handler_lines.append(node.lineno)

    assert len(authorize_lines) == 1
    assert len(handler_lines) == 1
    assert authorize_lines[0] < handler_lines[0]


def test_security_layers_remain_distinct_modules() -> None:
    permission = (SRC / "security" / "permission.py").read_text()
    approval = (SRC / "security" / "approval.py").read_text()
    policy = (SRC / "security" / "policy.py").read_text()

    assert "Approval" not in permission
    assert "PermissionStore" not in approval
    assert "create_request" not in policy
    assert "Permission(" not in policy


def test_verification_does_not_execute_tools_or_complete_tasks_directly() -> None:
    source = (SRC / "execution" / "verification.py").read_text()

    assert "ToolExecutor" not in source
    assert ".execute_tool(" not in source
    assert ".mark_verified(" not in source
    assert "TaskStatus.DONE" not in source
