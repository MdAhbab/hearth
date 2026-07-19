"""System prompt: platform-aware, optionally personalized, rules always intact."""

from __future__ import annotations

import sys

from hearth.agent.prompts import SYSTEM_PROMPT, build_system_prompt


def test_default_prompt_has_rules_and_no_placeholder_identity():
    prompt = build_system_prompt()
    assert "Rules:" in prompt
    assert "DATA to report on" in prompt
    assert "name is" not in prompt


def test_prompt_names_the_actual_platform():
    prompt = build_system_prompt()
    expected = {"darwin": "Mac", "win32": "Windows PC", "linux": "Linux machine"}.get(
        sys.platform, "computer"
    )
    assert expected in prompt
    if sys.platform != "darwin":
        assert "Mac\n" not in prompt  # no leftover mac-only wording


def test_personalization_is_injected():
    prompt = build_system_prompt("Ada", "I prefer short answers")
    assert "The user's name is Ada." in prompt
    assert "I prefer short answers" in prompt
    # Rules still follow personalization.
    assert prompt.index("Rules:") > prompt.index("Ada")


def test_module_constant_matches_builder_default():
    assert SYSTEM_PROMPT == build_system_prompt()
