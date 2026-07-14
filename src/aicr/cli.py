"""``click`` entry points for the ``aicr`` command.

Commands: ``review``, ``init`` (interactive setup), ``enable``/``disable`` (git
hook), ``install-hook`` / ``uninstall-hook`` (kept as aliases), and ``config``.

The CLI owns the asyncio event loop (``asyncio.run``) — click itself is sync, so
each command calls into async code at the boundary (review §2.1/§3). ``review``
is warn-only by default: infra failures print a warning and exit 0, never
blocking the commit (plan §8). Opt-in blocking mode (``--strict`` /
``severity_block_threshold``) is the sole exception.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import click

from aicr import __version__
from aicr.cache import ReviewCache
from aicr.config import DEFAULT_MODEL, Config, ConfigError, load_config
from aicr.diff.source import DiffMode, DiffSourceError, LocalGitSource
from aicr.engine import run_review
from aicr.models import ReviewResult, Severity
from aicr.providers.base import ProviderError
from aicr.providers.ollama import DEFAULT_OLLAMA_BASE_URL
from aicr.providers.registry import build_provider
from aicr.report import cli_renderer, json_renderer

# Quick-reference shown on `aicr --help`. The leading `\b` tells click not to
# rewrap this block, so the columns stay aligned.
_EPILOG = """\
\b
Quick start:
  aicr init                   Interactive setup (provider, key, hook)
  aicr enable                 Install the pre-commit hook in this repo
  git add .  &&  git commit   Review runs automatically before the commit
  aicr review                 Review staged changes on demand (no commit)

\b
Common flags:
  aicr review --unstaged      Review working-tree changes (not just staged)
  aicr review --range A..B    Review a commit range, e.g. main..HEAD
  aicr review --strict        Block (non-zero exit) on critical findings
  aicr review --format json   Machine-readable output (for CI/editors)
  aicr review --no-cache      Ignore the hunk cache for this run

\b
Config & secrets:
  .aicr.yaml                  Per-repo settings (provider, categories, model)
  OPENROUTER_API_KEY          Your key, from env or a gitignored .env file
  AICR_SKIP=1 git commit      Bypass the review for one commit
"""


def _warn(message: str) -> None:
    click.echo(click.style(f"aicr: {message}", fg="yellow"), err=True)


def _error(message: str) -> None:
    click.echo(click.style(f"aicr: {message}", fg="red"), err=True)


def _split_patterns(value: str | None) -> list[str]:
    """Split a whitespace/comma-separated flag value into a clean list.

    Lets ``--include "a.yml b.yml"`` and ``--include a.yml,b.yml`` both work.
    """
    if not value:
        return []
    return [item for item in re.split(r"[,\s]+", value.strip()) if item]


@click.group(epilog=_EPILOG)
@click.version_option(__version__, prog_name="aicr")
def cli() -> None:
    """AI Code Review — review your staged git diff before you commit."""


@cli.command()
@click.option("--staged", is_flag=True, help="Review staged changes (default).")
@click.option("--unstaged", is_flag=True, help="Review unstaged working-tree changes instead.")
@click.option("--range", "diff_range", default=None, help="Review a commit range, e.g. main..HEAD.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["cli", "json"]),
    default="cli",
    help="Output format.",
)
@click.option("--no-color", is_flag=True, help="Disable colored output.")
@click.option(
    "--category",
    "categories",
    multiple=True,
    type=click.Choice(["bugs", "security", "readability", "style"]),
    help="Override review categories (repeatable).",
)
@click.option("--model", default=None, help="Override the model for this run.")
@click.option(
    "--include",
    "include",
    default=None,
    help='Force-include file(s) that .aicr.yaml excludes, e.g. --include "docker-compose.yml".',
)
@click.option("--strict", is_flag=True, help="Block the commit (non-zero exit) on critical issues.")
@click.option("--no-cache", is_flag=True, help="Ignore the hunk cache for this run.")
@click.option("--debug", is_flag=True, help="Show full tracebacks and verbose errors.")
def review(
    staged: bool,
    unstaged: bool,
    diff_range: str | None,
    output_format: str,
    no_color: bool,
    categories: tuple[str, ...],
    model: str | None,
    include: str | None,
    strict: bool,
    no_cache: bool,
    debug: bool,
) -> None:
    """Review changes and print line-mapped comments (warn-only unless --strict)."""
    # Escape hatch — bypass entirely (review §6). Also honored by the shell hook.
    if os.environ.get("AICR_SKIP") == "1":
        raise SystemExit(0)

    if unstaged and diff_range:
        _error("Use only one of --unstaged or --range.")
        raise SystemExit(0)

    force_include = _split_patterns(include)
    mode = DiffMode.RANGE if diff_range else DiffMode.UNSTAGED if unstaged else DiffMode.STAGED
    try:
        config = _resolve_config(categories=categories, model=model)
        source = LocalGitSource(
            mode=mode,
            diff_range=diff_range,
            exclude_paths=config.exclude_paths,
            force_include=force_include,
        )
        files = source.get_diff_files()
    except (ConfigError, DiffSourceError) as exc:
        # Config/diff problems are user-actionable but must not block the commit.
        _error(str(exc))
        raise SystemExit(0) from None
    except Exception as exc:  # pragma: no cover - unexpected
        if debug:
            raise
        _error(f"unexpected error: {exc}")
        raise SystemExit(0) from None

    if not files:
        click.echo("Nothing to review.")
        raise SystemExit(0)

    try:
        provider = build_provider(config)
        cache = ReviewCache(Path.cwd(), enabled=config.cache_enabled and not no_cache)
        result: ReviewResult = asyncio.run(run_review(provider, files, config, cache=cache))
    except ProviderError as exc:
        _warn(f"review skipped ({exc}). Commit continues. Set AICR_SKIP=1 to silence.")
        raise SystemExit(0) from None
    except Exception as exc:  # pragma: no cover - unexpected
        if debug:
            raise
        _warn(f"review skipped (unexpected error: {exc}). Commit continues.")
        raise SystemExit(0) from None

    if output_format == "json":
        click.echo(json_renderer.render(result))
    else:
        use_color = not no_color and sys.stdout.isatty()
        click.echo(
            cli_renderer.render(
                result,
                use_color=use_color,
                display_threshold=config.severity_display_threshold,
            )
        )

    _maybe_block(result, config, strict=strict)
    # Warn-only default: exit 0 (plan §3 step 7).
    raise SystemExit(0)


def _maybe_block(result: ReviewResult, config: Config, *, strict: bool) -> None:
    """Exit non-zero if blocking is on and a finding meets the threshold.

    Blocking is fully opt-in: it triggers only when ``--strict`` is passed or
    ``severity_block_threshold`` is set in config. Default behavior is unchanged
    (warn-only, exit 0). ``--strict`` defaults the threshold to ``critical``.
    """
    threshold: Severity | None = config.severity_block_threshold
    if strict and threshold is None:
        threshold = "critical"
    if threshold is None:
        return
    if cli_renderer.has_blocking_comment(result.comments, threshold):
        _error(
            f"blocking: found issue(s) at or above '{threshold}'. "
            "Fix them, or bypass with AICR_SKIP=1."
        )
        raise SystemExit(1)


def _resolve_config(*, categories: tuple[str, ...], model: str | None) -> Config:
    """Load config, then apply CLI-flag overrides (highest precedence)."""
    config = load_config()
    if categories:
        from aicr.models import normalize_category

        config.categories = [normalize_category(c) for c in categories]
    if model:
        config.model = model
    return config


def _do_install(force: bool) -> None:
    from aicr.hooks.install import HookInstallError, install_hook

    try:
        path = install_hook(force=force)
    except HookInstallError as exc:
        _error(str(exc))
        raise SystemExit(1) from None
    click.echo(f"Installed pre-commit hook at {path}")
    click.echo("Set OPENROUTER_API_KEY (see .env.example), then just `git commit`.")


def _do_uninstall() -> None:
    from aicr.hooks.install import HookInstallError, uninstall_hook

    try:
        removed = uninstall_hook()
    except HookInstallError as exc:
        _error(str(exc))
        raise SystemExit(1) from None
    click.echo("Removed aicr pre-commit hook." if removed else "No aicr hook to remove.")


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite an existing pre-commit hook.")
def enable(force: bool) -> None:
    """Enable aicr — install the git pre-commit hook in this repository."""
    _do_install(force)


@cli.command()
def disable() -> None:
    """Disable aicr — remove the pre-commit hook from this repository."""
    _do_uninstall()


@cli.command(name="install-hook", hidden=True)
@click.option("--force", is_flag=True, help="Overwrite an existing pre-commit hook.")
def install_hook_cmd(force: bool) -> None:
    """Alias for `aicr enable` (kept for backward compatibility)."""
    _do_install(force)


@cli.command(name="uninstall-hook", hidden=True)
def uninstall_hook_cmd() -> None:
    """Alias for `aicr disable` (kept for backward compatibility)."""
    _do_uninstall()


@cli.command(name="config")
def config_cmd() -> None:
    """Show the resolved configuration (without the API key)."""
    try:
        config = load_config(require_api_key=False)
    except ConfigError as exc:
        _error(str(exc))
        raise SystemExit(1) from None
    key_state = "set" if config.api_key else "MISSING"
    click.echo(f"provider:   {config.provider}")
    click.echo(f"model:      {config.model}")
    if config.base_url:
        click.echo(f"base_url:   {config.base_url}")
    click.echo(f"categories: {', '.join(config.categories)}")
    click.echo(f"languages:  {', '.join(config.languages) or '(auto-detect)'}")
    click.echo(f"exclude:    {', '.join(config.exclude_paths) or '(none)'}")
    click.echo(f"max lines/file: {config.max_diff_lines_per_file}")
    click.echo(f"cache:      {'on' if config.cache_enabled else 'off'}")
    click.echo(f"blocking:   {config.severity_block_threshold or 'off (warn-only)'}")
    if config.requires_api_key():
        click.echo(f"API key:    {key_state} (OPENROUTER_API_KEY)")
    else:
        click.echo("API key:    not required for this provider")


# --------------------------------------------------------------------------- #
# `aicr init` — interactive, rclone-style setup wizard
# --------------------------------------------------------------------------- #

_CATEGORY_CHOICES = ["bugs", "security", "readability", "style"]


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite an existing .aicr.yaml without asking.")
def init(force: bool) -> None:
    """Interactively set up aicr in this repository (provider, model, hook)."""
    cwd = Path.cwd()
    config_path = cwd / ".aicr.yaml"

    click.echo(click.style("aicr setup", bold=True))
    click.echo("This will create a .aicr.yaml in the current directory.\n")

    if config_path.exists() and not force:
        if not click.confirm(f"{config_path.name} already exists. Overwrite it?", default=False):
            click.echo("Aborted — nothing changed.")
            raise SystemExit(0)

    provider = _prompt_provider()
    if provider == "openrouter":
        settings = _prompt_openrouter(cwd)
    else:
        settings = _prompt_ollama()

    settings["categories"] = _prompt_categories()
    settings["blocking"] = _prompt_blocking()

    _write_config(config_path, provider=provider, settings=settings)
    click.echo(f"\nWrote {config_path}")

    if click.confirm("Install the git pre-commit hook now?", default=True):
        try:
            from aicr.hooks.install import install_hook

            path = install_hook(force=True)
            click.echo(f"Installed pre-commit hook at {path}")
        except Exception as exc:  # HookInstallError or not-a-repo
            _warn(f"could not install hook ({exc}). Run `aicr enable` later.")

    click.echo(click.style("\nDone. Try: git add . && aicr review", bold=True))


def _prompt_menu(title: str, options: list[tuple[str, str]], *, default: int = 1) -> str:
    """rclone-style numbered menu; returns the chosen option's value."""
    click.echo(title)
    for i, (value, label) in enumerate(options, start=1):
        click.echo(f"  {i}) {value:<12} {label}")
    choice: int = click.prompt(
        "Choose a number",
        type=click.IntRange(1, len(options)),
        default=default,
    )
    return options[choice - 1][0]



def _prompt_provider() -> str:
    return _prompt_menu(
        "Which LLM provider?",
        [
            ("openrouter", "Cloud, many models (needs an API key)"),
            ("ollama", "Local models, private, no key (needs Ollama running)"),
        ],
        default=1,
    )


def _prompt_openrouter(cwd: Path) -> dict[str, object]:
    model = click.prompt("Model", default=DEFAULT_MODEL)
    settings: dict[str, object] = {"model": model}

    click.echo("\nYour API key is stored in .env (gitignored), never in .aicr.yaml.")
    if click.confirm("Enter your OpenRouter API key now?", default=True):
        key = click.prompt("OPENROUTER_API_KEY", hide_input=True, default="", show_default=False)
        if key:
            _write_env(cwd, key)
            click.echo("Saved key to .env")
    else:
        click.echo("Skipped — set OPENROUTER_API_KEY in your env or .env before reviewing.")
    return settings


def _prompt_ollama() -> dict[str, object]:
    base_url = click.prompt("Ollama base URL", default=DEFAULT_OLLAMA_BASE_URL)
    model = click.prompt("Local model name (must be pulled)", default="llama3.1")
    return {"model": model, "base_url": base_url}


def _prompt_categories() -> list[str]:
    default = ",".join(_CATEGORY_CHOICES)
    raw = click.prompt(
        f"Review categories (comma-separated from {', '.join(_CATEGORY_CHOICES)})",
        default=default,
    )
    chosen = [c for c in _split_patterns(raw) if c in _CATEGORY_CHOICES]
    return chosen or list(_CATEGORY_CHOICES)


def _prompt_blocking() -> str | None:
    if not click.confirm(
        "Block commits when serious issues are found? (default: warn only)",
        default=False,
    ):
        return None
    return _prompt_menu(
        "Block on which minimum severity?",
        [("critical", "only critical"), ("warning", "warning and critical")],
        default=1,
    )


def _write_env(cwd: Path, key: str) -> None:
    """Append/update OPENROUTER_API_KEY in .env without clobbering other vars."""
    env_path = cwd / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = [
            ln
            for ln in env_path.read_text(encoding="utf-8").splitlines()
            if not ln.startswith("OPENROUTER_API_KEY=")
        ]
    lines.append(f"OPENROUTER_API_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_config(config_path: Path, *, provider: str, settings: dict[str, object]) -> None:
    """Write a commented .aicr.yaml from the wizard's answers."""
    raw_categories = settings.get("categories")
    categories = raw_categories if isinstance(raw_categories, list) else list(_CATEGORY_CHOICES)
    lines = [
        "# aicr configuration — safe to commit. The API key lives in .env, never here.",
        f"provider: {provider}",
        f"model: {settings['model']}",
    ]
    if settings.get("base_url"):
        lines.append(f"base_url: {settings['base_url']}")
    cats = ", ".join(str(c) for c in categories)
    lines.append(f"categories: [{cats}]")

    lines.append("languages: []                 # empty = auto-detect per file by extension")
    lines.append('exclude_paths: ["*.lock", "dist/**", "node_modules/**"]')
    lines.append("max_diff_lines_per_file: 800")
    lines.append("cache_enabled: true")
    blocking = settings.get("blocking")
    if blocking:
        lines.append(f"severity_block_threshold: {blocking}   # blocking mode enabled")
    else:
        lines.append("# severity_block_threshold: critical   # uncomment to block commits")
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    cli()
