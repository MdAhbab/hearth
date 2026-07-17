#!/usr/bin/env bash
# Run Hearth from source (macOS/Linux). Windows: .venv\Scripts\python -m hearth
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m hearth
