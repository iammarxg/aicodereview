"""OpenRouter implementation of ``LLMProvider`` (plan §4.1).

Two distinct retry layers, kept separate on purpose (review §3):
  1. Transport-level: exponential backoff on HTTP 429/5xx via ``tenacity``.
  2. Content-level: one re-ask with a stricter system prompt if the response
     isn't valid JSON (plan §7).
No OpenRouter-specific detail leaks upward — callers only see ``ReviewComment``.
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

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class _RetryableHTTP(Exception):
    """Internal marker for HTTP statuses worth retrying (429/5xx)."""


class OpenRouterProvider(LLMProvider):
    """Reviews diffs using any model exposed through the OpenRouter API."""

    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("OpenRouter API key is required.")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = client  # injectable for tests

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Optional attribution headers recommended by OpenRouter.
            "HTTP-Referer": "https://github.com/iammarxg/aicodereview",
            "X-Title": "aicr",
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
                OPENROUTER_URL,
                headers=self._headers(),
                json={"model": self.model, "messages": messages, "temperature": 0},
                timeout=self.timeout,
            )
        except httpx.RequestError as exc:
            raise ProviderError(f"Network error contacting OpenRouter: {exc}") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise _RetryableHTTP(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ProviderError(
                f"OpenRouter returned HTTP {response.status_code}: {response.text[:300]}"
            )

        data = response.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Unexpected OpenRouter response shape: {data}") from exc

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
                try:
                    return parse_comments(retry_raw, diff_file)
                except MalformedResponseError:
                    # Give up on this file only; caller counts it as an error.
                    raise
        finally:
            if owns_client:
                await client.aclose()
