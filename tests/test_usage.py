"""Token/account usage: aggregation model, engine wiring, and the render bar."""

from __future__ import annotations

from aicr.config import Config
from aicr.engine import run_review
from aicr.models import AccountUsage, DiffFile, ReviewResult, TokenUsage
from aicr.report.cli_renderer import format_tokens, format_usage_bar, render
from tests.conftest import FakeProvider


def _file(path: str = "a.py") -> DiffFile:
    return DiffFile.model_validate(
        {
            "path": path,
            "hunks": [
                {"start_line": 1, "lines": [{"kind": "added", "line_no": 1, "content": "x"}]}
            ],
        }
    )


def _result(**kw) -> ReviewResult:
    defaults = dict(files_reviewed=1, provider="fake", model="m", duration_seconds=0.1)
    defaults.update(kw)
    return ReviewResult(**defaults)


# --- model ---------------------------------------------------------------- #


def test_token_usage_add_accumulates() -> None:
    total = TokenUsage()
    total.add(TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15))
    total.add(TokenUsage(prompt_tokens=2, completion_tokens=3))  # no total -> summed
    assert total.prompt_tokens == 12
    assert total.completion_tokens == 8
    assert total.total_tokens == 20


def test_account_usage_percent() -> None:
    assert AccountUsage(used=5, limit=10).percent == 50.0
    assert AccountUsage(used=5, limit=None).percent is None
    # Clamped to 100 even if over.
    assert AccountUsage(used=30, limit=10).percent == 100.0


# --- engine wiring -------------------------------------------------------- #


async def test_engine_aggregates_tokens_and_account_usage() -> None:
    provider = FakeProvider(
        {},
        account_usage=AccountUsage(used=0.4, limit=1.0, label="credits ($)"),
        tokens_per_call=7,
    )
    result = await run_review(provider, [_file("a.py"), _file("b.py")], Config(api_key="t"))
    assert result.token_usage.prompt_tokens == 14  # 7 per call * 2 files
    assert result.account_usage is not None
    assert result.account_usage.percent == 40.0


# --- renderer ------------------------------------------------------------- #


def test_format_usage_bar_with_limit() -> None:
    bar = format_usage_bar(AccountUsage(used=0.4, limit=1.0, label="credits ($)"), width=10)
    assert "40%" in bar
    assert "█" in bar and "░" in bar


def test_format_usage_bar_without_limit() -> None:
    bar = format_usage_bar(AccountUsage(used=0.4, limit=None, label="credits ($)"))
    assert "used" in bar
    assert "%" not in bar


def test_format_tokens_none_when_empty() -> None:
    assert format_tokens(_result()) is None


def test_render_includes_usage_lines() -> None:
    result = _result(
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        account_usage=AccountUsage(used=0.4, limit=1.0, label="credits ($)"),
    )
    out = render(result, use_color=False)
    assert "15 tokens" in out
    assert "API usage:" in out
    assert "40%" in out
