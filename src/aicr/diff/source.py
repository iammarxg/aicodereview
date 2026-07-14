"""``DiffSource`` interface + ``LocalGitSource`` (plan §4.2, review §2.1).

The interface is intentionally *sync*: both git-subprocess and (future) GitHub
fetch are fine as blocking I/O. Only the LLM provider layer is async. Keeping
this decoupled means a ``GitHubPRSource`` in v2 is a new class, not a rewrite —
nothing downstream knows where the diff came from.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from aicr.diff.parser import parse_diff
from aicr.models import DiffFile


class DiffSourceError(Exception):
    """Raised for user-facing diff-acquisition problems (no traceback shown)."""


class DiffSource(ABC):
    """A source of changed files + hunks to review."""

    @abstractmethod
    def get_diff_files(self) -> list[DiffFile]:
        """Return the set of changed files + hunks to review."""
        ...


class LocalGitSource(DiffSource):
    """Reads the staged diff from a local git repository (``git diff --cached``)."""

    def __init__(
        self,
        repo_dir: Path | None = None,
        *,
        exclude_paths: list[str] | None = None,
        force_include: list[str] | None = None,
        context_lines: int = 3,
    ) -> None:
        self.repo_dir = repo_dir or Path.cwd()
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

    def get_raw_diff(self) -> str:
        """Return the raw unified diff of staged changes."""
        # Verify we're inside a work tree first for a clean error message.
        inside = self._run_git("rev-parse", "--is-inside-work-tree").strip()
        if inside != "true":
            raise DiffSourceError("Not inside a git repository.")
        return self._run_git(
            "diff",
            "--cached",
            f"--unified={self.context_lines}",
            "--no-color",
        )

    def get_diff_files(self) -> list[DiffFile]:
        raw = self.get_raw_diff()
        if not raw.strip():
            return []
        return parse_diff(
            raw,
            exclude_paths=self.exclude_paths,
            force_include=self.force_include,
        )
