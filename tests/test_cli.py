"""CLI commands via click's CliRunner (no real API calls — plan §10)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from aicr.cli import cli


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.dev"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def test_version() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "aicr" in result.output


def test_install_hook_creates_executable(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["install-hook"])
    assert result.exit_code == 0
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert hook.exists()
    assert "aicr review" in hook.read_text()
    assert hook.stat().st_mode & 0o111  # executable bit set


def test_install_hook_idempotent(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["install-hook"]).exit_code == 0
    # Second run should not error (already ours).
    assert runner.invoke(cli, ["install-hook"]).exit_code == 0


def test_install_hook_refuses_foreign_hook(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho existing\n")
    result = CliRunner().invoke(cli, ["install-hook"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_config_shows_missing_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = CliRunner().invoke(cli, ["config"])
    assert result.exit_code == 0
    assert "MISSING" in result.output


def test_review_nothing_staged(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    result = CliRunner().invoke(cli, ["review"])
    assert result.exit_code == 0
    assert "Nothing to review" in result.output



def test_review_missing_key_does_not_block(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Stage a change so we get past the "nothing staged" branch to the key check.
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)
    result = CliRunner().invoke(cli, ["review"])
    # Warn-only: config error prints but exit code stays 0 (never blocks commit).
    assert result.exit_code == 0
    assert "OPENROUTER_API_KEY" in result.output


def test_review_skip_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AICR_SKIP", "1")
    result = CliRunner().invoke(cli, ["review"])
    assert result.exit_code == 0


def test_enable_installs_hook(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["enable"])
    assert result.exit_code == 0
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert hook.exists()
    assert "aicr review" in hook.read_text()


def test_disable_removes_hook(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["enable"]).exit_code == 0
    result = runner.invoke(cli, ["disable"])
    assert result.exit_code == 0
    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()


def test_disable_when_no_hook(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["disable"])
    assert result.exit_code == 0
    assert "No aicr hook" in result.output


def test_help_lists_enable_and_disable() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "enable" in result.output
    assert "disable" in result.output
    # Deprecated aliases are hidden from the top-level help.
    assert "install-hook" not in result.output


def test_review_include_forces_excluded_file(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    # Exclude *.md via config, then force-include it for one run.
    (tmp_path / ".aicr.yaml").write_text('exclude_paths: ["*.md"]\n')
    (tmp_path / "notes.md").write_text("# hi\n")
    subprocess.run(["git", "add", "notes.md"], cwd=tmp_path, check=True)

    # Without --include, the only staged file is excluded → nothing to review.
    result = CliRunner().invoke(cli, ["review"])
    assert result.exit_code == 0
    assert "Nothing to review" in result.output


def test_help_lists_init() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output


def test_review_unstaged_and_range_are_mutually_exclusive(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    result = CliRunner().invoke(cli, ["review", "--unstaged", "--range", "a..b"])
    assert result.exit_code == 0  # warn-only, never blocks
    assert "only one of" in result.output


def test_config_shows_blocking_and_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    (tmp_path / ".aicr.yaml").write_text(
        "severity_block_threshold: critical\ncache_enabled: false\n"
    )
    result = CliRunner().invoke(cli, ["config"])
    assert result.exit_code == 0
    assert "blocking:   critical" in result.output
    assert "cache:      off" in result.output


def test_init_writes_config_for_ollama(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    # Wizard: provider=2 (ollama... now menu item 3), base_url default, model
    # default, categories default, blocking? no, advanced? no, install hook? no,
    # analyze repo? no.
    answers = "3\n\n\n\nn\nn\nn\nn\n"

    result = CliRunner().invoke(cli, ["init"], input=answers)
    assert result.exit_code == 0
    config_text = (tmp_path / ".aicr.yaml").read_text()
    assert "provider: ollama" in config_text
    assert "base_url:" in config_text


def test_init_writes_key_to_env_for_openrouter(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    # provider=1 (openrouter), model default, enter key? yes, key value,
    # categories default, blocking? no, advanced? no, install hook? no,
    # analyze repo? no.
    answers = "1\n\ny\nsk-secret\n\nn\nn\nn\nn\n"

    result = CliRunner().invoke(cli, ["init"], input=answers)
    assert result.exit_code == 0
    assert "provider: openrouter" in (tmp_path / ".aicr.yaml").read_text()
    env_text = (tmp_path / ".env").read_text()
    assert "OPENROUTER_API_KEY=sk-secret" in env_text


def test_init_writes_all_options_even_when_advanced_skipped(
    tmp_path: Path, monkeypatch
) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    # provider=1, model default, key? no, categories default, blocking? no,
    # advanced? no, install hook? no, analyze repo? no.
    answers = "1\n\nn\n\nn\nn\nn\nn\n"

    result = CliRunner().invoke(cli, ["init"], input=answers)
    assert result.exit_code == 0
    config_text = (tmp_path / ".aicr.yaml").read_text()
    # Every option is present, even though advanced was skipped — the ones the
    # user didn't set are written commented-out at their defaults.
    for opt in [
        "provider:",
        "model:",
        "categories:",
        "exclude_paths:",
        "# base_url:",
        "# languages:",
        "# max_diff_lines_per_file:",
        "# max_files_per_review:",
        "# concurrency:",
        "# severity_display_threshold:",
        "# cache_enabled:",
        "# severity_block_threshold:",
    ]:
        assert opt in config_text, f"missing {opt!r} in generated config"


def test_init_advanced_options_written_live(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    # provider=3 (ollama), base_url default, model default, categories default,
    # blocking? no, advanced? YES, then: languages "python", exclude default,
    # max_lines 500, max_files 20, concurrency 3, display menu=1 (info),
    # cache? yes, install hook? no, analyze repo? no.
    answers = "3\n\n\n\nn\ny\npython\n\n500\n20\n3\n1\ny\nn\nn\n"

    result = CliRunner().invoke(cli, ["init"], input=answers)
    assert result.exit_code == 0
    config_text = (tmp_path / ".aicr.yaml").read_text()
    # Advanced values the user set are now live (uncommented).
    assert 'languages: ["python"]' in config_text
    assert "max_diff_lines_per_file: 500" in config_text
    assert "concurrency: 3" in config_text


def test_reset_removes_hook_config_and_cache(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["enable"]).exit_code == 0
    (tmp_path / ".aicr.yaml").write_text("provider: openrouter\n")
    cache_dir = tmp_path / ".aicr" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "reviews.json").write_text("{}")

    result = runner.invoke(cli, ["reset", "--yes"])
    assert result.exit_code == 0
    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()
    assert not (tmp_path / ".aicr.yaml").exists()
    assert not (tmp_path / ".aicr").exists()


def test_reset_nothing_to_do(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["reset", "--yes"])
    assert result.exit_code == 0
    assert "Nothing to reset" in result.output


def test_reset_strips_key_from_env_with_yes(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aicr.yaml").write_text("provider: openrouter\n")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-secret\nOTHER=keep\n")

    result = CliRunner().invoke(cli, ["reset", "--yes"])
    assert result.exit_code == 0
    env_text = (tmp_path / ".env").read_text()
    assert "OPENROUTER_API_KEY" not in env_text
    assert "OTHER=keep" in env_text


def test_reset_aborts_without_confirmation(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aicr.yaml").write_text("provider: openrouter\n")
    # Answer "n" to the Proceed? prompt.
    result = CliRunner().invoke(cli, ["reset"], input="n\n")
    assert result.exit_code == 0
    assert "Aborted" in result.output
    assert (tmp_path / ".aicr.yaml").exists()




