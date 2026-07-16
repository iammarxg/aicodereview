"""SARIF 2.1.0 output for a ``ReviewResult`` — for CI code-scanning dashboards.

SARIF (Static Analysis Results Interchange Format) is the format GitHub code
scanning, Azure DevOps, and other CI systems ingest to show findings as
first-class annotations on a PR. Emitting it lets ``aicr review --format sarif``
(and ``aicr scan``) feed those pipelines without a bespoke integration.

Like ``json_renderer``, this is a pure consumer of ``ReviewResult`` — it changes
nothing upstream (plan §13). The mapping is deliberately small and stable:

- one ``run`` with tool driver ``aicr`` + version,
- one ``rule`` per review category (so results group cleanly in dashboards),
- one ``result`` per ``ReviewComment`` (path + line + severity + message).
"""

from __future__ import annotations

import json

from aicr import __version__
from aicr.models import Category, ReviewComment, ReviewResult, Severity

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
INFORMATION_URI = "https://github.com/iammarxg/aicodereview"

# aicr severity -> SARIF result level. SARIF has no "critical"; "error" is the
# most severe level, so critical maps there. info maps to "note" (the least
# severe), matching how dashboards de-emphasize informational findings.
_LEVEL_BY_SEVERITY: dict[Severity, str] = {
    "critical": "error",
    "warning": "warning",
    "info": "note",
}

# One stable rule per category. The id is what dashboards group and filter on,
# so it must never change once shipped. Order here is the order rules appear.
_RULES: list[tuple[Category, str]] = [
    ("bug", "Possible bug or logic error"),
    ("security", "Potential security issue"),
    ("readability", "Readability concern"),
    ("style", "Style suggestion"),
]

# Map a category to its 0-based index in the driver's ``rules`` array, so each
# result can reference its rule by both id and index (SARIF allows/encourages both).
_RULE_INDEX: dict[Category, int] = {cat: i for i, (cat, _) in enumerate(_RULES)}


def _rule_object(category: Category, description: str) -> dict[str, object]:
    """One reporting-descriptor (rule) entry for the tool driver."""
    return {
        "id": category,
        "name": category.capitalize(),
        "shortDescription": {"text": description},
    }


def _result_object(comment: ReviewComment) -> dict[str, object]:
    """Map a single ``ReviewComment`` to a SARIF result."""
    return {
        "ruleId": comment.category,
        "ruleIndex": _RULE_INDEX[comment.category],
        "level": _LEVEL_BY_SEVERITY[comment.severity],
        "message": {"text": comment.comment},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": comment.file},
                    # SARIF regions are 1-based; our line numbers already are.
                    "region": {"startLine": max(1, comment.line)},
                }
            }
        ],
    }


def build_sarif(result: ReviewResult) -> dict[str, object]:
    """Build the SARIF document as a plain dict (also useful for tests)."""
    rules = [_rule_object(cat, desc) for cat, desc in _RULES]
    results = [_result_object(c) for c in result.comments]
    return {
        "version": SARIF_VERSION,
        "$schema": SARIF_SCHEMA,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "aicr",
                        "informationUri": INFORMATION_URI,
                        "version": __version__,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def render(result: ReviewResult, *, indent: int | None = 2) -> str:
    """Serialize a ``ReviewResult`` to a SARIF 2.1.0 JSON string."""
    return json.dumps(build_sarif(result), indent=indent)
