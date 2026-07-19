"""Opt-in cloud fallback providers (OpenAI-compatible chat APIs).

Hearth is local-first: these providers are used only when the user has
enabled fallback in Settings AND the local model is unreachable, and every
cloud-answered turn is labeled in the chat. All four supported services
(Google Gemini, OpenAI, DeepSeek, NVIDIA) speak the OpenAI chat-completions
protocol, so one provider class covers them; API keys live in the OS
credential store, never in files.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import FallbackConfig
from ..storage.keychain import SecretStore
from .provider import ChatChunk, ChatMessage, ChatResult, ToolCall

log = logging.getLogger(__name__)


class CloudProviderError(RuntimeError):
    """A cloud request failed; the message is safe to show the user."""


@dataclass(frozen=True)
class CloudSpec:
    id: str
    label: str
    base_url: str
    supports_vision: bool
    key_name: str  # keychain entry holding the API key


CLOUD_PROVIDERS: dict[str, CloudSpec] = {
    "gemini": CloudSpec(
        "gemini",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        supports_vision=True,
        key_name="cloud_gemini_api_key",
    ),
    "openai": CloudSpec(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        supports_vision=True,
        key_name="cloud_openai_api_key",
    ),
    "deepseek": CloudSpec(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com/v1",
        supports_vision=False,
        key_name="cloud_deepseek_api_key",
    ),
    "nvidia": CloudSpec(
        "nvidia",
        "NVIDIA",
        "https://integrate.api.nvidia.com/v1",
        supports_vision=False,
        key_name="cloud_nvidia_api_key",
    ),
}


class OpenAICompatProvider:
    """ChatProvider over an OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        spec: CloudSpec,
        api_key: str,
        model: str,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.spec = spec
        self.label = spec.label
        self.model = model
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._transport = transport  # tests inject httpx.MockTransport

    def _to_wire(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Convert to OpenAI wire format.

        The OpenAI protocol pairs tool results to tool calls by id; Ollama's
        does not carry ids, so synthesize them in order — the agent loop
        always emits assistant(tool_calls) directly followed by its results.
        """
        wire: list[dict[str, Any]] = []
        pending_ids: list[str] = []
        call_counter = 0
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                calls = []
                pending_ids = []
                for call in m.tool_calls:
                    call_id = f"call_{call_counter}"
                    call_counter += 1
                    pending_ids.append(call_id)
                    calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, default=str),
                            },
                        }
                    )
                wire.append({"role": "assistant", "content": m.content, "tool_calls": calls})
            elif m.role == "tool":
                call_id = pending_ids.pop(0) if pending_ids else "call_0"
                wire.append({"role": "tool", "tool_call_id": call_id, "content": m.content})
            elif m.images and self.spec.supports_vision:
                parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
                for image in m.images:
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image}"},
                        }
                    )
                wire.append({"role": m.role, "content": parts})
            elif m.images:
                note = f"{m.content}\n[an image was attached, but {self.label} cannot see images]"
                wire.append({"role": m.role, "content": note})
            else:
                wire.append({"role": m.role, "content": m.content})
        return wire

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[ChatChunk], None] | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_wire(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        headers = {"Authorization": f"Bearer {self._api_key}"}

        content_parts: list[str] = []
        call_accum: dict[int, dict[str, str]] = {}

        timeout = httpx.Timeout(self._timeout_s, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.spec.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode(errors="replace")[:300]
                        hint = (
                            " — check the API key in Settings"
                            if resp.status_code in (401, 403)
                            else ""
                        )
                        raise CloudProviderError(
                            f"{self.label}: HTTP {resp.status_code}{hint}: {body}"
                        )
                    async with aclosing(self._iter_sse(resp)) as events:
                        async for data in events:
                            if data == "[DONE]":
                                break
                            delta = self._delta(data)
                            if text := delta.get("content"):
                                content_parts.append(text)
                                if on_chunk:
                                    on_chunk(ChatChunk(text=text))
                            for fragment in delta.get("tool_calls") or []:
                                slot = call_accum.setdefault(
                                    fragment.get("index", 0), {"name": "", "args": ""}
                                )
                                fn = fragment.get("function") or {}
                                slot["name"] += fn.get("name") or ""
                                slot["args"] += fn.get("arguments") or ""
            except httpx.HTTPError as exc:
                raise CloudProviderError(f"{self.label}: {exc}") from exc

        tool_calls = []
        for index in sorted(call_accum):
            slot = call_accum[index]
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {}
            if slot["name"]:
                tool_calls.append(ToolCall(name=slot["name"], arguments=args))
        return ChatResult(content="".join(content_parts), tool_calls=tool_calls)

    @staticmethod
    def _delta(data: str) -> dict[str, Any]:
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return {}
        choices = chunk.get("choices") or []
        return (choices[0].get("delta") or {}) if choices else {}

    @staticmethod
    async def _iter_sse(resp: httpx.Response):
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                yield line[5:].strip()


class FallbackProvider:
    """Tries each configured cloud provider in order until one answers.

    A provider that fails BEFORE emitting any output is skipped silently
    (with a notice via ``on_switch``); one that fails mid-stream raises,
    because retrying would duplicate text the user has already seen.
    """

    def __init__(
        self,
        providers: list[OpenAICompatProvider],
        on_switch: Callable[[str], None] | None = None,
    ):
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        self._providers = providers
        self._on_switch = on_switch

    @property
    def label(self) -> str:
        return self._providers[0].label

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[ChatChunk], None] | None = None,
    ) -> ChatResult:
        errors: list[str] = []
        for provider in self._providers:
            emitted = False

            def counting_chunk(chunk: ChatChunk) -> None:
                nonlocal emitted
                emitted = True
                if on_chunk:
                    on_chunk(chunk)

            try:
                return await provider.chat(messages, tools=tools, on_chunk=counting_chunk)
            except CloudProviderError as exc:
                if emitted:
                    raise
                log.warning("Cloud provider failed, trying next: %s", exc)
                errors.append(str(exc))
                if self._on_switch and provider is not self._providers[-1]:
                    self._on_switch(provider.label)
        raise CloudProviderError("All cloud fallbacks failed: " + " | ".join(errors))


def configured_model(fallback: FallbackConfig, provider_id: str) -> str:
    """The model id configured for a cloud provider (config [fallback] section)."""
    return {
        "gemini": fallback.gemini_model,
        "openai": fallback.openai_model,
        "deepseek": fallback.deepseek_model,
        "nvidia": fallback.nvidia_model,
    }.get(provider_id, "")


def build_cloud_chain(
    fallback: FallbackConfig,
    secrets: SecretStore,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[OpenAICompatProvider]:
    """Providers with a stored API key, in the configured priority order."""
    chain: list[OpenAICompatProvider] = []
    for provider_id in fallback.order:
        spec = CLOUD_PROVIDERS.get(provider_id)
        if spec is None:
            log.warning("Unknown fallback provider in config: %s", provider_id)
            continue
        key = secrets.get(spec.key_name)
        if key:
            chain.append(
                OpenAICompatProvider(
                    spec, key, configured_model(fallback, provider_id), transport=transport
                )
            )
    return chain


def build_primary_provider(
    provider_id: str,
    model: str,
    secrets: SecretStore,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAICompatProvider | None:
    """A single cloud provider chosen as the *primary* model in Settings.

    Returns None when the id is unknown or no API key is stored — the caller
    explains that in the chat instead of failing.
    """
    spec = CLOUD_PROVIDERS.get(provider_id)
    if spec is None:
        return None
    key = secrets.get(spec.key_name)
    if not key:
        return None
    return OpenAICompatProvider(spec, key, model, transport=transport)
