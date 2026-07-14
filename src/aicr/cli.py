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
from aicr.config import (
    DEFAULT_MODEL,
    Config,
    ConfigError,
    api_key_env_var,
    load_config,
)
from aicr.diff.source import DiffMode, DiffSourceError, LocalGitSource
from aicr.engine import run_review
from aicr.models import ReviewResult, Severity
from aicr.providers.base import ProviderError
from aicr.providers.gemini import DEFAULT_GEMINI_MODEL
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


_PROGRESS_WIDTH = 70


class _ProgressPrinter:
    """Live 'Reviewing …' status line, callable as an engine progress callback.

    The status is written to **stderr** and overwrites itself in place via a
    carriage return, so it never pollutes stdout (keeping `--format json` clean)
    or interleaves with the final report. It shows only on an interactive
    stderr; in pipes/CI it stays silent. ``clear()`` wipes the line before the
    report prints, so results still appear all at once — the progress is real
    (it fires as each file actually starts going to the LLM), not a fake spinner.
    """

    def __init__(self) -> None:
        self.enabled = sys.stderr.isatty()

    def __call__(self, path: str, done: int, total: int) -> None:
        if not self.enabled:
            return
        msg = f"Reviewing ({done}/{total}) {path} …"
        click.echo(f"\r{msg:<{_PROGRESS_WIDTH}}"[:_PROGRESS_WIDTH], nl=False, err=True)

    def clear(self) -> None:
        if self.enabled:
            click.echo("\r" + " " * _PROGRESS_WIDTH + "\r", nl=False, err=True)




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

    # A live status is pointless for JSON output (which must stay a clean pipe).
    progress = _ProgressPrinter() if output_format != "json" else None
    try:
        provider = build_provider(config)
        cache = ReviewCache(Path.cwd(), enabled=config.cache_enabled and not no_cache)
        result: ReviewResult = asyncio.run(
            run_review(provider, files, config, cache=cache, progress_callback=progress)
        )
    except ProviderError as exc:
        if progress is not None:
            progress.clear()
        _warn(f"review skipped ({exc}). Commit continues. Set AICR_SKIP=1 to silence.")
        raise SystemExit(0) from None
    except Exception as exc:  # pragma: no cover - unexpected
        if progress is not None:
            progress.clear()
        if debug:
            raise
        _warn(f"review skipped (unexpected error: {exc}). Commit continues.")
        raise SystemExit(0) from None
    finally:
        if progress is not None:
            progress.clear()


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
        env_var = api_key_env_var(config.provider) or "OPENROUTER_API_KEY"
        click.echo(f"API key:    {key_state} ({env_var})")
    else:
        click.echo("API key:    not required for this provider")


@cli.command()
@click.option("--yes", "assume_yes", is_flag=True, help="Skip the confirmation prompt.")
def reset(assume_yes: bool) -> None:
    """Fully remove aicr from this repo: hook, .aicr.yaml, and the cache.

    Unlike ``disable`` (which only removes the hook), ``reset`` tears down
    everything aicr created in the repository. It never touches ``.env`` without
    asking — that file may hold your key or unrelated secrets — but it will offer
    to strip the aicr key line if it finds one.
    """
    from aicr.cache import CACHE_DIRNAME
    from aicr.hooks.install import HookInstallError, uninstall_hook

    cwd = Path.cwd()
    config_path = cwd / ".aicr.yaml"
    cache_dir = cwd / CACHE_DIRNAME

    # Build the list of things that actually exist, so we can show the user
    # exactly what will be removed before they confirm.
    targets: list[str] = []
    if config_path.exists():
        targets.append(config_path.name)
    if cache_dir.exists():
        targets.append(f"{CACHE_DIRNAME}/ (cache)")
    hook_present = _aicr_hook_present(cwd)
    if hook_present:
        targets.append("pre-commit hook")

    if not targets:
        click.echo("Nothing to reset — aicr isn't set up in this repo.")
        return

    click.echo("This will remove:")
    for t in targets:
        click.echo(f"  - {t}")
    if not assume_yes and not click.confirm("Proceed?", default=False):
        click.echo("Aborted — nothing changed.")
        return

    if hook_present:
        try:
            if uninstall_hook():
                click.echo("Removed pre-commit hook.")
        except HookInstallError as exc:
            _warn(str(exc))
    if config_path.exists():
        config_path.unlink()
        click.echo(f"Removed {config_path.name}.")
    if cache_dir.exists():
        import shutil

        shutil.rmtree(cache_dir, ignore_errors=True)
        click.echo(f"Removed {CACHE_DIRNAME}/ cache.")

    _maybe_strip_env_key(cwd, assume_yes=assume_yes)
    click.echo("Done. aicr has been removed from this repository.")


def _aicr_hook_present(cwd: Path) -> bool:
    """True if the repo's pre-commit hook was installed by aicr."""
    try:
        from aicr.hooks.install import _git_hooks_dir

        hook = _git_hooks_dir(cwd) / "pre-commit"
    except Exception:
        return False
    if not hook.exists():
        return False
    return "aicr review" in hook.read_text(encoding="utf-8", errors="replace")


def _maybe_strip_env_key(cwd: Path, *, assume_yes: bool) -> None:
    """Offer to remove aicr's API-key line(s) from .env, leaving the rest intact."""
    from aicr.config import PROVIDER_API_KEY_ENV

    env_path = cwd / ".env"
    if not env_path.exists():
        return
    key_vars = set(PROVIDER_API_KEY_ENV.values())
    lines = env_path.read_text(encoding="utf-8").splitlines()
    key_lines = [ln for ln in lines if ln.split("=", 1)[0].strip() in key_vars]
    if not key_lines:
        return
    names = ", ".join(sorted({ln.split("=", 1)[0].strip() for ln in key_lines}))
    if not assume_yes and not click.confirm(
        f"Also remove the API key line(s) ({names}) from .env?", default=False
    ):
        click.echo("Left .env untouched.")
        return
    remaining = [ln for ln in lines if ln.split("=", 1)[0].strip() not in key_vars]
    if remaining:
        env_path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    else:
        # Nothing else in the file — remove it entirely rather than leave it empty.
        env_path.unlink()
    click.echo(f"Removed {names} from .env.")


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
        settings = _prompt_cloud(cwd, provider="openrouter")
    elif provider == "gemini":
        settings = _prompt_cloud(cwd, provider="gemini")
    else:
        settings = _prompt_ollama()

    settings["categories"] = _prompt_categories()
    settings["blocking"] = _prompt_blocking()

    # Advanced options are optional: if declined, they're still written to the
    # config file (commented out at their defaults) so everything is discoverable.
    if click.confirm("\nConfigure advanced options?", default=False):
        settings.update(_prompt_advanced())

    _write_config(config_path, provider=provider, settings=settings)
    click.echo(f"\nWrote {config_path}")

    if click.confirm("Install the git pre-commit hook now?", default=True):
        try:
            from aicr.hooks.install import install_hook

            path = install_hook(force=True)
            click.echo(f"Installed pre-commit hook at {path}")
        except Exception as exc:  # HookInstallError or not-a-repo
            _warn(f"could not install hook ({exc}). Run `aicr enable` later.")

    # Offer a repo analysis last: it's the most useful when everything else is
    # already configured, and it can recommend settings tuned to this codebase.
    _maybe_analyze_repo(cwd, config_path, provider=provider, settings=settings)

    click.echo(click.style("\nDone. Try: git add . && aicr review", bold=True))



def _maybe_analyze_repo(
    cwd: Path, config_path: Path, *, provider: str, settings: dict[str, object]
) -> None:
    """Offer a fast, local repo analysis and optionally apply recommended settings.

    This scans tracked files (no LLM calls) to report how much code the repo has,
    detect languages and heavy directories, and recommend efficient ``.aicr.yaml``
    settings tuned to its size. It also estimates how long a full-repo review
    would take, so the user knows what a future ``aicr scan`` would cost.
    """
    click.echo(
        "\nI can analyze this repo to recommend settings that make reviews fast "
        "and efficient for its size (and estimate a full-repo scan time)."
    )
    if not click.confirm("Analyze the repository now? (fast, no API calls)", default=True):
        return

    from aicr.analyze import AnalysisError, analyze_repo, estimate_scan_seconds

    try:
        analysis = analyze_repo(cwd)
    except AnalysisError as exc:
        _warn(f"couldn't analyze the repo ({exc}).")
        return

    langs = ", ".join(f"{name} ({n})" for name, n in analysis.languages[:6]) or "none detected"
    click.echo(
        f"\n  {analysis.total_files} files · {analysis.total_lines:,} lines · "
        f"~{analysis.total_chars:,} chars · ~{analysis.estimated_tokens:,} tokens (approx.)"
    )
    click.echo(f"  Languages: {langs}")
    click.echo(f"  Recommended excludes:  {', '.join(analysis.recommended_excludes)}")
    click.echo(
        f"  Recommended limits:    max_files={analysis.recommended_max_files}, "
        f"concurrency={analysis.recommended_concurrency}"
    )

    # A rough, clearly-labelled full-scan estimate using a conservative default
    # throughput (a real timed sample is a future `aicr scan` feature).
    low, high = estimate_scan_seconds(
        analysis,
        tokens_per_second=800.0,  # conservative default; provider/model dependent
        concurrency=analysis.recommended_concurrency,
    )
    click.echo(
        f"  Full-repo scan estimate: ~{low / 60:.1f}–{high / 60:.1f} min "
        "(very approximate; depends on your model's speed)."
    )

    if click.confirm("\nApply these recommended settings to .aicr.yaml?", default=True):
        settings["languages"] = ", ".join(analysis.recommended_languages)
        settings["exclude_paths"] = ", ".join(analysis.recommended_excludes)
        settings["max_files_per_review"] = analysis.recommended_max_files
        settings["concurrency"] = analysis.recommended_concurrency
        _write_config(config_path, provider=provider, settings=settings)
        click.echo(f"Updated {config_path.name} with recommended settings.")


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
            ("gemini", "Cloud, Google Gemini (generous free tier, needs a key)"),
            ("ollama", "Local models, private, no key (needs Ollama running)"),
        ],
        default=1,
    )


# Per-cloud-provider defaults for the wizard: default model and key sign-up URL.
_CLOUD_DEFAULTS: dict[str, tuple[str, str]] = {
    "openrouter": (DEFAULT_MODEL, "https://openrouter.ai/keys"),
    "gemini": (DEFAULT_GEMINI_MODEL, "https://aistudio.google.com/apikey"),
}


def _prompt_cloud(cwd: Path, *, provider: str) -> dict[str, object]:
    """Prompt for a cloud provider's model and API key (stored in .env)."""
    default_model, key_url = _CLOUD_DEFAULTS[provider]
    env_var = api_key_env_var(provider) or "OPENROUTER_API_KEY"

    model = click.prompt("Model", default=default_model)
    settings: dict[str, object] = {"model": model}

    click.echo("\nYour API key is stored in .env (gitignored), never in .aicr.yaml.")
    click.echo(f"Get a key at {key_url}")
    if click.confirm(f"Enter your {provider} API key now?", default=True):
        key = click.prompt(env_var, hide_input=True, default="", show_default=False)
        if key:
            _write_env(cwd, key, env_var=env_var)
            click.echo(f"Saved key to .env ({env_var})")
    else:
        click.echo(f"Skipped — set {env_var} in your env or .env before reviewing.")
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


def _prompt_advanced() -> dict[str, object]:
    """Prompt for the less-common tuning knobs, shown only on request.

    Uses the same prompt style as the main wizard. Returns only the keys the
    user actually set; everything else is written commented-out by
    ``_write_config`` so the whole option surface stays discoverable.
    """
    advanced: dict[str, object] = {}
    advanced["languages"] = click.prompt(
        "Languages in scope (comma-separated, empty = auto-detect)",
        default="",
        show_default=False,
    )
    advanced["exclude_paths"] = click.prompt(
        "Exclude paths (comma-separated globs)",
        default="*.lock, dist/**, node_modules/**",
    )
    advanced["max_diff_lines_per_file"] = click.prompt(
        "Max added lines per file before skipping",
        type=int,
        default=800,
    )
    advanced["max_files_per_review"] = click.prompt(
        "Max files reviewed per run",
        type=int,
        default=50,
    )
    advanced["concurrency"] = click.prompt(
        "Max simultaneous provider requests",
        type=int,
        default=5,
    )
    advanced["severity_display_threshold"] = _prompt_menu(
        "Show comments at or above which severity?",
        [
            ("info", "everything"),
            ("warning", "warning and critical"),
            ("critical", "critical only"),
        ],
        default=1,
    )
    advanced["cache_enabled"] = click.confirm(
        "Enable the hunk cache (reuse results for unchanged files)?",
        default=True,
    )
    return advanced



def _write_env(cwd: Path, key: str, *, env_var: str = "OPENROUTER_API_KEY") -> None:
    """Append/update ``env_var`` in .env without clobbering other vars."""
    env_path = cwd / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = [
            ln
            for ln in env_path.read_text(encoding="utf-8").splitlines()
            if not ln.startswith(f"{env_var}=")
        ]
    lines.append(f"{env_var}={key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")



def _yaml_list(items: list[str]) -> str:
    """Render a list of strings as an inline YAML array."""
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"


def _write_config(config_path: Path, *, provider: str, settings: dict[str, object]) -> None:
    """Write a fully-populated, commented ``.aicr.yaml`` from the wizard.

    Every supported option appears in the file. Options the user set are written
    live; options they left at the default are written **commented out** at their
    default value — so the whole configuration surface is always discoverable in
    one place, whether or not the user touched the advanced step.
    """
    raw_categories = settings.get("categories")
    categories = raw_categories if isinstance(raw_categories, list) else list(_CATEGORY_CHOICES)

    def line(key: str, present: bool, value: str, comment: str) -> str:
        # A single option row: live when the user set it, else commented at default.
        body = f"{key}: {value}"
        prefix = "" if present else "# "
        pad = " " * max(1, 30 - len(prefix + body))
        return f"{prefix}{body}{pad}# {comment}"

    base_url = settings.get("base_url")
    languages = str(settings.get("languages") or "").strip()
    lang_list = _split_patterns(languages)
    exclude = _split_patterns(str(settings.get("exclude_paths") or "")) or [
        "*.lock",
        "dist/**",
        "node_modules/**",
    ]
    max_lines = settings.get("max_diff_lines_per_file")
    max_files = settings.get("max_files_per_review")
    concurrency = settings.get("concurrency")
    display = settings.get("severity_display_threshold")
    cache_enabled = settings.get("cache_enabled")
    blocking = settings.get("blocking")

    lines = [
        "# aicr configuration — safe to commit. The API key lives in .env, never here.",
        "# Every option is listed below; commented lines show the default value.",
        "",
        f"provider: {provider}",
        f"model: {settings['model']}",
        line("base_url", bool(base_url), str(base_url or "http://localhost:11434/v1"),
             "override the provider API endpoint (e.g. Ollama)"),
        f"categories: {_yaml_list([str(c) for c in categories])}",
        line("languages", bool(lang_list), _yaml_list(lang_list),
             "empty = auto-detect per file by extension"),
        f"exclude_paths: {_yaml_list(exclude)}",
        line("max_diff_lines_per_file", max_lines is not None, str(max_lines or 800),
             "skip files with more added lines than this"),
        line("max_files_per_review", max_files is not None, str(max_files or 50),
             "cap files reviewed per run"),
        line("concurrency", concurrency is not None, str(concurrency or 5),
             "max simultaneous provider requests"),
        line("severity_display_threshold", display is not None, str(display or "info"),
             "show comments at/above: info | warning | critical"),
        line("cache_enabled", cache_enabled is not None,
             "true" if cache_enabled is not False else "false",
             "reuse results for unchanged files across runs"),
        line("severity_block_threshold", bool(blocking), str(blocking or "critical"),
             "block commits at/above this severity (opt-in)"),
    ]
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")



if __name__ == "__main__":  # pragma: no cover
    cli()
