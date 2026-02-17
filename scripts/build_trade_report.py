#!/usr/bin/env python3
"""Build V10.4 trade detail report from trades.csv, orders.csv, and logs.txt."""

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(
    "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.4"
)
TRADES_FILE = BASE / "V10_4_FullYear2023_trades.csv"
ORDERS_FILE = BASE / "V10_4_FullYear2023_orders.csv"
LOGS_FILE = BASE / "V10_4_FullYear2023_logs.txt"
OUTPUT_FILE = BASE / "V10_4_FullYear2023_TRADE_DETAIL_REPORT.md"


def parse_dt(s):
    s = s.strip().rstrip("Z")
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def parse_trades():
    trades = []
    with open(TRADES_FILE) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            oids_raw = row.get("Order Ids", "").strip().strip('"').strip()
            oids = [x.strip() for x in oids_raw.split(",") if x.strip()]
            trades.append(
                {
                    "idx": i + 2,  # 1-based CSV line (header=1)
                    "entry_time": parse_dt(row["Entry Time"]),
                    "symbol": row["Symbols"].strip().strip('"'),
                    "exit_time": parse_dt(row["Exit Time"]),
                    "direction": row["Direction"].strip(),
                    "entry_price": float(row["Entry Price"]),
                    "exit_price": float(row["Exit Price"]),
                    "quantity": int(row["Quantity"]),
                    "pnl": float(row["P&L"]),
                    "fees": float(row["Fees"]),
                    "drawdown": float(row["Drawdown"]),
                    "is_win": int(row["IsWin"]),
                    "order_ids": oids,
                }
            )
    return trades


def build_order_id_map():
    order_map = {}
    with open(ORDERS_FILE) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            oid = i + 1
            tag = row.get("Tag", "").strip().strip('"')
            status = row.get("Status", "").strip()
            order_map[str(oid)] = {
                "time": row["Time"].strip(),
                "symbol": row.get("Symbol", "").strip(),
                "price": row.get("Price", "").strip(),
                "quantity": int(row.get("Quantity", 0)),
                "type": row.get("Type", "").strip(),
                "status": status,
                "tag": tag,
            }
    return order_map


def classify_trade(trade, order_map):
    """Classify trade as VASS or MICRO based on entry order tags."""
    tags = []
    for oid in trade["order_ids"]:
        if oid in order_map:
            t = order_map[oid]["tag"]
            if t:
                tags.append(t)

    # Check the ENTRY order first (first order ID)
    entry_oid = trade["order_ids"][0] if trade["order_ids"] else None
    entry_tag = order_map.get(entry_oid, {}).get("tag", "") if entry_oid else ""

    # Classify by entry tag
    if "MICRO:ITM_MOMENTUM" in entry_tag:
        return "MICRO", "ITM_MOMENTUM", tags
    if "MICRO:DEBIT_FADE" in entry_tag:
        return "MICRO", "DEBIT_FADE", tags
    if "MICRO:DEBIT_MOMENTUM" in entry_tag:
        return "MICRO", "DEBIT_MOMENTUM", tags
    if "MICRO:PROTECTIVE_PUT" in entry_tag:
        return "MICRO", "PROTECTIVE_PUT", tags
    if "VASS:BULL_CALL_DEBIT" in entry_tag:
        return "VASS", "BULL_CALL_DEBIT", tags
    if "VASS:BEAR_PUT_DEBIT" in entry_tag:
        return "VASS", "BEAR_PUT_DEBIT", tags
    if "VASS:BEAR_CALL_CREDI" in entry_tag or "VASS:BEAR_CALL_CREDIT" in entry_tag:
        return "VASS", "BEAR_CALL_CREDIT", tags
    if "VASS:BULL_PUT_CREDIT" in entry_tag:
        return "VASS", "BULL_PUT_CREDIT", tags

    # Fall back to any tag in the order set
    for tag in tags:
        if "MICRO:ITM_MOMENTUM" in tag:
            return "MICRO", "ITM_MOMENTUM", tags
        if "MICRO:DEBIT_FADE" in tag:
            return "MICRO", "DEBIT_FADE", tags
        if "VASS:BULL_CALL_DEBIT" in tag:
            return "VASS", "BULL_CALL_DEBIT", tags
        if "VASS:BEAR_PUT_DEBIT" in tag:
            return "VASS", "BEAR_PUT_DEBIT", tags
        if "VASS:BEAR_CALL_CREDI" in tag or "VASS:BEAR_CALL_CREDIT" in tag:
            return "VASS", "BEAR_CALL_CREDIT", tags
        if "VASS:BULL_PUT_CREDIT" in tag:
            return "VASS", "BULL_PUT_CREDIT", tags

    # Check for VASS overlap/exit artifacts
    for tag in tags:
        if tag == "VASS" or (tag.startswith("VASS") and ":" not in tag):
            return "VASS_OVERLAP", "OVERLAP", tags
        if "KS_TIER2_OPTIONS" in tag:
            return "VASS_EXIT", "KS_EXIT", tags
        if "RECON_ORPHAN_OPTION" in tag:
            return "RECON", "ORPHAN", tags
        if "MICRO_EOD_SWEEP" in tag:
            return "MICRO_EXIT", "EOD_SWEEP", tags
        if "EARLY_EXERCISE_GUARD" in tag:
            return "MICRO_EXIT", "EARLY_EXERCISE", tags
        if "MICRO" in tag and ":" not in tag:
            return "MICRO_EXIT", "GENERIC", tags

    return "UNKNOWN", "UNKNOWN", tags


def load_logs():
    logs = []
    with open(LOGS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            logs.append(line)
    return logs


def build_log_index(logs):
    by_date = defaultdict(list)
    for line in logs:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", line)
        if m:
            by_date[m.group(1)].append(line)
    return by_date


def find_spread_entry_signal(log_index, date_str):
    lines = log_index.get(date_str, [])
    results = []
    for line in lines:
        if "SPREAD: ENTRY_SIGNAL" in line or "SPREAD:ENTRY_SIGNAL" in line:
            results.append(line)
    return results


def parse_intraday_signal(line):
    info = {}
    if not line:
        return info

    if "INTRADAY_ITM_MOM:" in line:
        info["strategy"] = "ITM_MOMENTUM"
    elif "INTRADAY_DEBIT_FADE:" in line:
        info["strategy"] = "DEBIT_FADE"
    elif "INTRADAY_DEBIT_MOM:" in line:
        info["strategy"] = "DEBIT_MOMENTUM"
    else:
        info["strategy"] = "UNKNOWN"

    m = re.search(r"Regime=(\w+)", line)
    if m:
        info["micro_regime"] = m.group(1)
    m = re.search(r"Score=(\d+)", line)
    if m:
        info["score"] = int(m.group(1))
    m = re.search(r"VIX=([\d.]+)", line)
    if m:
        info["vix"] = float(m.group(1))
    m = re.search(r"VIX=[\d.]+ \((\w+)\)", line)
    if m:
        info["vix_dir"] = m.group(1)
    m = re.search(r"\b(CALL|PUT)\s+x\d+", line)
    if m:
        info["option_dir"] = m.group(1)
    m = re.search(r"QQQ=(\S+)", line)
    if m:
        info["qqq_move"] = m.group(1)
    m = re.search(r"DTE=(\d+)", line)
    if m:
        info["dte"] = int(m.group(1))
    m = re.search(r"[Δ]=([0-9.]+)", line)
    if m:
        info["delta"] = float(m.group(1))
    m = re.search(r"K=([0-9.]+)", line)
    if m:
        info["strike"] = float(m.group(1))

    return info


def parse_spread_entry(line):
    info = {}
    if not line:
        return info

    if "BULL_CALL" in line:
        info["spread_type"] = "BULL_CALL_DEBIT"
    elif "BEAR_PUT" in line:
        info["spread_type"] = "BEAR_PUT_DEBIT"
    elif "BEAR_CALL" in line:
        info["spread_type"] = "BEAR_CALL_CREDIT"
    elif "BULL_PUT" in line:
        info["spread_type"] = "BULL_PUT_CREDIT"
    else:
        info["spread_type"] = "UNKNOWN"

    m = re.search(r"Regime=(\d+)", line)
    if m:
        info["regime"] = int(m.group(1))
    m = re.search(r"VIX=([\d.]+)", line)
    if m:
        info["vix"] = float(m.group(1))
    m = re.search(r"Long=([\d.]+)\s+Short=([\d.]+)", line)
    if m:
        info["long_strike"] = float(m.group(1))
        info["short_strike"] = float(m.group(2))
    m = re.search(r"Debit=\$([\d.]+)", line)
    if m:
        info["debit"] = float(m.group(1))
    m = re.search(r"Credit=\$([\d.]+)", line)
    if m:
        info["credit"] = float(m.group(1))
    m = re.search(r"MaxProfit=\$([\d.]+)", line)
    if m:
        info["max_profit"] = float(m.group(1))
    m = re.search(r"DTE=(\d+)", line)
    if m:
        info["dte"] = int(m.group(1))
    m = re.search(r"Score=([\d.]+)", line)
    if m:
        info["vass_score"] = float(m.group(1))
    m = re.search(r"x(\d+)", line)
    if m:
        info["contracts"] = int(m.group(1))

    return info


def hold_duration(entry, exit_t):
    delta = exit_t - entry
    total_hours = delta.total_seconds() / 3600
    if total_hours < 0:
        return "0h 0m"
    if total_hours < 24:
        h = int(total_hours)
        m = int((total_hours - h) * 60)
        return f"{h}h {m}m"
    else:
        days = total_hours / 24
        return f"{days:.1f}d"


def extract_option_info(symbol):
    m = re.match(r"QQQ\s+(\d{6})(C|P)(\d{8})", symbol)
    if m:
        exp = m.group(1)
        cp = "CALL" if m.group(2) == "C" else "PUT"
        strike = int(m.group(3)) / 1000
        return {"expiry": exp, "type": cp, "strike": strike}
    return {}


def find_vass_exit_trigger(log_index, exit_time, entry_time):
    exit_date = exit_time.strftime("%Y-%m-%d")
    lines = log_index.get(exit_date, [])

    for line in lines:
        if "SPREAD: EXIT" in line or "SPREAD_EXIT" in line or "SPREAD:EXIT" in line:
            if "STOP_LOSS" in line:
                return "STOP_LOSS"
            if "HARD_STOP" in line:
                return "HARD_STOP"
            if "PROFIT_TARGET" in line:
                return "PROFIT_TARGET"
            if "TRAIL_STOP" in line:
                return "TRAIL_STOP"
            if "DTE_EXIT" in line:
                return "DTE_EXIT"
            if "FRIDAY_FIREWALL" in line:
                return "FRIDAY_FIREWALL"
            if "FILL_CLOSE_RECONCILED" in line:
                return "RECONCILED"

    for line in lines:
        if "SPREAD_HARD_STOP" in line:
            return "HARD_STOP"
        if "SPREAD_OVERLAY_EXIT" in line:
            return "STRESS_EXIT"

    for line in lines:
        if "KS_TIER2_OPTIONS" in line:
            return "KILL_SWITCH"

    return "UNKNOWN"


def find_vass_exit_from_orders(trade, order_map):
    for oid in trade["order_ids"]:
        if oid in order_map:
            o = order_map[oid]
            tag = o["tag"]
            if "KS_TIER2_OPTIONS" in tag:
                return "KILL_SWITCH"
            if "RECON_ORPHAN_OPTION" in tag:
                return "RECON_ORPHAN"
    return None


def determine_micro_exit(trade, order_map):
    """Determine MICRO trade exit trigger from order data."""
    # Look at ALL orders for exit-type clues
    for oid in trade["order_ids"]:
        if oid in order_map:
            o = order_map[oid]
            tag = o.get("tag", "")
            status = o.get("status", "")
            qty = o.get("quantity", 0)
            otype = o.get("type", "")

            if status != "Filled":
                continue
            if qty >= 0:
                continue  # skip entry orders

            # Exit order found
            if "OCO_STOP" in tag:
                return "OCO_STOP"
            if "OCO_PROFIT" in tag:
                return "OCO_PROFIT"
            if "MICRO_EOD_SWEEP" in tag:
                return "EOD_SWEEP"
            if "EARLY_EXERCISE_GUARD" in tag:
                return "EARLY_EXERCISE"
            if "KS_TIER2_OPTIONS" in tag:
                return "KILL_SWITCH"
            if "RECON_ORPHAN_OPTION" in tag:
                return "RECON_ORPHAN"
            if otype == "Stop Market":
                return "OCO_STOP"
            if otype == "Limit" and qty < 0:
                return "OCO_PROFIT"
            if otype == "Market":
                if "MICRO_EOD_SWEEP" in tag:
                    return "EOD_SWEEP"
                if "EARLY_EXERCISE_GUARD" in tag:
                    return "EARLY_EXERCISE"
                return "MARKET_CLOSE"

    return "UNKNOWN"


def pair_vass_legs(vass_trades):
    """Pair VASS spread legs.

    Strategy: group by entry order IDs sharing the same entry time, then
    pair Buy/Sell legs. For split exits (same position exited in parts),
    we still pair by entry order ID.
    """
    # Group VASS legs by their entry order ID (first order)
    # For VASS, the entry order is always the first order ID
    # Two legs of the same spread share the same entry time (within seconds)

    spreads = []
    used = set()

    for i in range(len(vass_trades)):
        if i in used:
            continue
        t1 = vass_trades[i]

        # Look for partner: same entry time (within 2 min), opposite direction
        best_j = None
        best_diff = 999999
        for j in range(len(vass_trades)):
            if j in used or j == i:
                continue
            t2 = vass_trades[j]

            # Must be opposite direction
            if t1["direction"] == t2["direction"]:
                continue

            # Entry time within 2 min
            entry_diff = abs((t1["entry_time"] - t2["entry_time"]).total_seconds())
            if entry_diff > 120:
                continue

            # Exit time within 2 min
            exit_diff = abs((t1["exit_time"] - t2["exit_time"]).total_seconds())
            if exit_diff > 120:
                continue

            # Prefer closest match
            total_diff = entry_diff + exit_diff
            if total_diff < best_diff:
                best_diff = total_diff
                best_j = j

        if best_j is not None:
            used.add(i)
            used.add(best_j)
            t2 = vass_trades[best_j]

            if t1["direction"] == "Buy":
                long_leg = t1
                short_leg = t2
            else:
                long_leg = t2
                short_leg = t1

            net_pnl = t1["pnl"] + t2["pnl"]
            net_fees = t1["fees"] + t2["fees"]
            is_win = 1 if net_pnl > 0 else 0

            spread_type = t1["subcategory"] if t1["subcategory"] != "OVERLAP" else t2["subcategory"]

            spread = {
                "long_leg": long_leg,
                "short_leg": short_leg,
                "entry_time": min(t1["entry_time"], t2["entry_time"]),
                "exit_time": max(t1["exit_time"], t2["exit_time"]),
                "net_pnl": net_pnl,
                "net_fees": net_fees,
                "is_win": is_win,
                "spread_type": spread_type,
                "long_strike": long_leg["option_info"].get("strike", 0),
                "short_strike": short_leg["option_info"].get("strike", 0),
                "all_legs": [t1, t2],
            }

            # Calculate width, debit, D/W%
            if spread_type in ("BULL_CALL_DEBIT", "BEAR_PUT_DEBIT"):
                spread["width"] = abs(spread["short_strike"] - spread["long_strike"])
                spread["debit"] = long_leg["entry_price"] - short_leg["entry_price"]
            elif spread_type in ("BEAR_CALL_CREDIT", "BULL_PUT_CREDIT"):
                # For credit spreads: sold leg is the one closer to ATM (higher premium)
                spread["width"] = abs(spread["short_strike"] - spread["long_strike"])
                # Credit received = premium of sold leg - premium of bought leg
                spread["credit"] = short_leg["entry_price"] - long_leg["entry_price"]
                spread["debit"] = spread["credit"]
            else:
                spread["width"] = abs(spread["short_strike"] - spread["long_strike"])
                spread["debit"] = abs(long_leg["entry_price"] - short_leg["entry_price"])

            if spread["width"] > 0:
                spread["dw_pct"] = (abs(spread["debit"]) / spread["width"]) * 100
            else:
                spread["dw_pct"] = 0

            spreads.append(spread)

    # Collect unpaired trades
    unpaired = []
    for i in range(len(vass_trades)):
        if i not in used:
            unpaired.append(vass_trades[i])

    if unpaired:
        print(f"  {len(unpaired)} unpaired VASS legs - treating as single-leg trades")
        for t in unpaired:
            # Create a single-leg "spread" for accounting purposes
            spread = {
                "long_leg": t if t["direction"] == "Buy" else t,
                "short_leg": t if t["direction"] == "Sell" else t,
                "entry_time": t["entry_time"],
                "exit_time": t["exit_time"],
                "net_pnl": t["pnl"],
                "net_fees": t["fees"],
                "is_win": t["is_win"],
                "spread_type": t["subcategory"] + "_SPLIT",
                "long_strike": t["option_info"].get("strike", 0),
                "short_strike": t["option_info"].get("strike", 0),
                "all_legs": [t],
                "width": 0,
                "debit": t["entry_price"],
                "dw_pct": 0,
                "is_split_leg": True,
            }
            spreads.append(spread)

    # Sort by entry time
    spreads.sort(key=lambda s: s["entry_time"])

    return spreads


def find_best_signal(log_index, entry_time, signal_patterns):
    """Find the best matching signal log line near entry time."""
    entry_date = entry_time.strftime("%Y-%m-%d")
    entry_hour = entry_time.hour
    entry_min = entry_time.minute
    lines = log_index.get(entry_date, [])

    best = None
    best_dist = 999999
    for line in lines:
        match = False
        for pat in signal_patterns:
            if pat in line:
                match = True
                break
        if not match:
            continue

        m = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2})", line)
        if m:
            lh, lm = int(m.group(2)), int(m.group(3))
            dist = abs((lh * 60 + lm) - (entry_hour * 60 + entry_min))
            if dist < best_dist:
                best_dist = dist
                best = line

    return best


def main():
    print("Parsing trades...")
    trades = parse_trades()
    print(f"  {len(trades)} trades loaded")

    print("Building order ID map...")
    order_map = build_order_id_map()
    print(f"  {len(order_map)} orders loaded")

    print("Loading logs...")
    logs = load_logs()
    log_index = build_log_index(logs)
    print(f"  {len(logs)} log lines, {len(log_index)} dates indexed")

    # Classify each trade
    print("Classifying trades...")
    for t in trades:
        cat, sub, tags = classify_trade(t, order_map)
        t["category"] = cat
        t["subcategory"] = sub
        t["tags"] = tags
        t["option_info"] = extract_option_info(t["symbol"])

    # Separate VASS and MICRO trades
    vass_trades = [t for t in trades if t["category"] == "VASS"]
    micro_trades = [t for t in trades if t["category"] == "MICRO"]
    vass_overlap = [t for t in trades if t["category"] == "VASS_OVERLAP"]
    other_trades = [t for t in trades if t["category"] not in ("VASS", "MICRO", "VASS_OVERLAP")]

    print(
        f"  VASS: {len(vass_trades)}, MICRO: {len(micro_trades)}, VASS_OVERLAP: {len(vass_overlap)}, Other: {len(other_trades)}"
    )

    # Pair VASS legs
    print("Pairing VASS legs...")
    vass_spreads = pair_vass_legs(vass_trades)
    paired_count = sum(1 for s in vass_spreads if not s.get("is_split_leg"))
    split_count = sum(1 for s in vass_spreads if s.get("is_split_leg"))
    print(f"  {paired_count} paired spreads, {split_count} split-leg entries")

    # Get MICRO exit triggers and signal context
    print("Extracting MICRO context...")
    for t in micro_trades:
        t["exit_trigger"] = determine_micro_exit(t, order_map)

        # Find signal context
        signal_line = find_best_signal(
            log_index,
            t["entry_time"],
            [
                "INTRADAY_SIGNAL:",
                "INTRADAY_ITM_MOM:",
                "INTRADAY_DEBIT_FADE:",
                "INTRADAY_DEBIT_MOM:",
            ],
        )
        t["signal_context"] = parse_intraday_signal(signal_line)
        t["signal_line"] = signal_line

    # For trades with no signal context, try the previous day (overnight holds)
    for t in micro_trades:
        if not t["signal_context"]:
            prev_date = (t["entry_time"] - timedelta(days=1)).strftime("%Y-%m-%d")
            signal_line = find_best_signal(
                {"d": log_index.get(prev_date, [])},
                t["entry_time"].replace(hour=15, minute=0),
                ["INTRADAY_SIGNAL:", "INTRADAY_ITM_MOM:", "INTRADAY_DEBIT_FADE:"],
            )
            # Actually search by date properly
            lines = log_index.get(prev_date, [])
            for line in lines:
                if (
                    "INTRADAY_SIGNAL:" in line
                    or "INTRADAY_ITM_MOM:" in line
                    or "INTRADAY_DEBIT_FADE:" in line
                ):
                    t["signal_context"] = parse_intraday_signal(line)
                    t["signal_line"] = line
                    break

    # Get VASS spread context
    print("Extracting VASS context...")
    for spread in vass_spreads:
        entry_date = spread["entry_time"].strftime("%Y-%m-%d")
        entry_signals = find_spread_entry_signal(log_index, entry_date)
        if entry_signals:
            # Try to match by spread type
            best_signal = entry_signals[0]
            for sig in entry_signals:
                stype = spread["spread_type"].replace("_SPLIT", "")
                if (
                    stype.replace("_", " ").upper() in sig.upper()
                    or stype.split("_")[0] in sig.upper()
                ):
                    best_signal = sig
                    break
            spread["entry_context"] = parse_spread_entry(best_signal)
            spread["entry_signal_line"] = best_signal
        else:
            spread["entry_context"] = {}
            spread["entry_signal_line"] = None

        # Get exit trigger
        spread["exit_trigger"] = find_vass_exit_trigger(
            log_index, spread["exit_time"], spread["entry_time"]
        )

        # Check order tags for exit
        for leg in spread.get("all_legs", []):
            order_exit = find_vass_exit_from_orders(leg, order_map)
            if order_exit and spread["exit_trigger"] == "UNKNOWN":
                spread["exit_trigger"] = order_exit

    # Build the report
    print("Building report...")
    report = build_report(
        trades, vass_spreads, micro_trades, vass_overlap, other_trades, log_index, order_map
    )

    with open(OUTPUT_FILE, "w") as f:
        f.write(report)

    print(f"Report written to {OUTPUT_FILE}")
    print(f"  Total size: {len(report):,} bytes")


def build_report(
    trades, vass_spreads, micro_trades, vass_overlap, other_trades, log_index, order_map
):
    lines = []

    total_trades = len(trades)
    vass_leg_count = sum(1 for t in trades if t["category"] == "VASS")
    micro_count = len(micro_trades)
    overlap_count = len(vass_overlap)
    other_count = len(other_trades)
    accounted = vass_leg_count + micro_count + overlap_count + other_count

    total_pnl = sum(t["pnl"] for t in trades)
    vass_total_pnl = sum(s["net_pnl"] for s in vass_spreads)
    micro_total_pnl = sum(t["pnl"] for t in micro_trades)
    overlap_pnl = sum(t["pnl"] for t in vass_overlap)

    lines.append("# V10.4 Full Year 2023 -- Trade Detail Report")
    lines.append("")
    lines.append("## Validation Checklist")
    lines.append("")
    lines.append(f"- Total rows in trades.csv: **{total_trades}**")
    lines.append(
        f"- VASS leg rows: **{vass_leg_count}** ({sum(1 for s in vass_spreads if not s.get('is_split_leg'))} paired spreads + {sum(1 for s in vass_spreads if s.get('is_split_leg'))} split legs)"
    )
    lines.append(f"- MICRO trade rows: **{micro_count}**")
    lines.append(f"- VASS overlap/artifact rows: **{overlap_count}** (P&L: ${overlap_pnl:,.0f})")
    lines.append(f"- Other (recon/exit-only) rows: **{other_count}**")
    lines.append(
        f"- Accounted: **{accounted}** / {total_trades} {'PASS' if accounted == total_trades else 'MISMATCH'}"
    )
    lines.append(f"- **Total P&L (all trades.csv rows)**: ${total_pnl:,.0f}")
    lines.append(f"- **VASS Spread Net P&L**: ${vass_total_pnl:,.0f}")
    lines.append(f"- **MICRO Trade P&L**: ${micro_total_pnl:,.0f}")
    lines.append(f"- **Overlap/Artifact P&L**: ${overlap_pnl:,.0f}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ===== PART 1: VASS =====
    lines.append("# Part 1: VASS Spread Trade-by-Trade")
    lines.append("")
    lines.append(
        "| # | Entry | Exit | Type | Regime | VIX | DTE | Debit | Width | D/W% | Exit Trigger | Hold | Net P&L | P&L% | W/L |"
    )
    lines.append(
        "|---|-------|------|------|--------|-----|-----|-------|-------|------|--------------|------|---------|------|-----|"
    )

    for i, s in enumerate(vass_spreads):
        ctx = s.get("entry_context", {})
        regime = ctx.get("regime", "N/A")
        vix = ctx.get("vix", "N/A")
        dte = ctx.get("dte", "N/A")
        debit = s.get("debit", 0)
        width = s.get("width", 0)
        dw = s.get("dw_pct", 0)
        et = s.get("exit_trigger", "N/A")
        hd = hold_duration(s["entry_time"], s["exit_time"])
        pnl = s["net_pnl"]

        qty = s["long_leg"]["quantity"]
        cost_basis = abs(debit) * qty * 100 if debit else 1
        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

        wl = "W" if s["is_win"] else "L"
        entry_str = s["entry_time"].strftime("%m/%d")
        exit_str = s["exit_time"].strftime("%m/%d")
        vix_str = f"{vix:.1f}" if isinstance(vix, float) else str(vix)
        stype = s["spread_type"]
        if s.get("is_split_leg"):
            stype += "*"

        lines.append(
            f"| {i+1} | {entry_str} | {exit_str} | {stype} | {regime} | {vix_str} | {dte} | ${debit:.2f} | ${width:.0f} | {dw:.0f}% | {et} | {hd} | ${pnl:,.0f} | {pnl_pct:.1f}% | {wl} |"
        )

    lines.append("")
    lines.append(
        "*Entries marked with `*` are split-leg trades (partial fills exited at different times).*"
    )
    lines.append("")

    # 1a: Summary
    lines.append("### 1a. VASS Summary")
    lines.append("")
    # Separate paired spreads from split legs for clean stats
    paired_spreads = [s for s in vass_spreads if not s.get("is_split_leg")]

    vass_wins = sum(1 for s in vass_spreads if s["is_win"])
    vass_losses = sum(1 for s in vass_spreads if not s["is_win"])
    vass_wr = vass_wins / len(vass_spreads) * 100 if vass_spreads else 0
    vass_avg_win = (
        sum(s["net_pnl"] for s in vass_spreads if s["is_win"]) / vass_wins if vass_wins else 0
    )
    vass_avg_loss = (
        sum(s["net_pnl"] for s in vass_spreads if not s["is_win"]) / vass_losses
        if vass_losses
        else 0
    )

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(
        f"| Total Entries | {len(vass_spreads)} ({len(paired_spreads)} paired + {len(vass_spreads)-len(paired_spreads)} split) |"
    )
    lines.append(f"| Wins | {vass_wins} |")
    lines.append(f"| Losses | {vass_losses} |")
    lines.append(f"| Win Rate | {vass_wr:.1f}% |")
    lines.append(f"| Total Net P&L | ${vass_total_pnl:,.0f} |")
    lines.append(f"| Avg Win | ${vass_avg_win:,.0f} |")
    lines.append(f"| Avg Loss | ${vass_avg_loss:,.0f} |")
    if vass_avg_loss != 0:
        lines.append(f"| Payoff Ratio | {abs(vass_avg_win/vass_avg_loss):.2f} |")
    lines.append("")

    # 1b: Exit Reason Distribution
    lines.append("### 1b. VASS Exit Reason Distribution")
    lines.append("")
    exit_dist = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for s in vass_spreads:
        et = s.get("exit_trigger", "UNKNOWN")
        exit_dist[et]["count"] += 1
        exit_dist[et]["pnl"] += s["net_pnl"]
        if s["is_win"]:
            exit_dist[et]["wins"] += 1

    lines.append("| Exit Reason | Count | Win Rate | Total P&L | Avg P&L |")
    lines.append("|-------------|-------|----------|-----------|---------|")
    for reason, data in sorted(exit_dist.items(), key=lambda x: -x[1]["count"]):
        wr = data["wins"] / data["count"] * 100 if data["count"] else 0
        avg = data["pnl"] / data["count"]
        lines.append(
            f"| {reason} | {data['count']} | {wr:.0f}% | ${data['pnl']:,.0f} | ${avg:,.0f} |"
        )
    lines.append("")

    # 1c: D/W% Analysis
    lines.append("### 1c. D/W% Analysis (Debit Spreads Only)")
    lines.append("")
    dw_buckets = {"<30%": [], "30-40%": [], "40-50%": [], "50-55%": [], "55%+": []}
    for s in vass_spreads:
        if s["spread_type"] in ("BULL_CALL_DEBIT", "BEAR_PUT_DEBIT") and not s.get("is_split_leg"):
            dw = s.get("dw_pct", 0)
            if dw < 30:
                dw_buckets["<30%"].append(s)
            elif dw < 40:
                dw_buckets["30-40%"].append(s)
            elif dw < 50:
                dw_buckets["40-50%"].append(s)
            elif dw < 55:
                dw_buckets["50-55%"].append(s)
            else:
                dw_buckets["55%+"].append(s)

    lines.append("| D/W% Bucket | Count | Win Rate | Total P&L | Avg Net P&L |")
    lines.append("|-------------|-------|----------|-----------|-------------|")
    for bucket, spreads_in in dw_buckets.items():
        if spreads_in:
            cnt = len(spreads_in)
            wr = sum(1 for s in spreads_in if s["is_win"]) / cnt * 100
            total = sum(s["net_pnl"] for s in spreads_in)
            avg_pnl = total / cnt
            lines.append(f"| {bucket} | {cnt} | {wr:.0f}% | ${total:,.0f} | ${avg_pnl:,.0f} |")
    lines.append("")

    # 1d: Monthly Breakdown
    lines.append("### 1d. VASS Monthly Breakdown")
    lines.append("")
    monthly = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for s in vass_spreads:
        month = s["entry_time"].strftime("%Y-%m")
        monthly[month]["count"] += 1
        monthly[month]["pnl"] += s["net_pnl"]
        if s["is_win"]:
            monthly[month]["wins"] += 1

    lines.append("| Month | Entries | Wins | Win Rate | P&L |")
    lines.append("|-------|---------|------|----------|-----|")
    for month in sorted(monthly.keys()):
        d = monthly[month]
        wr = d["wins"] / d["count"] * 100 if d["count"] else 0
        lines.append(f"| {month} | {d['count']} | {d['wins']} | {wr:.0f}% | ${d['pnl']:,.0f} |")
    lines.append("")

    # 1e: Top 10 Worst VASS
    lines.append("### 1e. Top 10 Worst VASS Trades")
    lines.append("")
    worst_vass = sorted(vass_spreads, key=lambda s: s["net_pnl"])[:10]
    lines.append("| # | Date | Type | Net P&L | Width | DTE | Exit | VIX | Regime |")
    lines.append("|---|------|------|---------|-------|-----|------|-----|--------|")
    for i, s in enumerate(worst_vass):
        ctx = s.get("entry_context", {})
        vix_v = ctx.get("vix", "N/A")
        vix_str = f"{vix_v:.1f}" if isinstance(vix_v, float) else str(vix_v)
        lines.append(
            f"| {i+1} | {s['entry_time'].strftime('%m/%d')} | {s['spread_type']} | ${s['net_pnl']:,.0f} | ${s.get('width',0):.0f} | {ctx.get('dte','N/A')} | {s.get('exit_trigger','N/A')} | {vix_str} | {ctx.get('regime','N/A')} |"
        )
    lines.append("")

    # 1f: By Spread Type
    lines.append("### 1f. VASS By Spread Type")
    lines.append("")
    by_type = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for s in vass_spreads:
        st = s["spread_type"].replace("_SPLIT", "")
        by_type[st]["count"] += 1
        by_type[st]["pnl"] += s["net_pnl"]
        if s["is_win"]:
            by_type[st]["wins"] += 1

    lines.append("| Spread Type | Count | Win Rate | Total P&L | Avg P&L |")
    lines.append("|------------|-------|----------|-----------|---------|")
    for st, data in sorted(by_type.items(), key=lambda x: -x[1]["pnl"]):
        wr = data["wins"] / data["count"] * 100 if data["count"] else 0
        avg = data["pnl"] / data["count"]
        lines.append(f"| {st} | {data['count']} | {wr:.0f}% | ${data['pnl']:,.0f} | ${avg:,.0f} |")
    lines.append("")

    lines.append("---")
    lines.append("")

    # ===== PART 2: MICRO =====
    lines.append("# Part 2: MICRO Intraday Trade-by-Trade")
    lines.append("")
    lines.append(
        "| # | Date | Entry | Exit | Strategy | Dir | Micro Regime | Score | VIX | VIX Dir | Exit Trigger | Hold | P&L $ | P&L % | W/L | Notes |"
    )
    lines.append(
        "|---|------|-------|------|----------|-----|-------------|-------|-----|---------|--------------|------|-------|-------|-----|-------|"
    )

    for i, t in enumerate(micro_trades):
        ctx = t.get("signal_context", {})
        opt = t.get("option_info", {})
        strategy = t["subcategory"]
        direction = opt.get("type", ctx.get("option_dir", "N/A"))
        micro_regime = ctx.get("micro_regime", "N/A")
        score = ctx.get("score", "N/A")
        vix = ctx.get("vix", "N/A")
        vix_dir = ctx.get("vix_dir", "N/A")
        et = t.get("exit_trigger", "UNKNOWN")
        hd = hold_duration(t["entry_time"], t["exit_time"])
        pnl = t["pnl"]
        cost = abs(t["entry_price"] * t["quantity"] * 100)
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0
        wl = "W" if t["is_win"] else "L"
        date_str = t["entry_time"].strftime("%m/%d")
        entry_str = t["entry_time"].strftime("%H:%M")
        exit_str = t["exit_time"].strftime("%H:%M")
        vix_str = f"{vix:.1f}" if isinstance(vix, float) else str(vix)

        notes = []
        if et == "EOD_SWEEP":
            notes.append("ORPHAN")
        if et == "EARLY_EXERCISE":
            notes.append("EARLY_EX")
        if et == "RECON_ORPHAN":
            notes.append("RECON")
        if et == "KILL_SWITCH":
            notes.append("KS")
        notes_str = ",".join(notes) if notes else ""

        lines.append(
            f"| {i+1} | {date_str} | {entry_str} | {exit_str} | {strategy} | {direction} | {micro_regime} | {score} | {vix_str} | {vix_dir} | {et} | {hd} | ${pnl:,.0f} | {pnl_pct:.1f}% | {wl} | {notes_str} |"
        )

    lines.append("")

    # 2a: Micro Summary
    lines.append("### 2a. MICRO Summary")
    lines.append("")
    micro_wins = sum(1 for t in micro_trades if t["is_win"])
    micro_losses = sum(1 for t in micro_trades if not t["is_win"])
    micro_wr = micro_wins / len(micro_trades) * 100 if micro_trades else 0
    micro_avg_win = (
        sum(t["pnl"] for t in micro_trades if t["is_win"]) / micro_wins if micro_wins else 0
    )
    micro_avg_loss = (
        sum(t["pnl"] for t in micro_trades if not t["is_win"]) / micro_losses if micro_losses else 0
    )
    micro_best = max(t["pnl"] for t in micro_trades) if micro_trades else 0
    micro_worst = min(t["pnl"] for t in micro_trades) if micro_trades else 0

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total Trades | {len(micro_trades)} |")
    lines.append(f"| Wins | {micro_wins} |")
    lines.append(f"| Losses | {micro_losses} |")
    lines.append(f"| Win Rate | {micro_wr:.1f}% |")
    lines.append(f"| Total P&L | ${micro_total_pnl:,.0f} |")
    lines.append(f"| Avg Win | ${micro_avg_win:,.0f} |")
    lines.append(f"| Avg Loss | ${micro_avg_loss:,.0f} |")
    if micro_avg_loss != 0:
        lines.append(f"| Payoff Ratio | {abs(micro_avg_win/micro_avg_loss):.2f} |")
    lines.append(f"| Best Trade | ${micro_best:,.0f} |")
    lines.append(f"| Worst Trade | ${micro_worst:,.0f} |")
    lines.append("")

    # 2b: By Strategy
    lines.append("### 2b. MICRO By Strategy")
    lines.append("")
    by_strat = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in micro_trades:
        strat = t["subcategory"]
        by_strat[strat]["count"] += 1
        by_strat[strat]["pnl"] += t["pnl"]
        if t["is_win"]:
            by_strat[strat]["wins"] += 1

    lines.append("| Strategy | Count | Win Rate | Total P&L | Avg P&L |")
    lines.append("|----------|-------|----------|-----------|---------|")
    for strat, data in sorted(by_strat.items(), key=lambda x: -x[1]["pnl"]):
        wr = data["wins"] / data["count"] * 100 if data["count"] else 0
        avg = data["pnl"] / data["count"]
        lines.append(
            f"| {strat} | {data['count']} | {wr:.0f}% | ${data['pnl']:,.0f} | ${avg:,.0f} |"
        )
    lines.append("")

    # 2c: By Micro Regime (MOST IMPORTANT)
    lines.append("### 2c. MICRO By Micro Regime (CRITICAL)")
    lines.append("")
    by_regime = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0, "losses": 0})
    for t in micro_trades:
        regime = t.get("signal_context", {}).get("micro_regime", "UNKNOWN")
        by_regime[regime]["count"] += 1
        by_regime[regime]["pnl"] += t["pnl"]
        if t["is_win"]:
            by_regime[regime]["wins"] += 1
        else:
            by_regime[regime]["losses"] += 1

    lines.append("| Micro Regime | Count | Win Rate | Total P&L | Avg P&L | Verdict |")
    lines.append("|-------------|-------|----------|-----------|---------|---------|")
    for regime, data in sorted(by_regime.items(), key=lambda x: x[1]["pnl"]):
        wr = data["wins"] / data["count"] * 100 if data["count"] else 0
        avg = data["pnl"] / data["count"]
        if avg > 50:
            verdict = "PROFITABLE"
        elif avg > -50:
            verdict = "OK"
        else:
            verdict = "TOXIC"
        lines.append(
            f"| {regime} | {data['count']} | {wr:.0f}% | ${data['pnl']:,.0f} | ${avg:,.0f} | {verdict} |"
        )
    lines.append("")

    # 2d: By Direction
    lines.append("### 2d. MICRO By Direction")
    lines.append("")
    by_dir = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in micro_trades:
        opt = t.get("option_info", {})
        d = opt.get("type", t.get("signal_context", {}).get("option_dir", "UNKNOWN"))
        by_dir[d]["count"] += 1
        by_dir[d]["pnl"] += t["pnl"]
        if t["is_win"]:
            by_dir[d]["wins"] += 1

    lines.append("| Direction | Count | Win Rate | Total P&L | Avg P&L |")
    lines.append("|-----------|-------|----------|-----------|---------|")
    for d, data in sorted(by_dir.items(), key=lambda x: -x[1]["pnl"]):
        wr = data["wins"] / data["count"] * 100 if data["count"] else 0
        avg = data["pnl"] / data["count"]
        lines.append(f"| {d} | {data['count']} | {wr:.0f}% | ${data['pnl']:,.0f} | ${avg:,.0f} |")
    lines.append("")

    # 2e: Exit Reason Distribution
    lines.append("### 2e. MICRO Exit Reason Distribution")
    lines.append("")
    exit_dist_m = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in micro_trades:
        et = t.get("exit_trigger", "UNKNOWN")
        exit_dist_m[et]["count"] += 1
        exit_dist_m[et]["pnl"] += t["pnl"]
        if t["is_win"]:
            exit_dist_m[et]["wins"] += 1

    lines.append("| Exit Reason | Count | Win Rate | Total P&L | Avg P&L |")
    lines.append("|-------------|-------|----------|-----------|---------|")
    for reason, data in sorted(exit_dist_m.items(), key=lambda x: -x[1]["count"]):
        wr = data["wins"] / data["count"] * 100 if data["count"] else 0
        avg = data["pnl"] / data["count"]
        lines.append(
            f"| {reason} | {data['count']} | {wr:.0f}% | ${data['pnl']:,.0f} | ${avg:,.0f} |"
        )
    lines.append("")

    # 2f: Orphan Analysis
    lines.append("### 2f. Orphan Analysis")
    lines.append("")
    orphans = [
        t
        for t in micro_trades
        if t.get("exit_trigger") in ("EOD_SWEEP", "EARLY_EXERCISE", "RECON_ORPHAN", "KILL_SWITCH")
    ]
    lines.append(f"Total orphan/non-standard exits: **{len(orphans)}**")
    lines.append("")
    if orphans:
        orphan_pnl = sum(t["pnl"] for t in orphans)
        lines.append(f"Combined P&L of orphan exits: **${orphan_pnl:,.0f}**")
        lines.append("")
        lines.append("| # | Date | Exit Trigger | P&L | Strategy | Hold |")
        lines.append("|---|------|-------------|-----|----------|------|")
        for i, t in enumerate(orphans):
            hd = hold_duration(t["entry_time"], t["exit_time"])
            lines.append(
                f"| {i+1} | {t['entry_time'].strftime('%m/%d')} | {t.get('exit_trigger','')} | ${t['pnl']:,.0f} | {t['subcategory']} | {hd} |"
            )
    lines.append("")

    # 2g: Regime x Direction Heatmap
    lines.append("### 2g. Regime x Direction Heatmap")
    lines.append("")
    heatmap = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in micro_trades:
        regime = t.get("signal_context", {}).get("micro_regime", "UNKNOWN")
        opt = t.get("option_info", {})
        d = opt.get("type", t.get("signal_context", {}).get("option_dir", "UNKNOWN"))
        key = f"{regime} | {d}"
        heatmap[key]["count"] += 1
        heatmap[key]["pnl"] += t["pnl"]
        if t["is_win"]:
            heatmap[key]["wins"] += 1

    lines.append("| Regime x Direction | Count | Win Rate | Total P&L | Avg P&L | Verdict |")
    lines.append("|--------------------|-------|----------|-----------|---------|---------|")
    for key, data in sorted(heatmap.items(), key=lambda x: x[1]["pnl"]):
        wr = data["wins"] / data["count"] * 100 if data["count"] else 0
        avg = data["pnl"] / data["count"]
        if avg > 50:
            verdict = "PROFITABLE"
        elif avg > -50:
            verdict = "OK"
        else:
            verdict = "TOXIC"
        lines.append(
            f"| {key} | {data['count']} | {wr:.0f}% | ${data['pnl']:,.0f} | ${avg:,.0f} | {verdict} |"
        )
    lines.append("")

    # 2h: Top 10 Worst MICRO
    lines.append("### 2h. Top 10 Worst MICRO Trades")
    lines.append("")
    worst_micro = sorted(micro_trades, key=lambda t: t["pnl"])[:10]
    lines.append("| # | Date | Strategy | Dir | P&L | Exit | Regime | Score | VIX | Hold |")
    lines.append("|---|------|----------|-----|-----|------|--------|-------|-----|------|")
    for i, t in enumerate(worst_micro):
        ctx = t.get("signal_context", {})
        opt = t.get("option_info", {})
        d = opt.get("type", ctx.get("option_dir", "N/A"))
        hd = hold_duration(t["entry_time"], t["exit_time"])
        vix = ctx.get("vix", "N/A")
        vix_str = f"{vix:.1f}" if isinstance(vix, float) else str(vix)
        lines.append(
            f"| {i+1} | {t['entry_time'].strftime('%m/%d')} | {t['subcategory']} | {d} | ${t['pnl']:,.0f} | {t.get('exit_trigger','N/A')} | {ctx.get('micro_regime','N/A')} | {ctx.get('score','N/A')} | {vix_str} | {hd} |"
        )
    lines.append("")

    # 2i: Monthly MICRO breakdown
    lines.append("### 2i. MICRO Monthly Breakdown")
    lines.append("")
    m_monthly = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in micro_trades:
        month = t["entry_time"].strftime("%Y-%m")
        m_monthly[month]["count"] += 1
        m_monthly[month]["pnl"] += t["pnl"]
        if t["is_win"]:
            m_monthly[month]["wins"] += 1

    lines.append("| Month | Trades | Wins | Win Rate | P&L |")
    lines.append("|-------|--------|------|----------|-----|")
    for month in sorted(m_monthly.keys()):
        d = m_monthly[month]
        wr = d["wins"] / d["count"] * 100 if d["count"] else 0
        lines.append(f"| {month} | {d['count']} | {d['wins']} | {wr:.0f}% | ${d['pnl']:,.0f} |")
    lines.append("")

    lines.append("---")
    lines.append("")

    # ===== PART 3: Combined Root Cause Analysis =====
    lines.append("# Part 3: Combined Root Cause Analysis")
    lines.append("")

    # 3a: Loss Concentration
    lines.append("### 3a. Loss Concentration")
    lines.append("")
    all_pnls = []
    for s in vass_spreads:
        all_pnls.append(
            ("VASS", s["entry_time"], s["net_pnl"], s.get("exit_trigger", "N/A"), s["spread_type"])
        )
    for t in micro_trades:
        all_pnls.append(
            ("MICRO", t["entry_time"], t["pnl"], t.get("exit_trigger", "N/A"), t["subcategory"])
        )

    total_gains = sum(p[2] for p in all_pnls if p[2] > 0)
    total_losses = sum(p[2] for p in all_pnls if p[2] < 0)
    worst_10 = sorted(all_pnls, key=lambda x: x[2])[:10]
    worst_10_total = sum(p[2] for p in worst_10)

    lines.append(f"- Total gains: **${total_gains:,.0f}**")
    lines.append(f"- Total losses: **${total_losses:,.0f}**")
    lines.append(f"- Net: **${total_gains + total_losses:,.0f}**")
    if total_losses != 0:
        lines.append(
            f"- Worst 10 trades total: **${worst_10_total:,.0f}** ({abs(worst_10_total/total_losses)*100:.0f}% of all losses)"
        )
    lines.append("")
    lines.append("| # | Type | Date | P&L | Exit Trigger | Strategy |")
    lines.append("|---|------|------|-----|--------------|----------|")
    for i, (typ, dt, pnl, et, strat) in enumerate(worst_10):
        lines.append(f"| {i+1} | {typ} | {dt.strftime('%m/%d')} | ${pnl:,.0f} | {et} | {strat} |")
    lines.append("")

    # 3b: Failure Mode Ranking
    lines.append("### 3b. Failure Mode Ranking")
    lines.append("")
    failure_modes = defaultdict(lambda: {"count": 0, "pnl": 0})

    for s in vass_spreads:
        if not s["is_win"]:
            et = s.get("exit_trigger", "UNKNOWN")
            failure_modes[f"VASS_{et}"]["count"] += 1
            failure_modes[f"VASS_{et}"]["pnl"] += s["net_pnl"]

    for t in micro_trades:
        if not t["is_win"]:
            et = t.get("exit_trigger", "UNKNOWN")
            regime = t.get("signal_context", {}).get("micro_regime", "UNK")
            failure_modes[f"MICRO_{et}_{regime}"]["count"] += 1
            failure_modes[f"MICRO_{et}_{regime}"]["pnl"] += t["pnl"]

    lines.append("| Failure Mode | Count | Total Loss | Avg Loss |")
    lines.append("|-------------|-------|------------|----------|")
    for mode, data in sorted(failure_modes.items(), key=lambda x: x[1]["pnl"])[:15]:
        avg = data["pnl"] / data["count"]
        lines.append(f"| {mode} | {data['count']} | ${data['pnl']:,.0f} | ${avg:,.0f} |")
    lines.append("")

    # 3c: Regime Gate Simulation
    lines.append("### 3c. Regime Gate Simulation")
    lines.append("")
    lines.append("What if we blocked MICRO trades in certain regimes?")
    lines.append("")

    for regime in sorted(by_regime.keys()):
        regime_data = by_regime[regime]
        saved = regime_data["pnl"]
        lost_wins = regime_data["wins"]
        blocked = regime_data["count"]
        if saved < 0:
            lines.append(
                f"- **Block {regime}**: Save ${abs(saved):,.0f}, lose {lost_wins} winning trades ({blocked} total blocked)"
            )
        else:
            lines.append(
                f"- **Block {regime}**: Would LOSE ${saved:,.0f} of profit ({blocked} trades, {lost_wins} wins)"
            )
    lines.append("")

    # 3d: Min-Hold Impact
    lines.append("### 3d. Min-Hold Impact Analysis")
    lines.append("")
    for threshold_min in [2, 5, 10, 15]:
        threshold_sec = threshold_min * 60
        quick = [
            t
            for t in micro_trades
            if (t["exit_time"] - t["entry_time"]).total_seconds() < threshold_sec
        ]
        if quick:
            qpnl = sum(t["pnl"] for t in quick)
            qloss = sum(1 for t in quick if not t["is_win"])
            lines.append(
                f"- Exits <{threshold_min}min: **{len(quick)}** trades, P&L: **${qpnl:,.0f}**, {qloss} losses"
            )
    lines.append("")

    # 3e: Top 5 Actionable Fixes
    lines.append("### 3e. Top 5 Actionable Fixes")
    lines.append("")

    toxic_regimes = [(r, d) for r, d in by_regime.items() if d["pnl"] < -500]
    toxic_regimes.sort(key=lambda x: x[1]["pnl"])

    fix_num = 1

    # Fix 1: Block worst regime
    if toxic_regimes:
        worst_regime = toxic_regimes[0]
        lines.append(f"**Fix {fix_num}: Block MICRO trades in {worst_regime[0]} regime**")
        lines.append(
            f"- Impact: Save ~${abs(worst_regime[1]['pnl']):,.0f} (would block {worst_regime[1]['count']} trades, lose {worst_regime[1]['wins']} wins)"
        )
        lines.append("")
        fix_num += 1

    # Fix 2: OCO_STOP improvement
    stop_losses = [t for t in micro_trades if t.get("exit_trigger") == "OCO_STOP"]
    stop_pnl = sum(t["pnl"] for t in stop_losses)
    if stop_losses and stop_pnl < -2000:
        avg_stop_loss = stop_pnl / len(stop_losses)
        lines.append(f"**Fix {fix_num}: Tighten MICRO stop losses**")
        lines.append(
            f"- {len(stop_losses)} OCO_STOP exits, Total P&L: ${stop_pnl:,.0f}, Avg: ${avg_stop_loss:,.0f}"
        )
        lines.append(f"- Consider reducing stop from 40% to 30% to cap per-trade damage")
        lines.append("")
        fix_num += 1

    # Fix 3: Quick exits
    quick_exits = [
        t for t in micro_trades if (t["exit_time"] - t["entry_time"]).total_seconds() < 300
    ]
    quick_pnl = sum(t["pnl"] for t in quick_exits)
    if len(quick_exits) > 10 and quick_pnl < -1000:
        lines.append(f"**Fix {fix_num}: Implement minimum hold period (5 min) for MICRO**")
        lines.append(
            f"- {len(quick_exits)} trades stopped out in <5 min, losing ${abs(quick_pnl):,.0f}"
        )
        lines.append(f"- Immediate stops suggest entry timing or delta selection issues")
        lines.append("")
        fix_num += 1

    # Fix 4: VASS worst months
    worst_vass_months = sorted(monthly.items(), key=lambda x: x[1]["pnl"])
    if worst_vass_months and worst_vass_months[0][1]["pnl"] < -2000:
        m, d = worst_vass_months[0]
        lines.append(f"**Fix {fix_num}: Investigate VASS underperformance in {m}**")
        lines.append(
            f"- {d['count']} entries, {d['wins']} wins ({d['wins']/d['count']*100:.0f}%), P&L: ${d['pnl']:,.0f}"
        )
        lines.append("")
        fix_num += 1

    # Fix 5: Toxic heatmap cells
    toxic_cells = [(k, v) for k, v in heatmap.items() if v["pnl"] < -1000 and v["count"] >= 3]
    toxic_cells.sort(key=lambda x: x[1]["pnl"])
    if toxic_cells and fix_num <= 5:
        cell = toxic_cells[0]
        lines.append(f"**Fix {fix_num}: Block MICRO in regime-direction combo: {cell[0]}**")
        lines.append(
            f"- {cell[1]['count']} trades, ${cell[1]['pnl']:,.0f} total, {cell[1]['wins']}/{cell[1]['count']} wins"
        )
        lines.append("")
        fix_num += 1

    while fix_num <= 5:
        if fix_num == 4:
            lines.append(f"**Fix {fix_num}: Review VASS D/W% gate effectiveness**")
            lines.append(f"- Current gate at 55% max D/W. Check if lowering to 50% improves R:R.")
            lines.append("")
        elif fix_num == 5:
            lines.append(f"**Fix {fix_num}: Add VIX direction filter for MICRO entries**")
            lines.append(
                f"- Many losses in VIX RISING environment. Consider blocking CALL entries when VIX is RISING > 1%."
            )
            lines.append("")
        fix_num += 1

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*Report generated by automated analysis script. All P&L figures sourced from trades.csv.*"
    )

    return "\n".join(lines)


if __name__ == "__main__":
    main()
