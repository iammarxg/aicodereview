"""``LLMProvider`` interface + shared response parsing/validation (plan §4.1, §7).

The interface is async so multi-file reviews can run concurrently
(``asyncio.gather``) with the event loop owned by the CLI (review §2.1).
Response parsing and the out-of-range line drop (review §2.3) live here so every
provider gets correct, safe behavior for free.

Usage accounting is also shared here: providers call ``_record_usage`` per API
call to accumulate ``TokenUsage``, and may override ``account_usage`` to report
account-level credit consumption. Both are provider-agnostic — the engine and
renderer read them without knowing which provider produced them.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod

from pydantic import ValidationError

from aicr.models import AccountUsage, Category, DiffFile, ReviewComment, TokenUsage


class ProviderError(Exception):
    """Raised for user-facing provider/network problems (never blocks a commit)."""


class MalformedResponseError(Exception):
    """The LLM returned text that could not be parsed into valid comments."""


_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Minimum self-rated confidence for a comment to survive parsing. The prompts
# (v0.4.2) tell the model to only report findings it's ≥90% sure are real and to
# self-rate each one; this is the server-side backstop that drops overconfident
# noise even when a model ignores that instruction. Comments without a confidence
# default to 1.0 (see ``ReviewComment``), so this never penalizes providers/models
# that don't emit the field.
MIN_CONFIDENCE = 0.9



def _extract_json_array(text: str) -> str:
    """Best-effort extraction of a JSON array from an LLM response.

    Strips markdown fences and, if there's leading/trailing prose, slices from
    the first ``[`` to the last ``]``.
    """
    cleaned = _JSON_FENCE.sub("", text).strip()
    if cleaned.startswith("["):
        return cleaned
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def parse_comments(raw_text: str, diff_file: DiffFile) -> list[ReviewComment]:
    """Parse raw LLM text into validated ``ReviewComment``s for one file.

    Enforces the plan's contract *and* the review's line-mapping safety net:
    - JSON must decode to a list.
    - Each item must validate against ``ReviewComment``.
    - The ``file`` field is forced to the real path (models occasionally rename).
    - Any comment whose ``line`` is NOT a changed line in this file is dropped
      (review §2.3) — this is the guard against hallucinated line numbers.

    Raises:
        MalformedResponseError: If the text can't be parsed/validated at all.
    """
    payload = _extract_json_array(raw_text)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise MalformedResponseError(f"Response was not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise MalformedResponseError("Response JSON was not an array.")

    changed_lines = diff_file.changed_line_numbers()
    comments: list[ReviewComment] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        item["file"] = diff_file.path  # trust our path, not the model's
        try:
            comment = ReviewComment(**item)
        except ValidationError:
            continue  # drop individual malformed items rather than fail the file
        if changed_lines and comment.line not in changed_lines:
            continue  # hallucinated / context line — drop it
        if comment.confidence < MIN_CONFIDENCE:
            continue  # low-confidence / speculative — drop it (anti-hallucination)
        comments.append(comment)
    return comments



class LLMProvider(ABC):
    """Adapter interface: send a file's diff, get validated comments back."""

    name: str = "base"

    def __init__(self) -> None:
        # Per-run token accounting, accumulated across every API call this
        # provider instance makes. The engine reads ``.usage`` when done.
        self.usage = TokenUsage()

    def _record_usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        """Add one API call's token counts to this provider's running total.

        Providers extract these numbers from their own response shape (e.g.
        OpenRouter/OpenAI ``usage``) and call this — keeping the accumulation
        logic in one shared place regardless of provider.
        """
        self.usage.add(
            TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        )

    @abstractmethod
    async def review(
        self,
        diff_file: DiffFile,
        categories: list[Category],
        languages: list[str],
    ) -> list[ReviewComment]:
        """Review one file's diff and return validated, structured comments.

        Takes a ``DiffFile`` (not a raw string) so the provider can render the
        line-numbered prompt via ``diff_file.to_prompt_text()`` and reuse the
        file's changed-line set for the out-of-range guard (review §2.2/§2.3).
        """
        ...

    async def account_usage(self) -> AccountUsage | None:
        """Return account-level credit usage/limit, if the provider exposes it.

        Default: ``None`` (unknown). Providers that can report spend/limit (e.g.
        OpenRouter's ``/api/v1/key`` endpoint) override this so the renderer can
        show a "% API usage" bar. Must never raise — return ``None`` on failure.
        """
        return None
