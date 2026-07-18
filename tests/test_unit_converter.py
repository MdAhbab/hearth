"""Tests for the convert_units tool (Capability 5)."""

from __future__ import annotations

import pytest

from hearth.agent.tools import ToolRegistry
from hearth.config import WebConfig
from hearth.connectors.utility.tools import register_utility_tools


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_utility_tools(reg, WebConfig())
    return reg


async def _convert(registry, value, from_unit, to_unit):
    return await registry.get("convert_units").handler(
        registry.validate_args(
            "convert_units",
            {"value": value, "from_unit": from_unit, "to_unit": to_unit},
        )
    )


# ---------------------------------------------------------------------------
# Length
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_km_to_miles(registry):
    result = await _convert(registry, 1.0, "km", "miles")
    assert result.ok
    assert result.data["result"] == pytest.approx(0.621371, rel=1e-4)
    assert result.data["category"] == "length"


@pytest.mark.asyncio
async def test_feet_to_metres(registry):
    result = await _convert(registry, 10.0, "feet", "m")
    assert result.ok
    assert result.data["result"] == pytest.approx(3.048, rel=1e-4)


# ---------------------------------------------------------------------------
# Temperature (offset conversions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_celsius_to_fahrenheit(registry):
    result = await _convert(registry, 100.0, "celsius", "fahrenheit")
    assert result.ok
    assert result.data["result"] == pytest.approx(212.0, rel=1e-4)


@pytest.mark.asyncio
async def test_fahrenheit_to_celsius(registry):
    result = await _convert(registry, 32.0, "fahrenheit", "celsius")
    assert result.ok
    assert result.data["result"] == pytest.approx(0.0, abs=1e-5)


@pytest.mark.asyncio
async def test_celsius_to_kelvin(registry):
    result = await _convert(registry, 0.0, "celsius", "kelvin")
    assert result.ok
    assert result.data["result"] == pytest.approx(273.15, rel=1e-4)


# ---------------------------------------------------------------------------
# Mass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kg_to_pounds(registry):
    result = await _convert(registry, 1.0, "kg", "pounds")
    assert result.ok
    assert result.data["result"] == pytest.approx(2.20462, rel=1e-4)


@pytest.mark.asyncio
async def test_ounces_to_grams(registry):
    result = await _convert(registry, 1.0, "ounces", "grams")
    assert result.ok
    assert result.data["result"] == pytest.approx(28.3495, rel=1e-3)


# ---------------------------------------------------------------------------
# Speed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mph_to_kph(registry):
    result = await _convert(registry, 60.0, "mph", "kph")
    assert result.ok
    assert result.data["result"] == pytest.approx(96.5606, rel=1e-3)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gb_to_mb(registry):
    result = await _convert(registry, 1.0, "gb", "mb")
    assert result.ok
    assert result.data["result"] == pytest.approx(1024.0, rel=1e-5)


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_litres_to_gallons(registry):
    result = await _convert(registry, 1.0, "litre", "gallon")
    assert result.ok
    assert result.data["result"] == pytest.approx(0.264172, rel=1e-3)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hours_to_seconds(registry):
    result = await _convert(registry, 2.0, "hours", "seconds")
    assert result.ok
    assert result.data["result"] == pytest.approx(7200.0, rel=1e-5)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_unit(registry):
    result = await _convert(registry, 1.0, "parsec", "km")
    assert not result.ok
    assert "parsec" in result.error


@pytest.mark.asyncio
async def test_mismatched_categories(registry):
    result = await _convert(registry, 1.0, "km", "kg")
    assert not result.ok
    assert "different things" in result.error


@pytest.mark.asyncio
async def test_identity_conversion(registry):
    result = await _convert(registry, 42.0, "km", "km")
    assert result.ok
    assert result.data["result"] == pytest.approx(42.0, rel=1e-9)
