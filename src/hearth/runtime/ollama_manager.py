"""Lifecycle management for the local Ollama daemon.

Responsibilities:
- Detect whether the daemon is already running on the configured port.
- Start ``ollama serve`` only when needed, and remember that *we* started it
  so we can shut it down on app exit (never kill a daemon the user started).
- Warm the model lazily (first request pays the load cost, not app startup).
- Serialize generations: one concurrent request, which matters on 8 GB.
- Never pull a model implicitly; report a missing model as a typed state.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
from enum import StrEnum
from pathlib import Path

import httpx

from ..config import OllamaConfig

log = logging.getLogger(__name__)


class RuntimeState(StrEnum):
    UNKNOWN = "unknown"
    STARTING = "starting"
    READY = "ready"
    MODEL_MISSING = "model_missing"
    UNAVAILABLE = "unavailable"


def _default_ollama_binary() -> str:
    """Locate the ollama binary across platforms (PATH first, then well-known spots)."""
    if found := shutil.which("ollama"):
        return found
    candidates = {
        "darwin": [Path("/usr/local/bin/ollama"), Path("/opt/homebrew/bin/ollama")],
        "win32": [
            Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
            Path("C:/Program Files/Ollama/ollama.exe"),
        ],
        "linux": [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")],
    }.get(sys.platform, [])
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "ollama"  # last resort; Popen will raise a clear OSError


MODEL_CACHE_TTL_S = 30.0  # skip the per-message /api/tags round-trip


class OllamaRuntimeManager:
    def __init__(self, config: OllamaConfig, ollama_binary: str | None = None):
        self._config = config
        self._binary = ollama_binary or _default_ollama_binary()
        self._process: subprocess.Popen | None = None
        self.started_by_app = False
        self.state = RuntimeState.UNKNOWN
        # One generation at a time: the model barely fits in 8 GB unified memory.
        self.generation_lock = asyncio.Semaphore(1)
        self._models_cache: list[dict] | None = None
        self._models_cached_at = 0.0

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def update_config(self, config: OllamaConfig) -> None:
        """Apply new settings without losing daemon ownership state."""
        self._config = config
        self._models_cache = None

    async def is_daemon_up(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self.base_url}/api/version")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_running(self) -> RuntimeState:
        """Make sure the daemon is up; start it if configured to do so."""
        if await self.is_daemon_up():
            self.state = RuntimeState.READY
            return self.state

        if not self._config.autostart:
            self.state = RuntimeState.UNAVAILABLE
            return self.state

        self.state = RuntimeState.STARTING
        log.info("Ollama daemon not running; starting %s serve", self._binary)
        popen_kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            popen_kwargs["start_new_session"] = True
        try:
            self._process = subprocess.Popen([self._binary, "serve"], **popen_kwargs)
        except OSError as exc:
            log.error("Could not start ollama serve: %s", exc)
            self.state = RuntimeState.UNAVAILABLE
            return self.state

        deadline = asyncio.get_event_loop().time() + self._config.startup_timeout_s
        while asyncio.get_event_loop().time() < deadline:
            if await self.is_daemon_up():
                self.started_by_app = True
                self.state = RuntimeState.READY
                log.info("Ollama daemon started by app")
                return self.state
            await asyncio.sleep(0.5)

        log.error("Ollama did not become ready within %.0fs", self._config.startup_timeout_s)
        self.state = RuntimeState.UNAVAILABLE
        return self.state

    async def list_models(self, fresh: bool = False) -> list[dict]:
        """Installed models as [{"name": ..., "size_gb": ...}], newest first.

        Cached briefly so the per-message availability check doesn't hit the
        daemon every time; ``fresh=True`` (the Settings refresh) bypasses it.
        """
        now = asyncio.get_event_loop().time()
        if (
            not fresh
            and self._models_cache is not None
            and now - self._models_cached_at < MODEL_CACHE_TTL_S
        ):
            return self._models_cache
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                models = [
                    {
                        "name": m.get("name", ""),
                        "size_gb": round(m.get("size", 0) / 1024**3, 1),
                    }
                    for m in resp.json().get("models", [])
                    if m.get("name")
                ]
        except httpx.HTTPError:
            return self._models_cache or []
        self._models_cache = models
        self._models_cached_at = now
        return models

    async def model_available(self, model_name: str) -> bool:
        """Check the model exists locally. Never pulls."""
        models = [m["name"] for m in await self.list_models()]
        # Ollama lists names with an explicit tag (":latest" when untagged).
        return any(m == model_name or m.split(":")[0] == model_name for m in models)

    async def warm_model(self, model_name: str, keep_alive: str) -> None:
        """Load the model into memory without generating anything."""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                await client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model_name, "keep_alive": keep_alive},
                )
        except httpx.HTTPError as exc:
            log.warning("Model warm-up failed: %s", exc)

    async def unload_model(self, model_name: str) -> None:
        """Ask Ollama to release the model immediately (keep_alive=0)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model_name, "keep_alive": 0},
                )
        except httpx.HTTPError:
            pass

    def shutdown(self) -> None:
        """Terminate the daemon only if this app started it."""
        if self.started_by_app and self._process and self._process.poll() is None:
            log.info("Stopping Ollama daemon that this app started")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self.started_by_app = False
