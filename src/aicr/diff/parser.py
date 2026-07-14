"""Wrap ``unidiff`` to turn a raw unified diff into ``DiffFile`` objects.

Handles binary-file detection, path exclusion (glob), and per-file language
detection by extension. Line numbers come straight from unidiff's ``target_line_no``
so mapping stays correct (plan §3, review §2.3).
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from unidiff import PatchSet

from aicr.models import DiffFile, DiffHunk, DiffLine

# Extension -> language name used for prompt tuning (review §2.4).
EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".md": "markdown",
}


def detect_language(path: str) -> str | None:
    """Return a language name for a file path, or None if unknown."""
    suffix = PurePosixPath(path).suffix.lower()
    return EXTENSION_LANGUAGE.get(suffix)


def _matches(path: str, patterns: list[str]) -> bool:
    """True if ``path`` matches any glob (matches full path or basename)."""
    name = PurePosixPath(path).name
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern):
            return True
    return False


def parse_diff(
    raw_diff: str,
    exclude_paths: list[str] | None = None,
    force_include: list[str] | None = None,
) -> list[DiffFile]:
    """Parse a raw unified diff into ``DiffFile`` objects.

    Args:
        raw_diff: The unified diff text (e.g. from ``git diff --cached``).
        exclude_paths: Glob patterns for paths to drop from the result.
        force_include: Glob patterns that override an exclusion for this run
            (e.g. the ``--include`` flag), so a normally-skipped file is still
            reviewed. Force-include always wins over exclusion.

    Returns:
        One ``DiffFile`` per non-excluded file. Binary files are included with
        ``is_binary=True`` and no hunks (so the caller can count/skip them).
    """
    exclude_paths = exclude_paths or []
    force_include = force_include or []
    patch = PatchSet(raw_diff)
    files: list[DiffFile] = []

    for patched_file in patch:
        # Prefer the target (new) path; fall back to source for deletions.
        path = patched_file.path
        # Force-include wins: only skip if excluded AND not force-included.
        if _matches(path, exclude_paths) and not _matches(path, force_include):
            continue

        if patched_file.is_binary_file:
            files.append(DiffFile(path=path, language=detect_language(path), is_binary=True))
            continue

        hunks: list[DiffHunk] = []
        for hunk in patched_file:
            lines: list[DiffLine] = []
            for line in hunk:
                if line.is_removed:
                    continue  # removed lines have no new-file number; skip
                if line.target_line_no is None:
                    continue
                lines.append(
                    DiffLine(
                        kind="added" if line.is_added else "context",
                        line_no=line.target_line_no,
                        content=line.value.rstrip("\n"),
                    )
                )
            if not lines:
                continue
            hunks.append(DiffHunk(start_line=lines[0].line_no, lines=lines))

        files.append(DiffFile(path=path, language=detect_language(path), hunks=hunks))

    return files
