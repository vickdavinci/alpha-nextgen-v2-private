#!/usr/bin/env python3
"""Validate that a minified lean workspace remains executable and telemetry-capable.

Usage:
  python3 scripts/validate_lean_minified.py --root ../lean-workspace/AlphaNextGen
"""

from __future__ import annotations

import argparse
import py_compile
import sys
from pathlib import Path

REQUIRED_FILES = [
    "main.py",
    "main_options_mixin.py",
    "main_reconcile_mixin.py",
    "main_intraday_close_mixin.py",
    "main_risk_monitor_mixin.py",
    "engines/satellite/options_engine.py",
    "engines/satellite/options_micro_signal.py",
    "engines/satellite/options_position_manager.py",
    "engines/satellite/options_primitives.py",
    "engines/satellite/vass_signal_validator.py",
    "engines/satellite/vass_exit_evaluator.py",
    "engines/satellite/vass_entry_engine.py",
    "portfolio/portfolio_router.py",
    "config.py",
]

REQUIRED_SUBSTRINGS = {
    "main.py": [
        "VASS_REJECTION",
        "INTRADAY_DTE_ROUTING",
        "ORDER_LIFECYCLE_CAP_HIT",
        "INTRADAY_SIGNAL_DROPPED",
        "INTRADAY_ROUTER_REJECTED",
        "MICRO_DTE_DIAG_SUMMARY",
    ],
    "main_options_mixin.py": [
        "MICRO_UPDATE:",
        "_record_micro_drop_reason_dte",
    ],
    "main_reconcile_mixin.py": [
        "RECON_ORPHAN_CLOSE_SUBMITTED",
        "RECON_ORPHAN_GUARD_HOLD",
    ],
    "main_intraday_close_mixin.py": [
        "INTRADAY_FORCE_EXIT",
    ],
    "main_risk_monitor_mixin.py": [
        "OCO_CANCEL",
    ],
    "engines/satellite/vass_entry_engine.py": [
        "VASS_SKIPPED",
        "VASS_REJECTION",
    ],
    "engines/satellite/options_engine.py": [
        "should_log_vass_rejection",
        "_last_micro_no_trade_log_by_key",
        "WIN_RATE_GATE:",
    ],
    "engines/satellite/options_micro_signal.py": [
        "MICRO_NO_TRADE",
    ],
    "engines/satellite/options_position_manager.py": [
        "SPREAD: POSITION_REGISTERED",
        "SPREAD: POSITION_REMOVED",
    ],
    "engines/satellite/options_primitives.py": [
        "MIN_MOVE_",
    ],
    "engines/satellite/vass_signal_validator.py": [
        "SPREAD: ENTRY_SIGNAL",
        "CREDIT_SPREAD: ENTRY_SIGNAL",
    ],
    "engines/satellite/vass_exit_evaluator.py": [
        "SPREAD_EXIT_GUARD_HOLD",
        "SPREAD: EXIT_SIGNAL",
    ],
    "portfolio/portfolio_router.py": [
        "R_CLOSE_NO_QTY",
        "ROUTER: CLOSE_QTY_INFERRED",
        "R_DUPLICATE_ORDER",
        "R_NO_PRICE",
        "MARGIN_TRACK:",
    ],
    "config.py": [
        "MICRO_DTE_DIAG_LOG_INTERVAL_MIN",
        "MICRO_DTE_DIAG_LOG_BACKTEST_ENABLED",
        "VASS_LOG_REJECTION_INTERVAL_MINUTES",
        "MICRO_NO_TRADE_LOG_INTERVAL_MINUTES",
        "MICRO_UPDATE_LOG_BACKTEST_ENABLED",
        "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED",
        "LOG_ORDER_LIFECYCLE_MAX_PER_DAY",
    ],
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"OK:   {msg}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default="../lean-workspace/AlphaNextGen",
        help="Path to minified lean workspace root",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail fast on first missing marker/file/compile error",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        fail(f"root does not exist: {root}")
        return 2

    errors = 0

    # 1) Required files exist
    for rel in REQUIRED_FILES:
        fp = root / rel
        if fp.exists():
            ok(f"exists: {rel}")
        else:
            fail(f"missing required file: {rel}")
            errors += 1
            if args.strict:
                print("\nRESULT: FAIL (strict mode)")
                return 1

    # 2) Python compile sanity
    for rel in REQUIRED_FILES:
        fp = root / rel
        if not fp.exists():
            continue
        try:
            py_compile.compile(str(fp), doraise=True)
            ok(f"compiles: {rel}")
        except Exception as exc:
            fail(f"compile error in {rel}: {exc}")
            errors += 1
            if args.strict:
                print("\nRESULT: FAIL (strict mode)")
                return 1

    # 3) Telemetry markers survived minification
    for rel, markers in REQUIRED_SUBSTRINGS.items():
        fp = root / rel
        if not fp.exists():
            continue
        text = fp.read_text(encoding="utf-8", errors="ignore")
        for marker in markers:
            if marker in text:
                ok(f"marker present: {rel} :: {marker}")
            else:
                fail(f"marker missing: {rel} :: {marker}")
                errors += 1
                if args.strict:
                    print("\nRESULT: FAIL (strict mode)")
                    return 1

    if errors:
        print(f"\nRESULT: {errors} issue(s) found")
        return 1

    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
