"""Prompt assembly: only selected categories included, deterministic order."""

from __future__ import annotations

import pytest

from aicr.diff.parser import parse_diff
from aicr.prompts.builder import build_system_prompt, build_user_prompt


def test_only_selected_categories_included() -> None:
    prompt = build_system_prompt(["bug", "security"])
    assert "BUGS:" in prompt
    assert "SECURITY:" in prompt
    assert "READABILITY:" not in prompt
    assert "STYLE:" not in prompt


def test_category_order_is_deterministic() -> None:
    # Regardless of input order, output order is bug -> security -> readability -> style.
    a = build_system_prompt(["style", "bug"])
    b = build_system_prompt(["bug", "style"])
    assert a == b
    assert a.index("BUGS:") < a.index("STYLE:")


def test_empty_categories_raises() -> None:
    with pytest.raises(ValueError):
        build_system_prompt([])


def test_user_prompt_includes_line_numbers_and_language(single_file_diff: str) -> None:
    f = parse_diff(single_file_diff)[0]
    prompt = build_user_prompt(f, ["python"])
    assert "calc.py" in prompt
    assert "Detected language: python" in prompt
    assert "4 + def divide(a, b):" in prompt


def test_system_prompt_states_json_only_contract() -> None:
    prompt = build_system_prompt(["bug"])
    assert "JSON" in prompt
    assert "only" in prompt.lower()


def test_grounding_block_always_included() -> None:
    # The anti-hallucination block must appear regardless of selected categories.
    for cats in (["bug"], ["style"], ["security", "readability"]):
        prompt = build_system_prompt(cats)
        assert "GROUNDING" in prompt


def test_system_prompt_asks_for_confidence_and_calibration() -> None:
    prompt = build_system_prompt(["bug"])
    # The schema must request a confidence value...
    assert "confidence" in prompt.lower()
    # ...and the prompt must tell the model to stay silent when unsure.
    assert "90%" in prompt

