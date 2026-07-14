"""Shared test fixtures and a FakeProvider (no real API calls — plan §10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicr.models import AccountUsage, Category, DiffFile, ReviewComment
from aicr.providers.base import LLMProvider

FIXTURES = Path(__file__).parent / "fixtures" / "sample_diffs"


def load_diff(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def single_file_diff() -> str:
    return load_diff("single_file.diff")


@pytest.fixture
def multi_file_diff() -> str:
    return load_diff("multi_file.diff")


@pytest.fixture
def binary_diff() -> str:
    return load_diff("binary.diff")


class FakeProvider(LLMProvider):
    """Returns preset comments per file path — used to test the pipeline offline."""

    name = "fake"

    def __init__(
        self,
        comments_by_path: dict[str, list[ReviewComment]] | None = None,
        *,
        account_usage: AccountUsage | None = None,
        tokens_per_call: int = 0,
    ) -> None:
        super().__init__()
        self.comments_by_path = comments_by_path or {}
        self.calls: list[str] = []
        self._account_usage = account_usage
        self._tokens_per_call = tokens_per_call

    async def review(
        self,
        diff_file: DiffFile,
        categories: list[Category],
        languages: list[str],
    ) -> list[ReviewComment]:
        self.calls.append(diff_file.path)
        if self._tokens_per_call:
            self._record_usage(
                prompt_tokens=self._tokens_per_call,
                completion_tokens=self._tokens_per_call,
            )
        return self.comments_by_path.get(diff_file.path, [])

    async def account_usage(self) -> AccountUsage | None:
        return self._account_usage


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()
