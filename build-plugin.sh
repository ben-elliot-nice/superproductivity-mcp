#!/usr/bin/env bash
# Build the Super Productivity plugin zip for upload.
# Usage: ./build-plugin.sh
# Output: plugin/plugin-v<version>.zip  (version read from plugin/manifest.json)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$SCRIPT_DIR/plugin"
MANIFEST="$PLUGIN_DIR/manifest.json"

VERSION=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$MANIFEST")
OUT="$PLUGIN_DIR/plugin-v${VERSION}.zip"

echo "Building plugin v${VERSION}..."

# Remove any previous build of this version
rm -f "$OUT"

cd "$PLUGIN_DIR"
zip -j "$OUT" index.html manifest.json plugin.js "$SCRIPT_DIR/README.md"

echo "Built: $OUT"
