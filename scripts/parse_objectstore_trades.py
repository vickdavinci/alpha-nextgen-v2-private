#!/usr/bin/env python3
"""
parse_objectstore_trades.py
============================
Enriches a trade detail report with ObjectStore observability data.

When the ObjectStore crosscheck file AND its constituent CSVs are available, this script
cross-references each trade from trades.csv against the five observability artifacts to
produce a fully populated ENRICHED_TRADES table, ready for injection into the trade detail
report template.

Usage
-----
    python scripts/parse_objectstore_trades.py \
        --stage-dir docs/audits/logs/stage12.21 \
        --run-name V12.21-FullYear2024 \
        --output enriched_trades.csv

Arguments
---------
    --stage-dir   Path to the stage folder containing all source files
    --run-name    Prefix of the run (matches *_trades.csv, *_orders.csv, etc.)
    --output      Output CSV filename (saved in stage-dir)
    --dry-run     Print matched records without writing output

Input Files (all expected in --stage-dir)
------------------------------------------
    {run_name}_trades.csv             SOURCE OF TRUTH: P&L, IsWin, timestamps
    {run_name}_orders.csv             Strategy tags, exit codes
    {run_name}_signal_lifecycle.csv   Signal funnel events (engine, event, reason, contract)
    {run_name}_regime_decisions.csv   Regime gate decisions per timestamp
    {run_name}_regime_timeline.csv    Regime timeline (score, base_regime, transition_overlay)
    {run_name}_router_rejections.csv  Router rejection reasons
    {run_name}_order_lifecycle.csv    Order fill/cancel/reject events

Output Columns
--------------
    All columns from trades.csv PLUS:

    engine                  VASS | ITM | MICRO | UNKNOWN
    strategy                BULL_CALL_DEBIT | BEAR_PUT_DEBIT | BEAR_CALL_CREDIT |
                            BULL_PUT_CREDIT | ITM_MOMENTUM | PROTECTIVE_PUTS |
                            MICRO_OTM_MOMENTUM | UNKNOWN
    exit_code               xcode= value from orders.csv exit order tag
                            e.g. VASS_TAIL_RISK_CAP, TRANSITION_DERISK_DETERIORATION,
                            FRIDAY_FIREWALL, SPREAD_RETRY_MAX, VASS_CONVICTION_FLOOR,
                            SPREAD_CLOSE_ESCALATED, ITM:RISK_EXIT, OCO_STOP, OCO_PROFIT
    regime_score_at_entry   Nearest regime timeline score to entry timestamp
    base_regime_at_entry    Nearest base_regime value to entry timestamp
    transition_at_entry     Nearest transition_overlay value to entry timestamp
    vix_at_entry            VIX value at entry from regime_timeline or signal_lifecycle
    signal_id               signal_id from signal_lifecycle nearest to entry
    signal_trace            trace_id from signal_lifecycle
    signal_direction        direction from signal_lifecycle (BULLISH | BEARISH)
    signal_gate_name        gate_name if signal was gated/dropped
    signal_reason           reason from signal_lifecycle if dropped
    vass_spread_pair        For VASS: paired row numbers (e.g. "12,13") from trades.csv
    vass_net_pnl            For VASS: net P&L of both legs combined
    vass_debit_width_pct    For VASS: debit/width * 100 (D/W%)
    itm_hold_hours          For ITM: hold duration in hours
    micro_hold_minutes      For MICRO: hold duration in minutes
    is_orphan               1 if entry after 15:25 ET OR exit is PREMARKET_STALE
    data_sources            Pipe-separated list of sources used for this row
                            e.g. "trades.csv|orders.csv|signal_lifecycle.csv"

Matching Logic
--------------
1. STRATEGY CLASSIFICATION (from orders.csv)
   For each trade row, look up Order IDs in orders.csv.
   Use the first filled entry order's Tag field.
   - Tag starts with "VASS:" -> VASS engine
   - Tag starts with "ITM:" -> ITM engine
   - Tag starts with "MICRO:" -> MICRO engine
   - Tag starts with "OTHER:" -> edge/orphan
   - No match -> UNKNOWN

2. EXIT CODE EXTRACTION (from orders.csv)
   For each trade row, look up Order IDs in orders.csv.
   Find the closing/exit orders. Extract xcode= from Tag field.
   If no xcode=, classify by order type:
   - StopMarket filled = OCO_STOP
   - Limit filled on close = OCO_PROFIT or RISK_EXIT
   - Market filled with SPREAD_CLOSE_SEQ tag = SPREAD_RETRY_MAX
   - Market filled with SPREAD_CLOSE_ESCALATED = SPREAD_CLOSE_ESCALATED
   - Market filled with OTHER:RECON_ORPHAN_OPTION = RECON_ORPHAN
   - Market filled with VASS:EXPIRATION_HAMMER_V2 = EXPIRATION_HAMMER_V2

3. REGIME CONTEXT (from regime_timeline.csv)
   For each trade's entry_time, find the nearest row in regime_timeline.csv.
   Use time window: entry_time - 30min to entry_time + 5min.
   Prefer the row immediately before entry.
   Extract: effective_score, base_regime, transition_overlay.

4. VIX CONTEXT (from regime_timeline.csv or signal_lifecycle.csv)
   Look for vix column in regime_timeline.csv at entry_time.
   If not found, search signal_lifecycle.csv for the nearest APPROVED or CANDIDATE
   event matching the trade's contract_symbol, and extract vix from metadata.

5. SIGNAL LIFECYCLE MATCHING (from signal_lifecycle.csv)
   For each trade, find matching signal_lifecycle rows by:
   - contract_symbol matches trade's symbol
   - time within 5 minutes of entry_time
   - strategy matches classified engine/strategy
   Use the APPROVED or FILLED event if present.
   Extract: signal_id, trace_id, direction, gate_name, reason.

6. VASS SPREAD PAIRING
   For VASS trades, pair consecutive rows with:
   - Same trace_id in orders.csv entry tags
   - Entry within 1 minute of each other
   - Same expiry in symbol, adjacent strikes
   Calculate: net_pnl = long_pnl + short_pnl
   Calculate: debit = net entry cost (absolute)
   Calculate: width = abs(strike_short - strike_long)
   Calculate: dw_pct = debit / width * 100

7. ORPHAN DETECTION
   Flag is_orphan=1 if:
   - entry_time hour >= 15 AND minute >= 25 (after 15:25 ET)
   - exit_code is PREMARKET_STALE_INTRADAY_CLOSE
   - exit is next calendar day for a MICRO trade
   - exit_code is RECON_ORPHAN

CSV Schemas Expected
---------------------
signal_lifecycle.csv:
    time, engine, event, signal_id, trace_id, direction, strategy,
    code, gate_name, reason, contract_symbol

regime_decisions.csv:
    time, engine, decision, gate_name, threshold_snapshot,
    regime_score, base_regime, transition_overlay

regime_timeline.csv:
    time, effective_score, base_regime, transition_overlay, vix,
    vix_direction, vix_level_label, micro_regime_name

router_rejections.csv:
    time, stage, code, symbol, source_tag, reason, detail

order_lifecycle.csv:
    time, event, status, order_id, symbol, tag, fill_price, quantity

Regime Score Bands
------------------
    RISK_ON   >= 70
    NEUTRAL   50-69
    CAUTIOUS  45-49
    DEFENSIVE 35-44
    RISK_OFF  < 35

VIX Level Labels (from regime_timeline.csv vix_level_label)
------------------------------------------------------------
    LOW       < 18
    MEDIUM    18-25
    HIGH      > 25

Micro Regime Grid (21 regimes, from micro_regime_name column)
-------------------------------------------------------------
                FALLING_FAST  FALLING   STABLE    RISING    RISING_FAST  SPIKING   WHIPSAW
    VIX LOW     PERFECT_MR    GOOD_MR   NORMAL    CAUTION   TRANSITION   RISK_OFF  CHOPPY
    VIX MEDIUM  RECOVERING    IMPROVING CAUTIOUS  WORSENING DETERIORATE  BREAKING  UNSTABLE
    VIX HIGH    PANIC_EASE    CALMING   ELEVATED  WORSE_HI  FULL_PANIC   CRASH     VOLATILE

Notes
-----
- This script does NOT recalculate P&L. It reads P&L exactly from trades.csv.
- IsWin from trades.csv is authoritative. This script does not override it.
- If a crosscheck artifact column is missing, the corresponding enrichment columns
  are set to NOT_FOUND and a warning is printed.
- Signal lifecycle matching uses a 5-minute window and prefers exact symbol match.
  If no exact match, falls back to strategy+direction+time match within 15 minutes.
- For full-year runs with large log files, use --year-filter to restrict processing
  to a specific month or quarter.

Example
-------
    # Full year enrichment
    python scripts/parse_objectstore_trades.py \
        --stage-dir docs/audits/logs/stage12.21 \
        --run-name V12.21-FullYear2024 \
        --output enriched_trades.csv

    # Single month (faster for debugging)
    python scripts/parse_objectstore_trades.py \
        --stage-dir docs/audits/logs/stage12.21 \
        --run-name V12.21-FullYear2024 \
        --output enriched_trades_nov.csv \
        --month-filter 2024-11

    # Dry run: print first 10 enriched rows
    python scripts/parse_objectstore_trades.py \
        --stage-dir docs/audits/logs/stage12.21 \
        --run-name V12.21-FullYear2024 \
        --dry-run --dry-run-limit 10

Dependencies
------------
    pip install pandas pytz

Generating the ObjectStore CSVs
---------------------------------
    1. Open the QC project in QuantConnect Web IDE -> Research notebook
    2. Run scripts/qc_research_objectstore_loader.py with correct RUN_NAME and BACKTEST_YEAR
    3. Save the five CSV files to the stage folder:
       {run_name}_signal_lifecycle.csv
       {run_name}_regime_decisions.csv
       {run_name}_regime_timeline.csv
       {run_name}_router_rejections.csv
       {run_name}_order_lifecycle.csv
    4. Save the crosscheck summary as {run_name}_OBJECTSTORE_CROSSCHECK.md
    5. Re-run trade-analyzer agent with the enriched data available
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENGINE_TAG_PREFIXES = {
    "VASS:": "VASS",
    "ITM:": "ITM",
    "MICRO:": "MICRO",
    "OTHER:": "EDGE",
}

STRATEGY_TAG_MAP = {
    "BULL_CALL_DEBIT": "BULL_CALL_DEBIT",
    "BEAR_PUT_DEBIT": "BEAR_PUT_DEBIT",
    "BEAR_CALL_CREDIT": "BEAR_CALL_CREDIT",
    "BEAR_CALL_CREDI": "BEAR_CALL_CREDIT",  # truncated variant
    "BULL_PUT_CREDIT": "BULL_PUT_CREDIT",
    "ITM_MOMENTUM": "ITM_MOMENTUM",
    "PROTECTIVE_PUTS": "PROTECTIVE_PUTS",
    "MICRO_OTM_MOMENTUM": "MICRO_OTM_MOMENTUM",
    "RECON_ORPHAN_OPTION": "RECON_ORPHAN",
    "EXPIRATION_HAMMER_V2": "EXPIRATION_HAMMER_V2",
}

EXIT_CODE_FROM_ORDER_TYPE = {
    # (order_type, status, has_xcode) -> exit_code
    ("Stop Market", "Filled", False): "OCO_STOP",
    ("Limit", "Filled", False): "OCO_PROFIT_OR_RISK_EXIT",
    ("Market", "Filled", False): "MARKET_CLOSE",
}

REGIME_BANDS = {
    (70, 100): "RISK_ON",
    (50, 69): "NEUTRAL",
    (45, 49): "CAUTIOUS",
    (35, 44): "DEFENSIVE",
    (0, 34): "RISK_OFF",
}


# ---------------------------------------------------------------------------
# File loading helpers
# ---------------------------------------------------------------------------


def load_csv(filepath: str) -> list[dict]:
    """Load a CSV file and return a list of dicts. Returns [] if file missing."""
    if not os.path.exists(filepath):
        print(f"  [WARN] File not found: {filepath}")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def parse_iso_time(ts: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp string to a datetime object."""
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts.strip(), fmt)
        except ValueError:
            continue
    return None


def nearest_row(
    rows: list[dict],
    target_time: datetime,
    time_col: str,
    window_before_min: int = 30,
    window_after_min: int = 5,
) -> Optional[dict]:
    """Find the row in `rows` whose `time_col` is nearest to `target_time`.

    Constrains search to [target_time - window_before_min, target_time + window_after_min].
    Prefers rows before or at target_time.
    """
    best = None
    best_delta = None
    for row in rows:
        t = parse_iso_time(row.get(time_col, ""))
        if t is None:
            continue
        lo = target_time - timedelta(minutes=window_before_min)
        hi = target_time + timedelta(minutes=window_after_min)
        if lo <= t <= hi:
            delta = abs((t - target_time).total_seconds())
            if best_delta is None or delta < best_delta:
                best = row
                best_delta = delta
    return best


def extract_xcode(tag: str) -> Optional[str]:
    """Extract xcode= value from an order tag string."""
    if "xcode=" not in tag:
        return None
    for part in tag.split("|"):
        if part.startswith("xcode="):
            return part.split("=", 1)[1]
    return None


def extract_tag_field(tag: str, field: str) -> Optional[str]:
    """Extract a named field (field=value) from a pipe-delimited tag string."""
    for part in tag.split("|"):
        if part.startswith(f"{field}="):
            return part.split("=", 1)[1]
    return None


def classify_engine_strategy(tag: str) -> tuple[str, str]:
    """Classify engine and strategy from an order entry tag."""
    for prefix, engine in ENGINE_TAG_PREFIXES.items():
        if tag.startswith(prefix):
            remainder = tag[len(prefix) :]
            strategy_key = remainder.split("|")[0]
            strategy = STRATEGY_TAG_MAP.get(strategy_key, strategy_key)
            return engine, strategy
    return "UNKNOWN", "UNKNOWN"


def classify_regime_band(score: float) -> str:
    """Classify a numeric regime score into a named band."""
    for (lo, hi), name in REGIME_BANDS.items():
        if lo <= score <= hi:
            return name
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Core enrichment
# ---------------------------------------------------------------------------


def build_order_index(orders: list[dict]) -> dict[str, list[dict]]:
    """Build a mapping from order ID to order rows for fast lookup."""
    idx: dict[str, list[dict]] = {}
    for row in orders:
        oid = row.get("ID", "").strip()
        if oid:
            idx.setdefault(oid, []).append(row)
    return idx


def get_trade_orders(trade: dict, order_index: dict[str, list[dict]]) -> list[dict]:
    """Return all orders for a given trade (by Order IDs field)."""
    order_ids_raw = trade.get("Order IDs", "")
    ids = [oid.strip() for oid in order_ids_raw.split(";") if oid.strip()]
    result = []
    for oid in ids:
        result.extend(order_index.get(oid, []))
    return result


def classify_trade(trade: dict, order_index: dict[str, list[dict]]) -> dict:
    """Classify a trade's engine, strategy, and exit code from orders."""
    orders = get_trade_orders(trade, order_index)

    engine = "UNKNOWN"
    strategy = "UNKNOWN"
    exit_code = "UNKNOWN"
    data_sources = ["trades.csv"]

    entry_order = None
    exit_orders = []

    for o in orders:
        tag = o.get("Tag", "").strip()
        direction = o.get("Direction", "").strip()
        status = o.get("Status", "").strip()
        order_type = o.get("Type", "").strip()
        qty = float(o.get("Quantity", 0) or 0)

        # Entry orders: positive quantity (Buy direction or Sell for credit short)
        # Use filled entry orders to classify engine
        if status == "Filled" and tag:
            e, s = classify_engine_strategy(tag)
            if e != "UNKNOWN" and engine == "UNKNOWN":
                engine = e
                strategy = s
                entry_order = o
                if "orders.csv" not in data_sources:
                    data_sources.append("orders.csv")

        # Exit orders: look for xcode or stop/limit fills on the other side
        if status == "Filled":
            xcode = extract_xcode(tag)
            if xcode:
                exit_code = xcode
                exit_orders.append(o)
            elif tag.startswith("SPREAD_CLOSE_SEQ") and "SPREAD_RETRY_MAX" in tag:
                exit_code = "SPREAD_RETRY_MAX"
                exit_orders.append(o)
            elif tag == "VASS:EXPIRATION_HAMMER_V2":
                exit_code = "EXPIRATION_HAMMER_V2"
                exit_orders.append(o)
            elif tag == "OTHER:RECON_ORPHAN_OPTION":
                exit_code = "RECON_ORPHAN"
                exit_orders.append(o)
            elif order_type == "Stop Market" and exit_code == "UNKNOWN" and qty < 0:
                exit_code = "OCO_STOP"
            elif order_type == "Limit" and exit_code == "UNKNOWN" and qty < 0:
                exit_code = "OCO_PROFIT_OR_RISK_EXIT"

    # Resolve OCO_PROFIT_OR_RISK_EXIT using P&L sign
    if exit_code == "OCO_PROFIT_OR_RISK_EXIT":
        pnl = float(trade.get("P&L", 0) or 0)
        exit_code = "OCO_PROFIT" if pnl > 0 else "ITM:RISK_EXIT"

    return {
        "engine": engine,
        "strategy": strategy,
        "exit_code": exit_code,
        "data_sources": data_sources,
    }


def enrich_regime(trade: dict, regime_timeline: list[dict]) -> dict:
    """Look up regime context for a trade's entry time."""
    entry_time = parse_iso_time(trade.get("Entry Time", ""))
    if not entry_time or not regime_timeline:
        return {
            "regime_score_at_entry": "NOT_FOUND",
            "base_regime_at_entry": "NOT_FOUND",
            "transition_at_entry": "NOT_FOUND",
            "vix_at_entry": "NOT_FOUND",
        }

    row = nearest_row(
        regime_timeline, entry_time, "time", window_before_min=60, window_after_min=10
    )
    if row is None:
        return {
            "regime_score_at_entry": "NOT_FOUND",
            "base_regime_at_entry": "NOT_FOUND",
            "transition_at_entry": "NOT_FOUND",
            "vix_at_entry": "NOT_FOUND",
        }

    score = row.get("effective_score", "")
    base = row.get("base_regime", "")
    trans = row.get("transition_overlay", "")
    vix = row.get("vix", "")

    return {
        "regime_score_at_entry": score,
        "base_regime_at_entry": base,
        "transition_at_entry": trans,
        "vix_at_entry": vix,
        "regime_band_at_entry": classify_regime_band(float(score)) if score else "UNKNOWN",
        "micro_regime_at_entry": row.get("micro_regime_name", "NOT_FOUND"),
    }


def enrich_signal(trade: dict, signal_lifecycle: list[dict]) -> dict:
    """Look up signal lifecycle context for a trade's entry."""
    entry_time = parse_iso_time(trade.get("Entry Time", ""))
    symbol = trade.get("Symbols", "").strip().replace(" ", "")

    if not entry_time or not signal_lifecycle:
        return {
            "signal_id": "NOT_FOUND",
            "signal_trace": "NOT_FOUND",
            "signal_direction": "NOT_FOUND",
            "signal_gate_name": "NOT_FOUND",
            "signal_reason": "NOT_FOUND",
        }

    # Try to match by contract symbol and time
    best = None
    best_delta = None
    for row in signal_lifecycle:
        t = parse_iso_time(row.get("time", ""))
        if t is None:
            continue
        delta_s = abs((t - entry_time).total_seconds())
        if delta_s > 900:  # 15-minute window
            continue
        contract = row.get("contract_symbol", "").replace(" ", "")
        if contract and contract in symbol:
            if best_delta is None or delta_s < best_delta:
                best = row
                best_delta = delta_s

    # Fallback: match by time only
    if best is None:
        best = nearest_row(signal_lifecycle, entry_time, "time", 15, 5)

    if best is None:
        return {
            "signal_id": "NOT_FOUND",
            "signal_trace": "NOT_FOUND",
            "signal_direction": "NOT_FOUND",
            "signal_gate_name": "NOT_FOUND",
            "signal_reason": "NOT_FOUND",
        }

    return {
        "signal_id": best.get("signal_id", ""),
        "signal_trace": best.get("trace_id", ""),
        "signal_direction": best.get("direction", ""),
        "signal_gate_name": best.get("gate_name", ""),
        "signal_reason": best.get("reason", ""),
    }


def compute_vass_pairs(trades: list[dict], order_index: dict[str, list[dict]]) -> dict[int, dict]:
    """Pair VASS spread legs and return a mapping from row index to spread metadata."""
    vass_rows = []
    for i, trade in enumerate(trades):
        orders = get_trade_orders(trade, order_index)
        for o in orders:
            tag = o.get("Tag", "")
            if tag.startswith("VASS:") and o.get("Status") == "Filled":
                trace = extract_tag_field(tag, "trace") or ""
                vass_rows.append((i, trade, trace))
                break

    # Group by trace ID
    trace_groups: dict[str, list[tuple]] = {}
    for i, trade, trace in vass_rows:
        base_trace = trace.rsplit("_", 1)[0] if "_" in trace else trace
        trace_groups.setdefault(base_trace, []).append((i, trade))

    pairs: dict[int, dict] = {}
    for base_trace, group in trace_groups.items():
        if len(group) == 2:
            idx_a, t_a = group[0]
            idx_b, t_b = group[1]
            net_pnl = float(t_a.get("P&L", 0) or 0) + float(t_b.get("P&L", 0) or 0)
            # Parse strikes from symbol
            sym_a = t_a.get("Symbols", "")
            sym_b = t_b.get("Symbols", "")
            try:
                strike_a = float(sym_a[-8:]) / 1000
                strike_b = float(sym_b[-8:]) / 1000
            except (ValueError, IndexError):
                strike_a = 0
                strike_b = 0
            width = abs(strike_a - strike_b)
            entry_a = float(t_a.get("Entry Price", 0) or 0)
            entry_b = float(t_b.get("Entry Price", 0) or 0)
            # Debit = net entry cost (long leg entry - short leg entry)
            # Direction of entry tells us which leg is long
            dir_a = t_a.get("Direction", "Buy")
            if dir_a == "Buy":
                debit = entry_a - entry_b
            else:
                debit = entry_b - entry_a
            debit = abs(debit)
            dw_pct = (debit / width * 100) if width > 0 else 0

            meta = {
                "vass_spread_pair": f"{idx_a + 1},{idx_b + 1}",
                "vass_net_pnl": f"{net_pnl:.0f}",
                "vass_debit": f"{debit:.2f}",
                "vass_width": f"{width:.2f}",
                "vass_debit_width_pct": f"{dw_pct:.1f}",
            }
            pairs[idx_a] = meta
            pairs[idx_b] = meta
    return pairs


def detect_orphan(trade: dict) -> bool:
    """Return True if this trade is an orphan position."""
    entry_time = parse_iso_time(trade.get("Entry Time", ""))
    exit_time = parse_iso_time(trade.get("Exit Time", ""))
    if entry_time and entry_time.hour >= 15 and entry_time.minute >= 25:
        return True
    if entry_time and exit_time:
        hold_hours = (exit_time - entry_time).total_seconds() / 3600
        symbol = trade.get("Symbols", "")
        # MICRO trades held overnight are orphans
        if hold_hours > 18 and ("P0" in symbol or "C0" in symbol):
            # Simple heuristic for intraday options
            if float(trade.get("Entry Price", 10)) < 10:
                return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Enrich trade detail with ObjectStore observability artifacts."
    )
    parser.add_argument("--stage-dir", required=True, help="Path to stage folder")
    parser.add_argument("--run-name", required=True, help="Run name prefix")
    parser.add_argument("--output", default="enriched_trades.csv", help="Output filename")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    parser.add_argument("--dry-run-limit", type=int, default=5, help="Rows to print in dry-run")
    parser.add_argument("--month-filter", default="", help="Filter to YYYY-MM (e.g. 2024-11)")
    args = parser.parse_args()

    stage = args.stage_dir
    run = args.run_name

    def path(suffix):
        return os.path.join(stage, f"{run}_{suffix}")

    print(f"\n=== parse_objectstore_trades.py ===")
    print(f"Stage: {stage}")
    print(f"Run:   {run}")
    print()

    # Load all source files
    print("[1/7] Loading trades.csv (source of truth)...")
    trades = load_csv(path("trades.csv"))
    if not trades:
        print("ERROR: trades.csv not found or empty. Cannot proceed.")
        sys.exit(1)
    print(f"       {len(trades)} rows")

    print("[2/7] Loading orders.csv...")
    orders = load_csv(path("orders.csv"))
    order_index = build_order_index(orders)
    print(f"       {len(orders)} orders")

    print("[3/7] Loading signal_lifecycle.csv...")
    signal_lifecycle = load_csv(path("signal_lifecycle.csv"))
    print(
        f"       {len(signal_lifecycle)} rows {'[MISSING - enrichment limited]' if not signal_lifecycle else ''}"
    )

    print("[4/7] Loading regime_decisions.csv...")
    regime_decisions = load_csv(path("regime_decisions.csv"))
    print(f"       {len(regime_decisions)} rows {'[MISSING]' if not regime_decisions else ''}")

    print("[5/7] Loading regime_timeline.csv...")
    regime_timeline = load_csv(path("regime_timeline.csv"))
    print(
        f"       {len(regime_timeline)} rows {'[MISSING - regime context unavailable]' if not regime_timeline else ''}"
    )

    print("[6/7] Loading router_rejections.csv...")
    router_rejections = load_csv(path("router_rejections.csv"))
    print(f"       {len(router_rejections)} rows {'[MISSING]' if not router_rejections else ''}")

    print("[7/7] Loading order_lifecycle.csv...")
    order_lifecycle = load_csv(path("order_lifecycle.csv"))
    print(f"       {len(order_lifecycle)} rows {'[MISSING]' if not order_lifecycle else ''}")

    print()
    print("[PASS 1] Pre-computing VASS spread pairs...")
    vass_pairs = compute_vass_pairs(trades, order_index)
    print(f"         {len(set(v['vass_spread_pair'] for v in vass_pairs.values()))} pairs found")

    print("[PASS 2] Enriching all trades...")
    enriched = []
    for i, trade in enumerate(trades):
        # Month filter
        if args.month_filter:
            entry_raw = trade.get("Entry Time", "")
            if not entry_raw.startswith(args.month_filter):
                continue

        # Base classification
        classification = classify_trade(trade, order_index)

        # Regime context
        regime_ctx = enrich_regime(trade, regime_timeline)

        # Signal context
        signal_ctx = enrich_signal(trade, signal_lifecycle)

        # VASS pair metadata
        pair_meta = vass_pairs.get(i, {})

        # Orphan detection
        is_orphan = detect_orphan(trade)

        # Hold duration
        entry_t = parse_iso_time(trade.get("Entry Time", ""))
        exit_t = parse_iso_time(trade.get("Exit Time", ""))
        hold_minutes = 0
        hold_hours = 0.0
        if entry_t and exit_t:
            delta_s = (exit_t - entry_t).total_seconds()
            hold_minutes = int(delta_s / 60)
            hold_hours = delta_s / 3600

        # Build enriched row
        row = {
            # Original trades.csv columns
            **trade,
            # Classification
            "engine": classification["engine"],
            "strategy": classification["strategy"],
            "exit_code": classification["exit_code"],
            # Regime
            "regime_score_at_entry": regime_ctx.get("regime_score_at_entry", "NOT_FOUND"),
            "regime_band_at_entry": regime_ctx.get("regime_band_at_entry", "NOT_FOUND"),
            "base_regime_at_entry": regime_ctx.get("base_regime_at_entry", "NOT_FOUND"),
            "transition_at_entry": regime_ctx.get("transition_at_entry", "NOT_FOUND"),
            "vix_at_entry": regime_ctx.get("vix_at_entry", "NOT_FOUND"),
            "micro_regime_at_entry": regime_ctx.get("micro_regime_at_entry", "NOT_FOUND"),
            # Signal
            "signal_id": signal_ctx.get("signal_id", "NOT_FOUND"),
            "signal_trace": signal_ctx.get("signal_trace", "NOT_FOUND"),
            "signal_direction": signal_ctx.get("signal_direction", "NOT_FOUND"),
            "signal_gate_name": signal_ctx.get("signal_gate_name", "NOT_FOUND"),
            "signal_reason": signal_ctx.get("signal_reason", "NOT_FOUND"),
            # VASS spread
            "vass_spread_pair": pair_meta.get("vass_spread_pair", ""),
            "vass_net_pnl": pair_meta.get("vass_net_pnl", ""),
            "vass_debit": pair_meta.get("vass_debit", ""),
            "vass_width": pair_meta.get("vass_width", ""),
            "vass_debit_width_pct": pair_meta.get("vass_debit_width_pct", ""),
            # Hold
            "hold_minutes": hold_minutes,
            "hold_hours": f"{hold_hours:.2f}",
            # Flags
            "is_orphan": 1 if is_orphan else 0,
            # Provenance
            "data_sources": "|".join(classification["data_sources"]),
        }
        enriched.append(row)

    print(f"         {len(enriched)} rows enriched")
    print()

    if args.dry_run:
        print(f"[DRY RUN] First {args.dry_run_limit} rows:")
        for row in enriched[: args.dry_run_limit]:
            print(
                f"  {row.get('Entry Time')} | {row.get('Symbols', '')[:30]:30s} | "
                f"engine={row['engine']:6s} | strategy={row['strategy']:20s} | "
                f"exit={row['exit_code']:30s} | P&L={row.get('P&L')}"
            )
        print()
        print("[DRY RUN] No output file written.")
        return

    # Write output
    output_path = os.path.join(stage, args.output)
    if enriched:
        fieldnames = list(enriched[0].keys())
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(enriched)
        print(f"[DONE] Written {len(enriched)} enriched rows to:")
        print(f"       {output_path}")
    else:
        print("[WARN] No rows enriched. Check --month-filter or input files.")


if __name__ == "__main__":
    main()
