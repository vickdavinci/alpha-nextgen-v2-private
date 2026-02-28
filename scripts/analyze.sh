#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAGE_DIR=""
RUN_NAME=""

usage() {
    cat <<'EOF'
Usage:
  ./scripts/analyze.sh --stage-dir <dir> [--run-name <name>]

Notes:
  - If --run-name is omitted, latest *_logs.txt in stage dir is used.
  - Generates/refreshes REPORT, SIGNAL_FLOW_REPORT, TRADE_DETAIL_REPORT.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage-dir)
            STAGE_DIR="${2:-}"
            shift 2
            ;;
        --run-name)
            RUN_NAME="${2:-}"
            shift 2
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

if [[ -z "$STAGE_DIR" ]]; then
    echo "--stage-dir is required."
    usage
    exit 1
fi

if [[ ! -d "$STAGE_DIR" ]]; then
    echo "Stage dir does not exist: $STAGE_DIR"
    exit 1
fi

if [[ -z "$RUN_NAME" ]]; then
    latest_logs="$(ls -1t "$STAGE_DIR"/*_logs.txt 2>/dev/null | head -1 || true)"
    if [[ -z "$latest_logs" ]]; then
        echo "No *_logs.txt found in $STAGE_DIR"
        exit 1
    fi
    RUN_NAME="$(basename "$latest_logs" _logs.txt)"
fi

PYTHON_BIN="${PYTHON_BIN:-$ROOT/venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3)"
fi

cmd=("$PYTHON_BIN" "$ROOT/scripts/generate_run_reports.py" "--stage-dir" "$STAGE_DIR" "--run-name" "$RUN_NAME")
echo "Running: ${cmd[*]}"
"${cmd[@]}"

required=(
    "$STAGE_DIR/${RUN_NAME}_REPORT.md"
    "$STAGE_DIR/${RUN_NAME}_SIGNAL_FLOW_REPORT.md"
    "$STAGE_DIR/${RUN_NAME}_TRADE_DETAIL_REPORT.md"
)

for f in "${required[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "Missing required report: $f"
        exit 1
    fi
done

echo "Reports ready for run: $RUN_NAME"
