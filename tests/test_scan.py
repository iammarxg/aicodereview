"""Full-repo scan: DiffFile synthesis, file discovery, and the CLI command."""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from aicr.cli import cli
from aicr.scan import build_scan_file, collect_scan_files, estimate_scan_tokens


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.dev"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _add(path: Path, rel: str, content: str) -> None:
    file = path / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=path, check=True)


def test_build_scan_file_marks_all_lines_added() -> None:
    df = build_scan_file("app.py", "a = 1\nb = 2\nc = 3\n")
    assert df.path == "app.py"
    assert df.language == "python"
    # Every line is reviewable (added), numbered 1..N contiguously.
    assert df.changed_line_numbers() == {1, 2, 3}
    assert df.added_line_count() == 3


def test_build_scan_file_empty_has_no_hunks() -> None:
    df = build_scan_file("empty.py", "")
    assert df.hunks == []
    assert df.added_line_count() == 0


def test_estimate_scan_tokens_reflects_actual_files() -> None:
    # The pre-scan token number is computed from the files being scanned, and
    # includes per-file overhead — so it grows with both content and file count.
    one = [build_scan_file("a.py", "x = 1\n")]
    two = [build_scan_file("a.py", "x = 1\n"), build_scan_file("b.py", "y = 2\n")]
    assert estimate_scan_tokens(two) > estimate_scan_tokens(one)
    assert estimate_scan_tokens([]) == 0


def test_collect_scan_files_discovers_reviewable_and_applies_excludes(tmp_path: Path) -> None:

    _init_repo(tmp_path)
    _add(tmp_path, "app.py", "x = 1\n")
    _add(tmp_path, "lib/util.py", "y = 2\n")
    _add(tmp_path, "node_modules/pkg/index.js", "module.exports = 1;\n")
    _add(tmp_path, "notes.md", "# hi\n")

    files = collect_scan_files(tmp_path, exclude_paths=["*.md"])
    paths = {f.path for f in files}
    # node_modules is filtered as a heavy dir; *.md is excluded by config.
    assert paths == {"app.py", "lib/util.py"}


def test_scan_nothing_to_scan(tmp_path: Path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    result = CliRunner().invoke(cli, ["scan", "--yes"])
    assert result.exit_code == 0
    assert "Nothing to scan" in result.output


def test_scan_runs_with_fake_provider(tmp_path: Path, monkeypatch) -> None:
    from tests.conftest import FakeProvider

    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    _add(tmp_path, "app.py", "x = 1\n")

    fake = FakeProvider()
    monkeypatch.setattr("aicr.cli.build_provider", lambda config: fake)

    result = CliRunner().invoke(cli, ["scan", "--yes"])
    assert result.exit_code == 0
    # The synthesized full-content file was sent to the provider.
    assert fake.calls == ["app.py"]


def test_scan_aborts_without_confirmation(tmp_path: Path, monkeypatch) -> None:
    from tests.conftest import FakeProvider

    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    _add(tmp_path, "app.py", "x = 1\n")
    _add(tmp_path, "lib.py", "y = 2\n")

    fake = FakeProvider()
    monkeypatch.setattr("aicr.cli.build_provider", lambda config: fake)

    result = CliRunner().invoke(cli, ["scan"], input="n\n")
    assert result.exit_code == 0
    assert "Aborted" in result.output
    # The measured estimate reviews exactly one sample file; the rest are not sent.
    assert len(fake.calls) == 1


def test_scan_prints_measured_estimate(tmp_path: Path, monkeypatch) -> None:
    from tests.conftest import FakeProvider

    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    _add(tmp_path, "app.py", "x = 1\n")

    fake = FakeProvider()
    monkeypatch.setattr("aicr.cli.build_provider", lambda config: fake)

    # Confirm the scan (input "y") after the estimate is shown.
    result = CliRunner().invoke(cli, ["scan"], input="y\n")
    assert result.exit_code == 0
    assert "Estimated time:" in result.output

