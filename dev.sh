#!/usr/bin/env bash
# Start the local services at once: API and frontend (the model server runs separately).
#   ./dev.sh [--debug [--wait]] [--no-frontend]   (see dev.py)
set -euo pipefail
cd "$(dirname "$0")"
exec uv run python dev.py "$@"
