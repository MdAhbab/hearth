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
