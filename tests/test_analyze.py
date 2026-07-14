"""Repo analysis: counts, language detection, recommendations, scan estimate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aicr.analyze import (
    AnalysisError,
    RepoAnalysis,
    analyze_repo,
    estimate_from_sample,
    estimate_scan_seconds,
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.dev"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _add(path: Path, rel: str, content: str) -> None:
    file = path / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=path, check=True)


def test_analyze_counts_and_detects_languages(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _add(tmp_path, "app.py", "x = 1\ny = 2\n")
    _add(tmp_path, "main.py", "print('hi')\n")
    _add(tmp_path, "index.ts", "const a = 1;\n")

    analysis = analyze_repo(tmp_path)
    assert analysis.total_files == 3
    assert analysis.total_lines == 4
    assert analysis.total_chars > 0
    langs = dict(analysis.languages)
    assert langs["python"] == 2
    assert langs["typescript"] == 1
    assert "python" in analysis.recommended_languages


def test_analyze_skips_binary_and_heavy_dirs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _add(tmp_path, "app.py", "x = 1\n")
    _add(tmp_path, "node_modules/pkg/index.js", "module.exports = 1;\n")
    _add(tmp_path, "logo.png", "not really an image but has the ext\n")

    analysis = analyze_repo(tmp_path)
    # Only app.py is reviewable; node_modules and .png are excluded from counts.
    assert analysis.total_files == 1
    assert "node_modules/**" in analysis.recommended_excludes


def test_analyze_raises_outside_git_repo(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError):
        analyze_repo(tmp_path)


def test_estimated_tokens_from_chars() -> None:
    analysis = RepoAnalysis(total_chars=4000)
    assert analysis.estimated_tokens == 1000


def test_analyze_recommends_excluding_present_non_source_types(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _add(tmp_path, "app.py", "x = 1\n")
    _add(tmp_path, "README.md", "# docs\n")
    _add(tmp_path, "config.yaml", "a: 1\n")
    _add(tmp_path, "notes.txt", "hello\n")

    analysis = analyze_repo(tmp_path)
    # Only the Python file is reviewable; docs/config are excluded from counts.
    assert analysis.total_files == 1
    excludes = analysis.recommended_excludes
    # Recommends excluding the non-source types that are actually present…
    assert "*.md" in excludes
    assert "*.yaml" in excludes
    assert "*.txt" in excludes
    # …but not types the repo doesn't have (no static, repo-blind guesses).
    assert "*.min.js" not in excludes
    assert "*.min.css" not in excludes


def test_estimate_scan_seconds_scales_with_concurrency() -> None:
    analysis = RepoAnalysis(total_chars=40000, total_files=40)  # ~10k tokens
    low1, high1 = estimate_scan_seconds(analysis, tokens_per_second=1000, concurrency=1)
    low4, high4 = estimate_scan_seconds(analysis, tokens_per_second=1000, concurrency=4)
    # More concurrency → shorter estimate; range is ordered.
    assert low1 < high1
    assert high4 < high1


def test_estimate_scan_seconds_scales_with_file_count() -> None:
    # A per-file model must charge per-call overhead: more files → more time,
    # never collapsing to ~0 for a small-but-many-files repo (the old bug).
    small = RepoAnalysis(total_chars=4000, total_files=2)
    large = RepoAnalysis(total_chars=4000, total_files=40)
    _, small_high = estimate_scan_seconds(small, tokens_per_second=800, concurrency=5)
    _, large_high = estimate_scan_seconds(large, tokens_per_second=800, concurrency=5)
    assert large_high > small_high
    # Even a tiny repo takes at least a per-call round-trip's worth of time.
    assert small_high > 0.0


def test_estimate_scan_seconds_zero_files_is_zero() -> None:
    analysis = RepoAnalysis(total_chars=0, total_files=0)
    assert estimate_scan_seconds(analysis, tokens_per_second=800, concurrency=5) == (0.0, 0.0)


def test_estimate_from_sample_extrapolates() -> None:
    # 10 files at concurrency 5 = 2 batches; ~3s/file → ~6s point, ±40%.
    low, high = estimate_from_sample(file_count=10, concurrency=5, sample_seconds=3.0)
    assert low < high
    assert low == pytest.approx(6.0 * 0.6)
    assert high == pytest.approx(6.0 * 1.4)


def test_estimate_from_sample_zero_files() -> None:
    assert estimate_from_sample(file_count=0, concurrency=5, sample_seconds=3.0) == (0.0, 0.0)

