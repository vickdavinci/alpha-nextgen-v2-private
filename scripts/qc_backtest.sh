#!/bin/bash
# =============================================================================
# QC Backtest Script - Automated sync, minify, validate, push, and backtest
# =============================================================================
# Usage:
#   ./scripts/qc_backtest.sh                         # Fire-and-forget (async)
#   ./scripts/qc_backtest.sh "My-Backtest-Name"      # Custom name (async)
#   ./scripts/qc_backtest.sh --open                  # Wait for completion
#   ./scripts/qc_backtest.sh "My-Name" --open        # Custom name + wait
#
# What it does:
#   1. Syncs project files to lean-workspace/AlphaNextGen
#   2. Runs standard + ultra minification
#   3. Validates telemetry/syntax markers after minification
#   4. Enforces QC per-file size guard pre-push
#   5. Pushes to QuantConnect cloud
#   6. Starts a backtest with the specified name
# =============================================================================

set -e

# Configuration
SRC="/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private"
LEAN_WORKSPACE="/Users/vigneshwaranarumugam/Documents/Trading Github/lean-workspace"
DEST="$LEAN_WORKSPACE/AlphaNextGen"
PROJECT_NAME="AlphaNextGen"
MAX_FILE_CHARS=262144

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

# Step 1: Sync
echo -e "${BLUE}[1/5]${NC} Syncing files to lean workspace..."
"$SRC/scripts/sync_to_lean.sh" >/dev/null
FILE_COUNT=$(find "$DEST" -type f -name "*.py" | wc -l | tr -d ' ')
echo -e "${GREEN}   ✓ Synced $FILE_COUNT Python files${NC}"

# Step 2: Minify
echo -e "${BLUE}[2/5]${NC} Minifying workspace files..."
python3 "$SRC/scripts/minify_workspace.py"
python3 "$SRC/scripts/ultra_minify.py" --workspace "$DEST" --target-indent 1
echo -e "${GREEN}   ✓ Minified (standard + ultra)${NC}"

# Step 3: Validate
echo -e "${BLUE}[3/5]${NC} Validating minified workspace..."
python3 "$SRC/scripts/validate_lean_minified.py" --root "$DEST" --strict
echo -e "${GREEN}   ✓ Validation passed${NC}"

# Step 4: Size guards
echo -e "${BLUE}[4/5]${NC} Checking QC size limits..."
LARGEST_FILE_LINE=$(find "$DEST" -type f -name "*.py" -exec wc -m {} + | grep -v ' total$' | sort -nr | head -1)
LARGEST_SIZE=$(echo "$LARGEST_FILE_LINE" | awk '{print $1}')
LARGEST_PATH=$(echo "$LARGEST_FILE_LINE" | sed -E 's/^[[:space:]]*[0-9]+[[:space:]]+//')
echo -e "${YELLOW}   Largest file:${NC} $LARGEST_PATH (${LARGEST_SIZE:-0} chars)"

if [ "${LARGEST_SIZE:-0}" -gt "$MAX_FILE_CHARS" ]; then
    echo -e "${RED}   ✗ Size guard FAILED: per-file size exceeds ${MAX_FILE_CHARS} characters${NC}"
    exit 1
fi
echo -e "${GREEN}   ✓ Size checks passed${NC}"

# Step 5: Push + backtest
echo -e "${BLUE}[5/5]${NC} Pushing to QuantConnect cloud..."
cd "$LEAN_WORKSPACE"
PUSH_OUTPUT=$(lean cloud push --project "$PROJECT_NAME" 2>&1)
PUSH_EXIT=$?
echo "$PUSH_OUTPUT"
if [ "$PUSH_EXIT" -ne 0 ] || echo "$PUSH_OUTPUT" | grep -qi "413\|exceed.*size\|failed"; then
    echo -e "${RED}   ✗ Push FAILED${NC}"
    echo -e "${RED}   QC limit: per-file size must be <= 256KB${NC}"
    exit 1
fi
echo -e "${GREEN}   ✓ Push complete${NC}"

echo -e "${BLUE}Starting backtest...${NC}"
if [ -n "$OPEN_FLAG" ]; then
    lean cloud backtest "$PROJECT_NAME" --name "$BACKTEST_NAME" --open 2>&1
    echo -e "${GREEN}Backtest completed.${NC}"
else
    OUTPUT=$(lean cloud backtest "$PROJECT_NAME" --name "$BACKTEST_NAME" 2>&1)
    BACKTEST_URL=$(echo "$OUTPUT" | grep -o 'https://www.quantconnect.com/project/[^ ]*' | head -1)
    echo -e "${GREEN}   ✓ Backtest started${NC}"
    echo -e "${YELLOW}URL:${NC} $BACKTEST_URL"
fi
