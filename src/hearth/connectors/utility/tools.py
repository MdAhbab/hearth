"""Utility tools.

- time_now / calculate: pure, local, no permission needed ("core" is always
  granted). Small local models are bad at dates and arithmetic; these keep
  answers honest.
- web_fetch: reads a page as plain text. Opt-in ("web" permission, default
  off) because it sends a request off the machine. Responses are size-capped
  and framed as data by the agent loop like every other tool result.
"""

from __future__ import annotations

import ast
import operator
import re
from datetime import datetime

import httpx
from pydantic import BaseModel, Field, HttpUrl

from ...agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec
from ...config import WebConfig


class TimeNowParams(BaseModel):
    pass


class CalculateParams(BaseModel):
    expression: str = Field(
        min_length=1,
        max_length=200,
        description="Arithmetic expression, e.g. '(1520*0.15)+42' or '2**10'",
    )


class WebFetchParams(BaseModel):
    url: HttpUrl = Field(description="http(s) URL of the page to read")


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def safe_eval(expression: str) -> float:
    """Evaluate arithmetic only — numbers and + - * / // % ** parentheses."""

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            if isinstance(node.op, ast.Pow):
                left, right = walk(node.left), walk(node.right)
                if abs(right) > 64:
                    raise ValueError("Exponent too large")
                return _ALLOWED_BINOPS[type(node.op)](left, right)
            return _ALLOWED_BINOPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](walk(node.operand))
        raise ValueError(f"Unsupported syntax: {ast.dump(node)[:60]}")

    return walk(ast.parse(expression, mode="eval"))


_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t\n]*")


def html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _HTML_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return _WS_RE.sub("\n", text).strip()


def register_utility_tools(registry: ToolRegistry, web_config: WebConfig) -> None:
    async def time_now(_: TimeNowParams) -> ToolResult:
        now = datetime.now().astimezone()
        return ToolResult(
            ok=True,
            data={
                "local": now.strftime("%A, %Y-%m-%d %H:%M:%S"),
                "timezone": str(now.tzinfo),
                "iso": now.isoformat(),
            },
        )

    async def calculate(p: CalculateParams) -> ToolResult:
        try:
            value = safe_eval(p.expression)
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
            return ToolResult(ok=False, error=f"Cannot evaluate: {exc}")
        return ToolResult(ok=True, data={"expression": p.expression, "result": value})

    async def web_fetch(p: WebFetchParams) -> ToolResult:
        try:
            async with httpx.AsyncClient(
                timeout=web_config.fetch_timeout_s,
                follow_redirects=True,
                headers={"User-Agent": "Hearth/0.1 (personal local assistant)"},
            ) as client:
                resp = await client.get(str(p.url))
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=f"Fetch failed: {exc}")
        body = resp.text[: web_config.max_fetch_bytes]
        content_type = resp.headers.get("content-type", "")
        text = html_to_text(body) if "html" in content_type else body
        return ToolResult(
            ok=True,
            data={
                "url": str(resp.url),
                "content_type": content_type,
                "text": text[:12_000],
                "truncated": len(text) > 12_000,
            },
        )

    registry.register(
        ToolSpec(
            name="time_now",
            description="Get the current local date, time, and timezone. Use before any date math.",
            params_model=TimeNowParams,
            risk=RiskLevel.READ,
            permission="core",
            handler=time_now,
            timeout_s=5,
        )
    )
    registry.register(
        ToolSpec(
            name="calculate",
            description=(
                "Evaluate an arithmetic expression exactly (+ - * / // % ** and parentheses)."
            ),
            params_model=CalculateParams,
            risk=RiskLevel.READ,
            permission="core",
            handler=calculate,
            timeout_s=5,
        )
    )
    registry.register(
        ToolSpec(
            name="web_fetch",
            description=(
                "Fetch a web page and return its readable text. Only works if the user "
                "enabled Web access in the Permission Center."
            ),
            params_model=WebFetchParams,
            risk=RiskLevel.READ,
            permission="web",
            handler=web_fetch,
            timeout_s=30,
        )
    )

    # ---- Capability 5: unit converter (always available, no permission needed) ----

    class ConvertUnitsParams(BaseModel):
        value: float = Field(description="The numeric value to convert")
        from_unit: str = Field(min_length=1, max_length=30, description="Source unit (e.g. 'km', 'kg', 'fahrenheit', 'mph')")
        to_unit: str = Field(min_length=1, max_length=30, description="Target unit (e.g. 'miles', 'lb', 'celsius', 'kph')")

    # Conversion table: all values are the factor to multiply to get to the canonical base unit.
    # Base units: metre (length), kilogram (mass), kelvin (temp), m/s (speed),
    #             m² (area), litre (volume), byte (data), second (time).
    _UNITS: dict[str, dict[str, float]] = {
        "length": {
            "m": 1.0, "metre": 1.0, "meter": 1.0, "metres": 1.0, "meters": 1.0,
            "km": 1000.0, "kilometre": 1000.0, "kilometer": 1000.0, "kilometres": 1000.0, "kilometers": 1000.0,
            "cm": 0.01, "centimetre": 0.01, "centimeter": 0.01,
            "mm": 0.001, "millimetre": 0.001, "millimeter": 0.001,
            "mile": 1609.344, "miles": 1609.344, "mi": 1609.344,
            "yard": 0.9144, "yards": 0.9144, "yd": 0.9144,
            "foot": 0.3048, "feet": 0.3048, "ft": 0.3048,
            "inch": 0.0254, "inches": 0.0254, "in": 0.0254,
            "nautical_mile": 1852.0, "nm": 1852.0,
            "light_year": 9.461e15,
        },
        "mass": {
            "kg": 1.0, "kilogram": 1.0, "kilograms": 1.0, "kilo": 1.0,
            "g": 0.001, "gram": 0.001, "grams": 0.001,
            "mg": 1e-6, "milligram": 1e-6, "milligrams": 1e-6,
            "tonne": 1000.0, "metric_ton": 1000.0,
            "lb": 0.453592, "pound": 0.453592, "pounds": 0.453592, "lbs": 0.453592,
            "oz": 0.0283495, "ounce": 0.0283495, "ounces": 0.0283495,
            "stone": 6.35029, "stones": 6.35029, "st": 6.35029,
            "short_ton": 907.185,
            "long_ton": 1016.047,
        },
        "speed": {
            "m/s": 1.0, "mps": 1.0,
            "km/h": 1 / 3.6, "kph": 1 / 3.6, "kmh": 1 / 3.6,
            "mph": 0.44704, "mi/h": 0.44704,
            "knot": 0.514444, "knots": 0.514444, "kt": 0.514444,
            "ft/s": 0.3048, "fps": 0.3048,
        },
        "area": {
            "m2": 1.0, "m²": 1.0, "sq_m": 1.0, "square_metre": 1.0, "square_meter": 1.0,
            "km2": 1e6, "km²": 1e6, "square_km": 1e6, "square_kilometre": 1e6,
            "cm2": 1e-4, "cm²": 1e-4,
            "hectare": 1e4, "ha": 1e4,
            "acre": 4046.86, "acres": 4046.86,
            "ft2": 0.092903, "ft²": 0.092903, "sq_ft": 0.092903, "square_foot": 0.092903, "square_feet": 0.092903,
            "mi2": 2.59e6, "mi²": 2.59e6, "sq_mile": 2.59e6, "square_mile": 2.59e6,
        },
        "volume": {
            "l": 1.0, "litre": 1.0, "liter": 1.0, "litres": 1.0, "liters": 1.0,
            "ml": 0.001, "millilitre": 0.001, "milliliter": 0.001,
            "m3": 1000.0, "m³": 1000.0, "cubic_metre": 1000.0, "cubic_meter": 1000.0,
            "gallon": 3.78541, "gallons": 3.78541, "gal": 3.78541,
            "us_gallon": 3.78541, "us_gallons": 3.78541,
            "uk_gallon": 4.54609, "imperial_gallon": 4.54609,
            "quart": 0.946353, "quarts": 0.946353, "qt": 0.946353,
            "pint": 0.473176, "pints": 0.473176, "pt": 0.473176,
            "cup": 0.236588, "cups": 0.236588,
            "fl_oz": 0.0295735, "fluid_ounce": 0.0295735,
            "tbsp": 0.0147868, "tablespoon": 0.0147868,
            "tsp": 0.00492892, "teaspoon": 0.00492892,
        },
        "data": {
            "byte": 1.0, "b": 1.0, "bytes": 1.0,
            "kb": 1024.0, "kilobyte": 1024.0, "kilobytes": 1024.0,
            "mb": 1024.0 ** 2, "megabyte": 1024.0 ** 2, "megabytes": 1024.0 ** 2,
            "gb": 1024.0 ** 3, "gigabyte": 1024.0 ** 3, "gigabytes": 1024.0 ** 3,
            "tb": 1024.0 ** 4, "terabyte": 1024.0 ** 4, "terabytes": 1024.0 ** 4,
            "pb": 1024.0 ** 5, "petabyte": 1024.0 ** 5, "petabytes": 1024.0 ** 5,
            "bit": 0.125, "bits": 0.125,
            "kbit": 128.0, "kilobit": 128.0,
            "mbit": 131072.0, "megabit": 131072.0,
            "gbit": 134217728.0, "gigabit": 134217728.0,
        },
        "time": {
            "second": 1.0, "seconds": 1.0, "sec": 1.0, "s": 1.0,
            "minute": 60.0, "minutes": 60.0, "min": 60.0,
            "hour": 3600.0, "hours": 3600.0, "hr": 3600.0, "h": 3600.0,
            "day": 86400.0, "days": 86400.0, "d": 86400.0,
            "week": 604800.0, "weeks": 604800.0, "wk": 604800.0,
            "month": 2628000.0, "months": 2628000.0,  # ~30.4 days
            "year": 31536000.0, "years": 31536000.0, "yr": 31536000.0,
        },
    }

    # Temperature is special (offset conversions) — handled separately
    _TEMP_CONVERSIONS: dict[tuple[str, str], object] = {}

    def _normalise_unit(unit: str) -> str:
        return unit.strip().lower().replace("-", "_").replace(" ", "_")

    def _find_unit(unit: str) -> tuple[str, str] | None:
        """Return (category, canonical_key) or None."""
        key = _normalise_unit(unit)
        for category, table in _UNITS.items():
            if key in table:
                return category, key
        return None

    _TEMP_KEYS = {
        "celsius", "c", "°c", "centigrade",
        "fahrenheit", "f", "°f",
        "kelvin", "k", "°k",
        "rankine", "ra", "°r",
    }

    def _to_kelvin(value: float, unit: str) -> float:
        u = _normalise_unit(unit)
        if u in {"celsius", "c", "°c", "centigrade"}:
            return value + 273.15
        if u in {"fahrenheit", "f", "°f"}:
            return (value + 459.67) * 5 / 9
        if u in {"kelvin", "k", "°k"}:
            return value
        if u in {"rankine", "ra", "°r"}:
            return value * 5 / 9
        raise ValueError(f"Unknown temperature unit: {unit!r}")

    def _from_kelvin(kelvin: float, unit: str) -> float:
        u = _normalise_unit(unit)
        if u in {"celsius", "c", "°c", "centigrade"}:
            return kelvin - 273.15
        if u in {"fahrenheit", "f", "°f"}:
            return kelvin * 9 / 5 - 459.67
        if u in {"kelvin", "k", "°k"}:
            return kelvin
        if u in {"rankine", "ra", "°r"}:
            return kelvin * 9 / 5
        raise ValueError(f"Unknown temperature unit: {unit!r}")

    async def convert_units(p: ConvertUnitsParams) -> ToolResult:
        from_norm = _normalise_unit(p.from_unit)
        to_norm = _normalise_unit(p.to_unit)

        # Temperature path
        if from_norm in _TEMP_KEYS or to_norm in _TEMP_KEYS:
            try:
                kelvin = _to_kelvin(p.value, p.from_unit)
                result_value = _from_kelvin(kelvin, p.to_unit)
            except ValueError as exc:
                return ToolResult(ok=False, error=str(exc))
            return ToolResult(
                ok=True,
                data={
                    "input": f"{p.value} {p.from_unit}",
                    "result": round(result_value, 6),
                    "result_unit": p.to_unit,
                    "category": "temperature",
                },
            )

        from_hit = _find_unit(p.from_unit)
        to_hit = _find_unit(p.to_unit)

        if from_hit is None:
            return ToolResult(ok=False, error=f"Unknown unit: {p.from_unit!r}")
        if to_hit is None:
            return ToolResult(ok=False, error=f"Unknown unit: {p.to_unit!r}")
        if from_hit[0] != to_hit[0]:
            return ToolResult(
                ok=False,
                error=(
                    f"Cannot convert between {from_hit[0]} ({p.from_unit}) "
                    f"and {to_hit[0]} ({p.to_unit}) — they measure different things."
                ),
            )
        category = from_hit[0]
        table = _UNITS[category]
        base_value = p.value * table[from_hit[1]]
        result_value = base_value / table[to_hit[1]]
        return ToolResult(
            ok=True,
            data={
                "input": f"{p.value} {p.from_unit}",
                "result": round(result_value, 9),
                "result_unit": p.to_unit,
                "category": category,
            },
        )

    registry.register(
        ToolSpec(
            name="convert_units",
            description=(
                "Convert a value between units of the same type. Supports: "
                "length (m, km, miles, feet, inches…), mass (kg, lb, g, oz…), "
                "temperature (celsius, fahrenheit, kelvin), speed (mph, kph, m/s, knots), "
                "area (m², acres, hectares…), volume (litres, gallons, cups…), "
                "data storage (bytes, KB, MB, GB, TB…), and time (seconds, minutes, hours, days…). "
                "Always available — no permission required."
            ),
            params_model=ConvertUnitsParams,
            risk=RiskLevel.READ,
            permission="core",
            handler=convert_units,
            timeout_s=5,
        )
    )

