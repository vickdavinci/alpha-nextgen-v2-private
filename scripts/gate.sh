#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="changed"
NO_TESTS=0
STAGED_ONLY=0

usage() {
    cat <<'EOF'
Usage:
  ./scripts/gate.sh [changed|full] [--no-tests] [--staged-only]

Examples:
  ./scripts/gate.sh changed
  ./scripts/gate.sh full
  ./scripts/gate.sh changed --no-tests
  ./scripts/gate.sh changed --no-tests --staged-only
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        changed|full)
            MODE="$1"
            shift
            ;;
        --no-tests)
            NO_TESTS=1
            shift
            ;;
        --staged-only)
            STAGED_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

PYTHON_BIN="${PYTHON_BIN:-$ROOT/venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python)"
    else
        echo "No Python interpreter found."
        exit 1
    fi
fi

section() {
    printf "\n[%s] %s\n" "$1" "$2"
}

add_test_target() {
    local target="$1"
    local existing
    for existing in "${TEST_TARGETS[@]:-}"; do
        [[ "$existing" == "$target" ]] && return 0
    done
    TEST_TARGETS+=("$target")
}

if [[ "$STAGED_ONLY" -eq 1 ]]; then
    CHANGED_FILES="$(git diff --cached --name-only --diff-filter=ACMRTUXB | sort -u || true)"
else
    CHANGED_FILES="$(
        {
            git diff --name-only --diff-filter=ACMRTUXB HEAD || true
            git diff --cached --name-only --diff-filter=ACMRTUXB || true
        } | sort -u
    )"
fi

if [[ "$MODE" == "full" ]]; then
    PY_FILES="$(rg --files -g '*.py' || true)"
else
    PY_FILES="$(printf "%s\n" "$CHANGED_FILES" | grep -E '\.py$' || true)"
fi

if [[ -z "${PY_FILES// /}" ]]; then
    section "GATE" "No Python files detected for mode=$MODE"
else
    section "COMPILE" "Checking Python syntax (${MODE})"
    PY_FILES_INPUT="$PY_FILES" "$PYTHON_BIN" - "$ROOT" <<'PY'
import os
import py_compile
import sys

root = sys.argv[1]
paths = [line.strip() for line in os.environ.get("PY_FILES_INPUT", "").splitlines() if line.strip()]
errors = []
for path in paths:
    abs_path = path if os.path.isabs(path) else os.path.join(root, path)
    if not os.path.exists(abs_path):
        continue
    try:
        py_compile.compile(abs_path, doraise=True)
    except Exception as exc:
        errors.append(f"{path}: {exc}")

if errors:
    print("Compile failures:")
    for err in errors:
        print(f"  - {err}")
    sys.exit(1)
print(f"Compile OK ({len(paths)} files)")
PY

    section "LINT" "Running static lint (ruff/pyflakes if available)"
    if "$PYTHON_BIN" -m ruff --version >/dev/null 2>&1; then
        "$PYTHON_BIN" -m ruff check $PY_FILES
    elif "$PYTHON_BIN" -m pyflakes --version >/dev/null 2>&1; then
        "$PYTHON_BIN" -m pyflakes $PY_FILES
    else
        echo "No ruff/pyflakes available; lint step skipped."
    fi
fi

if [[ "$MODE" == "full" ]] || printf "%s\n" "$CHANGED_FILES" | grep -q '^config\.py$'; then
    if [[ -f "$ROOT/scripts/validate_config.py" ]]; then
        section "CONFIG" "Validating config integrity"
        "$PYTHON_BIN" "$ROOT/scripts/validate_config.py"
    fi
fi

if [[ "$NO_TESTS" -eq 1 ]]; then
    section "TESTS" "Skipped (--no-tests)"
    section "DONE" "Gate passed (mode=$MODE)"
    exit 0
fi

TEST_TARGETS=()
if [[ "$MODE" == "full" ]]; then
    add_test_target "tests/test_options_engine.py"
    add_test_target "tests/test_micro_regime_engine.py"
    add_test_target "tests/test_portfolio_router.py"
    add_test_target "tests/test_daily_scheduler.py"
    add_test_target "tests/test_state_persistence.py"
else
    if printf "%s\n" "$CHANGED_FILES" | grep -Eq '^(engines/satellite/|main\.py$|main_.*_mixin\.py$|portfolio/|execution/|config\.py$)'; then
        add_test_target "tests/test_options_engine.py"
        add_test_target "tests/test_micro_regime_engine.py"
        add_test_target "tests/test_portfolio_router.py"
    fi
    if printf "%s\n" "$CHANGED_FILES" | grep -Eq '^(scheduling/|main_premarket_mixin\.py$|main_intraday_close_mixin\.py$)'; then
        add_test_target "tests/test_daily_scheduler.py"
    fi
    if printf "%s\n" "$CHANGED_FILES" | grep -Eq '^(persistence/|engines/satellite/options_state_manager\.py$)'; then
        add_test_target "tests/test_state_persistence.py"
    fi
fi

if [[ "${#TEST_TARGETS[@]}" -gt 0 ]]; then
    section "TESTS" "Running targeted pytest suite"
    if "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
        "$PYTHON_BIN" -m pytest -q "${TEST_TARGETS[@]}"
    else
        echo "pytest is not available in $PYTHON_BIN environment."
        exit 1
    fi
else
    section "TESTS" "No targeted tests selected for mode=$MODE"
fi

section "DONE" "Gate passed (mode=$MODE)"
