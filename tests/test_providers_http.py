"""Provider HTTP behavior via httpx.MockTransport — no real network calls.

Covers token-usage extraction, OpenRouter account usage (/key), the Ollama
provider, and the registry wiring both providers + base_url.
"""

from __future__ import annotations

import httpx
import pytest

from aicr.config import Config
from aicr.models import DiffFile
from aicr.providers.base import ProviderError
from aicr.providers.gemini import GeminiProvider
from aicr.providers.ollama import OllamaProvider
from aicr.providers.openrouter import OpenRouterProvider
from aicr.providers.registry import available_providers, build_provider


def _file() -> DiffFile:
    return DiffFile.model_validate(
        {
            "path": "a.py",
            "language": "python",
            "hunks": [
                {"start_line": 4, "lines": [{"kind": "added", "line_no": 4, "content": "x = 1"}]}
            ],
        }
    )


_COMMENTS_JSON = '[{"file":"a.py","line":4,"category":"bug","severity":"info","comment":"c"}]'


def _chat_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": _COMMENTS_JSON}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


async def test_openrouter_records_token_usage() -> None:
    transport = httpx.MockTransport(_chat_response)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenRouterProvider(api_key="k", model="m", client=client)
        comments = await provider.review(_file(), ["bug"], ["python"])
    assert len(comments) == 1
    assert provider.usage.prompt_tokens == 100
    assert provider.usage.completion_tokens == 20
    assert provider.usage.total_tokens == 120


async def test_openrouter_account_usage_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"usage": 0.42, "limit": 1.0}})
        return _chat_response(request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenRouterProvider(api_key="k", model="m", client=client)
        usage = await provider.account_usage()
    assert usage is not None
    assert usage.used == 0.42
    assert usage.limit == 1.0
    assert usage.percent == 42.0


async def test_openrouter_account_usage_none_on_error() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenRouterProvider(api_key="k", model="m", client=client)
        assert await provider.account_usage() is None


async def test_ollama_reviews_and_needs_no_key() -> None:
    transport = httpx.MockTransport(_chat_response)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(model="llama3.1", client=client)
        comments = await provider.review(_file(), ["bug"], ["python"])
    assert len(comments) == 1
    assert provider.usage.total_tokens == 120
    # Local models have no billing.
    assert await provider.account_usage() is None


async def test_gemini_reviews_and_records_usage() -> None:
    transport = httpx.MockTransport(_chat_response)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider(api_key="k", model="gemini-2.0-flash", client=client)
        comments = await provider.review(_file(), ["bug"], ["python"])
    assert len(comments) == 1
    assert provider.usage.total_tokens == 120
    # Credit usage isn't exposed via the OpenAI-compat layer.
    assert await provider.account_usage() is None


async def test_gemini_requires_key() -> None:
    with pytest.raises(ProviderError):
        GeminiProvider(api_key="", model="gemini-2.0-flash")


async def test_ollama_missing_model_is_friendly() -> None:

    transport = httpx.MockTransport(lambda req: httpx.Response(404, text="not found"))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(model="nope", client=client)
        with pytest.raises(ProviderError) as exc:
            await provider.review(_file(), ["bug"], ["python"])
    assert "ollama pull" in str(exc.value)


def test_registry_lists_all_providers() -> None:
    assert set(available_providers()) == {"openrouter", "gemini", "ollama"}


def test_registry_builds_gemini() -> None:
    config = Config(provider="gemini", model="gemini-2.0-flash", api_key="k")
    provider = build_provider(config)
    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-2.0-flash"



def test_registry_builds_ollama_with_base_url() -> None:
    config = Config(provider="ollama", model="llama3.1", base_url="http://host:1234/v1")
    provider = build_provider(config)
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://host:1234/v1"


def test_registry_unknown_provider_raises() -> None:
    with pytest.raises(ProviderError):
        build_provider(Config(provider="does-not-exist", api_key="k"))
