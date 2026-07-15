"""Full-repository scan (``aicr scan``) — review existing code, not just a diff.

Where ``aicr review`` looks at a git diff, ``scan`` reviews whole files. It reuses
the exact same downstream pipeline (engine → provider → renderer): the only new
idea is synthesizing a ``DiffFile`` whose lines are *all* marked "added", so the
provider reviews the file's full content while everything downstream — including
the "only comment on changed lines" safety net in ``parse_comments`` — keeps
working unchanged.

Discovery is shared with ``aicr init`` via ``analyze.py``, so a scan reviews
exactly the set the analysis reports. ``representative_sample`` picks one
average-sized file the CLI reviews (timed) to build a *measured* scan-time
estimate — the provider's actual speed — rather than guessing from a hardcoded
throughput constant.
"""


from __future__ import annotations

from pathlib import Path

from aicr.analyze import detect_language, estimate_total_tokens, reviewable_files
from aicr.models import DiffFile, DiffHunk, DiffLine


def build_scan_file(rel_path: str, text: str) -> DiffFile:
    """Turn a file's full text into a ``DiffFile`` with every line marked added.

    Line numbers are 1-based and contiguous, matching how the provider is asked
    to reference lines. An empty file yields a ``DiffFile`` with no hunks (the
    engine then skips it, since it has no added lines).
    """
    # splitlines() drops the trailing newline and handles \r\n / lone \n uniformly.
    raw_lines = text.splitlines()
    lines = [
        DiffLine(kind="added", line_no=i, content=content)
        for i, content in enumerate(raw_lines, start=1)
    ]
    hunks = [DiffHunk(start_line=1, lines=lines)] if lines else []
    return DiffFile(path=rel_path, language=detect_language(rel_path), hunks=hunks)


def file_char_count(diff_file: DiffFile) -> int:
    """Total characters across a scan file's lines (a size proxy for sampling)."""
    return sum(len(line.content) for hunk in diff_file.hunks for line in hunk.lines)


def estimate_scan_tokens(files: list[DiffFile]) -> int:
    """Estimate total tokens for scanning exactly ``files``.

    Uses the file contents actually being scanned (not the whole-repo analysis),
    so the "~tokens" shown before a capped or excluded scan matches the set that
    will be sent. Includes the same per-file prompt overhead + output allowance as
    the analysis estimate (see ``analyze.estimate_total_tokens``).
    """
    total_chars = sum(file_char_count(f) for f in files)
    return estimate_total_tokens(total_chars, len(files))


def representative_sample(files: list[DiffFile]) -> DiffFile:

    """Pick the file closest to the average size — a fair timing sample.

    The measured estimate reviews one real file to learn the provider's actual
    speed, then extrapolates. Sampling the biggest or smallest file would skew
    that, so we pick the one nearest the mean character count.
    """
    sizes = [(file_char_count(f), f) for f in files]
    avg = sum(size for size, _ in sizes) / len(sizes)
    return min(sizes, key=lambda pair: abs(pair[0] - avg))[1]


def collect_scan_files(

    repo_dir: Path | None = None,
    *,
    exclude_paths: list[str] | None = None,
) -> list[DiffFile]:
    """Discover reviewable tracked files and load each as a full-content DiffFile.

    Unreadable files (binary/permission/decode errors) are skipped silently — the
    same conservative filtering ``analyze.py`` uses, so the count here matches the
    analysis report the user just confirmed.
    """
    repo_dir = repo_dir or Path.cwd()
    files: list[DiffFile] = []
    for rel in reviewable_files(repo_dir, exclude_paths):
        try:
            text = (repo_dir / rel).read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeError):
            continue
        files.append(build_scan_file(rel, text))
    return files
