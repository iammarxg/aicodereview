"""Response parsing + the line-mapping safety net (review §2.3) — the key tests."""

from __future__ import annotations

import pytest

from aicr.diff.parser import parse_diff
from aicr.providers.base import MalformedResponseError, parse_comments


@pytest.fixture
def diff_file(single_file_diff: str):
    return parse_diff(single_file_diff)[0]


def test_in_range_comment_kept(diff_file) -> None:
    raw = '[{"file":"x","line":4,"category":"bug","severity":"warning","comment":"c"}]'
    comments = parse_comments(raw, diff_file)
    assert len(comments) == 1
    assert comments[0].line == 4
    assert comments[0].file == "calc.py"  # path forced to the real file


def test_out_of_range_comment_dropped(diff_file) -> None:
    # Line 2 is context, line 99 doesn't exist — both must be dropped.
    raw = (
        "["
        '{"file":"calc.py","line":2,"category":"bug","severity":"info","comment":"ctx"},'
        '{"file":"calc.py","line":99,"category":"bug","severity":"info","comment":"nope"}'
        "]"
    )
    assert parse_comments(raw, diff_file) == []


def test_markdown_fenced_json_is_parsed(diff_file) -> None:
    inner = '[{"file":"calc.py","line":5,"category":"style","severity":"info","comment":"c"}]'
    raw = f"```json\n{inner}\n```"
    comments = parse_comments(raw, diff_file)
    assert len(comments) == 1
    assert comments[0].line == 5


def test_prose_wrapped_array_is_extracted(diff_file) -> None:
    inner = '[{"file":"calc.py","line":4,"category":"bug","severity":"info","comment":"c"}]'
    raw = f"Here is the review:\n{inner}\nDone."
    assert len(parse_comments(raw, diff_file)) == 1



def test_empty_array_yields_no_comments(diff_file) -> None:
    assert parse_comments("[]", diff_file) == []


def test_individually_malformed_items_skipped(diff_file) -> None:
    # First item has an invalid category; it's dropped, the valid one is kept.
    raw = (
        "["
        '{"file":"calc.py","line":4,"category":"NOPE","severity":"info","comment":"c"},'
        '{"file":"calc.py","line":5,"category":"bug","severity":"info","comment":"ok"}'
        "]"
    )
    comments = parse_comments(raw, diff_file)
    assert len(comments) == 1
    assert comments[0].line == 5



def test_non_json_raises(diff_file) -> None:
    with pytest.raises(MalformedResponseError):
        parse_comments("I could not review this.", diff_file)


def test_non_array_json_raises(diff_file) -> None:
    with pytest.raises(MalformedResponseError):
        parse_comments('{"file":"calc.py"}', diff_file)


def test_missing_confidence_defaults_to_kept(diff_file) -> None:
    # No confidence field → defaults to 1.0 → kept (backward compatible with
    # providers/models that don't emit the field).
    raw = '[{"file":"calc.py","line":4,"category":"bug","severity":"info","comment":"c"}]'
    comments = parse_comments(raw, diff_file)
    assert len(comments) == 1
    assert comments[0].confidence == 1.0


def test_low_confidence_comment_dropped(diff_file) -> None:
    # A speculative, low-confidence finding is filtered out even though its line
    # is valid — the anti-hallucination backstop (v0.4.2).
    raw = (
        '[{"file":"calc.py","line":4,"category":"bug","severity":"warning",'
        '"comment":"maybe","confidence":0.4}]'
    )
    assert parse_comments(raw, diff_file) == []


def test_high_confidence_comment_kept(diff_file) -> None:
    raw = (
        '[{"file":"calc.py","line":4,"category":"bug","severity":"warning",'
        '"comment":"sure","confidence":0.95}]'
    )
    assert len(parse_comments(raw, diff_file)) == 1


def test_confidence_as_percentage_is_normalized(diff_file) -> None:
    # Models sometimes emit 95 instead of 0.95; treat >1 as a percentage.
    raw = (
        '[{"file":"calc.py","line":4,"category":"bug","severity":"info",'
        '"comment":"c","confidence":95}]'
    )
    comments = parse_comments(raw, diff_file)
    assert len(comments) == 1
    assert comments[0].confidence == 0.95


def test_confidence_at_threshold_is_kept(diff_file) -> None:
    # Exactly the minimum confidence survives (>= threshold).
    raw = (
        '[{"file":"calc.py","line":4,"category":"bug","severity":"info",'
        '"comment":"c","confidence":0.9}]'
    )
    assert len(parse_comments(raw, diff_file)) == 1

