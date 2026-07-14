"""Machine-readable JSON output for a ``ReviewResult``.

This renderer is the seam for future consumers (CI, editor extensions, a web
dashboard) — none of them require the core pipeline to change (plan §13).
"""

from __future__ import annotations

from aicr.models import ReviewResult


def render(result: ReviewResult, *, indent: int | None = 2) -> str:
    """Serialize a ``ReviewResult`` to a JSON string."""
    return result.model_dump_json(indent=indent)
