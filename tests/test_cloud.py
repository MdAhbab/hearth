"""Cloud fallback: OpenAI-compat wire format, SSE streaming, tool-call
accumulation, the fallback chain, and key-driven chain construction."""

import json

import httpx
import pytest

from hearth.config import Config, FallbackConfig
from hearth.runtime.cloud import (
    CLOUD_PROVIDERS,
    CloudProviderError,
    FallbackProvider,
    OpenAICompatProvider,
    build_cloud_chain,
)
from hearth.runtime.provider import ChatMessage, ChatResult, ToolCall
from hearth.storage.keychain import InMemorySecretStore


def _sse(*events: dict | str) -> str:
    lines = []
    for event in events:
        data = event if isinstance(event, str) else json.dumps(event)
        lines.append(f"data: {data}\n")
    lines.append("data: [DONE]\n")
    return "\n".join(lines)


def _delta(**delta) -> dict:
    return {"choices": [{"delta": delta}]}


def _provider(handler, provider_id="openai", model="gpt-test") -> OpenAICompatProvider:
    return OpenAICompatProvider(
        CLOUD_PROVIDERS[provider_id],
        api_key="sk-test",
        model=model,
        transport=httpx.MockTransport(handler),
    )


async def test_streams_content_with_auth_header():
    seen = {}
    chunks = []

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, text=_sse(_delta(content="Hel"), _delta(content="lo")))

    provider = _provider(handler)
    result = await provider.chat(
        [ChatMessage("user", "hi")], on_chunk=lambda c: chunks.append(c.text)
    )
    assert result.content == "Hello"
    assert chunks == ["Hel", "lo"]
    assert seen["auth"] == "Bearer sk-test"
    assert seen["payload"]["model"] == "gpt-test"
    assert seen["payload"]["stream"] is True


async def test_tool_call_accumulated_across_chunks():
    def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                _delta(tool_calls=[{"index": 0, "function": {"name": "get_weather"}}]),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"city": '}}]),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": '"Dhaka"}'}}]),
            ),
        )

    result = await _provider(handler).chat([ChatMessage("user", "weather?")], tools=[{}])
    assert result.tool_calls == [ToolCall("get_weather", {"city": "Dhaka"})]


def test_wire_pairs_tool_results_with_synthesized_ids():
    provider = _provider(lambda r: httpx.Response(200, text=_sse()))
    wire = provider._to_wire(
        [
            ChatMessage("system", "rules"),
            ChatMessage("user", "do it"),
            ChatMessage("assistant", "", tool_calls=[ToolCall("files_list", {"path": "~"})]),
            ChatMessage("tool", "result body", tool_name="files_list"),
            ChatMessage("assistant", "done"),
        ]
    )
    assistant = wire[2]
    tool = wire[3]
    assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "~"}
    assert "tool_name" not in tool  # OpenAI protocol uses ids, not names


def test_wire_images_as_parts_only_for_vision_models():
    vision = _provider(lambda r: httpx.Response(200), provider_id="gemini")
    wire = vision._to_wire([ChatMessage("user", "what is this?", images=["QUJD"])])
    parts = wire[0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1]["image_url"]["url"].endswith("QUJD")

    text_only = _provider(lambda r: httpx.Response(200), provider_id="deepseek")
    wire = text_only._to_wire([ChatMessage("user", "what is this?", images=["QUJD"])])
    assert isinstance(wire[0]["content"], str)
    assert "cannot see images" in wire[0]["content"]


async def test_auth_error_mentions_api_key():
    provider = _provider(lambda r: httpx.Response(401, text='{"error": "bad key"}'))
    with pytest.raises(CloudProviderError, match="API key"):
        await provider.chat([ChatMessage("user", "hi")])


async def test_fallback_skips_failed_provider_before_output():
    def broken(request):
        return httpx.Response(500, text="server error")

    def working(request):
        return httpx.Response(200, text=_sse(_delta(content="answer")))

    switches = []
    chain = FallbackProvider(
        [_provider(broken), _provider(working, provider_id="deepseek")],
        on_switch=switches.append,
    )
    result = await chain.chat([ChatMessage("user", "hi")])
    assert result.content == "answer"
    assert switches == ["OpenAI"]


async def test_fallback_does_not_retry_after_streamed_output():
    class MidStreamFailer(OpenAICompatProvider):
        async def chat(self, messages, tools=None, on_chunk=None):
            if on_chunk:
                on_chunk(type("C", (), {"text": "partial"})())
            raise CloudProviderError("died mid-stream")

    untouched = []

    def second(request):
        untouched.append(request)
        return httpx.Response(200, text=_sse(_delta(content="x")))

    chain = FallbackProvider(
        [
            MidStreamFailer(CLOUD_PROVIDERS["openai"], "k", "m"),
            _provider(second, provider_id="deepseek"),
        ]
    )
    with pytest.raises(CloudProviderError, match="mid-stream"):
        await chain.chat([ChatMessage("user", "hi")], on_chunk=lambda c: None)
    assert untouched == []


async def test_fallback_raises_when_all_fail():
    provider = _provider(lambda r: httpx.Response(503, text="down"))
    with pytest.raises(CloudProviderError, match="All cloud fallbacks failed"):
        await FallbackProvider([provider]).chat([ChatMessage("user", "hi")])


def test_build_cloud_chain_respects_keys_and_order():
    secrets = InMemorySecretStore()
    secrets.set("cloud_nvidia_api_key", "nv-key")
    secrets.set("cloud_gemini_api_key", "gm-key")
    chain = build_cloud_chain(FallbackConfig(order=["nvidia", "gemini", "openai"]), secrets)
    assert [p.label for p in chain] == ["NVIDIA", "Google Gemini"]
    assert chain[0].model == "nvidia/llama-3.1-nemotron-70b-instruct"

    assert build_cloud_chain(FallbackConfig(), InMemorySecretStore()) == []


def test_fallback_config_round_trips_through_toml(tmp_path):
    config = Config()
    config.fallback.enabled = True
    config.fallback.order = ["deepseek", "gemini"]
    path = tmp_path / "config.toml"
    config.save(path)
    reloaded = Config.load(path)
    assert reloaded.fallback.enabled is True
    assert reloaded.fallback.order == ["deepseek", "gemini"]


async def test_chat_result_protocol_shape():
    provider = _provider(lambda r: httpx.Response(200, text=_sse(_delta(content="ok"))))
    result = await provider.chat([ChatMessage("user", "hi")])
    assert isinstance(result, ChatResult) and result.done
