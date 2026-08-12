#!/usr/bin/env bash
# There is no CLI/scriptable way to install a Lightroom Classic plugin —
# Adobe's supported mechanism is File > Plug-in Manager > Add in the
# Lightroom Classic UI itself, pointed at the .lrplugin folder. This
# script only verifies the plugin folder looks valid and reveals it in
# Finder so that step is a one-click "Add" away.
set -euo pipefail
cd "$(dirname "$0")/.."

PLUGIN_DIR="lightroom-plugin/AICleanup.lrplugin"

if [ ! -f "$PLUGIN_DIR/Info.lua" ]; then
  echo "error: $PLUGIN_DIR/Info.lua not found — run this from the repo root." >&2
  exit 1
fi

ABS_PATH="$(cd "$PLUGIN_DIR" && pwd)"

cat <<EOF
Plugin folder looks valid: $ABS_PATH

To install in Lightroom Classic:
  1. Make sure the local service is running (scripts/run-server.sh).
  2. In Lightroom Classic: File > Plug-in Manager...
  3. Click "Add", then select:
       $ABS_PATH
  4. Select a photo or a few photos in the Library, then:
       Library > Plug-in Extras > AI Cleanup: Analyze Selected Photos

The Plug-in Manager's "AI Cleanup" section lets you change the local
service URL (default http://127.0.0.1:8765) and test the connection.
EOF

if command -v open >/dev/null 2>&1; then
  open -R "$ABS_PATH"
fi
