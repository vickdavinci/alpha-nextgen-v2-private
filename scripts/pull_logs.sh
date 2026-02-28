#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_NAME=""
BACKTEST_ID=""
STAGE_DIR=""
PROJECT_ID="27678023"
SKIP_REPORTS=0
SKIP_OBSERVABILITY=0

usage() {
    cat <<'EOF'
Usage:
  ./scripts/pull_logs.sh (--run-name <name> | --backtest-id <id>) --stage-dir <dir>
                         [--project-id <id>] [--skip-reports] [--skip-observability]
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-name)
            RUN_NAME="${2:-}"
            shift 2
            ;;
        --backtest-id)
            BACKTEST_ID="${2:-}"
            shift 2
            ;;
        --stage-dir)
            STAGE_DIR="${2:-}"
            shift 2
            ;;
        --project-id)
            PROJECT_ID="${2:-}"
            shift 2
            ;;
        --skip-reports)
            SKIP_REPORTS=1
            shift
            ;;
        --skip-observability)
            SKIP_OBSERVABILITY=1
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

if [[ -z "$STAGE_DIR" ]]; then
    echo "--stage-dir is required."
    usage
    exit 1
fi

if [[ -z "$RUN_NAME" && -z "$BACKTEST_ID" ]]; then
    echo "Provide --run-name or --backtest-id."
    usage
    exit 1
fi

mkdir -p "$STAGE_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT/venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3)"
fi

cmd=("$PYTHON_BIN" "$ROOT/scripts/qc_pull_backtest.py" "--all" "--project" "$PROJECT_ID" "--output" "$STAGE_DIR")
if [[ -n "$RUN_NAME" ]]; then
    cmd+=("$RUN_NAME")
else
    cmd+=("--id" "$BACKTEST_ID")
fi
if [[ "$SKIP_REPORTS" -eq 1 ]]; then
    cmd+=("--skip-reports")
fi
if [[ "$SKIP_OBSERVABILITY" -eq 1 ]]; then
    cmd+=("--skip-observability")
fi

echo "Running: ${cmd[*]}"
"${cmd[@]}"
