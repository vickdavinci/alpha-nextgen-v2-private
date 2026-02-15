#!/bin/bash
# sync_to_lean.sh - Sync alpha-nextgen to lean-workspace for QC backtesting
#
# Usage:
#   ./scripts/sync_to_lean.sh
#   ./scripts/sync_to_lean.sh --minify
#   ./scripts/sync_to_lean.sh --minify --validate
#   ./scripts/sync_to_lean.sh --push

set -e

SRC="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private"
DST="/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace/AlphaNextGen"
MINIFY=0
VALIDATE=0
PUSH=0

for arg in "$@"; do
    case "$arg" in
        --minify) MINIFY=1 ;;
        --validate) VALIDATE=1 ;;
        --push) PUSH=1 ;;
        *) echo "Unknown arg: $arg"; exit 2 ;;
    esac
done

echo "=== Alpha NextGen Sync Tool ==="
echo "Source: $SRC"
echo "Destination: $DST"
echo ""

if [ ! -d "$DST" ]; then
    echo "ERROR: Destination directory does not exist: $DST"
    exit 1
fi

echo "Syncing core files..."
cp "$SRC/main.py" "$DST/"
cp "$SRC/config.py" "$DST/"

DIRS="engines portfolio execution models persistence scheduling data utils"
for dir in $DIRS; do
    if [ -d "$SRC/$dir" ]; then
        echo "  - $dir/"
        rm -rf "$DST/$dir"
        cp -r "$SRC/$dir" "$DST/"
    fi
done

find "$DST" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$DST" -name ".DS_Store" -delete 2>/dev/null || true
find "$DST" -name "*.pyc" -delete 2>/dev/null || true

echo ""
echo "Sync complete!"

if [ "$MINIFY" -eq 1 ]; then
    echo ""
    echo "Running minification..."
    python3 "$SRC/scripts/minify_workspace.py"
    python3 "$SRC/scripts/ultra_minify.py" --workspace "$DST" --target-indent 1
fi

if [ "$VALIDATE" -eq 1 ]; then
    echo ""
    echo "Running lean validation..."
    python3 "$SRC/scripts/validate_lean_minified.py" --root "$DST" --strict
fi

if [ "$PUSH" -eq 1 ]; then
    echo ""
    echo "Pushing to QuantConnect cloud..."
    cd "$DST/.."
    lean cloud push --project AlphaNextGen
    echo "Push complete."
fi
