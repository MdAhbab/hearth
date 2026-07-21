"""Model-agnostic chat provider over Ollama's /api/chat.

The rest of the app depends only on ``ChatProvider``; swapping models (e.g.
gemma4:e2b -> gemma4:e4b) is a config change, and swapping backends means
implementing one protocol.

Tool calling: native Ollama tool calls are used when the model's template
supports them. If the server rejects the request with "does not support
tools", the provider transparently falls back to a JSON tool-call protocol
embedded in the system prompt, so small local models still get tool use.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ..config import ModelConfig, OllamaConfig

log = logging.getLogger(__name__)

# Ollama silently drops tokens past num_ctx from the *front* of the prompt, so
# an undersized window quietly truncates the system prompt and tool results and
# the model then answers with nonsense or nothing. The tool schemas alone can
# run to several thousand tokens, so we never send a window smaller than the
# prompt plus room to answer — raising it above the configured size if needed.
_RESPONSE_HEADROOM_TOKENS = 1024
_MAX_NUM_CTX = 32768
_CHARS_PER_TOKEN = 4  # coarse estimate; this is a guard, not a real tokenizer


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: str | None = None  # set on role="tool" messages
    images: list[str] = field(default_factory=list)  # base64, vision models only


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatChunk:
    """One streamed increment of assistant text."""

    text: str


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall]
    done: bool = True


class ChatProvider(Protocol):
    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[ChatChunk], None] | None = None,
    ) -> ChatResult: ...


JSON_TOOL_INSTRUCTIONS = """\
You can use tools. To call a tool, reply with ONLY a JSON object in this exact
shape and nothing else:
{"tool_call": {"name": "<tool name>", "arguments": {<arguments>}}}
Available tools (JSON Schema):
%s
If no tool is needed, reply with normal text. Never invent tool names.
"""


def _parse_json_tool_call(text: str) -> ToolCall | None:
    """Detect the fallback JSON tool-call shape in a model response."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    if not (candidate.startswith("{") and '"tool_call"' in candidate):
        return None
    try:
        obj = json.loads(candidate)
        call = obj.get("tool_call")
        if isinstance(call, dict) and isinstance(call.get("name"), str):
            args = call.get("arguments") or {}
            if isinstance(args, dict):
                return ToolCall(name=call["name"], arguments=args)
    except json.JSONDecodeError:
        return None
    return None


class OllamaProvider:
    def __init__(
        self,
        model_config: ModelConfig,
        ollama_config: OllamaConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._model = model_config
        self._ollama = ollama_config
        self._transport = transport  # tests inject httpx.MockTransport
        self._native_tools_supported: bool | None = None  # unknown until first try

    @property
    def model_name(self) -> str:
        return self._model.name

    def _to_wire(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        wire = []
        for m in messages:
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls
                ]
            if m.role == "tool" and m.tool_name:
                entry["tool_name"] = m.tool_name
            if m.images:
                entry["images"] = m.images
            wire.append(entry)
        return wire

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[ChatChunk], None] | None = None,
    ) -> ChatResult:
        use_native = bool(tools) and self._native_tools_supported is not False
        if tools and not use_native:
            messages = self._inject_json_tool_prompt(messages, tools)

        try:
            result = await self._request(messages, tools if use_native else None, on_chunk)
        except _ToolsUnsupportedError:
            log.info("Model %s lacks native tool support; using JSON fallback", self._model.name)
            self._native_tools_supported = False
            messages = self._inject_json_tool_prompt(messages, tools or [])
            result = await self._request(messages, None, on_chunk)

        if use_native and result.tool_calls:
            self._native_tools_supported = True
        if tools and not result.tool_calls:
            fallback_call = _parse_json_tool_call(result.content)
            if fallback_call:
                return ChatResult(content="", tool_calls=[fallback_call])
        return result

    def _inject_json_tool_prompt(
        self, messages: list[ChatMessage], tools: list[dict[str, Any]]
    ) -> list[ChatMessage]:
        instructions = JSON_TOOL_INSTRUCTIONS % json.dumps(tools, indent=1)
        if messages and messages[0].role == "system":
            merged = ChatMessage("system", messages[0].content + "\n\n" + instructions)
            return [merged, *messages[1:]]
        return [ChatMessage("system", instructions), *messages]

    def _effective_num_ctx(
        self, messages: list[ChatMessage], tools: list[dict[str, Any]] | None
    ) -> int:
        """Configured context window, raised so tools+prompt can't overflow it.

        Sizes from text only — image bytes don't map to text tokens — and always
        leaves room for the reply, capped at ``_MAX_NUM_CTX``. Returns the
        configured value unchanged when the prompt already fits.
        """
        chars = sum(len(m.content) for m in messages)
        for m in messages:
            for call in m.tool_calls:
                chars += len(call.name) + len(json.dumps(call.arguments, default=str))
        if tools:
            chars += len(json.dumps(tools, default=str))
        needed = chars // _CHARS_PER_TOKEN + _RESPONSE_HEADROOM_TOKENS
        effective = min(max(self._model.context_length, needed), _MAX_NUM_CTX)
        if effective > self._model.context_length:
            log.info(
                "Raising num_ctx %d -> %d so tools+prompt fit (~%d prompt tokens)",
                self._model.context_length,
                effective,
                chars // _CHARS_PER_TOKEN,
            )
        return effective

    async def _request(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        on_chunk: Callable[[ChatChunk], None] | None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self._model.name,
            "messages": self._to_wire(messages),
            "stream": True,
            "keep_alive": self._model.keep_alive,
            "options": {"num_ctx": self._effective_num_ctx(messages, tools)},
        }
        if tools:
            payload["tools"] = tools

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        timeout = httpx.Timeout(self._ollama.request_timeout_s, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            async with client.stream(
                "POST", f"{self._ollama.base_url}/api/chat", json=payload
            ) as resp:
                if resp.status_code == 400:
                    body = (await resp.aread()).decode(errors="replace")
                    if "does not support tools" in body:
                        raise _ToolsUnsupportedError(body)
                    raise RuntimeError(f"Ollama rejected request: {body}")
                resp.raise_for_status()
                # aclosing: we break on the final chunk, so close the stream
                # generator here rather than at GC time (avoids
                # "async generator ignored GeneratorExit" on shutdown).
                async with aclosing(self._iter_lines(resp)) as lines:
                    async for line in lines:
                        chunk = json.loads(line)
                        msg = chunk.get("message", {})
                        if text := msg.get("content"):
                            content_parts.append(text)
                            if on_chunk:
                                on_chunk(ChatChunk(text=text))
                        for call in msg.get("tool_calls") or []:
                            fn = call.get("function", {})
                            args = fn.get("arguments") or {}
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {}
                            tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args))
                        if chunk.get("done"):
                            break

        return ChatResult(content="".join(content_parts), tool_calls=tool_calls)

    @staticmethod
    async def _iter_lines(resp: httpx.Response) -> AsyncIterator[str]:
        async for line in resp.aiter_lines():
            if line.strip():
                yield line


class _ToolsUnsupportedError(RuntimeError):
    pass
