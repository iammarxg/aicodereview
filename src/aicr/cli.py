"""``click`` entry points for the ``aicr`` command.

Commands: ``review``, ``enable``/``disable`` (git hook), ``install-hook`` /
``uninstall-hook`` (kept as aliases), and ``config``.

The CLI owns the asyncio event loop (``asyncio.run``) — click itself is sync, so
each command calls into async code at the boundary (review §2.1/§3). Every
command is warn-only where a commit is involved: infra failures print a warning
and exit 0, never blocking the commit (plan §8).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

import click

from aicr import __version__
from aicr.config import Config, ConfigError, load_config
from aicr.diff.source import DiffSourceError, LocalGitSource
from aicr.engine import run_review
from aicr.models import ReviewResult
from aicr.providers.base import ProviderError
from aicr.providers.registry import build_provider
from aicr.report import cli_renderer, json_renderer

# Quick-reference shown on `aicr --help`. The leading `\b` tells click not to
# rewrap this block, so the columns stay aligned (see AGENT_HANDOVER §5).
_EPILOG = """\
\b
Quick start:
  aicr enable                 Install the pre-commit hook in this repo
  git add .  &&  git commit   Review runs automatically before the commit
  aicr review                 Review staged changes on demand (no commit)

\b
Common flags:
  aicr review --format json   Machine-readable output (for CI/editors)
  aicr review --include FILE  Review a normally-excluded file, just this once
  aicr review --model NAME    Use a specific OpenRouter model for this run

\b
Config & secrets:
  .aicr.yaml                  Per-repo settings (categories, excludes, model)
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
@click.option("--staged", is_flag=True, default=True, help="Review staged changes (default).")
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
@click.option("--debug", is_flag=True, help="Show full tracebacks and verbose errors.")
def review(
    staged: bool,
    output_format: str,
    no_color: bool,
    categories: tuple[str, ...],
    model: str | None,
    include: str | None,
    debug: bool,
) -> None:
    """Review staged changes and print line-mapped comments (never blocks)."""
    # Escape hatch — bypass entirely (review §6). Also honored by the shell hook.
    if os.environ.get("AICR_SKIP") == "1":
        raise SystemExit(0)

    force_include = _split_patterns(include)
    try:
        config = _resolve_config(categories=categories, model=model)
        source = LocalGitSource(
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
        click.echo("Nothing staged to review.")
        raise SystemExit(0)

    try:
        provider = build_provider(config)
        result: ReviewResult = asyncio.run(run_review(provider, files, config))
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
    # Warn-only: always exit 0 (plan §3 step 7).
    raise SystemExit(0)


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
    click.echo(f"categories: {', '.join(config.categories)}")
    click.echo(f"languages:  {', '.join(config.languages) or '(auto-detect)'}")
    click.echo(f"exclude:    {', '.join(config.exclude_paths) or '(none)'}")
    click.echo(f"max lines/file: {config.max_diff_lines_per_file}")
    click.echo(f"API key:    {key_state} (OPENROUTER_API_KEY)")


if __name__ == "__main__":  # pragma: no cover
    cli()
