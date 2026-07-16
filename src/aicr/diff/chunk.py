"""Split an oversized ``DiffFile`` into reviewable chunks (roadmap B1).

Before this, a file with more added lines than ``max_diff_lines_per_file`` was
skipped entirely — so the biggest, often most important files got no review.
Chunking splits such a file into windows small enough to review, each carrying
its **real** new-file line numbers (so comments still map back correctly) plus a
small overlap of lines at each boundary. The overlap means an issue that straddles
a split is visible in both neighboring chunks; ``dedupe_comments`` then removes the
resulting duplicate so the user sees it once.

Everything downstream is unchanged: each chunk is just a normal ``DiffFile`` with
the same ``path``, so the engine, provider, cache, and renderer treat it like any
other file. Chunks of one file share a path, which is how the engine re-groups
them into a single logical "file reviewed".
"""

from __future__ import annotations

from aicr.models import DiffFile, DiffHunk, ReviewComment

# Lines of context re-included at each chunk boundary so a finding spanning the
# split is visible on both sides. Small on purpose: enough for local context
# without materially inflating token cost. Duplicates are removed afterwards.
DEFAULT_OVERLAP = 10


def chunk_diff_file(
    diff_file: DiffFile,
    max_added_lines: int,
    *,
    overlap: int = DEFAULT_OVERLAP,
) -> list[DiffFile]:
    """Split ``diff_file`` into chunks of at most ``max_added_lines`` added lines.

    Returns ``[diff_file]`` unchanged when it already fits (the common case), so
    callers can use this unconditionally. Line numbers are preserved from the
    original, so comments on a chunk map straight back to the real file.
    """
    if max_added_lines <= 0:
        return [diff_file]

    # Flatten every line across hunks, preserving order. Chunk windows are cut
    # over this flat sequence but budgeted by *added* lines only (context lines
    # are "free" — they don't count toward the reviewable size).
    lines = [line for hunk in diff_file.hunks for line in hunk.lines]
    added_total = sum(1 for line in lines if line.kind == "added")
    if added_total <= max_added_lines:
        return [diff_file]

    chunks: list[DiffFile] = []
    n = len(lines)
    start = 0
    while start < n:
        window = []
        added_in_window = 0
        i = start
        while i < n and added_in_window < max_added_lines:
            line = lines[i]
            window.append(line)
            if line.kind == "added":
                added_in_window += 1
            i += 1

        if window:
            chunks.append(
                DiffFile(
                    path=diff_file.path,
                    language=diff_file.language,
                    hunks=[DiffHunk(start_line=window[0].line_no, lines=list(window))],
                    is_binary=diff_file.is_binary,
                )
            )

        if i >= n:
            break
        # Step back by ``overlap`` so the next chunk re-includes boundary context,
        # but always advance at least one line to guarantee termination.
        start = max(i - overlap, start + 1)

    return chunks


def dedupe_comments(comments: list[ReviewComment]) -> list[ReviewComment]:
    """Drop duplicate comments produced by overlapping chunk boundaries.

    Two comments are "the same" when they share file, line, category, and text —
    which is exactly what happens when an issue in an overlap region is reported
    by both adjacent chunks. Order is preserved (first occurrence wins).
    """
    seen: set[tuple[str, int, str, str]] = set()
    unique: list[ReviewComment] = []
    for comment in comments:
        key = (comment.file, comment.line, comment.category, comment.comment)
        if key in seen:
            continue
        seen.add(key)
        unique.append(comment)
    return unique
