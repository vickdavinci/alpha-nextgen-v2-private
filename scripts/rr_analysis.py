#!/usr/bin/env python3
"""
R:R Root Cause Analysis Script
Parses V9.1 backtest logs to extract per-trade lifecycle records
and compute R:R metrics across all options trade types.
"""

import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ── Log patterns ──────────────────────────────────────────────────────

# VASS Debit Entry: SPREAD: ENTRY_SIGNAL | BULL_CALL: Regime=61 | VIX=17.2 | Long=382.0 Short=386.0 | Debit=$3.05 MaxProfit=$0.95 | x19 | DTE=45 Score=3.02
RE_VASS_DEBIT_ENTRY = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) SPREAD: ENTRY_SIGNAL \| (\w+): Regime=(\d+) \| VIX=([\d.]+) \| "
    r"Long=([\d.]+) Short=([\d.]+) \| Debit=\$([\d.]+) MaxProfit=\$([\d.]+) \| x(\d+) \| DTE=(\d+) Score=([\d.]+)"
)

# VASS Credit Entry: CREDIT_SPREAD: ENTRY_SIGNAL | BEAR_CALL_CREDIT: Regime=43 | VIX=28.9 | Sell 352.0 Buy 357.0 | Credit=$2.22 Width=$5 | x12 | DTE=38 Score=2.31 | MaxProfit=$2664 MaxLoss=$3336
RE_VASS_CREDIT_ENTRY = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) CREDIT_SPREAD: ENTRY_SIGNAL \| (\w+): Regime=(\d+) \| VIX=([\d.]+) \| "
    r"Sell ([\d.]+) Buy ([\d.]+) \| Credit=\$([\d.]+) Width=\$(\d+) \| x(\d+) \| DTE=(\d+) Score=([\d.]+) \| "
    r"MaxProfit=\$(\d+) MaxLoss=\$(\d+)"
)

# VASS Position Registered
RE_VASS_REGISTERED = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) SPREAD: POSITION_REGISTERED \| (\w+) \| "
    r"Long=([\d.]+) @ \$([\d.]+) \| Short=([\d.]+) @ \$([\d.]+) \| Net Debit=\$([-\d.]+) \| "
    r"Max Profit=\$([-\d.]+) \| x(\d+) \| Target=\$([-\d.]+)"
)

# VASS Exit Signal: SPREAD: EXIT_SIGNAL | <REASON> ... | P&L=X%
RE_VASS_EXIT = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) SPREAD: EXIT_SIGNAL \| (.+?) \| P&L=([-\d.]+)%"
)

# MICRO Entry: INTRADAY_SIGNAL_APPROVED: ... | Direction=X | Strategy=Y | Contract=Z
RE_MICRO_ENTRY = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) INTRADAY_SIGNAL_APPROVED: (.+?) \| Direction=(\w+) \| Strategy=(\w+) \| Contract=(.+)"
)

# MICRO Result: INTRADAY_RESULT: WIN|LOSS | Entry=$A | Exit=$B | P&L=C%
RE_MICRO_RESULT = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) INTRADAY_RESULT: (\w+) \| Entry=\$([\d.]+) \| Exit=\$([\d.]+) \| P&L=([-\d.]+)%"
)

# Regime refresh: REGIME_REFRESH_INTRADAY: Score=56.5 | Time=09:35
RE_REGIME = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) REGIME_REFRESH_INTRADAY: Score=([\d.]+)"
)


def classify_regime(score):
    if score >= 70:
        return "RISK_ON"
    elif score >= 50:
        return "NEUTRAL"
    elif score >= 45:
        return "CAUTIOUS"
    elif score >= 35:
        return "DEFENSIVE"
    else:
        return "RISK_OFF"


def classify_micro_exit(pnl_pct, strategy):
    """Classify MICRO exit type based on P&L percentage."""
    if strategy == "ITM_MOMENTUM":
        target = 35.0
        stop = -28.0  # approx -28% to -35%
    elif strategy == "DEBIT_MOMENTUM":
        target = 60.0
        stop = -28.0
    elif strategy == "DEBIT_FADE":
        target = 60.0
        stop = -28.0
    elif strategy == "PROTECTIVE_PUTS":
        target = 60.0
        stop = -35.0
    else:
        target = 60.0
        stop = -28.0

    if pnl_pct >= target * 0.9:  # within 10% of target
        return "TARGET_HIT"
    elif pnl_pct <= stop * 0.85:  # near or beyond stop level
        return "STOP_HIT"
    else:
        return "TIME_EXIT"


def classify_vass_exit(reason_text):
    """Classify VASS exit type from reason text."""
    if "CREDIT_STOP_2X" in reason_text or "CREDIT_STOP_LOSS" in reason_text:
        return "CREDIT_STOP_LOSS"
    elif "SPREAD_TIME_STOP" in reason_text:
        return "TIME_STOP"
    elif "VIX_SPIKE_EXIT" in reason_text:
        return "VIX_SPIKE_EXIT"
    elif "REGIME_IMPROVEMENT" in reason_text:
        return "REGIME_EXIT"
    elif "PROFIT_TARGET" in reason_text:
        return "PROFIT_TARGET"
    elif "ATR_STOP" in reason_text:
        return "ATR_STOP"
    elif "SHORT_ITM" in reason_text:
        return "SHORT_ITM_EXIT"
    elif "DTE_EXIT" in reason_text or "DTE" in reason_text:
        return "DTE_EXIT"
    else:
        return f"OTHER({reason_text[:30]})"


def parse_hold_minutes_credit(entry_time_str, exit_time_str):
    """Parse hold time in minutes between entry and exit."""
    fmt = "%Y-%m-%d %H:%M:%S"
    entry = datetime.strptime(entry_time_str, fmt)
    exit_t = datetime.strptime(exit_time_str, fmt)
    return (exit_t - entry).total_seconds() / 60.0


def get_nearest_regime(regime_history, target_time_str):
    """Get regime score nearest to (but before) the target time."""
    fmt = "%Y-%m-%d %H:%M:%S"
    target = datetime.strptime(target_time_str, fmt)
    best_score = None
    best_dt = None
    for ts, score in regime_history:
        dt = datetime.strptime(ts, fmt)
        if dt <= target:
            if best_dt is None or dt > best_dt:
                best_dt = dt
                best_score = score
    return best_score


def parse_log_file(log_path):
    """Parse a single log file and return VASS trades, MICRO trades, and regime history."""
    vass_entries = []  # pending entry signals
    vass_registered = []  # pending registered (to match with entries)
    vass_trades = []  # completed trades
    micro_entries = []  # pending micro entries
    micro_trades = []  # completed micro trades
    regime_history = []  # [(timestamp, score)]

    # Track pending VASS entries for matching
    pending_vass = {}  # key: date string -> entry data
    pending_micro = []  # list of pending micro entries

    with open(log_path, "r") as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # ── Regime refresh ──
        m = RE_REGIME.search(line)
        if m:
            regime_history.append((m.group(1), float(m.group(2))))
            continue

        # ── VASS Debit Entry ──
        m = RE_VASS_DEBIT_ENTRY.search(line)
        if m:
            entry = {
                "timestamp": m.group(1),
                "date": m.group(1)[:10],
                "strategy": m.group(2),
                "regime": int(m.group(3)),
                "vix": float(m.group(4)),
                "long_strike": float(m.group(5)),
                "short_strike": float(m.group(6)),
                "planned_debit": float(m.group(7)),
                "planned_max_profit": float(m.group(8)),
                "contracts": int(m.group(9)),
                "dte": int(m.group(10)),
                "score": float(m.group(11)),
                "spread_type": "DEBIT",
                "width": abs(float(m.group(6)) - float(m.group(5))),
            }
            entry["planned_dw_ratio"] = (
                entry["planned_debit"] / entry["width"] if entry["width"] > 0 else 0
            )
            # Store by timestamp for matching with REGISTERED
            key = entry["timestamp"]
            pending_vass[key] = entry
            continue

        # ── VASS Credit Entry ──
        m = RE_VASS_CREDIT_ENTRY.search(line)
        if m:
            credit = float(m.group(7))
            width = float(m.group(8))
            entry = {
                "timestamp": m.group(1),
                "date": m.group(1)[:10],
                "strategy": m.group(2),
                "regime": int(m.group(3)),
                "vix": float(m.group(4)),
                "short_strike": float(m.group(5)),  # Sell strike
                "long_strike": float(m.group(6)),  # Buy strike
                "planned_credit": credit,
                "width": width,
                "contracts": int(m.group(9)),
                "dte": int(m.group(10)),
                "score": float(m.group(11)),
                "planned_max_profit": float(m.group(12)),
                "planned_max_loss": float(m.group(13)),
                "spread_type": "CREDIT",
                "planned_cw_ratio": credit / width if width > 0 else 0,
            }
            key = entry["timestamp"]
            pending_vass[key] = entry
            continue

        # ── VASS Position Registered ──
        m = RE_VASS_REGISTERED.search(line)
        if m:
            ts = m.group(1)
            reg = {
                "timestamp": ts,
                "strategy_name": m.group(2),
                "long_strike": float(m.group(3)),
                "long_price": float(m.group(4)),
                "short_strike": float(m.group(5)),
                "short_price": float(m.group(6)),
                "actual_net_debit": float(m.group(7)),
                "actual_max_profit": float(m.group(8)),
                "contracts": int(m.group(9)),
                "target": float(m.group(10)),
            }
            # Match with pending entry at same timestamp
            if ts in pending_vass:
                entry = pending_vass.pop(ts)
                entry["actual_net_debit"] = reg["actual_net_debit"]
                entry["actual_max_profit"] = reg["actual_max_profit"]
                entry["long_price"] = reg["long_price"]
                entry["short_price"] = reg["short_price"]
                entry["target"] = reg["target"]
                entry["strategy_name"] = reg["strategy_name"]
                # Calculate actual D/W for debit spreads
                if entry["spread_type"] == "DEBIT":
                    entry["actual_dw_ratio"] = (
                        entry["actual_net_debit"] / entry["width"] if entry["width"] > 0 else 0
                    )
                    entry["slippage_pct"] = (
                        (
                            (entry["actual_net_debit"] - entry["planned_debit"])
                            / entry["planned_debit"]
                            * 100
                        )
                        if entry["planned_debit"] > 0
                        else 0
                    )
                    entry["profit_erosion_pct"] = (
                        (
                            (entry["planned_max_profit"] - entry["actual_max_profit"])
                            / entry["planned_max_profit"]
                            * 100
                        )
                        if entry["planned_max_profit"] > 0
                        else 0
                    )
                else:  # CREDIT
                    actual_credit = abs(entry["actual_net_debit"])
                    entry["actual_cw_ratio"] = (
                        actual_credit / entry["width"] if entry["width"] > 0 else 0
                    )
                    entry["slippage_pct"] = (
                        ((entry["planned_credit"] - actual_credit) / entry["planned_credit"] * 100)
                        if entry["planned_credit"] > 0
                        else 0
                    )

                # Store as pending for exit matching
                # Use date as key to allow matching with exit on same or later date
                date_key = entry["date"]
                if date_key not in pending_vass:
                    pending_vass[date_key + "_active"] = entry
                else:
                    # Multiple entries on same day - use sequence number
                    for i in range(100):
                        k = f"{date_key}_active_{i}"
                        if k not in pending_vass:
                            pending_vass[k] = entry
                            break
            continue

        # ── VASS Exit ──
        m = RE_VASS_EXIT.search(line)
        if m:
            exit_ts = m.group(1)
            exit_date = exit_ts[:10]
            reason = m.group(2)
            pnl_pct = float(m.group(3))

            # Find the matching active entry
            # Try to match by finding the earliest active entry
            matched_key = None
            matched_entry = None
            for k, v in sorted(pending_vass.items()):
                if "_active" in k and isinstance(v, dict) and "strategy" in v:
                    matched_key = k
                    matched_entry = v
                    break

            if matched_entry:
                del pending_vass[matched_key]
                trade = {**matched_entry}
                trade["exit_timestamp"] = exit_ts
                trade["exit_reason"] = classify_vass_exit(reason)
                trade["exit_reason_raw"] = reason
                trade["pnl_pct"] = pnl_pct
                trade["hold_minutes"] = parse_hold_minutes_credit(trade["timestamp"], exit_ts)
                vass_trades.append(trade)
            continue

        # ── MICRO Entry ──
        m = RE_MICRO_ENTRY.search(line)
        if m:
            entry = {
                "timestamp": m.group(1),
                "date": m.group(1)[:10],
                "conviction_text": m.group(2),
                "direction": m.group(3),
                "strategy": m.group(4),
                "contract": m.group(5).strip(),
            }
            pending_micro.append(entry)
            continue

        # ── MICRO Result ──
        m = RE_MICRO_RESULT.search(line)
        if m:
            result_ts = m.group(1)
            outcome = m.group(2)
            entry_price = float(m.group(3))
            exit_price = float(m.group(4))
            pnl_pct = float(m.group(5))

            # Match with most recent pending micro entry
            if pending_micro:
                entry = pending_micro.pop(0)
                trade = {**entry}
                trade["exit_timestamp"] = result_ts
                trade["outcome"] = outcome
                trade["entry_price"] = entry_price
                trade["exit_price"] = exit_price
                trade["pnl_pct"] = pnl_pct
                trade["exit_type"] = classify_micro_exit(pnl_pct, entry["strategy"])
                trade["hold_minutes"] = parse_hold_minutes_credit(entry["timestamp"], result_ts)
                # Get nearest regime
                regime_score = get_nearest_regime(regime_history, entry["timestamp"])
                trade["regime_score"] = regime_score
                trade["regime_band"] = classify_regime(regime_score) if regime_score else "UNKNOWN"
                micro_trades.append(trade)
            continue

    return vass_trades, micro_trades, regime_history


def compute_rr_metrics(trades, label):
    """Compute R:R metrics for a group of trades."""
    if not trades:
        return {
            "label": label,
            "count": 0,
            "wins": 0,
            "losses": 0,
            "wr": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "planned_rr": "N/A",
            "actual_rr": "N/A",
            "breakeven_wr": 0,
            "wr_gap": 0,
            "net_pnl_pct": 0,
            "verdict": "NO DATA",
        }

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    wr = len(wins) / len(trades) * 100 if trades else 0
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0
    net_pnl_pct = sum(t["pnl_pct"] for t in trades)

    # R:R = avg_win / avg_loss
    actual_rr = avg_win / avg_loss if avg_loss > 0 else float("inf")
    # Breakeven WR = 1 / (1 + R:R)
    breakeven_wr = (1 / (1 + actual_rr)) * 100 if actual_rr > 0 else 50
    wr_gap = wr - breakeven_wr

    if wr_gap > 5:
        verdict = "POSITIVE EDGE"
    elif wr_gap > 0:
        verdict = "MARGINAL"
    elif wr_gap > -5:
        verdict = "BREAKEVEN"
    else:
        verdict = "NEGATIVE"

    return {
        "label": label,
        "count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "wr": wr,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "actual_rr": actual_rr,
        "breakeven_wr": breakeven_wr,
        "wr_gap": wr_gap,
        "net_pnl_pct": net_pnl_pct,
        "verdict": verdict,
    }


def compute_exit_distribution(trades, exit_key="exit_type"):
    """Count trades by exit type."""
    dist = defaultdict(int)
    for t in trades:
        dist[t.get(exit_key, "UNKNOWN")] += 1
    return dict(dist)


def compute_regime_matrix(trades):
    """Compute performance by regime band."""
    by_regime = defaultdict(list)
    for t in trades:
        band = t.get("regime_band", "UNKNOWN")
        by_regime[band].append(t)
    result = {}
    for band, group in by_regime.items():
        wins = [t for t in group if t["pnl_pct"] > 0]
        wr = len(wins) / len(group) * 100 if group else 0
        avg_pnl = sum(t["pnl_pct"] for t in group) / len(group) if group else 0
        result[band] = {"count": len(group), "wr": wr, "avg_pnl": avg_pnl}
    return result


def analyze_credit_stop_timing(vass_trades):
    """Analyze hold times for credit stop losses."""
    credit_stops = [
        t for t in vass_trades if t.get("exit_reason") in {"CREDIT_STOP_LOSS", "CREDIT_STOP_2X"}
    ]
    if not credit_stops:
        return {}

    hold_times = [t["hold_minutes"] for t in credit_stops]
    early_exits = [t for t in credit_stops if t["hold_minutes"] <= 30]
    pnl_vals = [t["pnl_pct"] for t in credit_stops]

    return {
        "total": len(credit_stops),
        "avg_hold_min": sum(hold_times) / len(hold_times),
        "median_hold_min": sorted(hold_times)[len(hold_times) // 2],
        "early_exits_30min": len(early_exits),
        "early_exit_pct": len(early_exits) / len(credit_stops) * 100,
        "avg_pnl": sum(pnl_vals) / len(pnl_vals),
        "min_pnl": min(pnl_vals),
        "max_pnl": max(pnl_vals),
    }


def analyze_slippage(vass_trades):
    """Analyze debit spread slippage."""
    debit_trades = [t for t in vass_trades if t.get("spread_type") == "DEBIT"]
    if not debit_trades:
        return {}

    slippages = [t.get("slippage_pct", 0) for t in debit_trades]
    erosions = [t.get("profit_erosion_pct", 0) for t in debit_trades]
    planned_dw = [t.get("planned_dw_ratio", 0) for t in debit_trades]
    actual_dw = [t.get("actual_dw_ratio", 0) for t in debit_trades]

    return {
        "count": len(debit_trades),
        "avg_slippage_pct": sum(slippages) / len(slippages) if slippages else 0,
        "max_slippage_pct": max(slippages) if slippages else 0,
        "avg_profit_erosion_pct": sum(erosions) / len(erosions) if erosions else 0,
        "avg_planned_dw": sum(planned_dw) / len(planned_dw) if planned_dw else 0,
        "avg_actual_dw": sum(actual_dw) / len(actual_dw) if actual_dw else 0,
        "trades": debit_trades,
    }


def analyze_credit_ratios(vass_trades):
    """Analyze credit spread credit/width ratios."""
    credit_trades = [t for t in vass_trades if t.get("spread_type") == "CREDIT"]
    if not credit_trades:
        return {}

    planned_cw = [t.get("planned_cw_ratio", 0) for t in credit_trades]
    actual_cw = [t.get("actual_cw_ratio", 0) for t in credit_trades]

    return {
        "count": len(credit_trades),
        "avg_planned_cw": sum(planned_cw) / len(planned_cw) if planned_cw else 0,
        "avg_actual_cw": sum(actual_cw) / len(actual_cw) if actual_cw else 0,
        "min_planned_cw": min(planned_cw) if planned_cw else 0,
        "max_planned_cw": max(planned_cw) if planned_cw else 0,
        "avg_width": sum(t["width"] for t in credit_trades) / len(credit_trades),
    }


def format_rr(rr):
    if isinstance(rr, str):
        return rr
    if rr == float("inf"):
        return "∞"
    return f"{rr:.2f}:1"


def main():
    base = Path(__file__).parent.parent / "docs" / "audits" / "logs" / "stage9.1"

    log_2022 = base / "V9_1_FullYear2022_r2_logs.txt"
    log_2017 = base / "V9_1_JulSep2017_logs.txt"

    print("=" * 80)
    print("R:R ROOT CAUSE ANALYSIS - ALL OPTIONS TRADE TYPES")
    print("=" * 80)

    # ── Parse 2022 logs ──
    print("\n[1/4] Parsing 2022 Full Year logs...")
    vass_2022, micro_2022, regime_2022 = parse_log_file(log_2022)
    print(f"  VASS trades: {len(vass_2022)}")
    print(f"  MICRO trades: {len(micro_2022)}")
    print(f"  Regime data points: {len(regime_2022)}")

    # ── Parse 2017 logs ──
    print("\n[2/4] Parsing 2017 Jul-Sep logs...")
    vass_2017, micro_2017, regime_2017 = parse_log_file(log_2017)
    print(f"  VASS trades: {len(vass_2017)}")
    print(f"  MICRO trades: {len(micro_2017)}")
    print(f"  Regime data points: {len(regime_2017)}")

    # ── Compute 2022 Metrics ──
    print("\n[3/4] Computing 2022 R:R metrics...")

    # Split VASS by type
    vass_credit_2022 = [t for t in vass_2022 if t.get("spread_type") == "CREDIT"]
    vass_debit_2022 = [t for t in vass_2022 if t.get("spread_type") == "DEBIT"]

    # Further split credit by strategy
    bear_call_credit = [t for t in vass_credit_2022 if "BEAR_CALL" in t.get("strategy", "")]
    bull_put_credit = [t for t in vass_credit_2022 if "BULL_PUT" in t.get("strategy", "")]
    bull_call_debit = [t for t in vass_debit_2022 if "BULL_CALL" in t.get("strategy", "")]

    # Split MICRO by strategy
    micro_itm = [t for t in micro_2022 if t["strategy"] == "ITM_MOMENTUM"]
    micro_debit = [t for t in micro_2022 if t["strategy"] == "DEBIT_MOMENTUM"]
    micro_prot = [t for t in micro_2022 if t["strategy"] == "PROTECTIVE_PUTS"]

    # Compute R:R for each
    rr_bear_call = compute_rr_metrics(bear_call_credit, "BEAR_CALL_CREDIT")
    rr_bull_put = compute_rr_metrics(bull_put_credit, "BULL_PUT_CREDIT")
    rr_bull_call = compute_rr_metrics(bull_call_debit, "BULL_CALL_DEBIT")
    rr_itm = compute_rr_metrics(micro_itm, "ITM_MOMENTUM")
    rr_debit = compute_rr_metrics(micro_debit, "DEBIT_MOMENTUM")
    rr_prot = compute_rr_metrics(micro_prot, "PROTECTIVE_PUTS")
    rr_all_vass = compute_rr_metrics(vass_2022, "ALL VASS")
    rr_all_micro = compute_rr_metrics(micro_2022, "ALL MICRO")

    # Exit type distributions
    vass_exit_dist = compute_exit_distribution(vass_2022, "exit_reason")
    micro_exit_dist_itm = compute_exit_distribution(micro_itm, "exit_type")
    micro_exit_dist_debit = compute_exit_distribution(micro_debit, "exit_type")
    micro_exit_dist_prot = compute_exit_distribution(micro_prot, "exit_type")
    micro_exit_dist_all = compute_exit_distribution(micro_2022, "exit_type")

    # Credit stop timing analysis
    credit_timing = analyze_credit_stop_timing(vass_2022)

    # Slippage analysis
    slippage = analyze_slippage(vass_2022)

    # Credit ratio analysis
    credit_ratios = analyze_credit_ratios(vass_2022)

    # Regime matrix for MICRO
    regime_matrix_itm = compute_regime_matrix(micro_itm)
    regime_matrix_debit = compute_regime_matrix(micro_debit)
    regime_matrix_prot = compute_regime_matrix(micro_prot)
    regime_matrix_all = compute_regime_matrix(micro_2022)

    # ── Compute 2017 Metrics ──
    print("\n[4/4] Computing 2017 R:R metrics...")

    # Split VASS 2017 by type
    vass_debit_2017 = [t for t in vass_2017 if t.get("spread_type") == "DEBIT"]
    bull_call_2017 = [t for t in vass_debit_2017 if "BULL_CALL" in t.get("strategy", "")]

    # Split MICRO 2017 by strategy
    micro_2017_by_strat = defaultdict(list)
    for t in micro_2017:
        micro_2017_by_strat[t["strategy"]].append(t)

    rr_bull_call_2017 = compute_rr_metrics(bull_call_2017, "BULL_CALL_DEBIT (2017)")
    rr_micro_2017 = compute_rr_metrics(micro_2017, "ALL MICRO (2017)")
    rr_micro_2017_by_strat = {}
    for strat, trades in micro_2017_by_strat.items():
        rr_micro_2017_by_strat[strat] = compute_rr_metrics(trades, f"{strat} (2017)")

    vass_exit_dist_2017 = compute_exit_distribution(vass_2017, "exit_reason")
    micro_exit_dist_2017 = compute_exit_distribution(micro_2017, "exit_type")
    regime_matrix_2017 = compute_regime_matrix(micro_2017)
    slippage_2017 = analyze_slippage(vass_2017)

    # ──────────────────────────────────────────────────────────────────
    # OUTPUT: Print comprehensive data for report generation
    # ──────────────────────────────────────────────────────────────────

    print("\n" + "=" * 80)
    print("2022 R:R SCORECARD")
    print("=" * 80)

    all_rr = [rr_bear_call, rr_bull_put, rr_bull_call, rr_itm, rr_debit, rr_prot]
    print(
        f"\n{'Strategy':<22} {'Trades':>6} {'WR%':>6} {'AvgWin':>8} {'AvgLoss':>8} {'R:R':>8} {'BE_WR':>6} {'Gap':>7} {'NetP&L%':>9} {'Verdict':<15}"
    )
    print("-" * 110)
    for rr in all_rr:
        print(
            f"{rr['label']:<22} {rr['count']:>6} {rr['wr']:>5.1f}% {rr['avg_win']:>+7.1f}% {rr['avg_loss']:>7.1f}% {format_rr(rr['actual_rr']):>8} {rr['breakeven_wr']:>5.1f}% {rr['wr_gap']:>+6.1f}% {rr['net_pnl_pct']:>+8.1f}% {rr['verdict']:<15}"
        )
    print("-" * 110)
    for rr in [rr_all_vass, rr_all_micro]:
        print(
            f"{rr['label']:<22} {rr['count']:>6} {rr['wr']:>5.1f}% {rr['avg_win']:>+7.1f}% {rr['avg_loss']:>7.1f}% {format_rr(rr['actual_rr']):>8} {rr['breakeven_wr']:>5.1f}% {rr['wr_gap']:>+6.1f}% {rr['net_pnl_pct']:>+8.1f}% {rr['verdict']:<15}"
        )

    # ── VASS Exit Distribution ──
    print("\n\nVASS EXIT TYPE DISTRIBUTION (2022):")
    print(f"{'Exit Type':<25} {'Count':>6} {'%':>6}")
    print("-" * 40)
    total = sum(vass_exit_dist.values())
    for etype, count in sorted(vass_exit_dist.items(), key=lambda x: -x[1]):
        print(f"{etype:<25} {count:>6} {count/total*100:>5.1f}%")

    # ── MICRO Exit Distribution ──
    print("\n\nMICRO EXIT TYPE DISTRIBUTION (2022):")
    print(f"{'Strategy':<20} {'Target':>7} {'Stop':>7} {'TimeEx':>7} {'Total':>7}")
    print("-" * 55)
    for name, dist in [
        ("ITM_MOMENTUM", micro_exit_dist_itm),
        ("DEBIT_MOMENTUM", micro_exit_dist_debit),
        ("PROTECTIVE_PUTS", micro_exit_dist_prot),
        ("ALL MICRO", micro_exit_dist_all),
    ]:
        target = dist.get("TARGET_HIT", 0)
        stop = dist.get("STOP_HIT", 0)
        time_ex = dist.get("TIME_EXIT", 0)
        total = target + stop + time_ex
        print(f"{name:<20} {target:>7} {stop:>7} {time_ex:>7} {total:>7}")

    # ── Credit Stop Timing ──
    print("\n\nCREDIT STOP TIMING ANALYSIS:")
    if credit_timing:
        for k, v in credit_timing.items():
            print(f"  {k}: {v}")

    # ── Credit Ratio Analysis ──
    print("\n\nCREDIT SPREAD RATIO ANALYSIS (2022):")
    if credit_ratios:
        for k, v in credit_ratios.items():
            print(f"  {k}: {v}")

    # ── Slippage Analysis ──
    print("\n\nDEBIT SPREAD SLIPPAGE ANALYSIS (2022):")
    if slippage:
        for k, v in slippage.items():
            if k != "trades":
                print(f"  {k}: {v}")
        print("\n  Per-trade details:")
        for t in slippage.get("trades", []):
            print(
                f"    {t['date']} | Planned D/W={t.get('planned_dw_ratio',0):.1%} | Actual D/W={t.get('actual_dw_ratio',0):.1%} | Slippage={t.get('slippage_pct',0):.1f}% | Profit erosion={t.get('profit_erosion_pct',0):.1f}% | P&L={t.get('pnl_pct','N/A')}"
            )

    # ── Regime Matrix ──
    print("\n\nREGIME PERFORMANCE MATRIX (2022 MICRO):")
    print(f"{'Regime':<12} {'ITM_MOM':>20} {'DEBIT_MOM':>20} {'PROT_PUT':>20} {'ALL MICRO':>20}")
    print("-" * 95)
    for band in ["RISK_ON", "NEUTRAL", "CAUTIOUS", "DEFENSIVE", "RISK_OFF", "UNKNOWN"]:
        itm = regime_matrix_itm.get(band, {"count": 0, "wr": 0})
        deb = regime_matrix_debit.get(band, {"count": 0, "wr": 0})
        prot = regime_matrix_prot.get(band, {"count": 0, "wr": 0})
        all_m = regime_matrix_all.get(band, {"count": 0, "wr": 0})
        if itm["count"] + deb["count"] + prot["count"] + all_m["count"] > 0:
            print(
                f"{band:<12} {itm['count']:>3}t/{itm['wr']:>5.1f}%WR  {deb['count']:>3}t/{deb['wr']:>5.1f}%WR  {prot['count']:>3}t/{prot['wr']:>5.1f}%WR  {all_m['count']:>3}t/{all_m['wr']:>5.1f}%WR"
            )

    # ── VASS Per-trade details ──
    print("\n\nVASS TRADE DETAILS (2022):")
    print(
        f"{'Date':<12} {'Strategy':<22} {'Type':<8} {'Regime':>6} {'VIX':>5} {'DTE':>4} {'Width':>5} {'D/W or C/W':>10} {'HoldMin':>8} {'ExitType':<20} {'P&L%':>7}"
    )
    print("-" * 130)
    for t in vass_2022:
        strat = t.get("strategy", "?")
        stype = t.get("spread_type", "?")
        if stype == "DEBIT":
            ratio = f"{t.get('actual_dw_ratio', 0):.1%}"
        else:
            ratio = f"{t.get('actual_cw_ratio', 0):.1%}"
        print(
            f"{t['date']:<12} {strat:<22} {stype:<8} {t['regime']:>6} {t['vix']:>5.1f} {t['dte']:>4} {t.get('width',0):>5.0f} {ratio:>10} {t.get('hold_minutes',0):>7.0f}m {t.get('exit_reason','?'):<20} {t.get('pnl_pct',0):>+6.1f}%"
        )

    # ── 2017 Cross-Year Comparison ──
    print("\n\n" + "=" * 80)
    print("2017 CROSS-YEAR COMPARISON (Jul-Sep Bull Market)")
    print("=" * 80)

    print(
        f"\n{'Strategy':<22} {'Trades':>6} {'WR%':>6} {'AvgWin':>8} {'AvgLoss':>8} {'R:R':>8} {'BE_WR':>6} {'Gap':>7} {'NetP&L%':>9} {'Verdict':<15}"
    )
    print("-" * 110)
    print(
        f"{rr_bull_call_2017['label']:<22} {rr_bull_call_2017['count']:>6} {rr_bull_call_2017['wr']:>5.1f}% {rr_bull_call_2017['avg_win']:>+7.1f}% {rr_bull_call_2017['avg_loss']:>7.1f}% {format_rr(rr_bull_call_2017['actual_rr']):>8} {rr_bull_call_2017['breakeven_wr']:>5.1f}% {rr_bull_call_2017['wr_gap']:>+6.1f}% {rr_bull_call_2017['net_pnl_pct']:>+8.1f}% {rr_bull_call_2017['verdict']:<15}"
    )
    for strat, rr in rr_micro_2017_by_strat.items():
        print(
            f"{rr['label']:<22} {rr['count']:>6} {rr['wr']:>5.1f}% {rr['avg_win']:>+7.1f}% {rr['avg_loss']:>7.1f}% {format_rr(rr['actual_rr']):>8} {rr['breakeven_wr']:>5.1f}% {rr['wr_gap']:>+6.1f}% {rr['net_pnl_pct']:>+8.1f}% {rr['verdict']:<15}"
        )
    print("-" * 110)
    print(
        f"{rr_micro_2017['label']:<22} {rr_micro_2017['count']:>6} {rr_micro_2017['wr']:>5.1f}% {rr_micro_2017['avg_win']:>+7.1f}% {rr_micro_2017['avg_loss']:>7.1f}% {format_rr(rr_micro_2017['actual_rr']):>8} {rr_micro_2017['breakeven_wr']:>5.1f}% {rr_micro_2017['wr_gap']:>+6.1f}% {rr_micro_2017['net_pnl_pct']:>+8.1f}% {rr_micro_2017['verdict']:<15}"
    )

    print(f"\nVASS Exit Distribution (2017):")
    for etype, count in sorted(vass_exit_dist_2017.items(), key=lambda x: -x[1]):
        print(f"  {etype}: {count}")

    print(f"\nMICRO Exit Distribution (2017):")
    for etype, count in sorted(micro_exit_dist_2017.items(), key=lambda x: -x[1]):
        print(f"  {etype}: {count}")

    print(f"\nRegime Matrix (2017 MICRO):")
    for band, data in sorted(regime_matrix_2017.items()):
        print(f"  {band}: {data['count']}t / {data['wr']:.1f}% WR / {data['avg_pnl']:.1f}% avg")

    print(f"\nSlippage (2017 Debit Spreads):")
    if slippage_2017:
        for k, v in slippage_2017.items():
            if k != "trades":
                print(f"  {k}: {v}")

    # VASS trade details 2017
    print(f"\nVASS TRADE DETAILS (2017):")
    print(
        f"{'Date':<12} {'Strategy':<22} {'Regime':>6} {'VIX':>5} {'DTE':>4} {'Width':>5} {'D/W':>8} {'HoldMin':>8} {'ExitType':<20} {'P&L%':>7}"
    )
    print("-" * 110)
    for t in vass_2017:
        ratio = f"{t.get('actual_dw_ratio', 0):.1%}"
        print(
            f"{t['date']:<12} {t.get('strategy','?'):<22} {t['regime']:>6} {t['vix']:>5.1f} {t['dte']:>4} {t.get('width',0):>5.0f} {ratio:>8} {t.get('hold_minutes',0):>7.0f}m {t.get('exit_reason','?'):<20} {t.get('pnl_pct',0):>+6.1f}%"
        )

    print("\n\nDONE. Use output above to generate final RCA report.")


if __name__ == "__main__":
    main()
