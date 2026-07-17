"""Agent loop behavior with a scripted fake provider (no model, no network)."""

from hearth.agent.loop import TOOL_RESULT_FRAME, AgentLoop
from hearth.runtime.provider import ChatResult, ToolCall


class ScriptedProvider:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # message lists seen

    async def chat(self, messages, tools=None, on_chunk=None):
        self.calls.append(messages)
        result = self.script.pop(0) if self.script else ChatResult("done", [])
        if on_chunk and result.content and not result.tool_calls:
            from hearth.runtime.provider import ChatChunk

            on_chunk(ChatChunk(result.content))
        return result


async def test_tool_call_then_answer(harness, registry):
    provider = ScriptedProvider(
        [
            ChatResult("", [ToolCall("echo_read", {"text": "ping"})]),
            ChatResult("The echo said ping.", []),
        ]
    )
    loop = AgentLoop(provider, registry, harness.gate, max_steps=6)
    answer = await loop.run([], "please echo ping")
    assert answer == "The echo said ping."
    assert harness.executed == ["ping"]


async def test_tool_results_are_framed_as_data(harness, registry):
    provider = ScriptedProvider(
        [
            ChatResult("", [ToolCall("echo_read", {"text": "ping"})]),
            ChatResult("ok", []),
        ]
    )
    loop = AgentLoop(provider, registry, harness.gate, max_steps=6)
    await loop.run([], "echo it")
    tool_messages = [m for m in provider.calls[-1] if m.role == "tool"]
    assert tool_messages
    assert tool_messages[0].content == TOOL_RESULT_FRAME.format(body="echo:ping")


async def test_step_cap_stops_runaway(harness, registry):
    provider = ScriptedProvider(
        [ChatResult("", [ToolCall("echo_read", {"text": f"n{i}"})]) for i in range(50)]
    )
    loop = AgentLoop(provider, registry, harness.gate, max_steps=3)
    answer = await loop.run([], "loop forever")
    assert len(harness.executed) == 3
    assert "limit" in answer


async def test_identical_failing_call_breaks_early(harness, registry):
    bad_call = ToolCall("echo_read", {"text": "x"})
    harness.granted = set()  # every call fails with permission error
    provider = ScriptedProvider(
        [
            ChatResult("", [bad_call]),
            ChatResult("", [ToolCall("echo_read", {"text": "x"})]),
            ChatResult("", [ToolCall("echo_read", {"text": "x"})]),
        ]
    )
    loop = AgentLoop(provider, registry, harness.gate, max_steps=6)
    answer = await loop.run([], "try")
    assert "failed twice" in answer
    assert len(provider.calls) == 2  # stopped, didn't burn all steps


async def test_unknown_tool_reported_not_crash(harness, registry):
    provider = ScriptedProvider(
        [
            ChatResult("", [ToolCall("not_a_tool", {})]),
            ChatResult("I could not find that tool.", []),
        ]
    )
    loop = AgentLoop(provider, registry, harness.gate, max_steps=6)
    answer = await loop.run([], "do something odd")
    assert answer == "I could not find that tool."
    tool_messages = [m for m in provider.calls[-1] if m.role == "tool"]
    assert "Unknown tool" in tool_messages[0].content


async def test_plain_chat_no_tools(harness, registry):
    provider = ScriptedProvider([ChatResult("Hello!", [])])
    loop = AgentLoop(provider, registry, harness.gate, max_steps=6)
    answer = await loop.run([], "hi")
    assert answer == "Hello!"
    assert harness.executed == []
