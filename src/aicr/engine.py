"""Review orchestration: DiffFiles -> concurrent provider calls -> ReviewResult.

This is the only place that knows how to drive the whole pipeline. It owns the
concurrency (a bounded ``asyncio.gather`` via a semaphore — review §3) and the
skip/error accounting. The CLI owns the event loop and calls ``run_review``.
"""

from __future__ import annotations

import asyncio
import time

from aicr.config import Config
from aicr.models import Category, DiffFile, ReviewComment, ReviewResult
from aicr.providers.base import LLMProvider, MalformedResponseError, ProviderError


def _reviewable(files: list[DiffFile], max_lines: int) -> tuple[list[DiffFile], int, int]:
    """Partition files into reviewable ones and count skipped binary/too-large."""
    reviewable: list[DiffFile] = []
    skipped_binary = 0
    skipped_too_large = 0
    for f in files:
        if f.is_binary:
            skipped_binary += 1
            continue
        if f.added_line_count() == 0:
            continue  # nothing added — skip silently, don't waste a call
        if len(f.hunks) and f.added_line_count() > max_lines:
            skipped_too_large += 1
            continue
        reviewable.append(f)
    return reviewable, skipped_binary, skipped_too_large


async def _review_one(
    provider: LLMProvider,
    diff_file: DiffFile,
    categories: list[Category],
    languages: list[str],
    semaphore: asyncio.Semaphore,
    errors: list[str],
) -> list[ReviewComment]:
    """Review a single file, converting failures into a counted error (never raise)."""
    async with semaphore:
        try:
            return await provider.review(diff_file, categories, languages)
        except (ProviderError, MalformedResponseError) as exc:
            errors.append(f"{diff_file.path}: {exc}")
            return []


async def run_review(
    provider: LLMProvider,
    files: list[DiffFile],
    config: Config,
) -> ReviewResult:
    """Run the full review over all changed files, concurrently and safely.

    Never raises for per-file failures — those are counted in
    ``ReviewResult.skipped_errors`` so one bad response can't block a commit
    (plan §7/§8).
    """
    start = time.perf_counter()
    reviewable, skipped_binary, skipped_too_large = _reviewable(
        files, config.max_diff_lines_per_file
    )

    # Cap the number of files to avoid runaway cost/latency (review §3).
    if len(reviewable) > config.max_files_per_review:
        reviewable = reviewable[: config.max_files_per_review]

    semaphore = asyncio.Semaphore(config.concurrency)
    errors: list[str] = []
    results = await asyncio.gather(
        *(
            _review_one(provider, f, config.categories, config.languages, semaphore, errors)
            for f in reviewable
        )
    )

    comments: list[ReviewComment] = [c for file_comments in results for c in file_comments]
    duration = time.perf_counter() - start

    return ReviewResult(
        files_reviewed=len(reviewable),
        comments=comments,
        provider=provider.name,
        model=config.model,
        duration_seconds=duration,
        skipped_binary=skipped_binary,
        skipped_too_large=skipped_too_large,
        skipped_errors=len(errors),
    )
