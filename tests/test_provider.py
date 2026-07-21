"""OllamaProvider against an httpx.MockTransport — streaming, native tool
calls, and the JSON fallback for models without tool support."""

import json

import httpx
import pytest

from hearth.config import ModelConfig, OllamaConfig
from hearth.runtime.provider import ChatMessage, OllamaProvider, _parse_json_tool_call


def ndjson(*chunks) -> str:
    return "\n".join(json.dumps(c) for c in chunks) + "\n"


def make_provider(handler) -> OllamaProvider:
    return OllamaProvider(ModelConfig(), OllamaConfig(), transport=httpx.MockTransport(handler))


async def test_streaming_text():
    def handler(request):
        return httpx.Response(
            200,
            text=ndjson(
                {"message": {"content": "Hel"}, "done": False},
                {"message": {"content": "lo"}, "done": True},
            ),
        )

    chunks = []
    provider = make_provider(handler)
    result = await provider.chat(
        [ChatMessage("user", "hi")], on_chunk=lambda c: chunks.append(c.text)
    )
    assert result.content == "Hello"
    assert chunks == ["Hel", "lo"]


async def test_native_tool_call_parsed():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["tools"]  # tools were sent natively
        return httpx.Response(
            200,
            text=ndjson(
                {
                    "message": {
                        "content": "",
                        "tool_calls": [{"function": {"name": "echo", "arguments": {"text": "hi"}}}],
                    },
                    "done": True,
                }
            ),
        )

    provider = make_provider(handler)
    result = await provider.chat(
        [ChatMessage("user", "hi")],
        tools=[{"type": "function", "function": {"name": "echo"}}],
    )
    assert result.tool_calls[0].name == "echo"
    assert result.tool_calls[0].arguments == {"text": "hi"}


async def test_fallback_when_model_lacks_tool_support():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        payload = json.loads(request.content)
        if "tools" in payload:
            return httpx.Response(400, json={"error": "model does not support tools"})
        # Second request: tool schema moved into the system prompt.
        assert "tool_call" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            text=ndjson(
                {
                    "message": {
                        "content": '{"tool_call": {"name": "echo", "arguments": {"text": "hi"}}}'
                    },
                    "done": True,
                }
            ),
        )

    provider = make_provider(handler)
    result = await provider.chat(
        [ChatMessage("user", "hi")],
        tools=[{"type": "function", "function": {"name": "echo"}}],
    )
    assert calls["n"] == 2
    assert result.tool_calls[0].name == "echo"


async def test_server_error_surfaces():
    def handler(request):
        return httpx.Response(500, text="boom")

    provider = make_provider(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.chat([ChatMessage("user", "hi")])


def _capture_num_ctx_handler(captured):
    def handler(request):
        payload = json.loads(request.content)
        captured["num_ctx"] = payload["options"]["num_ctx"]
        return httpx.Response(200, text=ndjson({"message": {"content": "ok"}, "done": True}))

    return handler


async def test_num_ctx_uses_configured_when_prompt_fits():
    captured = {}
    provider = OllamaProvider(
        ModelConfig(context_length=8192),
        OllamaConfig(),
        transport=httpx.MockTransport(_capture_num_ctx_handler(captured)),
    )
    await provider.chat([ChatMessage("user", "hi")])
    assert captured["num_ctx"] == 8192


async def test_num_ctx_bumped_so_large_tools_do_not_overflow():
    captured = {}
    # Tool schemas far larger than the configured window (~10k tokens of text).
    tools = [{"type": "function", "function": {"name": "big", "description": "x" * 40000}}]
    provider = OllamaProvider(
        ModelConfig(context_length=4096),
        OllamaConfig(),
        transport=httpx.MockTransport(_capture_num_ctx_handler(captured)),
    )
    await provider.chat([ChatMessage("user", "hi")], tools=tools)
    assert captured["num_ctx"] > 4096  # would have truncated the prompt otherwise
    assert captured["num_ctx"] <= 32768


async def test_num_ctx_capped_at_max():
    captured = {}
    tools = [{"type": "function", "function": {"name": "huge", "description": "x" * 400000}}]
    provider = OllamaProvider(
        ModelConfig(context_length=4096),
        OllamaConfig(),
        transport=httpx.MockTransport(_capture_num_ctx_handler(captured)),
    )
    await provider.chat([ChatMessage("user", "hi")], tools=tools)
    assert captured["num_ctx"] == 32768


def test_parse_json_tool_call_variants():
    ok = _parse_json_tool_call('{"tool_call": {"name": "t", "arguments": {"a": 1}}}')
    assert ok and ok.name == "t" and ok.arguments == {"a": 1}

    fenced = _parse_json_tool_call('```json\n{"tool_call": {"name": "t", "arguments": {}}}\n```')
    assert fenced and fenced.name == "t"

    assert _parse_json_tool_call("just some prose") is None
    assert _parse_json_tool_call('{"other": 1}') is None
    assert _parse_json_tool_call('{"tool_call": {"name": 5}}') is None
