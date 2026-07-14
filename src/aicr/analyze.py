"""Repository analysis for the ``aicr init`` wizard.

Everything here is **fast and local** — it shells out to ``git ls-files`` and
reads file sizes/line counts, no LLM calls. The wizard uses it to:

  * report how much code is in the repo (files / lines / characters / ~tokens),
  * detect the dominant languages and heavy directories worth excluding,
  * recommend efficient ``.aicr.yaml`` settings for a repo of this size, and
  * feed the scan-time estimator (see ``estimate_scan_seconds``).

The token estimate uses the common ~4-characters-per-token rule of thumb; it's
deliberately approximate and labelled as such wherever it's shown.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Roughly how many characters map to one LLM token (OpenAI/Gemini-ish average).
CHARS_PER_TOKEN = 4

# Extension → human language label, for the "languages" recommendation and
# report. Only the common ones; unknown extensions are simply ignored.
_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".sql": "sql",
}

# Directories/patterns almost never worth reviewing — recommended as excludes
# when present. Kept conservative so we don't hide real source.
_HEAVY_DIR_CANDIDATES: tuple[str, ...] = (
    "node_modules",
    "dist",
    "build",
    "vendor",
    "target",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    "out",
    "coverage",
)
_LOCKFILE_GLOBS: tuple[str, ...] = ("*.lock", "*.min.js", "*.min.css")

# Binary/asset extensions we skip when counting reviewable code.
_SKIP_EXT: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".pdf",
        ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".eot", ".mp4",
        ".mp3", ".wav", ".bin", ".so", ".dylib", ".dll", ".class", ".jar",
        ".lock",
    }
)


@dataclass
class RepoAnalysis:
    """Summary of a repository's reviewable code, plus recommendations."""

    total_files: int = 0
    total_lines: int = 0
    total_chars: int = 0
    languages: list[tuple[str, int]] = field(default_factory=list)  # (label, file count)
    recommended_excludes: list[str] = field(default_factory=list)
    recommended_languages: list[str] = field(default_factory=list)
    recommended_max_files: int = 50
    recommended_concurrency: int = 5

    @property
    def estimated_tokens(self) -> int:
        """Approximate total prompt tokens (~chars / 4)."""
        return self.total_chars // CHARS_PER_TOKEN


class AnalysisError(Exception):
    """Raised when the repo can't be analyzed (e.g. not a git repository)."""


def _tracked_files(repo_dir: Path) -> list[str]:
    """Return git-tracked file paths (relative), or raise AnalysisError."""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise AnalysisError("Not inside a git repository (or git unavailable).") from exc
    return [line for line in out.splitlines() if line.strip()]


def _is_reviewable(rel_path: str) -> bool:
    """Skip obvious non-source: heavy dirs and binary/asset extensions."""
    parts = set(Path(rel_path).parts)
    if parts & set(_HEAVY_DIR_CANDIDATES):
        return False
    return Path(rel_path).suffix.lower() not in _SKIP_EXT


def analyze_repo(repo_dir: Path | None = None) -> RepoAnalysis:
    """Analyze the git repo at ``repo_dir`` (defaults to cwd). Fast, no network.

    Counts reviewable tracked files (lines + characters), detects languages, and
    derives recommended ``.aicr.yaml`` settings scaled to the repo size.
    """
    repo_dir = repo_dir or Path.cwd()
    files = _tracked_files(repo_dir)

    analysis = RepoAnalysis()
    lang_counter: Counter[str] = Counter()
    seen_heavy: set[str] = set()

    for rel in files:
        # Note any heavy directories present so we can recommend excluding them.
        for part in Path(rel).parts:
            if part in _HEAVY_DIR_CANDIDATES:
                seen_heavy.add(part)
        if not _is_reviewable(rel):
            continue
        abs_path = repo_dir / rel
        try:
            text = abs_path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeError):
            continue
        analysis.total_files += 1
        analysis.total_lines += text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        analysis.total_chars += len(text)
        lang = _EXT_LANG.get(abs_path.suffix.lower())
        if lang:
            lang_counter[lang] += 1

    analysis.languages = lang_counter.most_common()
    analysis.recommended_languages = [lang for lang, _ in lang_counter.most_common(6)]
    analysis.recommended_excludes = _recommend_excludes(seen_heavy)
    analysis.recommended_max_files = _recommend_max_files(analysis.total_files)
    analysis.recommended_concurrency = _recommend_concurrency(analysis.total_files)
    return analysis


def _recommend_excludes(seen_heavy: set[str]) -> list[str]:
    """Recommend excludes: any heavy dirs present + common lockfile globs."""
    excludes = [f"{d}/**" for d in sorted(seen_heavy)]
    excludes.extend(_LOCKFILE_GLOBS)
    return excludes


def _recommend_max_files(total_files: int) -> int:
    """Cap files-per-review generously but bounded, scaled to repo size."""
    if total_files <= 50:
        return 50
    if total_files <= 200:
        return 100
    return 200


def _recommend_concurrency(total_files: int) -> int:
    """Suggest more parallelism for bigger repos (still polite to rate limits)."""
    if total_files <= 20:
        return 4
    if total_files <= 100:
        return 6
    return 8


def estimate_scan_seconds(
    analysis: RepoAnalysis,
    *,
    tokens_per_second: float,
    concurrency: int,
) -> tuple[float, float]:
    """Estimate a full-repo scan's wall-clock time as a (low, high) range.

    The model latency scales with text volume, so we drive the estimate from
    tokens (≈ chars/4) rather than file count:

        seconds ≈ (total_tokens / tokens_per_second) / effective_concurrency

    ``tokens_per_second`` should come from one real timed sample review so the
    number reflects the user's actual provider/model speed. We widen the point
    estimate into a ±40% range because per-file latency varies a lot in practice.
    """
    effective_concurrency = max(1, concurrency)
    tps = max(1.0, tokens_per_second)
    point = (analysis.estimated_tokens / tps) / effective_concurrency
    return point * 0.6, point * 1.4
