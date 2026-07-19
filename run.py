#!/usr/bin/env python3
"""Universal launcher for Hearth — macOS, Windows, and Linux.

    python run.py            (Windows: py run.py)

What it does, in order:
1. If a suitable interpreter is running this script and the project virtualenv
   exists with Hearth installed, it just starts the app.
2. Otherwise it bootstraps: finds a Python 3.11/3.12, creates `.venv/`,
   installs the project into it, then launches the app from the venv.

No arguments, no activation step, safe to run repeatedly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
WINDOWS = sys.platform == "win32"
VENV_PYTHON = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")

SUPPORTED = ((3, 11), (3, 12), (3, 13))


def version_ok(major: int, minor: int) -> bool:
    return (major, minor) in SUPPORTED


def find_compatible_python() -> list[str] | None:
    """Return an argv prefix for a Python 3.11/3.12, or None."""
    if version_ok(*sys.version_info[:2]):
        return [sys.executable]

    candidates: list[list[str]] = []
    if WINDOWS and shutil.which("py"):
        candidates += [["py", "-3.12"], ["py", "-3.13"], ["py", "-3.11"]]
    for name in ("python3.12", "python3.13", "python3.11"):
        if path := shutil.which(name):
            candidates.append([path])

    for argv in candidates:
        try:
            probe = subprocess.run(
                [*argv, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if probe.returncode == 0:
                major, minor = map(int, probe.stdout.split())
                if version_ok(major, minor):
                    return argv
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
    return None


def venv_ready() -> bool:
    if not VENV_PYTHON.exists():
        return False
    check = subprocess.run([str(VENV_PYTHON), "-c", "import hearth"], capture_output=True)
    return check.returncode == 0


def bootstrap() -> None:
    base = find_compatible_python()
    if base is None:
        sys.exit(
            "Hearth needs Python 3.11–3.13 and none was found.\n"
            "Install it from https://www.python.org/downloads/ "
            "(macOS: 'brew install python@3.11'), then run this again."
        )

    if not VENV_PYTHON.exists():
        print("Creating virtual environment (.venv)…", flush=True)
        subprocess.run([*base, "-m", "venv", str(VENV)], check=True)

    print("Installing Hearth and its dependencies (first run only, a few minutes)…", flush=True)
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        check=True,
    )
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--quiet", "-e", str(ROOT)],
        check=True,
    )
    # Voice input is optional (audio packages need PortAudio); best effort only.
    voice = subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--quiet", "-e", f"{ROOT}[voice]"],
    )
    if voice.returncode != 0:
        print(
            "Note: voice-input packages could not be installed. Hearth works without "
            "them; the mic button will explain how to add voice later.",
            flush=True,
        )
    print("Setup complete.", flush=True)


def launch() -> None:
    argv = [str(VENV_PYTHON), "-m", "hearth"]
    if WINDOWS:
        sys.exit(subprocess.run(argv, cwd=str(ROOT)).returncode)
    os.chdir(ROOT)
    os.execv(argv[0], argv)  # replace this process; signals/exit codes stay clean


def main() -> None:
    if not venv_ready():
        bootstrap()
        if not venv_ready():
            sys.exit(
                "Setup finished but the app still failed to import. "
                "Try deleting the .venv folder and running run.py again."
            )
    launch()


if __name__ == "__main__":
    main()
