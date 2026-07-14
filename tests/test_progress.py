"""Engine progress callback: real per-file events, cache-aware, never fatal."""

from __future__ import annotations

from aicr.config import Config
from aicr.engine import run_review
from aicr.models import DiffFile, ReviewComment
from tests.conftest import FakeProvider


def _file(path: str) -> DiffFile:
    return DiffFile.model_validate(
        {
            "path": path,
            "hunks": [
                {"start_line": 1, "lines": [{"kind": "added", "line_no": 1, "content": "x"}]}
            ],
        }
    )


def _config(**kw) -> Config:
    return Config(api_key="test", **kw)


async def test_progress_fires_once_per_reviewed_file() -> None:
    files = [_file("a.py"), _file("b.py"), _file("c.py")]
    events: list[tuple[str, int, int]] = []

    await run_review(
        FakeProvider(),
        files,
        _config(),
        progress_callback=lambda path, done, total: events.append((path, done, total)),
    )

    assert len(events) == 3
    # Counts are monotonic and totals correct.
    assert [e[1] for e in events] == [1, 2, 3]
    assert {e[2] for e in events} == {3}
    assert {e[0] for e in events} == {"a.py", "b.py", "c.py"}


async def test_progress_skips_cached_files(tmp_path) -> None:
    from aicr.cache import ReviewCache

    files = [_file("a.py")]
    comments = {"a.py": [ReviewComment(file="a.py", line=1, category="bug", comment="c")]}

    # First run populates the cache (one progress event).
    first: list[str] = []
    await run_review(
        FakeProvider(comments),
        files,
        _config(),
        cache=ReviewCache(tmp_path),
        progress_callback=lambda p, d, t: first.append(p),
    )
    assert first == ["a.py"]

    # Second run is a pure cache hit — no work, so no progress events.
    second: list[str] = []
    await run_review(
        FakeProvider(comments),
        files,
        _config(),
        cache=ReviewCache(tmp_path),
        progress_callback=lambda p, d, t: second.append(p),
    )
    assert second == []


async def test_no_callback_is_fine() -> None:
    # Omitting the callback must not change behavior.
    result = await run_review(FakeProvider(), [_file("a.py")], _config())
    assert result.files_reviewed == 1
