"""Weather connector — current conditions via Open-Meteo (free, no API key).

Flow:
  1. Geocode the city name → lat/lon via Open-Meteo Geocoding API.
  2. Fetch current weather + today's daily forecast from Open-Meteo Forecast API.
  3. Return structured data the model can summarise naturally.

This connector requires the 'weather' permission (off by default because it
makes two HTTPS requests to open-meteo.com and nominatim.openstreetmap.org).
"""

from __future__ import annotations

from datetime import datetime

import httpx
from pydantic import BaseModel, Field

from ...agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 15.0
_UA = "Hearth/0.1 (local personal AI; contact: open-source project)"

# WMO weather interpretation codes → human-readable string
_WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


class WeatherParams(BaseModel):
    location: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "City name (e.g. 'Tokyo', 'New York', 'Paris, France') or "
            "lat,lon coordinates (e.g. '35.68,139.69')"
        ),
    )
    units: str = Field(
        default="celsius",
        description="Temperature unit: 'celsius' or 'fahrenheit'",
    )


async def _geocode(location: str, client: httpx.AsyncClient) -> tuple[float, float, str]:
    """Return (lat, lon, display_name). Raises ValueError on failure."""
    # Try lat,lon shortcut first
    parts = location.split(",")
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1]), location
        except ValueError:
            pass

    resp = await client.get(
        _GEOCODE_URL,
        params={"name": location, "count": 1, "language": "en", "format": "json"},
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    if not results:
        raise ValueError(f"Could not find location: {location!r}")
    r = results[0]
    name = r.get("name", location)
    country = r.get("country", "")
    display = f"{name}, {country}".strip(", ")
    return float(r["latitude"]), float(r["longitude"]), display


def register_weather_tools(registry: ToolRegistry) -> None:

    async def weather_current(p: WeatherParams) -> ToolResult:
        temp_unit = "fahrenheit" if p.units.lower().startswith("f") else "celsius"
        wind_unit = "mph" if temp_unit == "fahrenheit" else "kmh"

        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            headers={"User-Agent": _UA},
            follow_redirects=True,
        ) as client:
            try:
                lat, lon, display_name = await _geocode(p.location, client)
            except (ValueError, httpx.HTTPError) as exc:
                return ToolResult(ok=False, error=f"Geocoding failed: {exc}")

            params = {
                "latitude": lat,
                "longitude": lon,
                "current": [
                    "temperature_2m",
                    "apparent_temperature",
                    "relative_humidity_2m",
                    "weather_code",
                    "wind_speed_10m",
                    "wind_direction_10m",
                    "precipitation",
                    "cloud_cover",
                    "is_day",
                ],
                "daily": [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_sum",
                    "sunrise",
                    "sunset",
                ],
                "temperature_unit": temp_unit,
                "wind_speed_unit": wind_unit,
                "precipitation_unit": "mm",
                "timezone": "auto",
                "forecast_days": 1,
            }
            try:
                resp = await client.get(_FORECAST_URL, params=params)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                return ToolResult(ok=False, error=f"Weather fetch failed: {exc}")

            data = resp.json()

        cur = data.get("current", {})
        cur_units = data.get("current_units", {})
        daily = data.get("daily", {})
        condition = _describe_code(cur.get("weather_code"))

        # Format wind direction
        wind_dir_deg = cur.get("wind_direction_10m")
        wind_dir_str = _degrees_to_compass(wind_dir_deg) if wind_dir_deg is not None else "N/A"

        result: dict = {
            "location": display_name,
            "latitude": lat,
            "longitude": lon,
            "observed_at": cur.get("time", datetime.now().isoformat()),
            "timezone": data.get("timezone", "UTC"),
            "condition": condition,
            "is_day": bool(cur.get("is_day", 1)),
            "temperature": {
                "current": cur.get("temperature_2m"),
                "feels_like": cur.get("apparent_temperature"),
                "unit": cur_units.get("temperature_2m", f"°{temp_unit[0].upper()}"),
                "today_high": (daily.get("temperature_2m_max") or [None])[0],
                "today_low": (daily.get("temperature_2m_min") or [None])[0],
            },
            "humidity_percent": cur.get("relative_humidity_2m"),
            "precipitation_mm": cur.get("precipitation"),
            "cloud_cover_percent": cur.get("cloud_cover"),
            "wind": {
                "speed": cur.get("wind_speed_10m"),
                "unit": cur_units.get("wind_speed_10m", wind_unit),
                "direction": wind_dir_str,
            },
            "today": {
                "sunrise": (daily.get("sunrise") or [None])[0],
                "sunset": (daily.get("sunset") or [None])[0],
                "precipitation_sum_mm": (daily.get("precipitation_sum") or [None])[0],
                "condition": _describe_code((daily.get("weather_code") or [None])[0]),
            },
        }
        return ToolResult(ok=True, data=result)

    registry.register(
        ToolSpec(
            name="weather_current",
            description=(
                "Get the current weather conditions and today's forecast for any city or "
                "lat/lon coordinates. Returns temperature, feels-like, humidity, wind, "
                "precipitation, sunrise/sunset, and a plain-English condition description. "
                "Only works when the user enables Weather in the Permission Center."
            ),
            params_model=WeatherParams,
            risk=RiskLevel.READ,
            permission="weather",
            handler=weather_current,
            timeout_s=20,
        )
    )


def _describe_code(code) -> str:
    """WMO code → text; tolerates missing/None values from the API."""
    if code is None:
        return "Unknown"
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "Unknown"
    return _WMO_CODES.get(code, f"WMO code {code}")


def _degrees_to_compass(degrees: float) -> str:
    dirs = [
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    ]
    ix = round(degrees / (360 / len(dirs))) % len(dirs)
    return dirs[ix]
