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

from aicr.models import Category, normalize_category

# `openrouter/free` is a virtual router that load-balances across OpenRouter's
# free models, which greatly reduces 429 rate-limit failures for new users with a
# fresh key. Override per-repo in `.aicr.yaml` or per-run with `--model`.
DEFAULT_MODEL = "openrouter/free"

DEFAULT_CATEGORIES = ["bugs", "security", "readability", "style"]
CONFIG_FILENAME = ".aicr.yaml"


class ConfigError(Exception):
    """Raised for user-facing configuration problems (printed without a traceback)."""


class Config(BaseModel):
    """Resolved runtime configuration for a review run."""

    provider: str = "openrouter"
    model: str = DEFAULT_MODEL
    categories: list[Category] = Field(
        default_factory=lambda: [normalize_category(c) for c in DEFAULT_CATEGORIES]
    )
    languages: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    max_diff_lines_per_file: int = 800
    max_files_per_review: int = 50
    concurrency: int = 5
    severity_display_threshold: str = "info"

    # Not persisted to YAML — sourced from the environment only.
    api_key: str | None = None

    @field_validator("categories", mode="before")
    @classmethod
    def _normalize_categories(cls, value: object) -> object:
        if isinstance(value, list):
            return [normalize_category(str(v)) for v in value]
        return value


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
        require_api_key: If True, raise a friendly ``ConfigError`` when the key is
            missing — this happens *before* any network call (plan §6/§8).
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

    config.api_key = os.environ.get("OPENROUTER_API_KEY")

    if require_api_key and not config.api_key:
        raise ConfigError(
            "OPENROUTER_API_KEY is not set.\n"
            "  1. Copy .env.example to .env\n"
            "  2. Add your key from https://openrouter.ai/keys\n"
            "  (or export OPENROUTER_API_KEY in your shell)"
        )
    return config
