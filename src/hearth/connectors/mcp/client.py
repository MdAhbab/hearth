"""Minimal MCP (Model Context Protocol) client over stdio.

Speaks newline-delimited JSON-RPC 2.0 to a server subprocess: initialize,
tools/list, tools/call — the subset needed to plug external tools into
Hearth's registry. Implemented directly (~150 lines) rather than pulling in
an SDK; the protocol surface used here is tiny and stable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    pass


class MCPServerConnection:
    """One running MCP server subprocess and its request/response loop."""

    def __init__(self, name: str, command: str, args: list[str], request_timeout_s: float = 30.0):
        self.name = name
        self._command = command
        self._args = args
        self._timeout = request_timeout_s
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._reader_task: asyncio.Task | None = None
        self.tools: list[dict[str, Any]] = []

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Spawn the server, run the MCP handshake, and fetch its tool list."""
        log.info("Starting MCP server '%s': %s %s", self.name, self._command, self._args)
        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._reader_task = asyncio.ensure_future(self._read_loop())

        await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "Hearth", "version": "0.3"},
            },
        )
        await self._notify("notifications/initialized", {})
        listing = await self._request("tools/list", {})
        self.tools = listing.get("tools", [])
        log.info("MCP server '%s' exposes %d tools", self.name, len(self.tools))

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request("tools/call", {"name": tool_name, "arguments": arguments})

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        self.kill()

    def kill(self) -> None:
        """Synchronous last-resort teardown (safe to call from Qt shutdown)."""
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
        self._process = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(MCPError(f"MCP server '{self.name}' stopped"))
        self._pending.clear()

    # -- JSON-RPC plumbing ----------------------------------------------------

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.running:
            raise MCPError(f"MCP server '{self.name}' is not running")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            raise MCPError(f"MCP server '{self.name}' timed out on {method}") from None
        finally:
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPError(f"MCP server '{self.name}' is not running")
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break  # server exited
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("MCP '%s': ignoring non-JSON line", self.name)
                    continue
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(MCPError(f"MCP server '{self.name}' disconnected"))

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "method" in message:
            # Server-initiated request or notification — nothing we support;
            # never confuse its id with one of our pending response ids.
            return
        request_id = message.get("id")
        future = self._pending.get(request_id) if request_id is not None else None
        if future is None or future.done():
            return  # notification or stale response — nothing waits on it
        if "error" in message:
            error = message["error"]
            future.set_exception(
                MCPError(f"{error.get('message', 'server error')} (code {error.get('code')})")
            )
        else:
            future.set_result(message.get("result") or {})
