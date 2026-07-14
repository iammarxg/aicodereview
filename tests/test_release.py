"""Version-bump logic for the release script (pure functions, no git calls)."""

from __future__ import annotations

import pytest

from scripts.release import ReleaseError, bump


def test_minor_bump_resets_patch() -> None:
    assert bump("0.2.0", "minor") == "0.3.0"
    assert bump("1.4.7", "minor") == "1.5.0"


def test_patch_bump() -> None:
    assert bump("0.2.0", "patch") == "0.2.1"
    assert bump("1.4.7", "patch") == "1.4.8"


def test_major_bump_resets_minor_and_patch() -> None:
    assert bump("0.2.5", "major") == "1.0.0"
    assert bump("3.9.9", "major") == "4.0.0"


def test_bad_version_raises() -> None:
    with pytest.raises(ReleaseError):
        bump("not-a-version", "minor")


def test_unknown_kind_raises() -> None:
    with pytest.raises(ReleaseError):
        bump("0.2.0", "sideways")
