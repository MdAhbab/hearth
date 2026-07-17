"""Utility tools (safe math, HTML-to-text, web permission) and log redaction."""

import pytest

from hearth.config import WebConfig
from hearth.connectors.utility.tools import html_to_text, register_utility_tools, safe_eval
from hearth.logging_setup import redact


@pytest.fixture
def util_env(harness, registry):
    register_utility_tools(registry, WebConfig())
    return harness


def test_safe_eval_arithmetic():
    assert safe_eval("2 + 3 * 4") == 14
    assert safe_eval("(1520 * 0.15) + 42") == pytest.approx(270.0)
    assert safe_eval("2**10") == 1024
    assert safe_eval("-7 // 2") == -4


@pytest.mark.parametrize(
    "evil",
    [
        "__import__('os').system('rm -rf /')",
        "open('/etc/passwd')",
        "'a' * 999999999",
        "2**9999",
        "lambda: 1",
        "[1,2,3]",
    ],
)
def test_safe_eval_rejects_non_arithmetic(evil):
    with pytest.raises((ValueError, SyntaxError)):
        safe_eval(evil)


def test_html_to_text_strips_scripts():
    html = "<html><script>steal()</script><style>x{}</style><p>Hello <b>world</b></p></html>"
    text = html_to_text(html)
    assert "Hello" in text and "world" in text
    assert "steal" not in text


async def test_time_now_always_allowed(util_env):
    result = await util_env.gate.execute("time_now", {})
    assert result.ok and "local" in result.data


async def test_calculate_through_gate(util_env):
    result = await util_env.gate.execute("calculate", {"expression": "6*7"})
    assert result.ok and result.data["result"] == 42


async def test_web_fetch_blocked_without_permission(util_env):
    result = await util_env.gate.execute("web_fetch", {"url": "https://example.com"})
    assert not result.ok and "permission" in result.error.lower()


def test_redaction_patterns():
    assert "[REDACTED]" in redact("Authorization: Bearer abc123def456ghi789")
    assert "[REDACTED]" in redact("refresh token ya29.a0AfH6SMBx-longtokenvalue")
    assert "[REDACTED]" in redact('{"token": "supersecretvalue"}')
    clean = "tool gmail_search completed in 1.2s"
    assert redact(clean) == clean
