"""LocalGitSource modes: staged (default), unstaged, and commit range."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aicr.diff.source import DiffMode, DiffSourceError, LocalGitSource


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.dev"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _commit_all(path: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)


def test_staged_mode_reads_index(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)
    source = LocalGitSource(tmp_path, mode=DiffMode.STAGED)
    files = source.get_diff_files()
    assert [f.path for f in files] == ["a.py"]


def test_unstaged_mode_reads_worktree(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    _commit_all(tmp_path, "init")
    # Modify without staging.
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")

    # Staged mode sees nothing; unstaged sees the change.
    assert LocalGitSource(tmp_path, mode=DiffMode.STAGED).get_diff_files() == []
    unstaged = LocalGitSource(tmp_path, mode=DiffMode.UNSTAGED).get_diff_files()
    assert [f.path for f in unstaged] == ["a.py"]


def test_range_mode_reads_commit_range(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    _commit_all(tmp_path, "first")
    (tmp_path / "b.py").write_text("y = 2\n")
    _commit_all(tmp_path, "second")

    source = LocalGitSource(tmp_path, mode=DiffMode.RANGE, diff_range="HEAD~1..HEAD")
    files = source.get_diff_files()
    assert [f.path for f in files] == ["b.py"]


def test_range_mode_requires_a_range() -> None:
    with pytest.raises(DiffSourceError):
        LocalGitSource(mode=DiffMode.RANGE)
