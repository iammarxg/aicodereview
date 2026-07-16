"""Engine orchestration: concurrency, skips, per-file error isolation."""

from __future__ import annotations

import asyncio

from aicr.config import Config
from aicr.diff.parser import parse_diff
from aicr.engine import run_review
from aicr.models import Category, DiffFile, ReviewComment
from aicr.providers.base import LLMProvider, ProviderError


def _config(**overrides) -> Config:
    return Config(api_key="test", **overrides)


async def test_multi_file_all_reviewed(multi_file_diff: str) -> None:
    files = parse_diff(multi_file_diff)
    from tests.conftest import FakeProvider

    provider = FakeProvider(
        {
            "app.py": [
                ReviewComment(file="app.py", line=12, category="bug", comment="c")
            ],
            "util.js": [
                ReviewComment(file="util.js", line=2, category="security", comment="s")
            ],
        }
    )
    result = await run_review(provider, files, _config())
    assert result.files_reviewed == 2
    assert len(result.comments) == 2
    assert set(provider.calls) == {"app.py", "util.js"}


async def test_binary_files_skipped(binary_diff: str) -> None:
    files = parse_diff(binary_diff)
    from tests.conftest import FakeProvider

    result = await run_review(FakeProvider(), files, _config())
    assert result.files_reviewed == 0
    assert result.skipped_binary == 1


async def test_too_large_file_skipped() -> None:
    from tests.conftest import FakeProvider

    big = DiffFile.model_validate(
        {
            "path": "big.py",
            "hunks": [
                {
                    "start_line": 1,
                    "lines": [
                        {"kind": "added", "line_no": i, "content": f"x{i}"}
                        for i in range(1, 11)
                    ],
                }
            ],
        }
    )
    # Chunking is on by default; disable it here to exercise the skip path.
    result = await run_review(
        FakeProvider(), [big], _config(max_diff_lines_per_file=5, chunk_large_files=False)
    )
    assert result.skipped_too_large == 1
    assert result.files_reviewed == 0



async def test_provider_error_isolated_to_file(multi_file_diff: str) -> None:
    files = parse_diff(multi_file_diff)

    class HalfBrokenProvider(LLMProvider):
        name = "halfbroken"

        async def review(
            self, diff_file: DiffFile, categories: list[Category], languages: list[str]
        ) -> list[ReviewComment]:
            if diff_file.path == "app.py":
                raise ProviderError("boom")
            return [ReviewComment(file=diff_file.path, line=2, category="bug", comment="ok")]

    result = await run_review(HalfBrokenProvider(), files, _config())
    # One file errored, the other still produced a comment — commit never blocked.
    assert result.skipped_errors == 1
    assert len(result.comments) == 1


async def test_concurrency_limit_respected() -> None:
    from tests.conftest import FakeProvider

    files = [
        DiffFile.model_validate(
            {
                "path": f"f{i}.py",
                "hunks": [
                    {"start_line": 1, "lines": [{"kind": "added", "line_no": 1, "content": "x"}]}
                ],
            }
        )
        for i in range(10)
    ]

    active = 0
    peak = 0

    class TrackingProvider(FakeProvider):
        async def review(self, diff_file, categories, languages):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return []

    result = await run_review(TrackingProvider(), files, _config(concurrency=3))
    assert result.files_reviewed == 10
    assert peak <= 3


async def test_max_files_cap() -> None:
    from tests.conftest import FakeProvider

    files = [
        DiffFile.model_validate(
            {
                "path": f"f{i}.py",
                "hunks": [
                    {"start_line": 1, "lines": [{"kind": "added", "line_no": 1, "content": "x"}]}
                ],
            }
        )
        for i in range(10)
    ]
    result = await run_review(FakeProvider(), files, _config(max_files_per_review=4))
    assert result.files_reviewed == 4
