"""Map a provider name (from config) to a concrete ``LLMProvider`` class.

Adding a new provider later (OpenAI direct, Anthropic direct, Ollama) is purely
additive: implement ``LLMProvider`` and register it here. Nothing else changes.
"""

from __future__ import annotations

from aicr.config import Config
from aicr.providers.base import LLMProvider, ProviderError
from aicr.providers.openrouter import OpenRouterProvider

_FACTORIES = {
    "openrouter": lambda config: OpenRouterProvider(
        api_key=config.api_key or "",
        model=config.model,
    ),
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
