#!/usr/bin/env bash
# Start the API only (see dev.sh for API + frontend).
set -euo pipefail
cd "$(dirname "$0")"
exec uv run uvicorn main:app --host 127.0.0.1 --port 8000
