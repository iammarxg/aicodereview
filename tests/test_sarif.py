"""Tests for the SARIF 2.1.0 renderer (B3) — shape + severity/level mapping."""

from __future__ import annotations

import json

from aicr import __version__
from aicr.models import ReviewComment, ReviewResult
from aicr.report import sarif_renderer


def _result(comments: list[ReviewComment]) -> ReviewResult:
    return ReviewResult(
        files_reviewed=1,
        comments=comments,
        provider="fake",
        model="test-model",
        duration_seconds=0.1,
    )


def test_empty_result_is_valid_sarif_shape() -> None:
    doc = sarif_renderer.build_sarif(_result([]))
    assert doc["version"] == "2.1.0"
    assert "$schema" in doc
    runs = doc["runs"]
    assert isinstance(runs, list) and len(runs) == 1
    driver = runs[0]["tool"]["driver"]
    assert driver["name"] == "aicr"
    assert driver["version"] == __version__
    # One rule per category, always present so results group cleanly.
    rule_ids = [r["id"] for r in driver["rules"]]
    assert rule_ids == ["bug", "security", "readability", "style"]
    assert runs[0]["results"] == []


def test_render_is_parseable_json() -> None:
    text = sarif_renderer.render(_result([]))
    parsed = json.loads(text)
    assert parsed["version"] == "2.1.0"


def test_severity_maps_to_sarif_level() -> None:
    comments = [
        ReviewComment(file="a.py", line=3, category="bug", severity="critical", comment="boom"),
        ReviewComment(file="a.py", line=5, category="style", severity="warning", comment="meh"),
        ReviewComment(file="b.py", line=9, category="security", severity="info", comment="fyi"),
    ]
    results = sarif_renderer.build_sarif(_result(comments))["runs"][0]["results"]
    levels = [r["level"] for r in results]
    assert levels == ["error", "warning", "note"]


def test_result_maps_location_and_rule() -> None:
    comment = ReviewComment(
        file="src/app.py", line=42, category="security", severity="critical", comment="SQLi"
    )
    result = sarif_renderer.build_sarif(_result([comment]))["runs"][0]["results"][0]
    assert result["ruleId"] == "security"
    # ruleIndex must point at the "security" rule in the driver's rules array.
    assert result["ruleIndex"] == 1
    assert result["message"]["text"] == "SQLi"
    loc = result["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/app.py"
    assert loc["region"]["startLine"] == 42


def test_line_floored_to_one() -> None:
    # SARIF regions are 1-based; a stray 0/negative line must not produce an
    # invalid region.
    comment = ReviewComment(file="a.py", line=0, category="bug", comment="x")
    result = sarif_renderer.build_sarif(_result([comment]))["runs"][0]["results"][0]
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 1
