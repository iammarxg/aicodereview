"""Assemble system + user prompts from selected categories and languages.

Only the templates for *selected* categories are concatenated (review §3), so the
prompt stays lean. Category order is normalized to keep prompts deterministic and
snapshot-testable.
"""

from __future__ import annotations

from functools import cache
from importlib import resources

from aicr.models import Category, DiffFile

# Deterministic order regardless of how the user listed categories.
CATEGORY_ORDER: list[Category] = ["bug", "security", "readability", "style"]

_TEMPLATE_FILE: dict[Category, str] = {
    "bug": "bugs.txt",
    "security": "security.txt",
    "readability": "readability.txt",
    "style": "style.txt",
}


@cache
def _read_template(name: str) -> str:
    """Read a template file from the packaged ``templates/`` directory."""
    return (
        resources.files("aicr.prompts.templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
        .strip()
    )


def build_system_prompt(categories: list[Category]) -> str:
    """Build the system prompt: base contract + selected category guidance."""
    ordered = [c for c in CATEGORY_ORDER if c in set(categories)]
    if not ordered:
        raise ValueError("At least one review category must be selected.")

    parts = [_read_template("system_base.txt"), "", "REVIEW CATEGORIES:"]
    parts.extend(_read_template(_TEMPLATE_FILE[c]) for c in ordered)
    return "\n\n".join(parts)


def build_user_prompt(diff_file: DiffFile, languages: list[str]) -> str:
    """Build the per-file user prompt with line-numbered, marked diff hunks."""
    language = diff_file.language or "unknown"
    declared = ", ".join(languages) if languages else "not specified"

    return (
        f"File: {diff_file.path}\n"
        f"Detected language: {language}\n"
        f"Languages in scope for this project: {declared}\n\n"
        "Below are the changed hunks. Each line is prefixed with its new-file "
        'line number and a marker: "+" means added/modified (review these), '
        'a space means unchanged context (do NOT comment on these).\n\n'
        "```\n"
        f"{diff_file.to_prompt_text()}\n"
        "```\n\n"
        "Return ONLY the JSON array of comments as specified."
    )


def build_retry_system_prompt(base_system_prompt: str) -> str:
    """Stricter system prompt used on the single JSON-repair retry (plan §7)."""
    return (
        "Your previous response was not valid JSON matching the required schema.\n"
        "Return ONLY a JSON array — no prose, no markdown code fences, no keys "
        "outside the schema. If there are no issues, return exactly: []\n\n"
        + base_system_prompt
    )
