#!/usr/bin/env bash
# Start the local FastAPI service. Binds to 127.0.0.1 only (see docs/safety.md) —
# do not override LR_CLEANUP_HOST to a non-loopback address.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "No .venv found. See README.md 'Local setup' before running this script." >&2
  exit 1
fi

source .venv/bin/activate
exec lr-cleanup-server
