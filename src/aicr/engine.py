"""Review orchestration: DiffFiles -> concurrent provider calls -> ReviewResult.

This is the only place that knows how to drive the whole pipeline. It owns the
concurrency (a bounded ``asyncio.gather`` via a semaphore — review §3), the
skip/error accounting, the optional hunk-level cache (roadmap §10), and the
aggregation of token/account usage. The CLI owns the event loop and calls
``run_review``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from aicr.cache import ReviewCache, make_key
from aicr.config import Config
from aicr.diff.chunk import chunk_diff_file, dedupe_comments
from aicr.models import Category, DiffFile, ReviewComment, ReviewResult
from aicr.providers.base import LLMProvider, MalformedResponseError, ProviderError

# Called when a file finishes being reviewed: (path, done_count, total_to_review).
# Used by the CLI to show a live "Reviewing …" status without altering the final
# report. Fired on completion (not start) so the count climbs visibly 1→N under
# concurrency rather than jumping straight to N/N.
ProgressCallback = Callable[[str, int, int], None]




def _reviewable(
    files: list[DiffFile], max_lines: int, *, chunk_large: bool
) -> tuple[list[DiffFile], int, int]:

    """Partition files into reviewable units and count skips.

    Returns ``(units, skipped_binary, skipped_too_large)`` where ``units`` is the
    list of ``DiffFile`` objects to send to the provider. A file larger than
    ``max_lines`` is either split into overlapping chunks (``chunk_large=True``,
    each chunk a unit sharing the file's path) or skipped (counted in
    ``skipped_too_large``). Because chunks of one file share that file's path,
    callers count distinct paths to report logical files reviewed.
    """
    units: list[DiffFile] = []
    skipped_binary = 0
    skipped_too_large = 0
    for f in files:
        if f.is_binary:
            skipped_binary += 1
            continue
        if f.added_line_count() == 0:
            continue  # nothing added — skip silently, don't waste a call
        if len(f.hunks) and f.added_line_count() > max_lines:
            if not chunk_large:
                skipped_too_large += 1
                continue
            units.extend(chunk_diff_file(f, max_lines))
            continue
        units.append(f)
    return units, skipped_binary, skipped_too_large




async def _review_one(
    provider: LLMProvider,
    diff_file: DiffFile,
    categories: list[Category],
    languages: list[str],
    semaphore: asyncio.Semaphore,
    errors: list[str],
    progress: _ProgressTracker | None,
) -> list[ReviewComment]:
    """Review a single file, converting failures into a counted error (never raise)."""
    async with semaphore:
        try:
            return await provider.review(diff_file, categories, languages)
        except (ProviderError, MalformedResponseError) as exc:
            errors.append(f"{diff_file.path}: {exc}")
            return []
        finally:
            # Report on *completion*, not start: reviews run concurrently, so all
            # files up to the concurrency limit begin almost simultaneously and a
            # start-based counter would jump straight to N/N. Completions are
            # spaced out over real LLM latency, so the count visibly climbs 1→N
            # and names the file that just finished.
            if progress is not None:
                progress.finished(diff_file.path)


class _ProgressTracker:
    """Turns per-file completion events into monotonic (path, n, total) callbacks."""

    def __init__(self, total: int, callback: ProgressCallback) -> None:
        self._total = total
        self._callback = callback
        self._done = 0

    def finished(self, path: str) -> None:
        self._done += 1
        self._callback(path, self._done, self._total)



async def run_review(
    provider: LLMProvider,
    files: list[DiffFile],
    config: Config,
    *,
    cache: ReviewCache | None = None,
    progress_callback: ProgressCallback | None = None,
) -> ReviewResult:
    """Run the full review over all changed files, concurrently and safely.

    Never raises for per-file failures — those are counted in
    ``ReviewResult.skipped_errors`` so one bad response can't block a commit
    (plan §7/§8). Files whose reviewable content is unchanged since a previous
    run are served from ``cache`` (when provided) instead of the provider.

    ``progress_callback`` (optional) is invoked as each file *finishes* being
    reviewed — this drives the live "Reviewing …" status. It fires only for
    cache misses (hits do no work), and never changes the final result.
    """
    start = time.perf_counter()

    units, skipped_binary, skipped_too_large = _reviewable(
        files, config.max_diff_lines_per_file, chunk_large=config.chunk_large_files
    )

    # Cap the number of units to avoid runaway cost/latency (review §3). Chunks
    # count as units here — a single huge file can consume the whole budget,
    # which is the intended safety valve.
    if len(units) > config.max_files_per_review:
        units = units[: config.max_files_per_review]

    # Distinct source paths among the (capped) units = logical files reviewed.
    # Chunks of one big file share a path, so a split file still counts once,
    # and the count reflects the cap rather than the pre-cap total.
    logical_files = len({f.path for f in units})


    # Split into cache hits (served from disk) and misses (sent to the provider).
    # Keys are keyed by unit index, not path: chunks of one big file share a path
    # but have distinct content, so a per-index key avoids collisions while
    # ``make_key`` (content-based) still gives each chunk its own cache entry.
    cached_comments: list[ReviewComment] = []
    to_review: list[tuple[int, DiffFile]] = []
    keys: dict[int, str] = {}
    cached_count = 0
    for idx, f in enumerate(units):
        key = make_key(f, provider=provider.name, model=config.model, categories=config.categories)
        keys[idx] = key
        hit = cache.get(key) if cache is not None else None
        if hit is not None:
            cached_comments.extend(hit)
            cached_count += 1
        else:
            to_review.append((idx, f))


    tracker = (
        _ProgressTracker(len(to_review), progress_callback)
        if progress_callback is not None and to_review
        else None
    )
    semaphore = asyncio.Semaphore(config.concurrency)
    errors: list[str] = []
    results = await asyncio.gather(
        *(
            _review_one(
                provider, f, config.categories, config.languages, semaphore, errors, tracker
            )
            for _, f in to_review
        )
    )

    # Record freshly-reviewed units back into the cache (only clean successes).
    errored_paths = {msg.split(":", 1)[0] for msg in errors}
    fresh_comments: list[ReviewComment] = []
    for (idx, f), file_comments in zip(to_review, results, strict=True):
        fresh_comments.extend(file_comments)
        if cache is not None and f.path not in errored_paths:
            cache.set(keys[idx], file_comments)
    if cache is not None:
        cache.save()

    # Overlapping chunks can report the same finding twice; collapse duplicates
    # so a split file reads like a single review.
    comments = dedupe_comments(cached_comments + fresh_comments)
    duration = time.perf_counter() - start

    result = ReviewResult(
        files_reviewed=logical_files,

        comments=comments,
        provider=provider.name,
        model=config.model,
        duration_seconds=duration,
        skipped_binary=skipped_binary,
        skipped_too_large=skipped_too_large,
        skipped_errors=len(errors),
        cached=cached_count,
        token_usage=provider.usage,
    )
    # Best-effort account usage for the "% API usage" display — never fatal.
    result.account_usage = await provider.account_usage()
    return result
