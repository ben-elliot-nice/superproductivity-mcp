#!/usr/bin/env bash
# Build the Super Productivity plugin zip for upload.
# Usage: ./build-plugin.sh
# Output: plugin/superproductivity-mcp-plugin-v<version>.zip  (version read from plugin/manifest.json)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$SCRIPT_DIR/plugin"
MANIFEST="$PLUGIN_DIR/manifest.json"

VERSION=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$MANIFEST")
OUT="$PLUGIN_DIR/superproductivity-mcp-plugin-v${VERSION}.zip"

echo "Building plugin v${VERSION}..."

# Remove any previous build of this version
rm -f "$OUT"

# Stamp version into a temp copy of index.html
TEMP_HTML="$(mktemp).html"
sed "s/__PLUGIN_VERSION__/${VERSION}/g" "$PLUGIN_DIR/index.html" > "$TEMP_HTML"
trap "rm -f '$TEMP_HTML'" EXIT

cd "$PLUGIN_DIR"
zip -j "$OUT" "$TEMP_HTML" manifest.json plugin.js "$SCRIPT_DIR/README.md"

# zip uses the temp filename — rename the entry to index.html inside the zip
python3 - <<PYEOF
import zipfile, os, shutil

out = "${OUT}"
tmp = out + ".tmp.zip"
with zipfile.ZipFile(out, 'r') as zin, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        # Rename the temp html file entry to index.html
        if item.filename.endswith('.html') and item.filename != 'index.html':
            item.filename = 'index.html'
        zout.writestr(item, data)
os.replace(tmp, out)
PYEOF

echo "Built: $OUT"
