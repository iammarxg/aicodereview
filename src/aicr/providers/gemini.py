"""Google Gemini implementation of ``LLMProvider``.

Gemini exposes an **OpenAI-compatible** chat-completions endpoint, so this
provider mirrors ``OpenRouterProvider`` almost exactly — same message shape, same
``usage`` block, same shared ``parse_comments`` — with a Gemini base URL and the
``GEMINI_API_KEY`` for auth. That's the whole point of the ``LLMProvider``
adapter: a new cloud model is additive, not a rewrite.

Gemini's free tier is generous, which makes it a friendly default for new users
who don't want to fund an OpenRouter balance. Account-level credit usage isn't
exposed through this compatibility layer, so ``account_usage`` stays ``None``
(the token line still shows; only the "% API usage" bar is absent).
"""

from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from aicr.models import Category, DiffFile, ReviewComment
from aicr.prompts.builder import (
    build_retry_system_prompt,
    build_system_prompt,
    build_user_prompt,
)
from aicr.providers.base import (
    LLMProvider,
    MalformedResponseError,
    ProviderError,
    parse_comments,
)

# Google's OpenAI-compatibility base. The provider appends /chat/completions.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
# A fast, cheap, widely-available default on the free tier.
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"


class _RetryableHTTP(Exception):
    """Internal marker for HTTP statuses worth retrying (429/5xx)."""


class GeminiProvider(LLMProvider):
    """Reviews diffs using a Google Gemini model via its OpenAI-compatible API."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = GEMINI_BASE_URL,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__()
        if not api_key:
            raise ProviderError("Gemini API key is required (GEMINI_API_KEY).")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client  # injectable for tests

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @retry(
        retry=retry_if_exception_type(_RetryableHTTP),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _chat(self, client: httpx.AsyncClient, messages: list[dict[str, str]]) -> str:
        """One chat completion call, with transport-level backoff on 429/5xx."""
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={"model": self.model, "messages": messages, "temperature": 0},
                timeout=self.timeout,
            )
        except httpx.RequestError as exc:
            raise ProviderError(f"Network error contacting Gemini: {exc}") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise _RetryableHTTP(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ProviderError(
                f"Gemini returned HTTP {response.status_code}: {response.text[:300]}"
            )

        data = response.json()
        self._record_response_usage(data)
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Unexpected Gemini response shape: {data}") from exc

    def _record_response_usage(self, data: object) -> None:
        """Pull the ``usage`` block (OpenAI-compatible) into the running total."""
        if not isinstance(data, dict):
            return
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return
        self._record_usage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
        )

    async def review(
        self,
        diff_file: DiffFile,
        categories: list[Category],
        languages: list[str],
    ) -> list[ReviewComment]:
        system_prompt = build_system_prompt(categories)
        user_prompt = build_user_prompt(diff_file, languages)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            raw = await self._chat(client, messages)
            try:
                return parse_comments(raw, diff_file)
            except MalformedResponseError:
                # Content-level retry: one stricter re-ask (plan §7).
                messages[0]["content"] = build_retry_system_prompt(system_prompt)
                retry_raw = await self._chat(client, messages)
                return parse_comments(retry_raw, diff_file)
        finally:
            if owns_client:
                await client.aclose()
