"""Offline protocol, resume, metric, and isolation tests for model evaluation."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest

from benchmarks.intentseal.corpus import load_scenarios
from benchmarks.intentseal.model_eval import (
    MODEL_ID,
    OLLAMA_BASE_URL,
    LocalOptionsTransport,
    NetworkIsolationError,
    ProviderResponse,
    ollama_only_network,
    run_evaluation,
)
from benchmarks.intentseal.model_harness import (
    build_model_request,
    execute_model_calls,
    model_visible_carrier,
    response_is_refusal,
)
from hearth.runtime.provider import ChatResult, ToolCall


class ScriptedProvider:
    def __init__(self, *results: ChatResult) -> None:
        self.results = list(results)
        self.requests: list[dict] = []

    async def chat_case(self, messages, tools, *, seed):
        self.requests.append(
            {
                "messages": messages,
                "tools": tools,
                "seed": seed,
            }
        )
        if not self.results:
            raise AssertionError("scripted provider received an unexpected call")
        return ProviderResponse(
            result=self.results.pop(0),
            ollama_http_requests=1,
            request_options=[
                {"temperature": 0, "seed": seed, "num_ctx": 8192}
            ],
        )


@pytest.fixture(scope="module")
def model_records():
    return {record["id"]: record for record in load_scenarios()}


@pytest.fixture
def inventory():
    value = {
        "ollama_version": "test-local",
        "model_id": MODEL_ID,
        "model_name": MODEL_ID,
        "model_digest": "a" * 64,
        "modified_at": "2026-01-01T00:00:00Z",
        "size_bytes": 1,
        "format": "gguf",
        "family": "gemma4",
        "parameter_size": "5.1B",
        "parameter_count": 5_123_179_235,
        "quantization": "Q4_K_M",
        "native_context_length": 131_072,
        "capabilities": ["completion", "tools"],
        "model_defaults": "temperature 1",
        "template_sha256": "b" * 64,
    }
    from hearth.assurance import stable_hash

    value["inventory_sha256"] = stable_hash(value)
    return value


def test_model_boundary_is_label_blind_and_schema_is_strict(model_records):
    record = model_records["G1-01"]
    messages, tools = build_model_request(record)
    wire = json.dumps(
        {
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "tools": tools,
        },
        sort_keys=True,
    )
    assert [message.role for message in messages] == ["system", "user"]
    assert record["trusted_goal"] in wire
    assert record["tool"] in wire
    assert '"is_control"' not in wire
    assert '"unsafe_property"' not in wire
    assert '"final_label"' not in wire
    assert '"expected_decision"' not in wire
    assert '"semantic_state"' not in wire
    assert '"source_trust"' not in wire
    schema = tools[0]["function"]["parameters"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(record["proposal"]["args"])
    assert "embedded_workflow_note" in model_visible_carrier(record)


async def test_exact_control_call_uses_gate_seal_staging_and_oracle(model_records):
    record = model_records["G1-01B"]
    outcome = await execute_model_calls(
        record,
        [ToolCall(record["tool"], dict(record["proposal"]["args"]))],
    )
    assert outcome["correct_tool_proposed"]
    assert outcome["action_gate_used"]
    assert outcome["tool_registry_used"]
    assert outcome["seal_verification_attempted"]
    assert outcome["handler_after_verified_seal"]
    assert outcome["staging_used"]
    assert outcome["postcondition_checked"]
    assert outcome["audit_chain_valid"]
    assert outcome["execution"]
    assert outcome["benign_task_success"]
    assert not outcome["external_network_used"]
    assert not outcome["real_connector_used"]


async def test_unexpected_tool_and_args_are_distinguished(model_records):
    record = model_records["G1-01B"]
    wrong_args = await execute_model_calls(
        record,
        [ToolCall(record["tool"], {"value": "different inert value"})],
    )
    assert wrong_args["unexpected_args"]
    assert not wrong_args["unexpected_tool"]
    assert wrong_args["action_gate_used"]
    assert not wrong_args["execution"]
    assert not wrong_args["handler_called"]

    wrong_tool = await execute_model_calls(
        record,
        [ToolCall("invented_external_tool", {})],
    )
    assert wrong_tool["unexpected_tool"]
    assert not wrong_tool["tool_registry_used"]
    assert not wrong_tool["execution"]


async def test_no_tool_and_refusal_remain_distinct(model_records):
    record = model_records["G1-01"]
    outcome = await execute_model_calls(record, [])
    assert outcome["no_tool_proposed"]
    assert not outcome["action_gate_used"]
    assert response_is_refusal("No tool is authorized for this task.", [])
    assert not response_is_refusal("The carrier was inspected.", [])


async def test_resumable_append_metrics_and_hash_validation(
    tmp_path, inventory, model_records
):
    first_record = model_records["G1-01"]
    first = ScriptedProvider(
        ChatResult(
            content="",
            tool_calls=[
                ToolCall(
                    first_record["tool"],
                    dict(first_record["proposal"]["args"]),
                )
            ],
        )
    )
    first_outcome = await run_evaluation(
        provider=first,
        inventory=inventory,
        output_dir=tmp_path,
        limit=1,
    )
    assert first_outcome["new_rows"] == 1
    assert first_outcome["summary"]["completed_model_calls"] == 1
    assert (
        first_outcome["summary"]["base_runs_only"]["model_behavior"][
            "proposal_diversion_rate_adversarial"
        ]
        == 1.0
    )
    assert len(first.requests) == 1

    with (tmp_path / "raw.jsonl").open("ab") as stream:
        stream.write(b'{"interrupted":')

    second = ScriptedProvider(ChatResult(content="Carrier inspected.", tool_calls=[]))
    second_outcome = await run_evaluation(
        provider=second,
        inventory=inventory,
        output_dir=tmp_path,
        limit=1,
    )
    assert second_outcome["new_rows"] == 1
    assert second_outcome["manifest"]["completed_model_calls"] == 2
    assert second_outcome["manifest"]["trailing_partial_repairs"] == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / "raw.jsonl").read_text().splitlines()
    ]
    assert [row["run_key"] for row in rows] == ["G1-01:0", "G1-01B:0"]
    assert (tmp_path / "results.csv").exists()
    assert (tmp_path / "summary.json").exists()

    mismatched = dict(inventory)
    mismatched["model_digest"] = "c" * 64
    from hearth.assurance import stable_hash

    without_hash = {
        key: value
        for key, value in mismatched.items()
        if key != "inventory_sha256"
    }
    mismatched["inventory_sha256"] = stable_hash(without_hash)
    with pytest.raises(RuntimeError, match="evaluation identity changed"):
        await run_evaluation(
            provider=ScriptedProvider(),
            inventory=mismatched,
            output_dir=tmp_path,
            limit=0,
        )


async def test_protocol_seed_and_options_are_recorded(
    tmp_path, inventory, model_records
):
    provider = ScriptedProvider(ChatResult(content="No action needed.", tool_calls=[]))
    outcome = await run_evaluation(
        provider=provider,
        inventory=inventory,
        output_dir=tmp_path,
        limit=1,
    )
    assert provider.requests[0]["seed"] == model_records["G1-01"]["seed"]
    row = json.loads((tmp_path / "raw.jsonl").read_text().splitlines()[0])
    assert row["temperature_requested"] == 0
    assert row["request_options"][0]["temperature"] == 0
    assert row["refusal"]
    assert row["refusal_or_parse_failure"]
    assert outcome["manifest"]["planned_model_calls"] == 280
    assert len(
        outcome["manifest"]["repeat_preregistration"]["record_ids"]
    ) == 40


async def test_local_transport_injects_options_and_blocks_other_hosts():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(await request.aread()))
        return httpx.Response(200, json={"ok": True})

    transport = LocalOptionsTransport(
        httpx.MockTransport(handler),
        seed=77,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"options": {"num_ctx": 8192}},
        )
    assert response.status_code == 200
    assert captured["options"] == {
        "num_ctx": 8192,
        "seed": 77,
        "temperature": 0,
    }
    with pytest.raises(RuntimeError, match="blocked non-Ollama endpoint"):
        LocalOptionsTransport.validate_url(
            httpx.URL("https://example.test/api/chat")
        )
    with pytest.raises(RuntimeError, match="blocked non-Ollama endpoint"):
        LocalOptionsTransport.validate_url(
            httpx.URL("http://localhost:11434/api/chat")
        )


async def test_inert_execution_uses_no_network(monkeypatch, model_records):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("benchmark attempted a real network operation")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    record = model_records["G1-01B"]
    outcome = await execute_model_calls(
        record,
        [ToolCall(record["tool"], dict(record["proposal"]["args"]))],
    )
    assert outcome["execution"]
    source = Path(
        "benchmarks/intentseal/model_harness.py"
    ).read_text(encoding="utf-8")
    assert "hearth.connectors" not in source


def test_process_network_guard_allows_only_exact_ollama_endpoint():
    with ollama_only_network():
        with pytest.raises(NetworkIsolationError):
            socket.getaddrinfo("example.test", 443)
        with pytest.raises(NetworkIsolationError):
            socket.create_connection(("127.0.0.1", 9999), timeout=0.01)
