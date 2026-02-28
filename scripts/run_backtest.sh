#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUN_NAME=""
START_DATE=""
END_DATE=""
BACKTEST_YEAR=""
SKIP_GATE=0
OPEN=1

usage() {
    cat <<'EOF'
Usage:
  ./scripts/run_backtest.sh [--run-name <name>] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
                            [--backtest-year YYYY] [--no-open] [--skip-gate]
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-name)
            RUN_NAME="${2:-}"
            shift 2
            ;;
        --start-date)
            START_DATE="${2:-}"
            shift 2
            ;;
        --end-date)
            END_DATE="${2:-}"
            shift 2
            ;;
        --backtest-year)
            BACKTEST_YEAR="${2:-}"
            shift 2
            ;;
        --skip-gate)
            SKIP_GATE=1
            shift
            ;;
        --no-open)
            OPEN=0
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

if [[ "$SKIP_GATE" -ne 1 ]]; then
    "$ROOT/scripts/gate.sh" changed
fi

cmd=("$ROOT/scripts/qc_backtest.sh")
if [[ -n "$RUN_NAME" ]]; then
    cmd+=("$RUN_NAME")
fi
if [[ "$OPEN" -eq 1 ]]; then
    cmd+=("--open")
fi
if [[ -n "$START_DATE" ]]; then
    cmd+=("--start-date" "$START_DATE")
fi
if [[ -n "$END_DATE" ]]; then
    cmd+=("--end-date" "$END_DATE")
fi
if [[ -n "$BACKTEST_YEAR" ]]; then
    cmd+=("--backtest-year" "$BACKTEST_YEAR")
fi

echo "Running: ${cmd[*]}"
"${cmd[@]}"
