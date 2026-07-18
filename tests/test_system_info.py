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


def _mock_proc(pid: int, name: str, cpu: float, mem: float) -> MagicMock:
    """Mock matching the two-pass sampling API (prime, wait, read)."""
    proc = MagicMock()
    proc.pid = pid
    proc.info = {"pid": pid, "name": name}
    proc.cpu_percent.return_value = cpu
    proc.memory_percent.return_value = mem
    return proc


@pytest.mark.asyncio
async def test_process_list_success(registry):
    procs = [
        _mock_proc(1234, "python3", 12.5, 1.5),
        _mock_proc(5678, "Finder", 0.1, 0.5),
    ]
    with patch("psutil.process_iter", return_value=procs), patch("time.sleep"):
        result = await registry.get("system_running_processes").handler(
            registry.validate_args("system_running_processes", {"top_n": 5})
        )
    assert result.ok
    listed = result.data["processes"]
    # Should be sorted by CPU% descending
    assert listed[0]["name"] == "python3"
    assert listed[0]["cpu_percent"] == 12.5
    assert listed[1]["name"] == "Finder"


@pytest.mark.asyncio
async def test_process_list_respects_top_n(registry):
    procs = [_mock_proc(i, f"proc{i}", float(i), 0.0) for i in range(20)]
    with patch("psutil.process_iter", return_value=procs), patch("time.sleep"):
        result = await registry.get("system_running_processes").handler(
            registry.validate_args("system_running_processes", {"top_n": 3})
        )
    assert result.ok
    assert len(result.data["processes"]) == 3


@pytest.mark.asyncio
async def test_tools_registered(registry):
    assert "system_disk_usage" in registry.names()
    assert "system_running_processes" in registry.names()
