#!/bin/bash
# =============================================================================
# ALPHA NEXTGEN V2 - HARD FORK SETUP SCRIPT
# =============================================================================
#
# PURPOSE: Fork V1 codebase to create V2 private repository
#
# STEPS:
#   1. Clone current directory to ../alpha-nextgen-v2-private
#   2. Detach git history (clean slate)
#   3. Initialize fresh git repo
#   4. Migrate V2.1 spec documents
#   5. Create initial commit
#
# USAGE: ./scripts/setup_v2_env.sh
#
# =============================================================================

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
V1_DIR="$(cd "$(dirname "$0")/.." && pwd)"
V2_DIR="${V1_DIR}/../alpha-nextgen-v2-private"
V2_DOCS_SOURCE="/Users/vigneshwaranarumugam/Downloads/Perp V2.0 docs"

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Alpha NextGen V2 - Hard Fork Setup${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# =============================================================================
# STEP 1: VALIDATE PRECONDITIONS
# =============================================================================
echo -e "${YELLOW}[1/6] Validating preconditions...${NC}"

if [ ! -d "$V1_DIR" ]; then
    echo -e "${RED}ERROR: V1 directory not found: $V1_DIR${NC}"
    exit 1
fi

if [ -d "$V2_DIR" ]; then
    echo -e "${RED}ERROR: V2 directory already exists: $V2_DIR${NC}"
    echo -e "${RED}       Remove it first if you want to start fresh.${NC}"
    exit 1
fi

if [ ! -d "$V2_DOCS_SOURCE" ]; then
    echo -e "${YELLOW}WARNING: V2.1 docs directory not found: $V2_DOCS_SOURCE${NC}"
    echo -e "${YELLOW}         Documents will not be migrated.${NC}"
fi

echo -e "${GREEN}  V1 directory: $V1_DIR${NC}"
echo -e "${GREEN}  V2 directory: $V2_DIR${NC}"
echo ""

# =============================================================================
# STEP 2: FORK CODEBASE (Clean local clone)
# =============================================================================
echo -e "${YELLOW}[2/6] Forking codebase to V2 directory...${NC}"

# Create V2 directory
mkdir -p "$V2_DIR"

# Copy all files except .git, venv, __pycache__, and other ignored files
rsync -av --progress "$V1_DIR/" "$V2_DIR/" \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='.mypy_cache' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='testing-logs.md'

echo -e "${GREEN}  Codebase forked successfully.${NC}"
echo ""

# =============================================================================
# STEP 3: DETACH GIT HISTORY
# =============================================================================
echo -e "${YELLOW}[3/6] Detaching git history (clean slate)...${NC}"

cd "$V2_DIR"

# Ensure no .git directory exists (should already be excluded)
if [ -d ".git" ]; then
    rm -rf .git
    echo -e "${GREEN}  Removed existing .git directory.${NC}"
fi

echo -e "${GREEN}  Git history detached.${NC}"
echo ""

# =============================================================================
# STEP 4: INITIALIZE FRESH V2 REPOSITORY
# =============================================================================
echo -e "${YELLOW}[4/6] Initializing fresh V2 repository...${NC}"

git init
git branch -m main

echo -e "${GREEN}  Git repository initialized on 'main' branch.${NC}"
echo ""

# =============================================================================
# STEP 5: MIGRATE V2.1 DOCUMENTATION
# =============================================================================
echo -e "${YELLOW}[5/6] Migrating V2.1 specification documents...${NC}"

# Create v2-specs directory for V2.1 documents
mkdir -p "$V2_DIR/docs/v2-specs"

if [ -d "$V2_DOCS_SOURCE" ]; then
    # Copy all V2.1 spec files
    for file in "$V2_DOCS_SOURCE"/*.txt "$V2_DOCS_SOURCE"/*.md; do
        if [ -f "$file" ]; then
            cp "$file" "$V2_DIR/docs/v2-specs/"
            echo -e "${GREEN}    Copied: $(basename "$file")${NC}"
        fi
    done
    echo -e "${GREEN}  V2.1 documents migrated to docs/v2-specs/${NC}"
else
    echo -e "${YELLOW}  Skipped document migration (source not found).${NC}"
fi
echo ""

# =============================================================================
# STEP 6: UPDATE PROJECT METADATA
# =============================================================================
echo -e "${YELLOW}[6/6] Updating project metadata for V2...${NC}"

# Update README badge
if [ -f "$V2_DIR/README.md" ]; then
    sed -i '' 's/v1.0.0%20Released/v2.0.0--dev-brightblue/g' "$V2_DIR/README.md"
    sed -i '' 's/Alpha NextGen/Alpha NextGen V2/g' "$V2_DIR/README.md" 2>/dev/null || true
fi

# Create V2 version marker
cat > "$V2_DIR/VERSION" << 'EOF'
2.0.0-dev
EOF

# Update .python-version if needed
echo "3.11" > "$V2_DIR/.python-version"

echo -e "${GREEN}  Project metadata updated.${NC}"
echo ""

# =============================================================================
# STEP 7: CREATE INITIAL COMMIT
# =============================================================================
echo -e "${YELLOW}[7/7] Creating initial V2 commit...${NC}"

cd "$V2_DIR"
git add -A
git commit -m "Initial V2 Commit (Forked from V1 v1.0.0)

Alpha NextGen V2.0.0 Development Branch

Forked from: alpha-nextgen v1.0.0
Fork date: $(date '+%Y-%m-%d')

V2 Architecture Changes (Planned):
- Core-Satellite engine structure (engines/core/, engines/satellite/)
- New Options Engine (20-30% allocation)
- Enhanced Trend Engine with ADX confirmation
- Mean Reversion with VIX filter
- 5-level circuit breaker system
- OCO order management

See docs/v2-specs/ for complete V2.1 specifications.
"

echo -e "${GREEN}  Initial commit created.${NC}"
echo ""

# =============================================================================
# SUMMARY
# =============================================================================
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}  V2 FORK COMPLETE!${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo -e "  V2 Directory: ${GREEN}$V2_DIR${NC}"
echo -e "  Git Branch:   ${GREEN}main${NC}"
echo -e "  V2.1 Specs:   ${GREEN}docs/v2-specs/${NC}"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo -e "  1. cd $V2_DIR"
echo -e "  2. python3.11 -m venv venv && source venv/bin/activate"
echo -e "  3. pip install -r requirements.lock"
echo -e "  4. make verify  # Confirm V1 tests still pass"
echo -e "  5. Begin V2 implementation per V2_IMPLEMENTATION_ROADMAP.md"
echo ""
echo -e "${BLUE}Happy coding!${NC}"
