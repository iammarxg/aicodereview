"""Diff parsing: line-number mapping, binary/empty handling, exclusion, language."""

from __future__ import annotations

from aicr.diff.parser import detect_language, parse_diff


def test_single_file_line_numbers(single_file_diff: str) -> None:
    files = parse_diff(single_file_diff)
    assert len(files) == 1
    f = files[0]
    assert f.path == "calc.py"
    assert f.language == "python"
    # New-file layout: 1 def add, 2 return a+b (context), 3 blank, 4 def divide,
    # 5 return a/b (added). Added lines are 3, 4, 5.
    changed = f.changed_line_numbers()
    assert 4 in changed  # def divide(a, b):
    assert 5 in changed  # return a / b
    # Context lines (1, 2) must NOT be in the changed set.
    assert 1 not in changed
    assert 2 not in changed



def test_multi_file(multi_file_diff: str) -> None:
    files = parse_diff(multi_file_diff)
    paths = {f.path for f in files}
    assert paths == {"app.py", "util.js"}
    js = next(f for f in files if f.path == "util.js")
    assert js.language == "javascript"
    assert 2 in js.changed_line_numbers()  # the added const API line


def test_binary_file_flagged(binary_diff: str) -> None:
    files = parse_diff(binary_diff)
    assert len(files) == 1
    assert files[0].is_binary is True
    assert files[0].hunks == []


def test_empty_diff() -> None:
    assert parse_diff("") == []


def test_exclusion_by_glob(multi_file_diff: str) -> None:
    files = parse_diff(multi_file_diff, exclude_paths=["*.js"])
    assert {f.path for f in files} == {"app.py"}


def test_force_include_overrides_exclusion(multi_file_diff: str) -> None:
    # *.js is excluded, but force-including util.js brings it back for this run.
    files = parse_diff(
        multi_file_diff,
        exclude_paths=["*.js"],
        force_include=["util.js"],
    )
    assert {f.path for f in files} == {"app.py", "util.js"}


def test_force_include_only_affects_matching_files(multi_file_diff: str) -> None:
    # Excluding both, but only force-including app.py — util.js stays excluded.
    files = parse_diff(
        multi_file_diff,
        exclude_paths=["*.py", "*.js"],
        force_include=["app.py"],
    )
    assert {f.path for f in files} == {"app.py"}



def test_to_prompt_text_has_markers_and_numbers(single_file_diff: str) -> None:
    f = parse_diff(single_file_diff)[0]
    text = f.to_prompt_text()
    # Added lines carry a '+' and their real new-file number; context carries ' '.
    assert "     4 + def divide(a, b):" in text
    assert "     1   def add(a, b):" in text


def test_detect_language() -> None:
    assert detect_language("a/b/c.py") == "python"
    assert detect_language("x.ts") == "typescript"
    assert detect_language("noext") is None
