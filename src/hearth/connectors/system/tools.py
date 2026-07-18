"""Narrow system tools.

Cross-platform: open URL (confirmed — the exact URL is shown, which also
blocks data exfiltration via crafted links), reveal in file manager, show a
notification, read/set clipboard.
macOS-only extras: open app, run *user-approved* Shortcuts by exact name,
read the active Chrome tab (Automation permission).

There is deliberately NO arbitrary shell, arbitrary AppleScript, or
screen-control tool. Every subprocess uses a fixed argv list — no shell
string interpolation anywhere.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field, HttpUrl

from ...agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec

# UI injects these (Qt clipboard + tray notifications are cross-platform).
ClipboardGet = Callable[[], str]
ClipboardSet = Callable[[str], None]
Notifier = Callable[[str, str], None]


class OpenUrlParams(BaseModel):
    url: HttpUrl = Field(description="http(s) URL to open in the default browser")


class RevealParams(BaseModel):
    path: str = Field(min_length=1, description="File or folder to show in the file manager")


class NotifyParams(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)


class ClipboardReadParams(BaseModel):
    pass


class ClipboardWriteParams(BaseModel):
    text: str = Field(max_length=100_000, description="Text to place on the clipboard")


class OpenAppParams(BaseModel):
    app: str = Field(
        min_length=1,
        description="macOS bundle id (com.apple.Notes) or exact app name (Notes)",
    )


class RunShortcutParams(BaseModel):
    name: str = Field(min_length=1, description="Exact name of an approved macOS Shortcut")
    input_text: str = Field(default="", description="Optional text input for the Shortcut")


class ChromeTabParams(BaseModel):
    pass


class ScreenshotParams(BaseModel):
    pass


# App injects this: captures the primary screen, returns base64 JPEG.
ScreenCapture = Callable[[], str]


async def _run(argv: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return await asyncio.to_thread(
        subprocess.run, argv, capture_output=True, text=True, timeout=timeout
    )


def register_system_tools(
    registry: ToolRegistry,
    clipboard_get: ClipboardGet,
    clipboard_set: ClipboardSet,
    notifier: Notifier,
    approved_shortcuts: Callable[[], list[tuple[str, bool]]],
    screen_capture: ScreenCapture | None = None,
) -> None:
    async def open_url(p: OpenUrlParams) -> ToolResult:
        ok = await asyncio.to_thread(webbrowser.open, str(p.url))
        return ToolResult(
            ok=bool(ok), data=f"Opened {p.url}", error="" if ok else "No browser available"
        )

    async def reveal(p: RevealParams) -> ToolResult:
        target = Path(p.path).expanduser()
        if not target.exists():
            return ToolResult(ok=False, error=f"Path does not exist: {p.path}")
        if sys.platform == "darwin":
            await _run(["open", "-R", str(target)])
        elif sys.platform == "win32":
            await _run(["explorer", "/select,", str(target)])
        else:
            await _run(["xdg-open", str(target.parent)])
        return ToolResult(ok=True, data=f"Revealed {target}")

    async def notify(p: NotifyParams) -> ToolResult:
        await asyncio.to_thread(notifier, p.title, p.message)
        return ToolResult(ok=True, data="Notification shown")

    async def clipboard_read(_: ClipboardReadParams) -> ToolResult:
        text = await asyncio.to_thread(clipboard_get)
        return ToolResult(ok=True, data={"clipboard": text[:20_000]})

    async def clipboard_write(p: ClipboardWriteParams) -> ToolResult:
        await asyncio.to_thread(clipboard_set, p.text)
        return ToolResult(ok=True, data=f"Copied {len(p.text)} characters to the clipboard")

    registry.register(
        ToolSpec(
            name="system_open_url",
            description="Open a URL in the user's default browser (user approves the exact URL).",
            params_model=OpenUrlParams,
            risk=RiskLevel.WRITE,
            permission="system",
            handler=open_url,
            preview=lambda p: f"Open in browser:\n{p.url}",
        )
    )
    registry.register(
        ToolSpec(
            name="system_reveal_file",
            description="Show a file or folder in Finder / Explorer / the file manager.",
            params_model=RevealParams,
            risk=RiskLevel.READ,
            permission="system",
            handler=reveal,
        )
    )
    registry.register(
        ToolSpec(
            name="system_notify",
            description="Show a desktop notification to the user.",
            params_model=NotifyParams,
            risk=RiskLevel.READ,
            permission="system",
            handler=notify,
        )
    )
    registry.register(
        ToolSpec(
            name="clipboard_read",
            description="Read the current text on the system clipboard.",
            params_model=ClipboardReadParams,
            risk=RiskLevel.READ,
            permission="system",
            handler=clipboard_read,
        )
    )
    registry.register(
        ToolSpec(
            name="clipboard_write",
            description="Put text on the system clipboard (replaces what's there).",
            params_model=ClipboardWriteParams,
            risk=RiskLevel.WRITE,
            permission="system",
            handler=clipboard_write,
            preview=lambda p: f"Replace clipboard contents with:\n{p.text[:800]}",
        )
    )

    if screen_capture is not None:

        async def take_screenshot(_: ScreenshotParams) -> ToolResult:
            try:
                encoded = await asyncio.to_thread(screen_capture)
            except Exception as exc:  # noqa: BLE001 — capture backends vary widely
                return ToolResult(
                    ok=False,
                    error=(
                        f"Screenshot failed: {exc}. On macOS, allow Screen Recording "
                        "for Hearth in System Settings > Privacy & Security."
                    ),
                )
            return ToolResult(
                ok=True,
                data="Screenshot captured. Describe or analyze what is on screen.",
                image_b64=encoded,
            )

        registry.register(
            ToolSpec(
                name="system_screenshot",
                description=(
                    "Capture a screenshot of the primary screen and analyze what it "
                    "shows. Requires approval every time — the screen may contain "
                    "sensitive content."
                ),
                params_model=ScreenshotParams,
                risk=RiskLevel.WRITE,  # screen contents are sensitive: always confirm
                permission="system",
                handler=take_screenshot,
                timeout_s=20,
                preview=lambda p: (
                    "Capture the entire primary screen and show it to the model.\n"
                    "Anything currently visible (messages, documents) will be included."
                ),
            )
        )

    if sys.platform == "darwin":
        _register_macos_tools(registry, approved_shortcuts)


def _register_macos_tools(
    registry: ToolRegistry,
    approved_shortcuts: Callable[[], list[tuple[str, bool]]],
) -> None:
    async def open_app(p: OpenAppParams) -> ToolResult:
        flag = "-b" if "." in p.app else "-a"
        proc = await _run(["open", flag, p.app])
        if proc.returncode != 0:
            return ToolResult(ok=False, error=f"Could not open {p.app}: {proc.stderr.strip()}")
        return ToolResult(ok=True, data=f"Opened {p.app}")

    async def run_shortcut(p: RunShortcutParams) -> ToolResult:
        approved = dict(approved_shortcuts())
        if p.name not in approved:
            return ToolResult(
                ok=False,
                error=(
                    f"Shortcut '{p.name}' is not on the approved list. The user can add "
                    "it in the Permission Center."
                ),
            )
        argv = ["shortcuts", "run", p.name]
        if p.input_text:
            argv += ["-i", "-"]
        proc = await asyncio.to_thread(
            subprocess.run,
            argv,
            capture_output=True,
            text=True,
            input=p.input_text or None,
            timeout=60,
        )
        if proc.returncode != 0:
            return ToolResult(ok=False, error=f"Shortcut failed: {proc.stderr.strip()}")
        return ToolResult(ok=True, data=proc.stdout.strip() or "Shortcut completed")

    async def chrome_tab(_: ChromeTabParams) -> ToolResult:
        # Fixed script, no interpolation. Requires macOS Automation permission.
        script = (
            'tell application "Google Chrome" to if (count of windows) > 0 then '
            "get {URL, title} of active tab of front window"
        )
        proc = await _run(["osascript", "-e", script])
        if proc.returncode != 0:
            return ToolResult(
                ok=False,
                error=(
                    "Could not read the Chrome tab. Is Chrome running, and is "
                    "Automation permission granted for Hearth?"
                ),
            )
        return ToolResult(ok=True, data={"active_tab": proc.stdout.strip()})

    registry.register(
        ToolSpec(
            name="system_open_app",
            description="Open a macOS application by bundle id or exact name.",
            params_model=OpenAppParams,
            risk=RiskLevel.READ,
            permission="system",
            handler=open_app,
        )
    )
    registry.register(
        ToolSpec(
            name="system_run_shortcut",
            description=(
                "Run a macOS Shortcut from the user's approved list by exact name. "
                "Shortcuts that change data require approval."
            ),
            params_model=RunShortcutParams,
            risk=RiskLevel.WRITE,
            permission="shortcuts",
            handler=run_shortcut,
            timeout_s=90,
            preview=lambda p: (
                f"Run macOS Shortcut: {p.name}\n"
                + (f"Input: {p.input_text[:300]}" if p.input_text else "No input")
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="chrome_active_tab",
            description="Read the URL and title of the active Google Chrome tab.",
            params_model=ChromeTabParams,
            risk=RiskLevel.READ,
            permission="automation",
            handler=chrome_tab,
        )
    )


# ---- Capability 2: system info tools (always registered, cross-platform) ----


def register_sysinfo_tools(registry: ToolRegistry) -> None:
    """Register disk-usage and process-list tools. Uses psutil — cross-platform.

    psutil is only imported when a tool actually runs, keeping it out of the
    startup footprint; availability is checked without importing.
    """
    import importlib.util

    if importlib.util.find_spec("psutil") is None:
        return  # psutil not installed — skip gracefully

    class DiskUsageParams(BaseModel):
        path: str = Field(
            default="/",
            description="Filesystem path to check (e.g. '/' on macOS/Linux, 'C:\\\\' on Windows)",
        )

    class ProcessListParams(BaseModel):
        top_n: int = Field(
            default=15,
            ge=1,
            le=50,
            description="How many processes to return, sorted by CPU usage descending",
        )

    async def disk_usage(p: DiskUsageParams) -> ToolResult:
        def _du() -> ToolResult:
            import psutil

            try:
                usage = psutil.disk_usage(p.path)
            except (FileNotFoundError, PermissionError) as exc:
                return ToolResult(ok=False, error=f"Cannot read disk usage for '{p.path}': {exc}")
            gb = 1024**3
            return ToolResult(
                ok=True,
                data={
                    "path": p.path,
                    "total_gb": round(usage.total / gb, 2),
                    "used_gb": round(usage.used / gb, 2),
                    "free_gb": round(usage.free / gb, 2),
                    "percent_used": usage.percent,
                },
            )

        return await asyncio.to_thread(_du)

    async def process_list(p: ProcessListParams) -> ToolResult:
        def _ps() -> ToolResult:
            import time

            import psutil

            # cpu_percent measures since the previous call, so the first
            # sample is always 0.0 — prime, wait a beat, then read.
            handles = []
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    proc.cpu_percent(None)
                    handles.append(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            time.sleep(0.25)

            procs = []
            for proc in handles:
                try:
                    procs.append(
                        {
                            "pid": proc.pid,
                            "name": proc.info.get("name") or "",
                            "cpu_percent": round(proc.cpu_percent(None), 1),
                            "memory_percent": round(proc.memory_percent(), 1),
                        }
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            procs.sort(key=lambda x: x["cpu_percent"], reverse=True)
            return ToolResult(
                ok=True,
                data={"processes": procs[: p.top_n], "total_visible": len(procs)},
            )

        return await asyncio.to_thread(_ps)

    registry.register(
        ToolSpec(
            name="system_disk_usage",
            description=(
                "Report free, used, and total disk space for a filesystem path. "
                "Defaults to the root/main drive. Results in GB and percent used."
            ),
            params_model=DiskUsageParams,
            risk=RiskLevel.READ,
            permission="system",
            handler=disk_usage,
            timeout_s=10,
        )
    )
    registry.register(
        ToolSpec(
            name="system_running_processes",
            description=(
                "List the top running processes sorted by CPU usage, with their name, "
                "PID, CPU%, and memory%. This is read-only — no process can be killed."
            ),
            params_model=ProcessListParams,
            risk=RiskLevel.READ,
            permission="system",
            handler=process_list,
            timeout_s=10,
        )
    )
