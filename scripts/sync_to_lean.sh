#!/bin/bash
# sync_to_lean.sh - Sync alpha-nextgen to lean-workspace for QC backtesting
#
# Usage: ./scripts/sync_to_lean.sh [--push]
#   --push: Also push to QC cloud after sync

set -e

SRC="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private"
DST="/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace/AlphaNextGen"

echo "=== Alpha NextGen Sync Tool ==="
echo "Source: $SRC"
echo "Destination: $DST"
echo ""

# Ensure destination exists
if [ ! -d "$DST" ]; then
    echo "ERROR: Destination directory does not exist: $DST"
    exit 1
fi

# Sync core files
echo "Syncing core files..."
cp "$SRC/main.py" "$DST/"
cp "$SRC/config.py" "$DST/"

# Sync directories
DIRS="engines portfolio execution models persistence scheduling data utils"
for dir in $DIRS; do
    if [ -d "$SRC/$dir" ]; then
        echo "  - $dir/"
        rm -rf "$DST/$dir"
        cp -r "$SRC/$dir" "$DST/"
    fi
done

echo ""
echo "Sync complete!"
echo ""

# Show what was synced
echo "Files synced:"
ls -la "$DST/main.py" "$DST/config.py"
echo ""
echo "Directories synced:"
for dir in $DIRS; do
    if [ -d "$DST/$dir" ]; then
        count=$(find "$DST/$dir" -name "*.py" | wc -l | tr -d ' ')
        echo "  - $dir/ ($count Python files)"
    fi
done

# Optional push to QC
if [ "$1" == "--push" ]; then
    echo ""
    echo "Pushing to QuantConnect cloud..."
    cd "$DST/.."
    lean cloud push --project AlphaNextGen
    echo ""
    echo "Push complete! Run backtest with:"
    echo "  lean cloud backtest AlphaNextGen"
else
    echo ""
    echo "Next steps:"
    echo "  1. cd $DST/.."
    echo "  2. lean cloud push --project AlphaNextGen"
    echo "  3. lean cloud backtest AlphaNextGen"
fi
