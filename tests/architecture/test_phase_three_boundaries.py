import ast
from dataclasses import fields
from pathlib import Path

from zyro.models.contracts import ModelDefinition
from zyro.tools.contracts import ToolDefinition

ROOT = Path(__file__).resolve().parents[2]
AGENT_SOURCE = ROOT / "src" / "zyro" / "agents"
PHASE_THREE_SOURCE = [
    ROOT / "src" / "zyro" / "models",
    ROOT / "src" / "zyro" / "tools",
    ROOT / "src" / "zyro" / "runtime" / "agent_runtime.py",
]
PROVIDER_NAMES = {"gemini", "openai", "anthropic", "google"}
FORBIDDEN_FUTURE_IMPORTS = {
    "zyro.memory",
    "zyro.knowledge",
    "zyro.state",
}


def _python_files(path: Path) -> list[Path]:
    return [path] if path.is_file() else sorted(path.glob("*.py"))


def test_agents_do_not_hardcode_or_import_model_providers() -> None:
    for source_file in _python_files(AGENT_SOURCE):
        source = source_file.read_text().lower()
        assert not PROVIDER_NAMES.intersection(source.split()), source_file
        tree = ast.parse(source_file.read_text())
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert "zyro.models.provider" not in imports
        assert "zyro.models.router" not in imports


def test_only_router_owns_provider_registry_lookup_in_product_source() -> None:
    importers: list[Path] = []
    for source_file in (ROOT / "src" / "zyro").rglob("*.py"):
        if "ProviderRegistry" in source_file.read_text():
            importers.append(source_file.relative_to(ROOT))

    assert importers == [
        Path("src/zyro/models/provider.py"),
        Path("src/zyro/models/router.py"),
    ]


def test_model_and_tool_definitions_have_no_secret_storage_fields() -> None:
    forbidden = {"secret", "password", "credential", "api_key", "access_token"}

    for contract in (ModelDefinition, ToolDefinition):
        names = {field.name for field in fields(contract)}
        assert not names.intersection(forbidden)


def test_phase_three_does_not_import_forbidden_future_subsystems() -> None:
    for source_root in PHASE_THREE_SOURCE:
        for source_file in _python_files(source_root):
            tree = ast.parse(source_file.read_text())
            imports = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            }
            assert not imports.intersection(FORBIDDEN_FUTURE_IMPORTS), source_file


def test_tool_handlers_are_invoked_only_behind_tool_executor() -> None:
    invokers: list[Path] = []
    for source_file in (ROOT / "src" / "zyro").rglob("*.py"):
        if "registered.handler.execute(context, call.arguments)" in source_file.read_text():
            invokers.append(source_file.relative_to(ROOT))

    assert invokers == [Path("src/zyro/tools/executor.py")]
