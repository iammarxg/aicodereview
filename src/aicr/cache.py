"""Hunk-level review cache — skip re-reviewing unchanged files (roadmap §10).

The common loop is "commit, notice a typo, fix, recommit". Between those runs
most files' *reviewable content* is identical, yet each re-review costs another
API call and more latency. This cache stores the comments produced for a file
keyed by a hash of its added lines (``DiffFile.content_signature``), so a file
whose changed content hasn't moved is served from disk instead of the provider.

Design choices that keep it safe:
- Key includes provider, model, and selected categories — a different model or a
  broader category set is a cache miss, never a stale hit.
- Stored under ``.aicr/cache/`` (gitignored) as one JSON file per run's map.
- Any read/write error is swallowed: the cache is an optimization, never a
  dependency. A corrupt cache degrades to "review everything", never a crash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aicr.models import Category, DiffFile, ReviewComment

CACHE_DIRNAME = ".aicr"
CACHE_SUBDIR = "cache"
CACHE_FILENAME = "reviews.json"
# Bump when the on-disk format changes so old entries are ignored, not misread.
_CACHE_VERSION = "1"


def _cache_path(repo_dir: Path) -> Path:
    return repo_dir / CACHE_DIRNAME / CACHE_SUBDIR / CACHE_FILENAME


def make_key(
    diff_file: DiffFile,
    *,
    provider: str,
    model: str,
    categories: list[Category],
) -> str:
    """Content-addressed cache key for one file under the current review settings.

    Changing the file's added lines, the provider, the model, or the category
    selection all change the key — so a hit always means "same content, same
    review parameters".
    """
    parts = [
        _CACHE_VERSION,
        provider,
        model,
        ",".join(sorted(categories)),
        diff_file.content_signature(),
    ]
    digest = hashlib.sha256("\u0000".join(parts).encode("utf-8")).hexdigest()
    return digest


class ReviewCache:
    """A JSON-backed map of ``cache key -> list[ReviewComment]`` for one repo."""

    def __init__(self, repo_dir: Path, *, enabled: bool = True) -> None:
        self.repo_dir = repo_dir
        self.enabled = enabled
        self._data: dict[str, list[dict[str, object]]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded or not self.enabled:
            return
        self._loaded = True
        path = _cache_path(self.repo_dir)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                # Best-effort shape check; ignore anything unexpected.
                self._data = {k: v for k, v in raw.items() if isinstance(v, list)}
        except (OSError, ValueError):
            self._data = {}  # missing or corrupt — start empty, never crash

    def get(self, key: str) -> list[ReviewComment] | None:
        """Return cached comments for ``key``, or None on a miss/disabled cache."""
        if not self.enabled:
            return None
        self._load()
        items = self._data.get(key)
        if items is None:
            return None
        try:
            return [ReviewComment.model_validate(item) for item in items]
        except (TypeError, ValueError):
            return None  # entry no longer matches the model — treat as a miss


    def set(self, key: str, comments: list[ReviewComment]) -> None:
        """Record ``comments`` for ``key`` in memory (persisted by ``save``)."""
        if not self.enabled:
            return
        self._load()
        self._data[key] = [c.model_dump() for c in comments]

    def save(self) -> None:
        """Persist the cache to disk. Silently no-ops on any failure."""
        if not self.enabled:
            return
        path = _cache_path(self.repo_dir)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._data), encoding="utf-8")
        except OSError:
            pass  # cache is best-effort; never fail a review over it
