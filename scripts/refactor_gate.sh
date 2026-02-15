#!/usr/bin/env bash
set -euo pipefail

# Refactor gate runner: syntax, size, telemetry, import, tests, sync guards.
# Usage:
#   scripts/refactor_gate.sh --phase 1
#   scripts/refactor_gate.sh --phase 4 --no-tests
#   scripts/refactor_gate.sh --source /path/to/repo --lean /path/to/lean-workspace/AlphaNextGen

PHASE=""
RUN_TESTS=1
SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEAN_ROOT="$HOME/Documents/Trading Github/lean-workspace/AlphaNextGen"
MAX_BYTES=262144

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase)
      PHASE="$2"
      shift 2
      ;;
    --source)
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --lean)
      LEAN_ROOT="$2"
      shift 2
      ;;
    --no-tests)
      RUN_TESTS=0
      shift
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$PHASE" ]]; then
  echo "--phase is required (0..5)" >&2
  exit 2
fi

if ! [[ "$PHASE" =~ ^[0-5]$ ]]; then
  echo "Invalid --phase '$PHASE' (must be 0..5)" >&2
  exit 2
fi

echo "=== Refactor Gate (Phase $PHASE) ==="
echo "Source: $SOURCE_ROOT"
echo "Lean:   $LEAN_ROOT"

if [[ ! -d "$SOURCE_ROOT" ]]; then
  echo "ERROR: source root not found: $SOURCE_ROOT" >&2
  exit 1
fi

if [[ ! -d "$LEAN_ROOT" ]]; then
  echo "ERROR: lean root not found: $LEAN_ROOT" >&2
  exit 1
fi

check_size_dir() {
  local root="$1"
  local bad=0
  while IFS= read -r -d '' f; do
    local bytes
    bytes=$(wc -c < "$f" | tr -d ' ')
    if (( bytes > MAX_BYTES )); then
      echo "SIZE_FAIL: $f ($bytes bytes > $MAX_BYTES)"
      bad=1
    fi
  done < <(find "$root"     -path "$root/.git" -prune -o     -path "$root/venv" -prune -o     -path "$root/.venv" -prune -o     -path "$root/__pycache__" -prune -o     -type f -name "*.py" -print0)

  if (( bad == 1 )); then
    return 1
  fi

  echo "SIZE_OK: all .py files in $root are <= $MAX_BYTES"
}

compile_dir() {
  local root="$1"
  local failed=0
  while IFS= read -r -d '' f; do
    if ! python3 -m py_compile "$f" 2>/dev/null; then
      echo "COMPILE_FAIL: $f"
      failed=1
    fi
  done < <(find "$root"     -path "$root/.git" -prune -o     -path "$root/venv" -prune -o     -path "$root/.venv" -prune -o     -path "$root/__pycache__" -prune -o     -type f -name "*.py" -print0)

  if (( failed == 1 )); then
    return 1
  fi

  echo "COMPILE_OK: all .py files in $root"
}

echo "[1] Size checks"
check_size_dir "$SOURCE_ROOT"
check_size_dir "$LEAN_ROOT"

echo "[2] Syntax checks"
compile_dir "$SOURCE_ROOT"
compile_dir "$LEAN_ROOT"

echo "[3] Import checks (source)"
pushd "$SOURCE_ROOT" >/dev/null
python3 - <<'PY'
from main import AlphaNextGen
from engines.satellite.options_engine import OptionsEngine
print("IMPORT_OK: AlphaNextGen, OptionsEngine")
PY
popd >/dev/null

echo "[4] Telemetry validator (strict)"
VALIDATOR="$SOURCE_ROOT/scripts/validate_lean_minified.py"
if [[ ! -f "$VALIDATOR" ]]; then
  echo "ERROR: validator missing at $VALIDATOR" >&2
  exit 1
fi
python3 "$VALIDATOR" --root "$SOURCE_ROOT" --strict
python3 "$VALIDATOR" --root "$LEAN_ROOT" --strict

if (( PHASE >= 4 )); then
  echo "[5] QC sync script guards"
  SYNC_SCRIPT="$SOURCE_ROOT/scripts/qc_backtest.sh"
  if [[ ! -f "$SYNC_SCRIPT" ]]; then
    echo "ERROR: missing $SYNC_SCRIPT" >&2
    exit 1
  fi
  if ! grep -q 'main_\*\.py\|main_\*' "$SYNC_SCRIPT"; then
    echo "SYNC_FAIL: scripts/qc_backtest.sh does not appear to sync main_*.py mixin files"
    exit 1
  fi
  echo "SYNC_OK: qc_backtest.sh appears to include main mixin sync"
fi

if (( RUN_TESTS == 1 )); then
  echo "[6] Tests"
  pushd "$SOURCE_ROOT" >/dev/null
  if command -v pytest >/dev/null 2>&1 && [[ -d tests ]]; then
    pytest tests/ -q
  else
    echo "TEST_WARN: pytest or tests/ not available; skipped"
  fi
  popd >/dev/null
else
  echo "[6] Tests skipped (--no-tests)"
fi

echo "=== PASS: Refactor gate checks passed for phase $PHASE ==="
