"""Map a provider name (from config) to a concrete ``LLMProvider`` class.

Adding a new provider later (OpenAI direct, Anthropic direct, ...) is purely
additive: implement ``LLMProvider`` and register it here. Nothing else changes.
"""

from __future__ import annotations

from collections.abc import Callable

from aicr.config import Config
from aicr.providers.base import LLMProvider, ProviderError
from aicr.providers.ollama import OllamaProvider
from aicr.providers.openrouter import OpenRouterProvider


def _build_openrouter(config: Config) -> LLMProvider:
    kwargs: dict[str, object] = {"api_key": config.api_key or "", "model": config.model}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return OpenRouterProvider(**kwargs)  # type: ignore[arg-type]


def _build_ollama(config: Config) -> LLMProvider:
    kwargs: dict[str, object] = {"model": config.model}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return OllamaProvider(**kwargs)  # type: ignore[arg-type]


_FACTORIES: dict[str, Callable[[Config], LLMProvider]] = {
    "openrouter": _build_openrouter,
    "ollama": _build_ollama,
}


def available_providers() -> list[str]:
    """Names of all registered providers."""
    return sorted(_FACTORIES)


def build_provider(config: Config) -> LLMProvider:
    """Instantiate the provider named in ``config.provider``.

    Raises:
        ProviderError: If the configured provider name is unknown.
    """
    factory = _FACTORIES.get(config.provider)
    if factory is None:
        raise ProviderError(
            f"Unknown provider {config.provider!r}. "
            f"Available: {', '.join(available_providers())}."
        )
    return factory(config)
