#!/usr/bin/env bash
# Build the Super Productivity plugin zip for upload.
# Usage: ./build-plugin.sh
# Output: plugin/plugin.zip

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$SCRIPT_DIR/plugin"
OUT="$PLUGIN_DIR/plugin.zip"

echo "Building plugin..."

# Remove stale zip so it doesn't get included in itself
rm -f "$OUT"

cd "$PLUGIN_DIR"
zip -j "$OUT" index.html manifest.json plugin.js

echo "Built: $OUT"
