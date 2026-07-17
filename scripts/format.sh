#!/usr/bin/env bash
# Format and lint. Windows: .venv\Scripts\python -m ruff ...
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m ruff format src tests
.venv/bin/python -m ruff check --fix src tests
