"""Pydantic data models — the only coupling between pipeline stages.

Nothing downstream (prompts, providers, report) should know whether a diff came
from local git or GitHub, or which LLM produced a comment. Everything flows
through ``DiffFile`` (input) and ``ReviewComment`` (output).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

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
    """A single line-mapped review comment produced by an LLM provider.

    ``confidence`` (0.0–1.0) is the model's self-rated certainty that the issue is
    real. It is the backstop for the anti-hallucination prompt work (v0.4.2): the
    model is told to report only findings it's highly sure about, and
    ``parse_comments`` drops anything below a threshold even if the model ignores
    that instruction. It's an internal signal used for filtering — not shown in the
    report. Comments missing the field default to ``1.0`` so older/again-simple
    providers aren't silently discarded.
    """

    file: str
    line: int = Field(..., description="New-file line number the comment refers to.")
    category: Category
    severity: Severity = "info"
    comment: str
    suggestion: str | None = None
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Model self-rated certainty (0–1) that the issue is real.",
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: object) -> object:
        """Accept a percentage (e.g. 95) or a fraction (0.95); normalize to 0–1.

        Models are inconsistent: some emit ``0.95``, some ``95``. Anything >1 is
        treated as a percentage. Unparseable values fall back to ``1.0`` (keep the
        comment) rather than dropping a possibly-valid finding on a formatting quirk.
        """
        if value is None:
            return 1.0
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 1.0
        if number > 1.0:
            number = number / 100.0
        return max(0.0, min(1.0, number))



class TokenUsage(BaseModel):
    """Token counts reported by a provider, aggregated across a review run.

    Provider-agnostic: any provider that surfaces token counts records them here
    via ``LLMProvider._record_usage`` (see ``providers/base.py``). Providers that
    don't report usage simply leave this at zero.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: TokenUsage) -> None:
        """Accumulate another call's usage into this one (in place)."""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        # Fall back to the sum if the provider didn't send a total.
        self.total_tokens += other.total_tokens or (
            other.prompt_tokens + other.completion_tokens
        )

    @property
    def is_empty(self) -> bool:
        return not (self.prompt_tokens or self.completion_tokens or self.total_tokens)


class AccountUsage(BaseModel):
    """Account-level credit usage/limit, when a provider exposes it.

    ``limit is None`` means "no cap / unlimited / unknown" — the renderer then
    shows the raw used amount instead of a percentage bar. This model is the
    provider-agnostic seam for the "% API usage" display: any provider that can
    report a spend/limit implements ``LLMProvider.account_usage`` to return one.
    """

    used: float = 0.0
    limit: float | None = None
    label: str = "credits"

    @property
    def percent(self) -> float | None:
        """Fraction of the limit consumed (0–100), or None if there's no limit."""
        if self.limit is None or self.limit <= 0:
            return None
        return max(0.0, min(100.0, (self.used / self.limit) * 100.0))


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

    def content_signature(self) -> str:
        """Stable text of the reviewable (added) content, for cache keys.

        Only added lines and their numbers matter — reordering context or
        touching unrelated files must not invalidate this file's cache entry
        (see ``cache.py``).
        """
        parts: list[str] = [self.path, self.language or ""]
        for hunk in self.hunks:
            for ln in hunk.lines:
                if ln.kind == "added":
                    parts.append(f"{ln.line_no}:{ln.content}")
        return "\n".join(parts)

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
    cached: int = 0
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    account_usage: AccountUsage | None = None
