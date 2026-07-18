"""Tests for files_search_content tool (Capability 1)."""

from __future__ import annotations

import pytest

from hearth.agent.tools import ToolRegistry
from hearth.connectors.files import ApprovedRoots, register_file_tools


@pytest.fixture()
def tmp_root(tmp_path):
    # Create a small tree inside an approved folder
    (tmp_path / "notes.txt").write_text("Meeting agenda: discuss project deadline\n")
    (tmp_path / "code.py").write_text("def deadline_check():\n    pass  # TODO: project deadline\n")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "log.log").write_text("ERROR: project deadline overdue\n")
    return tmp_path


@pytest.fixture()
def registry(tmp_root):
    reg = ToolRegistry()
    roots = ApprovedRoots(lambda: [str(tmp_root)])
    register_file_tools(reg, roots, max_read_bytes=262144)
    return reg


@pytest.mark.asyncio
async def test_search_content_finds_matches(registry):
    params = {"keyword": "project deadline"}
    result = await registry.get("files_search_content").handler(
        registry.validate_args("files_search_content", params)
    )
    assert result.ok
    assert result.data["match_count"] >= 3  # notes.txt, code.py, sub/log.log
    files = {m["file"] for m in result.data["matches"]}
    assert any("notes.txt" in f for f in files)
    assert any("code.py" in f for f in files)
    assert any("log.log" in f for f in files)


@pytest.mark.asyncio
async def test_search_content_case_insensitive(registry):
    params = {"keyword": "PROJECT DEADLINE", "case_sensitive": False}
    result = await registry.get("files_search_content").handler(
        registry.validate_args("files_search_content", params)
    )
    assert result.ok
    assert result.data["match_count"] >= 3


@pytest.mark.asyncio
async def test_search_content_case_sensitive_no_match(registry):
    params = {"keyword": "PROJECT DEADLINE", "case_sensitive": True}
    result = await registry.get("files_search_content").handler(
        registry.validate_args("files_search_content", params)
    )
    assert result.ok
    assert result.data["match_count"] == 0  # source files are lowercase


@pytest.mark.asyncio
async def test_search_content_skips_binary(registry):
    params = {"keyword": "\x00\x01"}
    result = await registry.get("files_search_content").handler(
        registry.validate_args("files_search_content", params)
    )
    assert result.ok
    # binary.bin has no recognised text suffix — should not be in matches
    files = {m["file"] for m in result.data["matches"]}
    assert all("binary.bin" not in f for f in files)


@pytest.mark.asyncio
async def test_search_content_returns_line_numbers(registry):
    params = {"keyword": "TODO"}
    result = await registry.get("files_search_content").handler(
        registry.validate_args("files_search_content", params)
    )
    assert result.ok
    for match in result.data["matches"]:
        assert "line" in match
        assert isinstance(match["line"], int)
        assert match["line"] >= 1
