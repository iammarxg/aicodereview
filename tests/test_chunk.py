"""Tests for large-file chunking (B1): splitting, line-number preservation, dedupe."""

from __future__ import annotations

import asyncio

from aicr.config import Config
from aicr.diff.chunk import chunk_diff_file, dedupe_comments
from aicr.engine import run_review
from aicr.models import DiffFile, DiffHunk, DiffLine, ReviewComment
from tests.conftest import FakeProvider


def _big_file(added_lines: int, path: str = "big.py") -> DiffFile:
    """A file with ``added_lines`` added lines, numbered 1..N."""
    lines = [
        DiffLine(kind="added", line_no=i, content=f"line {i}")
        for i in range(1, added_lines + 1)
    ]
    return DiffFile(path=path, language="python", hunks=[DiffHunk(start_line=1, lines=lines)])


def test_small_file_is_not_split() -> None:
    f = _big_file(10)
    chunks = chunk_diff_file(f, max_added_lines=50)
    assert chunks == [f]


def test_large_file_splits_into_multiple_chunks() -> None:
    f = _big_file(100)
    chunks = chunk_diff_file(f, max_added_lines=30, overlap=0)
    assert len(chunks) > 1
    # Every chunk keeps the same path so downstream treats them as one file.
    assert all(c.path == "big.py" for c in chunks)
    # No chunk exceeds the budget.
    assert all(c.added_line_count() <= 30 for c in chunks)


def test_chunks_preserve_real_line_numbers() -> None:
    f = _big_file(100)
    chunks = chunk_diff_file(f, max_added_lines=30, overlap=0)
    all_line_nos = [
        line.line_no for c in chunks for h in c.hunks for line in h.lines
    ]
    # With no overlap the union of chunk line numbers is exactly 1..100.
    assert sorted(set(all_line_nos)) == list(range(1, 101))


def test_overlap_repeats_boundary_lines() -> None:
    f = _big_file(100)
    no_overlap = chunk_diff_file(f, max_added_lines=30, overlap=0)
    with_overlap = chunk_diff_file(f, max_added_lines=30, overlap=10)
    # Overlap re-includes boundary lines, so it needs at least as many chunks.
    assert len(with_overlap) >= len(no_overlap)
    total_no = sum(c.added_line_count() for c in no_overlap)
    total_with = sum(c.added_line_count() for c in with_overlap)
    assert total_with > total_no  # boundary lines counted twice


def test_zero_max_returns_original() -> None:
    f = _big_file(100)
    assert chunk_diff_file(f, max_added_lines=0) == [f]


def test_dedupe_removes_identical_boundary_comments() -> None:
    c1 = ReviewComment(file="a.py", line=30, category="bug", comment="off-by-one")
    c2 = ReviewComment(file="a.py", line=30, category="bug", comment="off-by-one")  # dup
    c3 = ReviewComment(file="a.py", line=31, category="bug", comment="off-by-one")  # diff line
    unique = dedupe_comments([c1, c2, c3])
    assert len(unique) == 2
    assert unique[0] is c1  # first occurrence wins, order preserved


def test_engine_chunks_large_file_end_to_end() -> None:
    # One 100-line file, budget 30 → engine should chunk it, review each chunk,
    # count it as ONE logical file, and dedupe overlapping comments.
    dup = ReviewComment(file="big.py", line=5, category="bug", comment="same issue")
    provider = FakeProvider({"big.py": [dup]})
    config = Config(max_diff_lines_per_file=30, cache_enabled=False)

    result = asyncio.run(run_review(provider, [_big_file(100)], config))

    # Reviewed as one logical file despite multiple provider calls.
    assert result.files_reviewed == 1
    assert len(provider.calls) > 1  # actually split into multiple chunk calls
    # The identical comment returned per chunk is collapsed to one.
    assert len(result.comments) == 1
    assert result.skipped_too_large == 0


def test_engine_skips_large_file_when_chunking_disabled() -> None:
    provider = FakeProvider()
    config = Config(max_diff_lines_per_file=30, chunk_large_files=False, cache_enabled=False)
    result = asyncio.run(run_review(provider, [_big_file(100)], config))
    assert result.files_reviewed == 0
    assert result.skipped_too_large == 1
    assert provider.calls == []
