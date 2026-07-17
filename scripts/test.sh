#!/usr/bin/env bash
# Run the test suite. Windows: .venv\Scripts\python -m pytest
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m pytest "$@"
