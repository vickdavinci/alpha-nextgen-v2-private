#!/bin/bash
# =============================================================================
# QC Backtest Script - Automated sync, push, and backtest for QuantConnect
# =============================================================================
# Usage:
#   ./scripts/qc_backtest.sh                         # Fire-and-forget (async)
#   ./scripts/qc_backtest.sh "My-Backtest-Name"      # Custom name (async)
#   ./scripts/qc_backtest.sh --open                  # Wait for completion
#   ./scripts/qc_backtest.sh "My-Name" --open        # Custom name + wait
#
# What it does:
#   1. Syncs ALL project files to lean-workspace/AlphaNextGen
#   2. Pushes to QuantConnect cloud
#   3. Starts a backtest with the specified name
#   4. With --open: Waits for completion and outputs results
#   5. Prints the backtest URL
# =============================================================================

set -e  # Exit on any error

# Configuration
SRC="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private"
LEAN_WORKSPACE="/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace"
DEST="$LEAN_WORKSPACE/AlphaNextGen"
PROJECT_NAME="AlphaNextGen"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
BACKTEST_NAME=""
OPEN_FLAG=""

for arg in "$@"; do
    if [ "$arg" == "--open" ]; then
        OPEN_FLAG="--open"
    elif [ -z "$BACKTEST_NAME" ]; then
        BACKTEST_NAME="$arg"
    fi
done

# Generate backtest name if not provided
if [ -z "$BACKTEST_NAME" ]; then
    cd "$SRC"
    BRANCH=$(git branch --show-current)
    # Replace slashes with dashes, remove "testing/" prefix
    BACKTEST_NAME=$(echo "$BRANCH" | sed 's|testing/||g' | sed 's|/|-|g')
    BACKTEST_NAME="${BACKTEST_NAME}-$(date +%H%M)"
fi

echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           QC BACKTEST - AlphaNextGen V2                       ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Backtest Name:${NC} $BACKTEST_NAME"
if [ -n "$OPEN_FLAG" ]; then
    echo -e "${YELLOW}Mode:${NC} Wait for completion (--open)"
else
    echo -e "${YELLOW}Mode:${NC} Fire-and-forget (async)"
fi
echo ""

# Step 1: Sync files
echo -e "${BLUE}[1/3]${NC} Syncing files to lean workspace..."

# Copy main files
cp "$SRC/main.py" "$SRC/config.py" "$DEST/"

# Sync all directories (remove existing to ensure clean copy)
for dir in engines portfolio execution models persistence scheduling utils data; do
    if [ -d "$SRC/$dir" ]; then
        rm -rf "$DEST/$dir"
        cp -r "$SRC/$dir" "$DEST/"
    fi
done

# Count synced files
FILE_COUNT=$(find "$DEST" -type f -name "*.py" | wc -l | tr -d ' ')
echo -e "${GREEN}   ✓ Synced $FILE_COUNT Python files${NC}"

# Step 2: Push to QC
echo -e "${BLUE}[2/3]${NC} Pushing to QuantConnect cloud..."
cd "$LEAN_WORKSPACE"
lean cloud push --project "$PROJECT_NAME" 2>/dev/null
echo -e "${GREEN}   ✓ Push complete${NC}"

# Step 3: Start backtest
echo -e "${BLUE}[3/3]${NC} Starting backtest..."

if [ -n "$OPEN_FLAG" ]; then
    # --open mode: Wait for completion and show results
    echo -e "${YELLOW}   Waiting for backtest to complete...${NC}"
    lean cloud backtest "$PROJECT_NAME" --name "$BACKTEST_NAME" --open 2>&1
    BACKTEST_STATUS=$?

    echo ""
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                    BACKTEST COMPLETED                         ║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
else
    # Async mode: Fire and forget
    OUTPUT=$(lean cloud backtest "$PROJECT_NAME" --name "$BACKTEST_NAME" 2>&1)

    # Extract URL from output
    BACKTEST_URL=$(echo "$OUTPUT" | grep -o 'https://www.quantconnect.com/project/[^ ]*' | head -1)

    echo -e "${GREEN}   ✓ Backtest started${NC}"
    echo ""
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                      BACKTEST STARTED                         ║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${YELLOW}Name:${NC} $BACKTEST_NAME"
    echo -e "${YELLOW}URL:${NC}  $BACKTEST_URL"
    echo ""
    echo -e "${GREEN}View results at:${NC}"
    echo "$BACKTEST_URL"
fi
