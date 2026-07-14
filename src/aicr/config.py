"""Configuration loading: ``.aicr.yaml`` + environment (``.env``) into a Config.

Precedence (highest first): explicit CLI flags (applied by the caller) >
environment variables > ``.aicr.yaml`` > built-in defaults. The API key is
*never* read from the YAML file — only from the environment.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from aicr.models import Category, Severity, normalize_category

# `openrouter/free` is a virtual router that load-balances across OpenRouter's
# free models, which greatly reduces 429 rate-limit failures for new users with a
# fresh key. Override per-repo in `.aicr.yaml` or per-run with `--model`.
DEFAULT_MODEL = "openrouter/free"

DEFAULT_CATEGORIES = ["bugs", "security", "readability", "style"]
CONFIG_FILENAME = ".aicr.yaml"

# Valid severities, in ascending order (used to validate threshold settings).
_SEVERITIES: tuple[Severity, ...] = ("info", "warning", "critical")

# Which environment variable holds each cloud provider's API key. Local
# providers (e.g. Ollama) are absent here — they need no key. Adding a provider
# is purely additive: register its key var here and a factory in the registry.
PROVIDER_API_KEY_ENV: dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Providers that talk to a remote API and therefore need an API key.
_PROVIDERS_REQUIRING_KEY = set(PROVIDER_API_KEY_ENV)


def api_key_env_var(provider: str) -> str | None:
    """Return the env var name holding ``provider``'s API key, or None if local."""
    return PROVIDER_API_KEY_ENV.get(provider)



class ConfigError(Exception):
    """Raised for user-facing configuration problems (printed without a traceback)."""


class Config(BaseModel):
    """Resolved runtime configuration for a review run."""

    provider: str = "openrouter"
    model: str = DEFAULT_MODEL
    # Override the provider's API endpoint (e.g. a local Ollama URL or a proxy).
    base_url: str | None = None
    categories: list[Category] = Field(
        default_factory=lambda: [normalize_category(c) for c in DEFAULT_CATEGORIES]
    )
    languages: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    max_diff_lines_per_file: int = 800
    max_files_per_review: int = 50
    concurrency: int = 5
    severity_display_threshold: Severity = "info"
    # Blocking mode (opt-in). None = never block (warn-only default, plan §8).
    # When set (e.g. "critical"), a comment at/above this severity makes `aicr
    # review` exit non-zero, which aborts the commit from the pre-commit hook.
    severity_block_threshold: Severity | None = None
    # Hunk-level result cache (skip re-reviewing unchanged files across commits).
    cache_enabled: bool = True

    # Not persisted to YAML — sourced from the environment only.
    api_key: str | None = None

    @field_validator("categories", mode="before")
    @classmethod
    def _normalize_categories(cls, value: object) -> object:
        if isinstance(value, list):
            return [normalize_category(str(v)) for v in value]
        return value

    @field_validator("severity_display_threshold", "severity_block_threshold", mode="before")
    @classmethod
    def _validate_severity(cls, value: object) -> object:
        """Reject typos in severity thresholds instead of silently misbehaving."""
        if value is None:
            return value
        text = str(value).strip().lower()
        if text not in _SEVERITIES:
            raise ValueError(
                f"Invalid severity {value!r}. Valid: {', '.join(_SEVERITIES)}."
            )
        return text

    def requires_api_key(self) -> bool:
        """True if the configured provider needs ``OPENROUTER_API_KEY``."""
        return self.provider in _PROVIDERS_REQUIRING_KEY


def _find_config_file(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a ``.aicr.yaml`` (repo-root friendly)."""
    for directory in [start, *start.parents]:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(
    cwd: Path | None = None,
    *,
    require_api_key: bool = True,
    load_env: bool = True,
) -> Config:
    """Load configuration from ``.aicr.yaml`` and the environment.

    Args:
        cwd: Directory to start searching from (defaults to the process cwd).
        require_api_key: If True *and the provider needs one*, raise a friendly
            ``ConfigError`` when the key is missing — before any network call
            (plan §6/§8). Local providers (Ollama) never require a key.
        load_env: If True, load a local ``.env`` file into the environment first.

    Raises:
        ConfigError: On malformed YAML or a missing required API key.
    """
    cwd = cwd or Path.cwd()
    if load_env:
        load_dotenv(cwd / ".env")

    data: dict[str, object] = {}
    config_path = _find_config_file(cwd)
    if config_path is not None:
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Could not parse {config_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"{config_path} must contain a YAML mapping at the top level.")
        data = loaded
        # Guard against a key accidentally committed to the shared config file.
        data.pop("api_key", None)
        data.pop("openrouter_api_key", None)

    try:
        config = Config.model_validate(data)
    except Exception as exc:  # pydantic ValidationError -> friendly message
        raise ConfigError(f"Invalid configuration in {config_path}: {exc}") from exc

    # Read the key from the provider's own env var (falls back to OpenRouter's
    # for unknown/legacy providers so nothing regresses).
    env_var = api_key_env_var(config.provider) or "OPENROUTER_API_KEY"
    config.api_key = os.environ.get(env_var)

    if require_api_key and config.requires_api_key() and not config.api_key:
        raise ConfigError(_missing_key_message(config.provider, env_var))
    return config


def _missing_key_message(provider: str, env_var: str) -> str:
    """Friendly, provider-specific 'key is missing' guidance."""
    key_urls = {
        "openrouter": "https://openrouter.ai/keys",
        "gemini": "https://aistudio.google.com/apikey",
    }
    url = key_urls.get(provider, "your provider's dashboard")
    return (
        f"{env_var} is not set.\n"
        "  1. Copy .env.example to .env (or edit your shell env)\n"
        f"  2. Add your key from {url}\n"
        f"  (or export {env_var} in your shell)\n"
        "  Tip: run `aicr init`, or use a local provider with `provider: ollama`."
    )

