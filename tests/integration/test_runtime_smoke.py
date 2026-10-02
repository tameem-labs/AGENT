import os
import subprocess
import sys

import pytest

import zyro
from zyro.runtime.bootstrap import initialize_runtime


def test_package_import_and_runtime_initialization_need_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keeping this list explicit ensures commonly named credentials cannot affect startup.
    for variable in (
        "ZYRO_ENV",
        "ZYRO_LOG_LEVEL",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)

    runtime = initialize_runtime()

    assert zyro.__version__ == "0.3.0"
    assert runtime.config.environment == "development"


def test_module_entry_point_runs() -> None:
    clean_environment = {
        key: value for key, value in os.environ.items() if key not in {"ZYRO_ENV", "ZYRO_LOG_LEVEL"}
    }
    result = subprocess.run(
        [sys.executable, "-m", "zyro"],
        check=False,
        capture_output=True,
        text=True,
        env=clean_environment,
    )

    assert result.returncode == 0, result.stderr
    assert "ZYRO foundation runtime initialized" in result.stdout
