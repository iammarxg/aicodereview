"""Pydantic data models — the only coupling between pipeline stages.

Nothing downstream (prompts, providers, report) should know whether a diff came
from local git or GitHub, or which LLM produced a comment. Everything flows
through ``DiffFile`` (input) and ``ReviewComment`` (output).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["bug", "security", "readability", "style"]
Severity = Literal["info", "warning", "critical"]

# Map plural config category names to the singular enum used in comments (review §6).
CATEGORY_ALIASES: dict[str, Category] = {
    "bug": "bug",
    "bugs": "bug",
    "security": "security",
    "readability": "readability",
    "style": "style",
}


def normalize_category(name: str) -> Category:
    """Normalize a config/user category name (e.g. ``"bugs"``) to the enum value."""
    key = name.strip().lower()
    if key not in CATEGORY_ALIASES:
        raise ValueError(
            f"Unknown category {name!r}. Valid: bugs, security, readability, style."
        )
    return CATEGORY_ALIASES[key]


class ReviewComment(BaseModel):
    """A single line-mapped review comment produced by an LLM provider."""

    file: str
    line: int = Field(..., description="New-file line number the comment refers to.")
    category: Category
    severity: Severity = "info"
    comment: str
    suggestion: str | None = None


class DiffLine(BaseModel):
    """One line inside a hunk, tagged with its origin and new-file number."""

    kind: Literal["added", "context"]
    # new-file line number; None only for removed lines (which we don't keep here)
    line_no: int
    content: str


class DiffHunk(BaseModel):
    """A contiguous block of changed lines within a file."""

    start_line: int  # first new-file line number in this hunk
    lines: list[DiffLine]

    def changed_line_numbers(self) -> set[int]:
        """New-file line numbers that were actually added/modified in this hunk."""
        return {ln.line_no for ln in self.lines if ln.kind == "added"}


class DiffFile(BaseModel):
    """A changed file plus its hunks, decoupled from the diff's origin."""

    path: str
    language: str | None = None
    hunks: list[DiffHunk] = Field(default_factory=list)
    is_binary: bool = False

    def changed_line_numbers(self) -> set[int]:
        """All new-file line numbers added/modified across every hunk."""
        result: set[int] = set()
        for hunk in self.hunks:
            result |= hunk.changed_line_numbers()
        return result

    def added_line_count(self) -> int:
        return len(self.changed_line_numbers())

    def to_prompt_text(self) -> str:
        """Render hunks for the LLM with real new-file line numbers and +/- markers.

        This is the serialization step called out in the plan review (§2.2): the
        model must *see* which lines are added vs. context so it can (a) only
        comment on added lines and (b) copy the correct line number rather than
        count. Example line: ``  42 + def foo():``.
        """
        blocks: list[str] = []
        for hunk in self.hunks:
            rendered = []
            for ln in hunk.lines:
                marker = "+" if ln.kind == "added" else " "
                rendered.append(f"{ln.line_no:>6} {marker} {ln.content}")
            blocks.append("\n".join(rendered))
        return "\n...\n".join(blocks)


class ReviewResult(BaseModel):
    """Aggregate outcome of a review run — the object every renderer consumes."""

    files_reviewed: int
    comments: list[ReviewComment] = Field(default_factory=list)
    provider: str
    model: str
    duration_seconds: float
    skipped_binary: int = 0
    skipped_too_large: int = 0
    skipped_errors: int = 0
