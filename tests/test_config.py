"""Config loading: precedence, category normalization, friendly missing-key error."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicr.config import ConfigError, load_config


def test_defaults_when_no_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    config = load_config(cwd=tmp_path, load_env=False)
    assert config.provider == "openrouter"
    assert config.categories == ["bug", "security", "readability", "style"]
    assert config.api_key == "sk-test"


def test_missing_api_key_raises_friendly(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, load_env=False)
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_missing_key_ok_when_not_required(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = load_config(cwd=tmp_path, require_api_key=False, load_env=False)
    assert config.api_key is None


def test_yaml_categories_normalized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    (tmp_path / ".aicr.yaml").write_text(
        "categories: [bugs, style]\nmodel: some/model\n", encoding="utf-8"
    )
    config = load_config(cwd=tmp_path, load_env=False)
    assert config.categories == ["bug", "style"]  # plural -> singular
    assert config.model == "some/model"


def test_api_key_in_yaml_is_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")
    (tmp_path / ".aicr.yaml").write_text(
        "api_key: sk-should-be-ignored\n", encoding="utf-8"
    )
    config = load_config(cwd=tmp_path, load_env=False)
    assert config.api_key == "sk-from-env"


def test_malformed_yaml_raises_config_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    (tmp_path / ".aicr.yaml").write_text("categories: [unterminated\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cwd=tmp_path, load_env=False)


def test_config_found_in_parent_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    (tmp_path / ".aicr.yaml").write_text("model: parent/model\n", encoding="utf-8")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    config = load_config(cwd=sub, load_env=False)
    assert config.model == "parent/model"
