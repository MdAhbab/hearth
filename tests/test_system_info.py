"""Tests for system info tools — disk usage and process list (Capability 2).

psutil calls are mocked so the tests work without real system data.
"""

from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest

from hearth.agent.tools import ToolRegistry
from hearth.connectors.system.tools import register_sysinfo_tools


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_sysinfo_tools(reg)
    return reg


@pytest.mark.asyncio
async def test_disk_usage_success(registry):
    mock_usage = MagicMock()
    mock_usage.total = 500 * 1024**3
    mock_usage.used = 200 * 1024**3
    mock_usage.free = 300 * 1024**3
    mock_usage.percent = 40.0

    with patch("psutil.disk_usage", return_value=mock_usage):
        result = await registry.get("system_disk_usage").handler(
            registry.validate_args("system_disk_usage", {"path": "/"})
        )
    assert result.ok
    assert result.data["total_gb"] == pytest.approx(500.0, rel=1e-3)
    assert result.data["free_gb"] == pytest.approx(300.0, rel=1e-3)
    assert result.data["percent_used"] == 40.0


@pytest.mark.asyncio
async def test_disk_usage_missing_path(registry):
    with patch("psutil.disk_usage", side_effect=FileNotFoundError("no such path")):
        result = await registry.get("system_disk_usage").handler(
            registry.validate_args("system_disk_usage", {"path": "/nonexistent"})
        )
    assert not result.ok
    assert "nonexistent" in result.error


@pytest.mark.asyncio
async def test_process_list_success(registry):
    import psutil

    mock_proc = MagicMock()
    mock_proc.info = {"pid": 1234, "name": "python3", "cpu_percent": 12.5, "memory_percent": 1.5}
    mock_proc2 = MagicMock()
    mock_proc2.info = {"pid": 5678, "name": "Finder", "cpu_percent": 0.1, "memory_percent": 0.5}

    with patch("psutil.process_iter", return_value=[mock_proc, mock_proc2]):
        result = await registry.get("system_running_processes").handler(
            registry.validate_args("system_running_processes", {"top_n": 5})
        )
    assert result.ok
    procs = result.data["processes"]
    # Should be sorted by CPU% descending
    assert procs[0]["name"] == "python3"
    assert procs[0]["cpu_percent"] == 12.5
    assert procs[1]["name"] == "Finder"


@pytest.mark.asyncio
async def test_process_list_respects_top_n(registry):
    procs_data = [
        MagicMock(info={"pid": i, "name": f"proc{i}", "cpu_percent": float(i), "memory_percent": 0.0})
        for i in range(20)
    ]
    with patch("psutil.process_iter", return_value=procs_data):
        result = await registry.get("system_running_processes").handler(
            registry.validate_args("system_running_processes", {"top_n": 3})
        )
    assert result.ok
    assert len(result.data["processes"]) == 3


@pytest.mark.asyncio
async def test_tools_registered(registry):
    assert "system_disk_usage" in registry.names()
    assert "system_running_processes" in registry.names()
