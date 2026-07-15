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

import math
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

# Roughly how many characters map to one LLM token (OpenAI/Gemini-ish average).
CHARS_PER_TOKEN = 4

# Tokens sent on EVERY per-file call beyond the file's own content: the system
# base contract, the grounding block, the selected category templates, and the
# user-prompt scaffolding. A scan makes one call per file, so this fixed overhead
# is real and sizable — ignoring it (counting only content ~chars/4) undercounts
# total tokens badly on small files, which is what made the pre-scan "~tokens"
# number read ~40% low. Deliberately a round, slightly-conservative figure.
PROMPT_OVERHEAD_TOKENS_PER_FILE = 700

# Output tokens the model generates per file (the JSON comments). Also counted
# and billed, and also missing from a content-only estimate.
OUTPUT_TOKENS_PER_FILE = 200


# Per-file API-call overhead, in seconds: connection + prompt processing + output
# generation latency that doesn't scale with input size. A scan makes one call
# per file, so this fixed cost dominates for small files and must not be ignored
# (ignoring it is what produced the bogus "~0.0 min" estimate). Deliberately
# conservative; the measured estimate in ``aicr scan`` refines it from a real call.
_PER_CALL_OVERHEAD_SECONDS = 2.5

# Fallback throughput (tokens/sec) for the *un-measured* estimate shown in
# ``aicr init``. ``aicr scan`` measures the real value from one sample call.
DEFAULT_TOKENS_PER_SECOND = 800.0


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
# Binary/asset extensions we skip when counting reviewable code.
_SKIP_EXT: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".pdf",
        ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".eot", ".mp4",
        ".mp3", ".wav", ".bin", ".so", ".dylib", ".dll", ".class", ".jar",
        ".lock",
    }
)

# Non-source *text* files: excluded from review by default and not counted as
# reviewable code (they'd otherwise inflate the token estimate). Maps a file
# extension to the glob we recommend in .aicr.yaml when the type is present.
_NON_SOURCE_EXT_GLOB: dict[str, str] = {
    ".md": "*.md",
    ".markdown": "*.markdown",
    ".rst": "*.rst",
    ".txt": "*.txt",
    ".yaml": "*.yaml",
    ".yml": "*.yml",
    ".toml": "*.toml",
    ".ini": "*.ini",
    ".cfg": "*.cfg",
    ".json": "*.json",
    ".xml": "*.xml",
    ".csv": "*.csv",
}



@dataclass
class RepoAnalysis:
    """Summary of a repository's reviewable code, plus recommendations."""

    total_files: int = 0
    total_lines: int = 0
    total_chars: int = 0
    # Files reviewable by type but removed by the user's .aicr.yaml exclude globs.
    # Lets the wizard show "N tracked · M after your excludes" so its count agrees
    # with what `aicr scan` will actually review.
    total_excluded_by_config: int = 0
    languages: list[tuple[str, int]] = field(default_factory=list)  # (label, file count)

    recommended_excludes: list[str] = field(default_factory=list)
    recommended_languages: list[str] = field(default_factory=list)
    recommended_max_files: int = 50
    recommended_concurrency: int = 5

    @property
    def estimated_tokens(self) -> int:
        """Approximate total tokens for a scan of these files.

        Includes both the file *content* (~chars / 4) and the per-file prompt
        overhead + output allowance that every call incurs — a scan sends one call
        per file, so counting content alone undercounts real usage badly (that was
        the ~40%-low pre-scan number). See ``estimate_total_tokens``.
        """
        return estimate_total_tokens(self.total_chars, self.total_files)

    @property
    def content_tokens(self) -> int:
        """Just the file-content token estimate (~chars / 4), no call overhead."""
        return self.total_chars // CHARS_PER_TOKEN



def estimate_total_tokens(total_chars: int, file_count: int) -> int:
    """Estimate total tokens a scan will use: content + per-file call overhead.

    ``content ≈ total_chars / CHARS_PER_TOKEN`` plus, for each of the ``file_count``
    per-file calls, the fixed prompt overhead (system + grounding + category
    templates + scaffolding) and the model's output. A content-only estimate
    (the old behavior) ignored both and read ~40% low on small files.
    """
    content = total_chars // CHARS_PER_TOKEN
    per_file = PROMPT_OVERHEAD_TOKENS_PER_FILE + OUTPUT_TOKENS_PER_FILE
    return content + max(0, file_count) * per_file


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


def detect_language(rel_path: str) -> str | None:
    """Return the language label for a path's extension, or None if unknown."""
    return _EXT_LANG.get(Path(rel_path).suffix.lower())


def _is_reviewable(rel_path: str) -> bool:
    """Skip non-source by default: heavy dirs, binary/assets, and non-source text.

    Documentation and config files (``.md``, ``.yaml``, ``.json`, …) are treated
    as non-source here so they neither inflate the token estimate nor get scanned
    by default — matching the excludes we recommend for them.
    """
    parts = set(Path(rel_path).parts)
    if parts & set(_HEAVY_DIR_CANDIDATES):
        return False
    ext = Path(rel_path).suffix.lower()
    return ext not in _SKIP_EXT and ext not in _NON_SOURCE_EXT_GLOB



def reviewable_files(repo_dir: Path, exclude_paths: list[str] | None = None) -> list[str]:
    """Return git-tracked, reviewable file paths (relative), honoring excludes.

    Shares the discovery + binary/heavy-dir filtering used by ``analyze_repo`` so
    ``aicr scan`` reviews exactly the set the analysis reported. ``exclude_paths``
    globs (from ``.aicr.yaml``) are applied on top, matched against the full
    relative path and its basename.
    """
    excludes = exclude_paths or []

    result: list[str] = []
    for rel in _tracked_files(repo_dir):
        if not _is_reviewable(rel):
            continue
        name = Path(rel).name
        if any(fnmatch(rel, pat) or fnmatch(name, pat) for pat in excludes):
            continue
        result.append(rel)
    return result



def _matches_any(rel: str, patterns: list[str]) -> bool:
    """True if ``rel`` (or its basename) matches any glob in ``patterns``."""
    name = Path(rel).name
    return any(fnmatch(rel, pat) or fnmatch(name, pat) for pat in patterns)


def analyze_repo(
    repo_dir: Path | None = None,
    *,
    exclude_paths: list[str] | None = None,
) -> RepoAnalysis:
    """Analyze the git repo at ``repo_dir`` (defaults to cwd). Fast, no network.

    Counts reviewable tracked files (lines + characters), detects languages, and
    derives recommended ``.aicr.yaml`` settings scaled to the repo size.

    ``exclude_paths`` (the effective ``.aicr.yaml`` globs) is applied on top of the
    built-in non-source filtering, so ``total_files`` matches exactly what ``aicr
    scan`` will review. ``total_excluded_by_config`` records how many otherwise-
    reviewable files those user globs removed, so the wizard can show a
    tracked-vs-after-excludes breakdown instead of a number that disagrees with
    the scan.
    """
    repo_dir = repo_dir or Path.cwd()
    excludes = exclude_paths or []
    files = _tracked_files(repo_dir)

    analysis = RepoAnalysis()
    lang_counter: Counter[str] = Counter()
    seen_heavy: set[str] = set()
    # Non-source globs (docs/config/assets/lockfiles) actually present in the
    # repo — we only recommend excluding types the user really has.
    seen_non_source: set[str] = set()

    for rel in files:
        # Note any heavy directories present so we can recommend excluding them.
        for part in Path(rel).parts:
            if part in _HEAVY_DIR_CANDIDATES:
                seen_heavy.add(part)
        ext = Path(rel).suffix.lower()
        if ext in _NON_SOURCE_EXT_GLOB:
            seen_non_source.add(_NON_SOURCE_EXT_GLOB[ext])
        elif ext in _SKIP_EXT:
            seen_non_source.add(f"*{ext}")
        if not _is_reviewable(rel):
            continue
        # Reviewable by type, but removed by the user's config globs: count it as
        # excluded so init and scan agree on the final file set.
        if excludes and _matches_any(rel, excludes):
            analysis.total_excluded_by_config += 1
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
    analysis.recommended_excludes = _recommend_excludes(seen_heavy, seen_non_source)
    analysis.recommended_max_files = _recommend_max_files(analysis.total_files)
    analysis.recommended_concurrency = _recommend_concurrency(analysis.total_files)
    return analysis


def _recommend_excludes(seen_heavy: set[str], seen_non_source: set[str]) -> list[str]:
    """Recommend excludes based on what the repo actually contains.

    Combines: heavy build/dependency dirs that are present, the non-source file
    types (docs/config/assets/lockfiles) actually seen, and lockfiles. This is
    repo-specific rather than a static guess — a pure-Python repo won't be told to
    exclude ``*.min.js`` it doesn't have, and a repo full of ``.md``/``.yaml`` will
    be.
    """
    heavy = [f"{d}/**" for d in sorted(seen_heavy)]
    # Always suggest the common lockfile globs (cheap, near-universally correct).
    lockfiles = ["*.lock"]
    non_source = sorted(seen_non_source - set(lockfiles))
    # De-dupe while preserving a stable, readable order: dirs, then file globs.
    return heavy + non_source + lockfiles



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
    file_count: int | None = None,
    per_call_overhead: float = _PER_CALL_OVERHEAD_SECONDS,
) -> tuple[float, float]:
    """Estimate a full-repo scan's wall-clock time as a (low, high) range.

    A scan makes **one API call per file**, so the estimate must model per-call
    cost, not treat the repo as one continuous token stream (that older model
    produced absurd "~0 seconds" results for small repos). Each call costs a fixed
    ``per_call_overhead`` (connection + prompt processing + output latency) plus
    the time to process that file's tokens. Calls run ``concurrency`` at a time:

        per_file  ≈ per_call_overhead + (tokens_per_file / tokens_per_second)
        batches   = ceil(file_count / concurrency)
        seconds   ≈ batches × per_file

    ``file_count`` defaults to the analysis's reviewable file count; pass a smaller
    value to reflect a ``--max-files`` cap. ``tokens_per_second`` and
    ``per_call_overhead`` should come from one real timed sample (see ``aicr
    scan``) so the number reflects the user's actual provider/model speed. We widen
    the point estimate into a ±40% range because per-file latency varies a lot.
    """
    files = analysis.total_files if file_count is None else file_count
    if files <= 0:
        return 0.0, 0.0
    tps = max(1.0, tokens_per_second)
    effective_concurrency = max(1, concurrency)

    avg_tokens_per_file = analysis.estimated_tokens / files
    per_file_seconds = per_call_overhead + (avg_tokens_per_file / tps)
    batches = math.ceil(files / effective_concurrency)
    point = batches * per_file_seconds
    return point * 0.6, point * 1.4


def estimate_from_sample(
    *,
    file_count: int,
    concurrency: int,
    sample_seconds: float,
) -> tuple[float, float]:
    """Measured scan estimate: extrapolate from one real, timed sample review.

    ``sample_seconds`` is the wall-clock time a single representative-sized file
    took to review (measured in ``cli._print_scan_estimate``). Since files are reviewed

    ``concurrency`` at a time, the whole scan is roughly ``ceil(files/concurrency)``
    batches, each about as long as that sample:

        seconds ≈ ceil(file_count / concurrency) × sample_seconds

    This replaces the guessed-throughput estimate with the user's actual
    provider/model speed. Widened to a ±40% range for the usual latency variance.
    """
    if file_count <= 0:
        return 0.0, 0.0
    batches = math.ceil(file_count / max(1, concurrency))
    point = batches * max(0.0, sample_seconds)
    return point * 0.6, point * 1.4


