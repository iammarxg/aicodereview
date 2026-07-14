"""Ollama implementation of ``LLMProvider`` — fully local, no data leaves the box.

This directly addresses the privacy caveat in the README: with Ollama, diffs are
reviewed by a model running on ``localhost`` instead of a third-party API. It's
also the second concrete provider, proving the adapter pattern (plan §4.1) pays
off — the engine, prompts, and renderer are untouched.

Uses Ollama's OpenAI-compatible ``/v1/chat/completions`` endpoint, so response
parsing and token-usage extraction mirror the OpenRouter provider. No API key is
required. Account-level usage is ``None`` (local models have no billing).
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

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"


class _RetryableHTTP(Exception):
    """Internal marker for HTTP statuses worth retrying (5xx)."""


class OllamaProvider(LLMProvider):
    """Reviews diffs using a local model served by Ollama (no network egress)."""

    name = "ollama"

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__()
        # Local models can be slow; default to a generous timeout.
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client  # injectable for tests

    @retry(
        retry=retry_if_exception_type(_RetryableHTTP),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _chat(self, client: httpx.AsyncClient, messages: list[dict[str, str]]) -> str:
        """One chat completion call against the local Ollama server."""
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json={"model": self.model, "messages": messages, "temperature": 0},
                timeout=self.timeout,
            )
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"Could not reach Ollama at {self.base_url}. "
                "Is it running? Try `ollama serve` and `ollama pull <model>`."
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Network error contacting Ollama: {exc}") from exc

        if response.status_code >= 500:
            raise _RetryableHTTP(f"HTTP {response.status_code}")
        if response.status_code == 404:
            raise ProviderError(
                f"Ollama has no model {self.model!r}. Pull it first: "
                f"`ollama pull {self.model}`."
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:300]}"
            )

        data = response.json()
        self._record_response_usage(data)
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Unexpected Ollama response shape: {data}") from exc

    def _record_response_usage(self, data: object) -> None:
        """Record token counts from the OpenAI-compatible ``usage`` block, if any."""
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
