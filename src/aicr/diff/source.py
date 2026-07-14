"""``DiffSource`` interface + ``LocalGitSource`` (plan §4.2, review §2.1).

The interface is intentionally *sync*: both git-subprocess and (future) GitHub
fetch are fine as blocking I/O. Only the LLM provider layer is async. Keeping
this decoupled means a ``GitHubPRSource`` in v2 is a new class, not a rewrite —
nothing downstream knows where the diff came from.

``LocalGitSource`` supports three modes so review isn't limited to the
pre-commit moment: the staged index (default), unstaged working-tree changes, or
an arbitrary commit range (e.g. ``main..HEAD`` to review a branch before push).
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path

from aicr.diff.parser import parse_diff
from aicr.models import DiffFile


class DiffSourceError(Exception):
    """Raised for user-facing diff-acquisition problems (no traceback shown)."""


class DiffMode(StrEnum):
    """What set of changes ``LocalGitSource`` should read."""


    STAGED = "staged"  # git diff --cached (the pre-commit default)
    UNSTAGED = "unstaged"  # git diff (working tree vs. index)
    RANGE = "range"  # git diff <range>, e.g. main..HEAD


class DiffSource(ABC):
    """A source of changed files + hunks to review."""

    @abstractmethod
    def get_diff_files(self) -> list[DiffFile]:
        """Return the set of changed files + hunks to review."""
        ...


class LocalGitSource(DiffSource):
    """Reads a diff from a local git repository (staged, unstaged, or a range)."""

    def __init__(
        self,
        repo_dir: Path | None = None,
        *,
        mode: DiffMode = DiffMode.STAGED,
        diff_range: str | None = None,
        exclude_paths: list[str] | None = None,
        force_include: list[str] | None = None,
        context_lines: int = 3,
    ) -> None:
        if mode is DiffMode.RANGE and not diff_range:
            raise DiffSourceError("A commit range is required for range mode (e.g. main..HEAD).")
        self.repo_dir = repo_dir or Path.cwd()
        self.mode = mode
        self.diff_range = diff_range
        self.exclude_paths = exclude_paths or []
        # Patterns that override an exclusion for a single run (e.g. --include).
        self.force_include = force_include or []
        self.context_lines = context_lines

    def _run_git(self, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise DiffSourceError("git is not installed or not on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise DiffSourceError(f"git command failed: {stderr or exc}") from exc
        return result.stdout

    def _diff_args(self) -> list[str]:
        """Build the ``git diff`` argument list for the selected mode."""
        common = [f"--unified={self.context_lines}", "--no-color"]
        if self.mode is DiffMode.STAGED:
            return ["diff", "--cached", *common]
        if self.mode is DiffMode.UNSTAGED:
            return ["diff", *common]
        # RANGE — validated in __init__ that diff_range is set.
        assert self.diff_range is not None
        return ["diff", self.diff_range, *common]

    def get_raw_diff(self) -> str:
        """Return the raw unified diff for the configured mode."""
        # Verify we're inside a work tree first for a clean error message.
        inside = self._run_git("rev-parse", "--is-inside-work-tree").strip()
        if inside != "true":
            raise DiffSourceError("Not inside a git repository.")
        return self._run_git(*self._diff_args())

    def get_diff_files(self) -> list[DiffFile]:
        raw = self.get_raw_diff()
        if not raw.strip():
            return []
        return parse_diff(
            raw,
            exclude_paths=self.exclude_paths,
            force_include=self.force_include,
        )
