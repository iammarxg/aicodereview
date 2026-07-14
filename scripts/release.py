#!/usr/bin/env python3
"""Release helper: bump the version, tag it, and push — one interactive command.

Semantic-versioning policy agreed for this project:
  * new features            → minor bump  (v0.X.0)
  * bug/small fixes         → patch bump  (v0.0.X)
  * breaking changes        → major bump  (vX.0.0)

Run it with no arguments for an interactive prompt, or pass the bump kind:

    python scripts/release.py minor        # or: major | patch
    python scripts/release.py --version 0.4.2   # set an exact version

What it does, in order:
  1. Bumps ``__version__`` in ``src/aicr/__init__.py`` and ``version`` in
     ``pyproject.toml`` (the two sources of truth).
  2. Rolls the CHANGELOG "Unreleased" section into the new version heading and
     refreshes the compare links.
  3. Commits those files, creates an annotated ``vX.Y.Z`` git tag, and pushes
     both the branch and the tag.

It refuses to run with a dirty tree (other than the files it edits) so a release
commit never sweeps in unrelated changes. Nothing is pushed until the very end,
and it prints each step so you can follow along.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT_PY = ROOT / "src" / "aicr" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"
REPO_URL = "https://github.com/iammarxg/aicodereview"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class ReleaseError(Exception):
    """User-facing release problem (printed without a traceback)."""


def _run(*args: str, capture: bool = False) -> str:
    """Run a git/shell command, raising ReleaseError on failure."""
    result = subprocess.run(
        args, cwd=ROOT, text=True, capture_output=capture
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise ReleaseError(f"command failed: {' '.join(args)}\n{detail}".rstrip())
    return (result.stdout or "") if capture else ""


def current_version() -> str:
    """Read the current version from ``__init__.py``."""
    text = INIT_PY.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise ReleaseError(f"could not find __version__ in {INIT_PY}")
    return match.group(1)


def bump(version: str, kind: str) -> str:
    """Return the next version for a major/minor/patch bump."""
    match = _VERSION_RE.match(version)
    if not match:
        raise ReleaseError(f"current version {version!r} is not X.Y.Z")
    major, minor, patch = (int(g) for g in match.groups())
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseError(f"unknown bump kind {kind!r}")


def _ensure_clean_worktree() -> None:
    """Abort if the tree has uncommitted changes (avoids mixing into a release)."""
    dirty = _run("git", "status", "--porcelain", capture=True).strip()
    if dirty:
        raise ReleaseError(
            "working tree is not clean — commit or stash changes first:\n" + dirty
        )


def _write_version(new: str) -> None:
    """Update the version string in both sources of truth."""
    init_text = INIT_PY.read_text(encoding="utf-8")
    INIT_PY.write_text(
        re.sub(r'(__version__\s*=\s*")[^"]+(")', rf"\g<1>{new}\g<2>", init_text),
        encoding="utf-8",
    )
    pyproject_text = PYPROJECT.read_text(encoding="utf-8")
    PYPROJECT.write_text(
        re.sub(r'(?m)^(version\s*=\s*")[^"]+(")', rf"\g<1>{new}\g<2>", pyproject_text),
        encoding="utf-8",
    )


def _update_changelog(new: str) -> None:
    """Roll 'Unreleased' into a dated release heading and refresh links.

    Best-effort: if the CHANGELOG doesn't match the expected shape, it's left
    alone (the release still proceeds) and a note is printed.
    """
    if not CHANGELOG.exists():
        return
    text = CHANGELOG.read_text(encoding="utf-8")
    today = date.today().isoformat()

    if "## [Unreleased]" in text:
        text = text.replace(
            "## [Unreleased]",
            f"## [Unreleased]\n\n## [{new}] — {today}",
            1,
        )
    # Refresh/prepend the compare links at the bottom if the pattern is present.
    if f"[{new}]:" not in text:
        link_block = (
            f"[Unreleased]: {REPO_URL}/compare/v{new}...HEAD\n"
            f"[{new}]: {REPO_URL}/releases/tag/v{new}\n"
        )
        # Drop an old Unreleased link line (it's superseded) then append fresh.
        text = re.sub(r"(?m)^\[Unreleased\]:.*\n", "", text)
        text = text.rstrip() + "\n" + link_block
    CHANGELOG.write_text(text, encoding="utf-8")


def _prompt_kind() -> str:
    print("What kind of release is this?")
    print("  1) minor   new features            → v0.X.0")
    print("  2) patch   bug fixes / small fixes → v0.0.X")
    print("  3) major   breaking changes        → vX.0.0")
    choice = input("Choose a number [1]: ").strip() or "1"
    return {"1": "minor", "2": "patch", "3": "major"}.get(choice, "minor")


def _confirm(prompt: str) -> bool:
    return (input(f"{prompt} [y/N]: ").strip().lower() or "n") in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bump version, tag, and push a release.")
    parser.add_argument(
        "kind",
        nargs="?",
        choices=["major", "minor", "patch"],
        help="Bump kind. Omit for an interactive prompt.",
    )
    parser.add_argument("--version", help="Set an exact version (X.Y.Z) instead of bumping.")
    parser.add_argument("--no-push", action="store_true", help="Commit and tag but don't push.")
    args = parser.parse_args(argv)

    try:
        _ensure_clean_worktree()
        current = current_version()
        if args.version:
            if not _VERSION_RE.match(args.version):
                raise ReleaseError(f"--version {args.version!r} is not X.Y.Z")
            new = args.version
        else:
            kind = args.kind or _prompt_kind()
            new = bump(current, kind)

        print(f"\nCurrent version: {current}")
        print(f"New version:     {new}")
        if not _confirm(f"Release v{new}?"):
            print("Aborted — nothing changed.")
            return 0

        _write_version(new)
        _update_changelog(new)
        print(f"Updated version → {new} in __init__.py, pyproject.toml, CHANGELOG.md")

        _run("git", "add", "src/aicr/__init__.py", "pyproject.toml", "CHANGELOG.md")
        _run("git", "commit", "-m", f"Release v{new}")
        _run("git", "tag", "-a", f"v{new}", "-m", f"aicr v{new}")
        print(f"Committed and tagged v{new}")

        if args.no_push:
            print("Skipped push (--no-push). Push later with:")
            print("  git push && git push --tags")
            return 0

        branch = _run("git", "rev-parse", "--abbrev-ref", "HEAD", capture=True).strip()
        _run("git", "push", "origin", branch)
        _run("git", "push", "origin", f"v{new}")
        print(f"\nReleased v{new} — pushed branch {branch!r} and tag v{new}.")
        return 0
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
