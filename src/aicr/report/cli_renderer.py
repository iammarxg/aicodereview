"""Colored, grouped-by-file terminal output for a ``ReviewResult``.

Formatting logic (grouping, ordering, summary text, the usage bar, and the
block decision) is kept separate from color application so it can be tested
independent of ANSI codes (plan §10).
"""

from __future__ import annotations

from collections import defaultdict

from aicr.models import AccountUsage, ReviewComment, ReviewResult, Severity

# Severity ordering for sorting (most serious first) and threshold filtering.
SEVERITY_RANK: dict[Severity, int] = {"critical": 0, "warning": 1, "info": 2}

_RESET = "\033[0m"
_COLORS = {
    "critical": "\033[31m",  # red
    "warning": "\033[33m",  # yellow
    "info": "\033[36m",  # cyan
}
_BOLD = "\033[1m"
_DIM = "\033[2m"

_SEVERITY_LABEL: dict[Severity, str] = {
    "critical": "CRITICAL",
    "warning": "WARNING",
    "info": "INFO",
}

_BAR_WIDTH = 10  # cells in the mini usage bar


def _colorize(text: str, code: str, use_color: bool) -> str:
    return f"{code}{text}{_RESET}" if use_color else text


def _threshold_rank(threshold: str) -> int:
    for severity, rank in SEVERITY_RANK.items():
        if severity == threshold:
            return rank
    return SEVERITY_RANK["info"]


def has_blocking_comment(comments: list[ReviewComment], threshold: Severity) -> bool:
    """True if any comment is at or above ``threshold`` (for blocking mode).

    Pure logic, no color/exit handling — the CLI turns this into an exit code so
    the decision itself stays unit-testable.
    """
    limit = SEVERITY_RANK[threshold]
    return any(SEVERITY_RANK[c.severity] <= limit for c in comments)


def filter_and_group(
    comments: list[ReviewComment],
    display_threshold: str = "info",
) -> dict[str, list[ReviewComment]]:
    """Filter by severity threshold, then group by file and sort within each file.

    Within a file, comments are ordered by line number, then by severity so the
    most serious issue on a line shows first. Pure logic, no color — testable.
    """
    max_rank = _threshold_rank(display_threshold)
    grouped: dict[str, list[ReviewComment]] = defaultdict(list)
    for comment in comments:
        if SEVERITY_RANK[comment.severity] <= max_rank:
            grouped[comment.file].append(comment)

    for file_comments in grouped.values():
        file_comments.sort(key=lambda c: (c.line, SEVERITY_RANK[c.severity]))
    return dict(sorted(grouped.items()))


def format_summary_line(result: ReviewResult) -> str:
    """One-line summary of skips and counts, independent of color."""
    parts = [f"{result.files_reviewed} file(s) reviewed"]
    if result.cached:
        parts.append(f"{result.cached} from cache")
    if result.skipped_binary:
        parts.append(f"{result.skipped_binary} binary skipped")
    if result.skipped_too_large:
        parts.append(f"{result.skipped_too_large} too large skipped")
    if result.skipped_errors:
        parts.append(f"{result.skipped_errors} errored")
    return ", ".join(parts)


def format_usage_bar(usage: AccountUsage, *, width: int = _BAR_WIDTH) -> str:
    """Render account credit consumption as a mini progress bar.

    With a known limit: ``[████░░░░░░] 42% · 0.42/1.00 credits ($)``.
    Without a limit: ``0.42 credits ($) used`` (no percentage possible).
    """
    percent = usage.percent
    if percent is None:
        return f"{usage.used:g} {usage.label} used"
    filled = int(round((percent / 100.0) * width))
    filled = max(0, min(width, filled))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {percent:.0f}% · {usage.used:g}/{usage.limit:g} {usage.label}"


def format_tokens(result: ReviewResult) -> str | None:
    """One-line token summary, or None if the provider reported no usage."""
    usage = result.token_usage
    if usage.is_empty:
        return None
    return (
        f"~{usage.total_tokens} tokens "
        f"({usage.prompt_tokens} in / {usage.completion_tokens} out)"
    )


def render(result: ReviewResult, *, use_color: bool = True, display_threshold: str = "info") -> str:
    """Render a full review report as a terminal-ready string."""
    grouped = filter_and_group(result.comments, display_threshold)
    lines: list[str] = []

    header = _colorize("AI Code Review", _BOLD, use_color)
    lines.append(f"{header}  ({result.provider}:{result.model})")
    lines.append("")

    if not grouped:
        lines.append(_colorize("No issues found in the staged changes. 🎉", _DIM, use_color))
    else:
        for file_path, comments in grouped.items():
            lines.append(_colorize(file_path, _BOLD, use_color))
            for c in comments:
                label = _colorize(
                    f"[{_SEVERITY_LABEL[c.severity]}]", _COLORS[c.severity], use_color
                )
                loc = _colorize(f"L{c.line}", _DIM, use_color)
                lines.append(f"  {label} {loc} ({c.category}) {c.comment}")
                if c.suggestion:
                    suggestion = _colorize(f"    ↳ {c.suggestion}", _DIM, use_color)
                    lines.append(suggestion)
            lines.append("")

    total = sum(len(v) for v in grouped.values())
    footer = f"{total} comment(s) · " + format_summary_line(result)
    footer += f" · {result.duration_seconds:.1f}s"
    lines.append(_colorize(footer, _DIM, use_color))

    tokens = format_tokens(result)
    if tokens:
        lines.append(_colorize(tokens, _DIM, use_color))
    if result.account_usage is not None:
        bar = "API usage: " + format_usage_bar(result.account_usage)
        lines.append(_colorize(bar, _DIM, use_color))

    return "\n".join(lines)

