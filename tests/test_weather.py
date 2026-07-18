"""Tests for weather_current tool with mocked httpx (Capability 4)."""

from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hearth.agent.tools import ToolRegistry
from hearth.connectors.weather.tools import register_weather_tools, _degrees_to_compass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_geocode_response(lat=35.68, lon=139.69, name="Tokyo", country="Japan"):
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "results": [{"latitude": lat, "longitude": lon, "name": name, "country": country}]
    }
    return mock


def _make_forecast_response():
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "current": {
            "time": "2026-07-18T15:00",
            "temperature_2m": 31.5,
            "apparent_temperature": 34.0,
            "relative_humidity_2m": 68,
            "weather_code": 2,
            "wind_speed_10m": 18.0,
            "wind_direction_10m": 180,
            "precipitation": 0.0,
            "cloud_cover": 40,
            "is_day": 1,
        },
        "current_units": {
            "temperature_2m": "°C",
            "wind_speed_10m": "km/h",
        },
        "daily": {
            "weather_code": [2],
            "temperature_2m_max": [33.0],
            "temperature_2m_min": [24.0],
            "precipitation_sum": [0.0],
            "sunrise": ["2026-07-18T04:41"],
            "sunset": ["2026-07-18T19:01"],
        },
        "timezone": "Asia/Tokyo",
    }
    return mock


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_weather_tools(reg)
    return reg


@pytest.mark.asyncio
async def test_weather_current_success(registry):
    geocode_resp = _make_geocode_response()
    forecast_resp = _make_forecast_response()

    async def mock_get(url, **kwargs):
        if "geocoding" in url:
            return geocode_resp
        return forecast_resp

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("hearth.connectors.weather.tools.httpx.AsyncClient", return_value=mock_client):
        result = await registry.get("weather_current").handler(
            registry.validate_args("weather_current", {"location": "Tokyo"})
        )

    assert result.ok
    d = result.data
    assert d["location"] == "Tokyo, Japan"
    assert d["condition"] == "Partly cloudy"
    assert d["temperature"]["current"] == 31.5
    assert d["temperature"]["today_high"] == 33.0
    assert d["wind"]["direction"] == "S"  # 180° = South
    assert d["timezone"] == "Asia/Tokyo"


@pytest.mark.asyncio
async def test_weather_unknown_location(registry):
    no_result = MagicMock()
    no_result.raise_for_status = MagicMock()
    no_result.json.return_value = {"results": []}

    async def mock_get(url, **kwargs):
        return no_result

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("hearth.connectors.weather.tools.httpx.AsyncClient", return_value=mock_client):
        result = await registry.get("weather_current").handler(
            registry.validate_args("weather_current", {"location": "xyznotacity"})
        )
    assert not result.ok
    assert "xyznotacity" in result.error


@pytest.mark.asyncio
async def test_weather_latlon_shortcut(registry):
    forecast_resp = _make_forecast_response()

    async def mock_get(url, **kwargs):
        return forecast_resp

    mock_client = AsyncMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("hearth.connectors.weather.tools.httpx.AsyncClient", return_value=mock_client):
        result = await registry.get("weather_current").handler(
            registry.validate_args("weather_current", {"location": "35.68,139.69"})
        )
    assert result.ok
    assert result.data["latitude"] == 35.68


# ---------------------------------------------------------------------------
# Compass helper unit test
# ---------------------------------------------------------------------------

def test_compass_directions():
    assert _degrees_to_compass(0) == "N"
    assert _degrees_to_compass(90) == "E"
    assert _degrees_to_compass(180) == "S"
    assert _degrees_to_compass(270) == "W"
    assert _degrees_to_compass(360) == "N"
    assert _degrees_to_compass(45) == "NE"
