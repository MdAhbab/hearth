#!/usr/bin/env bash
# Build a distributable app for THIS platform (PyInstaller can't cross-compile).
#   macOS  -> dist/Hearth.app
#   Linux  -> dist/Hearth/Hearth
# Windows (PowerShell): .venv\Scripts\pyinstaller packaging\hearth.spec --noconfirm
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pyinstaller packaging/hearth.spec --noconfirm
echo "Build complete: see dist/"
