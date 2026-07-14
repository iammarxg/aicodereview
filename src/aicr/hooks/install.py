"""Install the standalone git ``pre-commit`` hook into a repository.

Users already on the ``pre-commit`` framework use ``.pre-commit-hooks.yaml``
instead (see repo root) and add this tool to their ``.pre-commit-config.yaml`` —
no file is written by this installer in that case.
"""

from __future__ import annotations

import stat
import subprocess
from importlib import resources
from pathlib import Path


class HookInstallError(Exception):
    """Raised for user-facing hook-installation problems."""


def _git_hooks_dir(repo_dir: Path) -> Path:
    """Resolve the repository's hooks directory (honors ``core.hooksPath``)."""
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise HookInstallError("Not inside a git repository (or git unavailable).") from exc
    hooks_dir = Path(top)
    if not hooks_dir.is_absolute():
        hooks_dir = repo_dir / hooks_dir
    return hooks_dir


def _load_template() -> str:
    return (
        resources.files("aicr.hooks")
        .joinpath("pre-commit-template.sh")
        .read_text(encoding="utf-8")
    )


def install_hook(repo_dir: Path | None = None, *, force: bool = False) -> Path:
    """Write ``.git/hooks/pre-commit`` and make it executable.

    Args:
        repo_dir: Repository root (defaults to cwd).
        force: Overwrite an existing pre-commit hook if present.

    Returns:
        The path to the installed hook.

    Raises:
        HookInstallError: If not a git repo, or a hook exists and ``force`` is False.
    """
    repo_dir = repo_dir or Path.cwd()
    hooks_dir = _git_hooks_dir(repo_dir)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"

    if hook_path.exists() and not force:
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if "aicr review" in existing:
            return hook_path  # already ours — idempotent
        raise HookInstallError(
            f"A pre-commit hook already exists at {hook_path}.\n"
            "Re-run with --force to overwrite it, or integrate aicr manually."
        )

    hook_path.write_text(_load_template(), encoding="utf-8")
    mode = hook_path.stat().st_mode
    hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook_path


def uninstall_hook(repo_dir: Path | None = None) -> bool:
    """Remove the aicr pre-commit hook if we installed it. Returns True if removed."""
    repo_dir = repo_dir or Path.cwd()
    hook_path = _git_hooks_dir(repo_dir) / "pre-commit"
    if not hook_path.exists():
        return False
    if "aicr review" not in hook_path.read_text(encoding="utf-8", errors="replace"):
        raise HookInstallError(
            f"The pre-commit hook at {hook_path} was not installed by aicr; leaving it alone."
        )
    hook_path.unlink()
    return True
