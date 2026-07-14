"""Report rendering: grouping/ordering/threshold logic, independent of color."""

from __future__ import annotations

from aicr.models import ReviewComment, ReviewResult
from aicr.report import json_renderer
from aicr.report.cli_renderer import filter_and_group, render


def _result(comments: list[ReviewComment], **kw) -> ReviewResult:
    defaults = dict(
        files_reviewed=1,
        comments=comments,
        provider="fake",
        model="test",
        duration_seconds=0.5,
    )
    defaults.update(kw)
    return ReviewResult(**defaults)


def test_grouping_by_file_and_line_order() -> None:
    comments = [
        ReviewComment(file="b.py", line=5, category="bug", comment="b5"),
        ReviewComment(file="a.py", line=10, category="bug", comment="a10"),
        ReviewComment(file="a.py", line=2, category="style", comment="a2"),
    ]
    grouped = filter_and_group(comments)
    assert list(grouped.keys()) == ["a.py", "b.py"]  # files sorted
    assert [c.line for c in grouped["a.py"]] == [2, 10]  # lines sorted within file


def test_severity_threshold_filters() -> None:
    comments = [
        ReviewComment(file="a.py", line=1, category="bug", severity="info", comment="i"),
        ReviewComment(file="a.py", line=2, category="bug", severity="warning", comment="w"),
        ReviewComment(file="a.py", line=3, category="bug", severity="critical", comment="c"),
    ]
    grouped = filter_and_group(comments, display_threshold="warning")
    kept = [c.severity for c in grouped["a.py"]]
    assert "info" not in kept
    assert set(kept) == {"warning", "critical"}


def test_render_no_issues_is_friendly() -> None:
    out = render(_result([]), use_color=False)
    assert "No issues found" in out


def test_render_includes_comment_and_suggestion() -> None:
    comments = [
        ReviewComment(
            file="a.py",
            line=4,
            category="bug",
            severity="critical",
            comment="Possible division by zero",
            suggestion="Guard against b == 0",
        )
    ]
    out = render(_result(comments), use_color=False)
    assert "a.py" in out
    assert "L4" in out
    assert "Possible division by zero" in out
    assert "Guard against b == 0" in out
    assert "CRITICAL" in out


def test_render_no_color_has_no_ansi() -> None:
    comments = [ReviewComment(file="a.py", line=1, category="bug", comment="x")]
    out = render(_result(comments), use_color=False)
    assert "\033[" not in out


def test_summary_counts_skips() -> None:
    out = render(
        _result([], skipped_binary=2, skipped_too_large=1, skipped_errors=1),
        use_color=False,
    )
    assert "2 binary skipped" in out
    assert "1 too large skipped" in out
    assert "1 errored" in out


def test_json_renderer_roundtrips() -> None:
    comments = [ReviewComment(file="a.py", line=1, category="bug", comment="x")]
    payload = json_renderer.render(_result(comments))
    assert '"file": "a.py"' in payload
    assert '"line": 1' in payload
