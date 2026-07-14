"""Shared test fixtures and a FakeProvider (no real API calls — plan §10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicr.models import Category, DiffFile, ReviewComment
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

    def __init__(self, comments_by_path: dict[str, list[ReviewComment]] | None = None) -> None:
        self.comments_by_path = comments_by_path or {}
        self.calls: list[str] = []

    async def review(
        self,
        diff_file: DiffFile,
        categories: list[Category],
        languages: list[str],
    ) -> list[ReviewComment]:
        self.calls.append(diff_file.path)
        return self.comments_by_path.get(diff_file.path, [])


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()
