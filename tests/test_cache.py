"""Hunk-level cache: keying, hits/misses, persistence, and engine integration."""

from __future__ import annotations

from pathlib import Path

from aicr.cache import ReviewCache, make_key
from aicr.config import Config
from aicr.engine import run_review
from aicr.models import DiffFile, ReviewComment
from tests.conftest import FakeProvider


def _file(path: str = "a.py", content: str = "x = 1") -> DiffFile:
    return DiffFile.model_validate(
        {
            "path": path,
            "hunks": [
                {"start_line": 1, "lines": [{"kind": "added", "line_no": 1, "content": content}]}
            ],
        }
    )


def _config(**kw) -> Config:
    return Config(api_key="test", **kw)


def test_key_stable_for_same_content() -> None:
    f1, f2 = _file(), _file()
    key1 = make_key(f1, provider="p", model="m", categories=["bug"])
    key2 = make_key(f2, provider="p", model="m", categories=["bug"])
    assert key1 == key2


def test_key_changes_with_content_model_and_categories() -> None:
    base = make_key(_file(), provider="p", model="m", categories=["bug"])
    assert base != make_key(_file(content="y = 2"), provider="p", model="m", categories=["bug"])
    assert base != make_key(_file(), provider="p", model="other", categories=["bug"])
    assert base != make_key(_file(), provider="other", model="m", categories=["bug"])
    assert base != make_key(_file(), provider="p", model="m", categories=["bug", "style"])


def test_set_get_roundtrip(tmp_path: Path) -> None:
    cache = ReviewCache(tmp_path)
    comments = [ReviewComment(file="a.py", line=1, category="bug", comment="c")]
    cache.set("k", comments)
    cache.save()

    reopened = ReviewCache(tmp_path)
    got = reopened.get("k")
    assert got is not None
    assert got[0].comment == "c"


def test_disabled_cache_never_hits(tmp_path: Path) -> None:
    cache = ReviewCache(tmp_path, enabled=False)
    cache.set("k", [ReviewComment(file="a.py", line=1, category="bug", comment="c")])
    cache.save()
    assert cache.get("k") is None


def test_corrupt_cache_degrades_to_miss(tmp_path: Path) -> None:
    path = tmp_path / ".aicr" / "cache" / "reviews.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    cache = ReviewCache(tmp_path)
    assert cache.get("anything") is None  # no crash


async def test_engine_second_run_uses_cache(tmp_path: Path) -> None:
    files = [_file()]
    comments = {"a.py": [ReviewComment(file="a.py", line=1, category="bug", comment="c")]}

    cache = ReviewCache(tmp_path)
    provider1 = FakeProvider(comments)
    result1 = await run_review(provider1, files, _config(), cache=cache)
    assert result1.cached == 0
    assert provider1.calls == ["a.py"]  # provider hit on first run

    # Second run: same content, fresh provider — must be served from cache.
    provider2 = FakeProvider(comments)
    result2 = await run_review(provider2, files, _config(), cache=ReviewCache(tmp_path))
    assert result2.cached == 1
    assert provider2.calls == []  # provider NOT called
    assert len(result2.comments) == 1


async def test_no_cache_still_reviews(tmp_path: Path) -> None:
    files = [_file()]
    comments = {"a.py": [ReviewComment(file="a.py", line=1, category="bug", comment="c")]}
    # cache=None means the engine always reviews.
    provider = FakeProvider(comments)
    result = await run_review(provider, files, _config(), cache=None)
    assert result.cached == 0
    assert provider.calls == ["a.py"]
