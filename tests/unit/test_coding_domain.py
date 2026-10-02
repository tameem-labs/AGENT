"""Unit tests for the Coding Department tools and handlers."""

from __future__ import annotations

from pathlib import Path

from zyro.agents.registry import AgentRegistry
from zyro.domains.coding import (
    ASTAnalysisHandler,
    CodingProjectDiscoveryHandler,
    SandboxedCommandRunnerHandler,
    register_coding_agents,
)
from zyro.tools.contracts import ToolExecutionContext


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("coding.tool", "req-1", "task-1", "agent-1", "inst-1", "corr-1")


def test_coding_project_discovery(tmp_path: Path) -> None:
    handler = CodingProjectDiscoveryHandler(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test-pkg'\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    res = handler.execute(_ctx(), {"path": str(tmp_path)})
    assert res.succeeded
    assert res.output is not None
    assert res.output["language"] == "python"
    assert res.output["has_git"] is True

    # Invalid path
    err_res = handler.execute(_ctx(), {"path": str(tmp_path / "nonexistent")})
    assert not err_res.succeeded
    assert err_res.error is not None
    assert err_res.error.code == "invalid_path"


def test_ast_analysis(tmp_path: Path) -> None:
    handler = ASTAnalysisHandler(tmp_path)
    py_file = tmp_path / "sample.py"
    code_content = (
        "import os\n"
        "from pathlib import Path\n\n"
        "class SampleClass:\n"
        "    def hello(self):\n"
        "        pass\n"
    )
    py_file.write_text(code_content, encoding="utf-8")

    res = handler.execute(_ctx(), {"file_path": "sample.py"})
    assert res.succeeded
    assert res.output is not None
    assert res.output["syntax_valid"] is True
    symbols = res.output["symbols"]
    symbol_names = [s["name"] for s in symbols]
    assert "SampleClass" in symbol_names
    assert "hello" in symbol_names
    imports = res.output["import_modules"]
    assert "os" in imports
    assert "pathlib" in imports

    # Sandbox escape
    escape_res = handler.execute(_ctx(), {"file_path": "../../outside.py"})
    assert not escape_res.succeeded
    assert escape_res.error is not None
    assert escape_res.error.code == "sandbox_escape"


def test_sandboxed_command_runner(tmp_path: Path) -> None:
    handler = SandboxedCommandRunnerHandler(tmp_path)

    # Disallowed command
    disallowed = handler.execute(_ctx(), {"command": "curl", "args": ["http://example.com"]})
    assert not disallowed.succeeded
    assert disallowed.error is not None
    assert disallowed.error.code == "command_denied"

    # Injection attempt
    injection = handler.execute(
        _ctx(), {"command": "python", "args": ["-c", "print(1); rm -rf /"]}
    )
    assert not injection.succeeded
    assert injection.error is not None
    assert injection.error.code == "injection_denied"

    # Safe python command
    safe_run = handler.execute(
        _ctx(), {"command": "python", "args": ["-c", "print('safe_output')"]}
    )
    assert safe_run.succeeded
    assert safe_run.output is not None
    assert safe_run.output["passed"] is True
    assert "safe_output" in safe_run.output["stdout"]


def test_register_coding_agents() -> None:
    reg = AgentRegistry()
    register_coding_agents(reg)
    assert reg.get("coding.discovery") is not None
    assert reg.get("coding.analysis") is not None
    assert reg.get("coding.testing") is not None
    assert reg.get("coding.review") is not None
