from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_BOUNDARIES = {
    "core",
    "agents",
    "runtime",
    "memory",
    "knowledge",
    "state",
    "tools",
    "execution",
    "security",
    "interfaces",
}
REQUIRED_ARCHITECTURE_DOCS = {
    "docs/00_MASTER/ZYRO_MASTER_SPEC.md",
    "docs/00_MASTER/ARCHITECTURE_INVARIANTS.md",
    "docs/01_CONTRACTS/CORE_CONTRACTS.md",
    "docs/03_SECURITY/SECURITY_PERMISSION_APPROVAL.md",
    "docs/04_RUNTIME/RUNTIME_POLICY.md",
}


def test_expected_package_boundaries_are_importable_packages() -> None:
    package_root = ROOT / "src" / "zyro"

    missing = [
        name for name in EXPECTED_BOUNDARIES if not (package_root / name / "__init__.py").is_file()
    ]

    assert not missing, f"missing ZYRO package boundaries: {sorted(missing)}"


def test_architecture_source_documents_remain_present() -> None:
    missing = [path for path in REQUIRED_ARCHITECTURE_DOCS if not (ROOT / path).is_file()]

    assert not missing, f"missing architecture source documents: {sorted(missing)}"


def test_test_layers_exist() -> None:
    for layer in ("unit", "integration", "architecture"):
        assert (ROOT / "tests" / layer).is_dir(), f"missing test layer: {layer}"
