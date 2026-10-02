from pathlib import Path

import pytest

from zyro.core.config import AppConfig, ConfigurationError, load_config


def test_safe_defaults_require_no_environment_or_api_key() -> None:
    assert load_config(environ={}) == AppConfig(environment="development", log_level="INFO")


def test_environment_overrides_file_settings(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.toml"
    config_file.write_text('[zyro]\nenvironment = "test"\nlog_level = "WARNING"\n')

    config = load_config(config_file, {"ZYRO_LOG_LEVEL": "debug"})

    assert config.environment == "test"
    assert config.log_level == "DEBUG"


def test_unknown_setting_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.toml"
    config_file.write_text('[zyro]\napi_key = "must-not-be-accepted"\n')

    with pytest.raises(ConfigurationError, match="unknown configuration"):
        load_config(config_file, {})


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="log_level"):
        load_config(environ={"ZYRO_LOG_LEVEL": "verbose"})


def test_invalid_toml_value_type_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.toml"
    config_file.write_text("[zyro]\nenvironment = 42\n")

    with pytest.raises(ConfigurationError, match="environment"):
        load_config(config_file, {})
