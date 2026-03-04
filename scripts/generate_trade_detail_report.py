#!/usr/bin/env python3
"""
Generate V10.8 Full Year 2024 Trade Detail Report.
Cross-references trades.csv, orders.csv, and logs.txt to produce
a comprehensive trade-by-trade analysis.

ACCURACY REQUIREMENTS:
- P&L must match trades.csv exactly
- IsWin from trades.csv is authoritative
- Every trade must appear in either VASS or MICRO table
- VASS legs must be paired
- Context columns populated (not N/A) where log data exists
"""

import csv
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

BASE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.8"
TRADES_FILE = os.path.join(BASE, "V10_8_FullYear2024_trades.csv")
ORDERS_FILE = os.path.join(BASE, "V10_8_FullYear2024_orders.csv")
LOGS_FILE = os.path.join(BASE, "V10_8_FullYear2024_logs.txt")
OUTPUT_FILE = os.path.join(BASE, "V10_8_FullYear2024_TRADE_DETAIL_REPORT.md")


def _is_reconciled_close_marker(text: str) -> bool:
    """Match reconciled-close reason variants with optional suffix payloads."""
    upper = str(text or "").upper()
    if not upper:
        return False
    return bool(
        re.search(r"\b(FILL_CLOSE_RECONCILED|RECONCILED_CLOSE(?:[:_|A-Z0-9-].*)?)\b", upper)
    )


def parse_trades():
    """Parse trades.csv - source of truth for P&L."""
    trades = []
    with open(TRADES_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cleaned = {}
            for k, v in row.items():
                cleaned[k.strip()] = v.strip() if v else ""
            order_ids_raw = cleaned.get("Order Ids", "").strip()
            order_ids = [x.strip() for x in order_ids_raw.split(",") if x.strip()]
            trades.append(
                {
                    "entry_time": cleaned["Entry Time"],
                    "symbol": cleaned["Symbols"].strip().strip('"'),
                    "exit_time": cleaned["Exit Time"],
                    "direction": cleaned["Direction"],
                    "entry_price": float(cleaned["Entry Price"]),
                    "exit_price": float(cleaned["Exit Price"]),
                    "quantity": int(cleaned["Quantity"]),
                    "pnl": float(cleaned["P&L"]),
                    "fees": float(cleaned["Fees"]),
                    "drawdown": float(cleaned["Drawdown"]),
                    "is_win": int(cleaned["IsWin"]),
                    "order_ids": order_ids,
                }
            )
    return trades


def parse_orders():
    """Parse orders.csv for strategy tags."""
    orders = {}
    with open(ORDERS_FILE, "r") as f:
        reader = csv.reader(f)
        header = next(reader)
        h = [x.strip() for x in header]
        time_idx = h.index("Time")
        sym_idx = h.index("Symbol")
        price_idx = h.index("Price")
        qty_idx = h.index("Quantity")
        type_idx = h.index("Type")
        status_idx = h.index("Status")
        tag_idx = h.index("Tag")

        order_num = 0
        for row in reader:
            order_num += 1
            tag = row[tag_idx].strip().strip('"') if len(row) > tag_idx else ""
            status = row[status_idx].strip() if len(row) > status_idx else ""
            orders[str(order_num)] = {
                "time": row[time_idx].strip(),
                "symbol": row[sym_idx].strip(),
                "price": row[price_idx].strip(),
                "quantity": row[qty_idx].strip(),
                "type": row[type_idx].strip(),
                "status": status,
                "tag": tag,
                "order_num": order_num,
            }
    return orders


def parse_logs():
    """Parse logs file and index by date for fast lookup."""
    logs_by_date = defaultdict(list)
    all_spread_exits = []  # Collect all SPREAD: EXIT lines
    all_spread_entries = []  # Collect all SPREAD: ENTRY_SIGNAL lines

    with open(LOGS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(.*)", line)
            if m:
                date_str = m.group(1)
                time_str = m.group(2)
                content = m.group(3)
                if date_str.startswith("2024"):
                    entry = {
                        "date": date_str,
                        "time": time_str,
                        "content": content,
                        "full": line,
                    }
                    logs_by_date[date_str].append(entry)

                    # Index spread exits separately
                    if "SPREAD: EXIT |" in content:
                        all_spread_exits.append(entry)
                    if (
                        "SPREAD: ENTRY_SIGNAL" in content
                        or "SPREAD: POSITION_REGISTERED" in content
                    ):
                        all_spread_entries.append(entry)

    return logs_by_date, all_spread_exits, all_spread_entries


def classify_trade(trade, orders):
    """Classify trade as VASS or MICRO based on order tags."""
    for oid in trade["order_ids"]:
        if oid in orders:
            tag = orders[oid]["tag"]
            if tag.startswith("VASS:"):
                return "VASS", tag
            elif tag.startswith("MICRO:"):
                return "MICRO", tag
            elif tag in ("VASS", "MICRO", "MICRO_EOD_SWEEP", "KS_SINGLE_LEG"):
                base = tag.split("_")[0] if "_" in tag else tag
                return base, tag
            elif tag == "RECON_ORPHAN_OPTION":
                return "ORPHAN", tag
            elif tag in (
                "EARLY_EXERCISE_GUARD",
                "RETRY_CANCELED_CLOSE",
                "EMERG_OPTION_RETRY_EXHAUSTED",
            ):
                continue
    # Fallback
    for oid in trade["order_ids"]:
        if oid in orders:
            tag = orders[oid]["tag"]
            if tag:
                if "VASS" in tag:
                    return "VASS", tag
                if "MICRO" in tag:
                    return "MICRO", tag
    return "UNKNOWN", ""


def get_entry_order_tag(trade, orders):
    """Get the entry order tag (first filled order with entry tag)."""
    for oid in trade["order_ids"]:
        if oid in orders:
            o = orders[oid]
            tag = o["tag"]
            if (
                o["status"] == "Filled"
                and tag
                and (tag.startswith("VASS:") or tag.startswith("MICRO:"))
            ):
                return tag
    if trade["order_ids"] and trade["order_ids"][0] in orders:
        return orders[trade["order_ids"][0]]["tag"]
    return ""


def parse_timestamp(ts_str):
    """Parse ISO timestamp to datetime."""
    return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")


def calc_hold_duration(entry_str, exit_str):
    """Calculate hold duration as string."""
    entry = parse_timestamp(entry_str)
    exit_dt = parse_timestamp(exit_str)
    diff = exit_dt - entry
    total_hours = diff.total_seconds() / 3600
    if total_hours < 24:
        return f"{total_hours:.1f}h"
    else:
        days = diff.days
        remaining_hours = (diff.total_seconds() - days * 86400) / 3600
        return f"{days}d{remaining_hours:.0f}h"


def parse_strike(sym):
    """Parse strike from OCC symbol."""
    m = re.search(r"[CP](\d{8})", sym)
    if m:
        return int(m.group(1)) / 1000
    return 0


def parse_expiry(sym):
    """Parse expiry date from OCC symbol."""
    m = re.search(r"QQQ\s+(\d{6})", sym)
    if m:
        return datetime.strptime("20" + m.group(1), "%Y%m%d")
    return None


def parse_option_type(sym):
    """Parse C/P from OCC symbol."""
    sym = sym.strip()
    for i, c in enumerate(sym):
        if c in ("C", "P") and i > 5:
            return "CALL" if c == "C" else "PUT"
    return "N/A"


def match_spread_exit(spread, all_spread_exits, tolerance=0.15):
    """Match a VASS spread to its SPREAD: EXIT log line using entry debit/credit and type."""
    exit_dt = parse_timestamp(spread["exit_time"])
    exit_date = exit_dt.strftime("%Y-%m-%d")

    entry_debit = spread["debit"]  # Always positive (abs value)
    spread_type_raw = spread["spread_type"].replace(" (ROLLED)", "")
    # Map to log type: BULL_CALL_DEBIT -> BULL_CALL, BEAR_CALL_CREDIT -> BEAR_CALL_CREDIT
    spread_type_log = spread_type_raw.replace("_DEBIT", "").replace("_CREDIT", "")

    # For credit spreads, log shows Entry as negative, for debit spreads as positive
    is_credit = "CREDIT" in spread_type_raw

    best_reason = "UNKNOWN"

    for log in all_spread_exits:
        if log["date"] != exit_date:
            continue

        content = log["content"]

        m_type = re.search(r"Type=(\w+(?:_\w+)*)", content)
        m_entry = re.search(r"Entry=(-?[\d.]+)", content)

        if not m_type or not m_entry:
            continue

        log_type = m_type.group(1)
        log_entry_val = float(m_entry.group(1))

        # Match type: credit spreads logged as BEAR_CALL_CREDIT, debit as BULL_CALL
        type_matches = False
        if is_credit and log_type == spread_type_raw.replace("_", "_"):
            type_matches = True
        elif is_credit and spread_type_log in log_type:
            type_matches = True
        elif not is_credit and log_type == spread_type_log:
            type_matches = True

        if not type_matches:
            continue

        # Match entry value: for credit spreads, log shows negative entry
        if is_credit:
            if abs(abs(log_entry_val) - entry_debit) < tolerance:
                pass  # matches
            else:
                continue
        else:
            if abs(log_entry_val - entry_debit) < tolerance:
                pass  # matches
            else:
                continue

        # Extract and categorize reason
        m_reason = re.search(r"Reason=([^|]+)", content)
        if m_reason:
            reason_full = m_reason.group(1).strip()
            if "HARD_STOP_TRIGGERED_WIDTH" in reason_full:
                best_reason = "HARD_STOP_WIDTH"
            elif "HARD_STOP" in reason_full:
                best_reason = "HARD_STOP"
            elif "CREDIT_STOP_LOSS" in reason_full:
                best_reason = "CREDIT_STOP_LOSS"
            elif "STOP_LOSS" in reason_full:
                best_reason = "STOP_LOSS"
            elif "CREDIT_PROFIT_TARGET" in reason_full:
                best_reason = "PROFIT_TARGET"
            elif "PROFIT_TARGET" in reason_full:
                best_reason = "PROFIT_TARGET"
            elif "TRAIL_STOP" in reason_full:
                best_reason = "TRAIL_STOP"
            elif "DTE_EXIT" in reason_full:
                best_reason = "DTE_EXIT"
            elif "DAY4_EOD_CLOSE" in reason_full:
                best_reason = "DAY4_EOD_CLOSE"
            elif "FRIDAY_FIREWALL" in reason_full:
                best_reason = "FRIDAY_FIREWALL"
            elif _is_reconciled_close_marker(reason_full):
                # FILL_CLOSE_RECONCILED at 15:45 = FRIDAY_FIREWALL exit
                if log["time"] == "15:45:00" or log["time"][:5] == "15:45":
                    best_reason = "FRIDAY_FIREWALL"
                else:
                    best_reason = "RECONCILED"
            elif "ASSIGNMENT_RISK" in reason_full:
                best_reason = "ASSIGNMENT_RISK"
            elif "SPREAD_CLOSE_RETRY" in reason_full:
                best_reason = "CLOSE_RETRY"
            else:
                best_reason = reason_full[:30]
            break

    return best_reason


def get_regime_for_date(logs_by_date, date_str):
    """Get regime score and VIX for a given date."""
    if date_str not in logs_by_date:
        return "N/A", "N/A", "N/A"

    score = "N/A"
    vix = "N/A"
    regime = "N/A"

    for log in logs_by_date[date_str]:
        content = log["content"]
        if "REGIME:" in content and "RegimeState" in content:
            m_score = re.search(r"Score=([\d.]+)", content)
            m_vix = re.search(r"lvl=([\d.]+)", content)
            m_name = re.search(r"RegimeState\((\w+)", content)
            if m_score:
                score = m_score.group(1)
            if m_vix:
                vix = m_vix.group(1)
            if m_name:
                regime = m_name.group(1)

    return regime, score, vix


def get_micro_context(logs_by_date, entry_time_str, symbol):
    """Extract MICRO context from logs for a specific entry time."""
    dt = parse_timestamp(entry_time_str)
    date_str = dt.strftime("%Y-%m-%d")
    # entry_time in trades.csv may be 1 min after actual fill
    # So look for MICRO_UPDATE at or before entry time
    entry_minutes = dt.hour * 60 + dt.minute

    context = {
        "strategy": "ITM_MOMENTUM",
        "micro_regime": "N/A",
        "score": "N/A",
        "vix": "N/A",
        "vix_dir": "N/A",
        "direction": parse_option_type(symbol),
        "qqq_move": "N/A",
        "dte": "N/A",
    }

    # Calculate DTE from symbol
    exp = parse_expiry(symbol)
    if exp:
        entry_date = dt.replace(hour=0, minute=0, second=0)
        context["dte"] = str((exp - entry_date).days)

    if date_str not in logs_by_date:
        return context

    # Find the closest MICRO_UPDATE at or before entry time
    best_update = None
    best_update_minutes = -1

    for log in logs_by_date[date_str]:
        log_minutes = int(log["time"][:2]) * 60 + int(log["time"][3:5])
        content = log["content"]

        if "MICRO_UPDATE:" in content and log_minutes <= entry_minutes + 5:
            if log_minutes > best_update_minutes:
                best_update_minutes = log_minutes
                m_vix = re.search(r"VIX_level=([\d.]+)", content)
                m_tier = re.search(r"VIX_tier=(\w+)", content)
                m_regime = re.search(r"Regime=(\w+)", content)
                m_dir = re.search(r"Dir=(\w+)", content)
                m_uvxy = re.search(r"UVXY\s+([+-]?[\d.]+%)", content)

                best_update = {}
                if m_vix:
                    best_update["vix"] = m_vix.group(1)
                if m_tier:
                    best_update["vix_tier"] = m_tier.group(1)
                if m_regime:
                    best_update["micro_regime"] = m_regime.group(1)
                if m_dir:
                    best_update["vix_dir"] = m_dir.group(1)
                if m_uvxy:
                    best_update["uvxy"] = m_uvxy.group(1)

    if best_update:
        context["vix"] = best_update.get("vix", "N/A")
        context["micro_regime"] = best_update.get("micro_regime", "N/A")
        context["vix_dir"] = best_update.get("vix_dir", "N/A")

    return context


def get_micro_exit_trigger(trade, orders):
    """Determine exit trigger for MICRO trade from order tags."""
    # Look at all orders for exit fills
    for oid in trade["order_ids"]:
        if oid in orders:
            o = orders[oid]
            tag = o["tag"]
            qty_raw = o["quantity"]
            qty = int(qty_raw) if qty_raw else 0
            filled = o["status"] == "Filled"

            if filled and qty < 0:
                if o["type"] == "Stop Market":
                    return "OCO_STOP"
                if tag == "MICRO_EOD_SWEEP":
                    return "EOD_SWEEP"
                if tag == "EARLY_EXERCISE_GUARD":
                    return "EARLY_EXERCISE_GUARD"
                if tag == "KS_SINGLE_LEG":
                    return "KILL_SWITCH"
                if tag == "RETRY_CANCELED_CLOSE":
                    return "RETRY_CLOSE"
                if tag == "EMERG_OPTION_RETRY_EXHAUSTED":
                    return "EMERG_RETRY"
                if tag == "RECON_ORPHAN_OPTION":
                    return "ORPHAN_RECON"
                if tag == "MICRO":
                    return "MICRO_LIMIT"
                if tag == "" and o["type"] == "Limit":
                    return "OCO_PROFIT"
                if tag == "" and o["type"] == "Market":
                    return "MARKET_EXIT"
            # Also check positive qty for Sell direction trades
            if filled and qty > 0:
                if tag == "RECON_ORPHAN_OPTION":
                    return "ORPHAN_RECON"
                if tag == "RETRY_CANCELED_CLOSE":
                    return "RETRY_CLOSE"
                if tag == "EMERG_OPTION_RETRY_EXHAUSTED":
                    return "EMERG_RETRY"

    return "UNKNOWN"


def main():
    print("Parsing trades.csv...")
    trades = parse_trades()
    print(f"  Found {len(trades)} trade rows")

    print("Parsing orders.csv...")
    orders = parse_orders()
    print(f"  Found {len(orders)} orders")

    print("Parsing logs.txt...")
    logs_by_date, all_spread_exits, all_spread_entries = parse_logs()
    print(
        f"  Found logs for {len(logs_by_date)} dates, {len(all_spread_exits)} spread exits, {len(all_spread_entries)} spread entries"
    )

    # Step 1: Classify all trades
    print("Classifying trades...")
    vass_trades = []
    micro_trades = []
    orphan_trades = []
    unknown_trades = []

    for i, trade in enumerate(trades):
        classification, tag = classify_trade(trade, orders)
        trade["classification"] = classification
        trade["entry_tag"] = get_entry_order_tag(trade, orders)
        trade["trade_num"] = i + 1

        if classification == "VASS":
            vass_trades.append(trade)
        elif classification == "MICRO":
            micro_trades.append(trade)
        elif classification == "ORPHAN":
            orphan_trades.append(trade)
        else:
            unknown_trades.append(trade)

    print(
        f"  VASS: {len(vass_trades)}, MICRO: {len(micro_trades)}, ORPHAN: {len(orphan_trades)}, UNKNOWN: {len(unknown_trades)}"
    )

    # Step 2: Pair VASS spread legs
    # Pairing logic: Two VASS trades with the same entry time form a spread pair.
    # They may have different exit times in roll scenarios (one leg closed/rolled
    # before the other), but they always enter together.
    print("Pairing VASS spreads...")
    vass_spreads = []
    i = 0
    while i < len(vass_trades):
        t1 = vass_trades[i]
        if i + 1 < len(vass_trades):
            t2 = vass_trades[i + 1]
            # Same entry time = spread pair (exit may differ in roll scenarios)
            if t1["entry_time"] == t2["entry_time"]:
                # Determine long/short legs
                if t1["direction"] == "Buy" and t2["direction"] == "Sell":
                    long_leg = t1
                    short_leg = t2
                elif t1["direction"] == "Sell" and t2["direction"] == "Buy":
                    long_leg = t2
                    short_leg = t1
                else:
                    long_leg = t1
                    short_leg = t2

                net_pnl = t1["pnl"] + t2["pnl"]
                net_fees = t1["fees"] + t2["fees"]
                is_win = 1 if net_pnl > 0 else 0

                # Determine spread type from entry tag
                entry_tag = t1["entry_tag"] or t2["entry_tag"]
                if "BULL_CALL" in entry_tag:
                    spread_type = "BULL_CALL_DEBIT"
                elif "BEAR_PUT" in entry_tag:
                    spread_type = "BEAR_PUT_DEBIT"
                elif "BEAR_CALL" in entry_tag:
                    spread_type = "BEAR_CALL_CREDIT"
                elif "BULL_PUT" in entry_tag:
                    spread_type = "BULL_PUT_CREDIT"
                else:
                    spread_type = entry_tag if entry_tag else "UNKNOWN"

                # Calculate debit and width
                long_entry = long_leg["entry_price"]
                short_entry = short_leg["entry_price"]

                if "CREDIT" in spread_type:
                    debit = short_entry - long_entry  # Credit received
                else:
                    debit = long_entry - short_entry  # Debit paid

                long_strike = parse_strike(long_leg["symbol"])
                short_strike = parse_strike(short_leg["symbol"])
                width = abs(long_strike - short_strike)

                if width > 0:
                    dw_pct = abs(debit) / width * 100
                else:
                    dw_pct = 0

                # DTE
                exp = parse_expiry(long_leg["symbol"])
                entry_dt = parse_timestamp(t1["entry_time"])
                dte = (exp - entry_dt.replace(hour=0, minute=0, second=0)).days if exp else 0

                # Use the later exit time for hold duration (handles roll scenarios)
                exit_time_for_hold = max(t1["exit_time"], t2["exit_time"])
                hold = calc_hold_duration(t1["entry_time"], exit_time_for_hold)

                # Regime context from logs
                entry_date_str = entry_dt.strftime("%Y-%m-%d")
                regime, regime_score, vix = get_regime_for_date(logs_by_date, entry_date_str)

                # P&L% based on net debit/credit
                qty = long_leg["quantity"]
                entry_cost = abs(debit) * qty * 100
                pnl_pct = (net_pnl / entry_cost * 100) if entry_cost > 0 else 0

                spread_data = {
                    "entry_time": t1["entry_time"],
                    "exit_time": exit_time_for_hold,
                    "spread_type": spread_type,
                    "regime": regime,
                    "regime_score": regime_score,
                    "vix": vix,
                    "dte": dte,
                    "debit": abs(debit),
                    "width": width,
                    "dw_pct": dw_pct,
                    "exit_trigger": "PENDING",  # Will be matched below
                    "hold": hold,
                    "net_pnl": net_pnl,
                    "net_fees": net_fees,
                    "pnl_pct": pnl_pct,
                    "is_win": is_win,
                    "long_leg": long_leg,
                    "short_leg": short_leg,
                    "quantity": qty,
                }

                # Match exit trigger from log
                spread_data["exit_trigger"] = match_spread_exit(spread_data, all_spread_exits)

                vass_spreads.append(spread_data)
                i += 2
                continue

        # Check if this unpaired leg can be paired with the NEXT unpaired leg
        # (happens in roll scenarios where legs enter on different days but exit together)
        if i + 1 < len(vass_trades):
            t_next = vass_trades[i + 1]
            # If they share the same exit time, they're likely a rolled spread
            if t1["exit_time"] == t_next["exit_time"]:
                # Pair them as a synthetic spread
                if t1["direction"] == "Buy":
                    long_leg = t1
                    short_leg = t_next
                else:
                    long_leg = t_next
                    short_leg = t1

                net_pnl = t1["pnl"] + t_next["pnl"]
                net_fees = t1["fees"] + t_next["fees"]
                is_win = 1 if net_pnl > 0 else 0

                entry_tag = t1["entry_tag"] or t_next["entry_tag"]
                if "BULL_CALL" in entry_tag:
                    spread_type = "BULL_CALL_DEBIT"
                elif "BEAR_CALL" in entry_tag:
                    spread_type = "BEAR_CALL_CREDIT"
                else:
                    spread_type = entry_tag if entry_tag else "ROLLED"

                long_entry = long_leg["entry_price"]
                short_entry = short_leg["entry_price"]
                if "CREDIT" in spread_type:
                    debit = short_entry - long_entry
                else:
                    debit = long_entry - short_entry

                long_strike = parse_strike(long_leg["symbol"])
                short_strike = parse_strike(short_leg["symbol"])
                width = abs(long_strike - short_strike)
                dw_pct = (abs(debit) / width * 100) if width > 0 else 0

                exp = parse_expiry(long_leg["symbol"])
                # Use earlier entry time for DTE calc
                earlier_entry = min(t1["entry_time"], t_next["entry_time"])
                entry_dt_r = parse_timestamp(earlier_entry)
                dte = (exp - entry_dt_r.replace(hour=0, minute=0, second=0)).days if exp else 0

                hold = calc_hold_duration(earlier_entry, t1["exit_time"])
                entry_date_str = entry_dt_r.strftime("%Y-%m-%d")
                regime, regime_score, vix = get_regime_for_date(logs_by_date, entry_date_str)

                qty = long_leg["quantity"]
                entry_cost = abs(debit) * qty * 100
                pnl_pct = (net_pnl / entry_cost * 100) if entry_cost > 0 else 0

                spread_data = {
                    "entry_time": earlier_entry,
                    "exit_time": t1["exit_time"],
                    "spread_type": spread_type + " (ROLLED)",
                    "regime": regime,
                    "regime_score": regime_score,
                    "vix": vix,
                    "dte": dte,
                    "debit": abs(debit),
                    "width": width,
                    "dw_pct": dw_pct,
                    "exit_trigger": "PENDING",
                    "hold": hold,
                    "net_pnl": net_pnl,
                    "net_fees": net_fees,
                    "pnl_pct": pnl_pct,
                    "is_win": is_win,
                    "long_leg": long_leg,
                    "short_leg": short_leg,
                    "quantity": qty,
                }
                # For rolled spreads, the log's entry debit may differ from calculated
                # because the legs entered at different times. Use wider tolerance.
                spread_data["exit_trigger"] = match_spread_exit(
                    spread_data, all_spread_exits, tolerance=0.30
                )
                vass_spreads.append(spread_data)
                i += 2
                continue

        # Truly unpaired VASS leg
        entry_date_str = parse_timestamp(t1["entry_time"]).strftime("%Y-%m-%d")
        regime, regime_score, vix = get_regime_for_date(logs_by_date, entry_date_str)
        vass_spreads.append(
            {
                "entry_time": t1["entry_time"],
                "exit_time": t1["exit_time"],
                "spread_type": t1["entry_tag"] or "UNPAIRED",
                "regime": regime,
                "regime_score": regime_score,
                "vix": vix,
                "dte": 0,
                "debit": t1["entry_price"],
                "width": 0,
                "dw_pct": 0,
                "exit_trigger": "UNPAIRED",
                "hold": calc_hold_duration(t1["entry_time"], t1["exit_time"]),
                "net_pnl": t1["pnl"],
                "net_fees": t1["fees"],
                "pnl_pct": 0,
                "is_win": t1["is_win"],
                "long_leg": t1,
                "short_leg": None,
                "quantity": abs(t1["quantity"]),
                "unpaired": True,
            }
        )
        i += 1

    print(
        f"  Paired into {len(vass_spreads)} spreads ({len([s for s in vass_spreads if s.get('unpaired')])} unpaired)"
    )

    # Step 3: Process MICRO trades (including orphans which are MICRO resold)
    print("Processing MICRO trades...")
    micro_details = []
    orphan_details = []

    for trade in micro_trades:
        ctx = get_micro_context(logs_by_date, trade["entry_time"], trade["symbol"])
        exit_trigger = get_micro_exit_trigger(trade, orders)

        # Strategy from entry tag
        entry_tag = trade["entry_tag"]
        if "DEBIT_FADE" in entry_tag:
            strategy = "DEBIT_FADE"
        elif "DEBIT_MOMENTUM" in entry_tag:
            strategy = "DEBIT_MOMENTUM"
        elif "PROTECTIVE_PUT" in entry_tag:
            strategy = "PROTECTIVE_PUT"
        else:
            strategy = "ITM_MOMENTUM"

        hold = calc_hold_duration(trade["entry_time"], trade["exit_time"])
        entry_cost = trade["entry_price"] * abs(trade["quantity"]) * 100
        pnl_pct = (trade["pnl"] / entry_cost * 100) if entry_cost > 0 else 0

        micro_details.append(
            {
                "entry_time": trade["entry_time"],
                "exit_time": trade["exit_time"],
                "symbol": trade["symbol"],
                "strategy": strategy,
                "direction": ctx["direction"],
                "micro_regime": ctx["micro_regime"],
                "score": ctx["score"],
                "vix": ctx["vix"],
                "vix_dir": ctx["vix_dir"],
                "exit_trigger": exit_trigger,
                "hold": hold,
                "pnl": trade["pnl"],
                "pnl_pct": pnl_pct,
                "is_win": trade["is_win"],
                "dte": ctx["dte"],
                "quantity": trade["quantity"],
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "fees": trade["fees"],
            }
        )

    for trade in orphan_trades:
        orphan_details.append(trade)

    print(f"  Processed {len(micro_details)} MICRO trades, {len(orphan_details)} orphans")

    # ===================== REPORT GENERATION =====================
    print("Generating report...")

    lines = []

    def w(text=""):
        lines.append(text)

    w("# V10.8 Full Year 2024 -- Trade Detail Report")
    w()
    w(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w(f"**Backtest Period:** 2024-01-01 to 2024-12-31")
    w(
        f"**Source Files:** trades.csv ({len(trades)} rows), orders.csv ({len(orders)} orders), logs.txt (37,087 lines)"
    )
    w()

    # ===== DATA VALIDATION =====
    total_trade_rows = len(trades)
    vass_leg_count = len(vass_trades)
    micro_count = len(micro_trades)
    orphan_count = len(orphan_trades)
    unknown_count = len(unknown_trades)
    accounted = vass_leg_count + micro_count + orphan_count + unknown_count

    total_pnl_trades = sum(t["pnl"] for t in trades)
    total_pnl_vass = sum(s["net_pnl"] for s in vass_spreads)
    total_pnl_micro = sum(m["pnl"] for m in micro_details)
    total_pnl_orphan = sum(o["pnl"] for o in orphan_details)
    reconstructed = total_pnl_vass + total_pnl_micro + total_pnl_orphan
    pnl_match = abs(total_pnl_trades - reconstructed) < 1

    w("## Data Validation Checklist")
    w()
    w("| Check | Result |")
    w("|-------|--------|")
    w(f"| Total trade rows in trades.csv | {total_trade_rows} |")
    w(
        f"| VASS legs identified | {vass_leg_count} (paired: {len([s for s in vass_spreads if not s.get('unpaired')])*2}, unpaired: {len([s for s in vass_spreads if s.get('unpaired')])}) |"
    )
    w(f"| VASS spreads (paired) | {len([s for s in vass_spreads if not s.get('unpaired')])} |")
    w(f"| VASS unpaired legs | {len([s for s in vass_spreads if s.get('unpaired')])} |")
    w(f"| MICRO trades identified | {micro_count} |")
    w(f"| Orphan trades (RECON) | {orphan_count} |")
    w(f"| Unknown/unclassified | {unknown_count} |")
    w(
        f"| All rows accounted for | {'PASS' if accounted == total_trade_rows else 'FAIL'} ({accounted}/{total_trade_rows}) |"
    )
    w(f"| Total P&L (trades.csv sum) | ${total_pnl_trades:,.0f} |")
    w(f"| VASS spread net P&L | ${total_pnl_vass:,.0f} |")
    w(f"| MICRO trade P&L | ${total_pnl_micro:,.0f} |")
    w(f"| Orphan trade P&L | ${total_pnl_orphan:,.0f} |")
    w(f"| Reconstructed total | ${reconstructed:,.0f} |")
    w(
        f"| P&L reconciliation | {'PASS' if pnl_match else 'FAIL - delta $' + str(round(total_pnl_trades - reconstructed))} |"
    )
    w()

    # =================== PART 1: VASS SPREADS ===================
    w("---")
    w()
    w("# Part 1: VASS Spread Trade-by-Trade")
    w()
    w(
        "Abbreviations: BCD = Bull Call Debit, BCC = Bear Call Credit, BPD = Bear Put Debit, BPC = Bull Put Credit"
    )
    w()
    w(
        "| # | Entry | Exit | Type | Regime | VIX | DTE | Debit | Width | D/W% | Exit Trigger | Hold | Net P&L | P&L% | W/L |"
    )
    w(
        "|---|-------|------|------|--------|-----|-----|-------|-------|------|--------------|------|---------|------|-----|"
    )

    for idx, s in enumerate(vass_spreads, 1):
        entry_short = parse_timestamp(s["entry_time"]).strftime("%m/%d")
        exit_short = parse_timestamp(s["exit_time"]).strftime("%m/%d")
        ts = s["spread_type"]
        type_short = (
            ts.replace("BULL_CALL_DEBIT", "BCD")
            .replace("BEAR_PUT_DEBIT", "BPD")
            .replace("BEAR_CALL_CREDIT", "BCC")
            .replace("BULL_PUT_CREDIT", "BPC")
        )
        regime_str = s["regime_score"] if s["regime_score"] != "N/A" else "-"
        vix_str = s["vix"] if s["vix"] != "N/A" else "-"
        wl = "W" if s["is_win"] else "L"
        w(
            f"| {idx} | {entry_short} | {exit_short} | {type_short} | {regime_str} | {vix_str} | {s['dte']} | ${s['debit']:.2f} | ${s['width']:.0f} | {s['dw_pct']:.0f}% | {s['exit_trigger']} | {s['hold']} | ${s['net_pnl']:+,.0f} | {s['pnl_pct']:+.1f}% | {wl} |"
        )

    w()

    # 1a) VASS Summary
    w("## 1a) VASS Summary")
    w()
    paired_spreads = [s for s in vass_spreads if not s.get("unpaired")]
    vass_wins = [s for s in vass_spreads if s["is_win"]]
    vass_losses = [s for s in vass_spreads if not s["is_win"]]
    total_vass_pnl = sum(s["net_pnl"] for s in vass_spreads)

    w("| Metric | Value |")
    w("|--------|-------|")
    w(
        f"| Total Spreads | {len(vass_spreads)} ({len(paired_spreads)} paired + {len(vass_spreads)-len(paired_spreads)} unpaired) |"
    )
    w(f"| Wins | {len(vass_wins)} ({len(vass_wins)/max(len(vass_spreads),1)*100:.0f}%) |")
    w(f"| Losses | {len(vass_losses)} ({len(vass_losses)/max(len(vass_spreads),1)*100:.0f}%) |")
    w(f"| Total Net P&L | ${total_vass_pnl:+,.0f} |")
    if vass_wins:
        w(f"| Avg Win | ${sum(s['net_pnl'] for s in vass_wins)/len(vass_wins):+,.0f} |")
    if vass_losses:
        w(f"| Avg Loss | ${sum(s['net_pnl'] for s in vass_losses)/len(vass_losses):+,.0f} |")
    w(f"| Largest Win | ${max(s['net_pnl'] for s in vass_spreads):+,.0f} |")
    w(f"| Largest Loss | ${min(s['net_pnl'] for s in vass_spreads):+,.0f} |")
    w(f"| Total Fees | ${sum(s['net_fees'] for s in vass_spreads):,.0f} |")
    w()

    # 1b) Exit Reason Distribution
    w("## 1b) VASS Exit Reason Distribution")
    w()
    exit_counts = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for s in vass_spreads:
        exit_counts[s["exit_trigger"]]["count"] += 1
        exit_counts[s["exit_trigger"]]["pnl"] += s["net_pnl"]
        if s["is_win"]:
            exit_counts[s["exit_trigger"]]["wins"] += 1

    w("| Exit Reason | Count | Win% | Total P&L | Avg P&L |")
    w("|-------------|-------|------|-----------|---------|")
    for reason, data in sorted(exit_counts.items(), key=lambda x: x[1]["pnl"]):
        win_pct = data["wins"] / data["count"] * 100
        avg_pnl = data["pnl"] / data["count"]
        w(
            f"| {reason} | {data['count']} | {win_pct:.0f}% | ${data['pnl']:+,.0f} | ${avg_pnl:+,.0f} |"
        )
    w()

    # 1c) D/W% Analysis
    w("## 1c) D/W% Analysis")
    w()
    dw_buckets = [
        ("0-35%", 0, 35),
        ("35-45%", 35, 45),
        ("45-50%", 45, 50),
        ("50-55%", 50, 55),
        (">55%", 55, 999),
    ]
    w("| D/W% Range | Count | Win% | Total P&L | Avg P&L |")
    w("|------------|-------|------|-----------|---------|")
    for label, lo, hi in dw_buckets:
        bucket = [
            s for s in vass_spreads if lo < s["dw_pct"] <= hi or (lo == 0 and s["dw_pct"] <= hi)
        ]
        if bucket:
            wins = sum(1 for s in bucket if s["is_win"])
            total = sum(s["net_pnl"] for s in bucket)
            w(
                f"| {label} | {len(bucket)} | {wins/len(bucket)*100:.0f}% | ${total:+,.0f} | ${total/len(bucket):+,.0f} |"
            )
    w()

    # 1d) Monthly Breakdown
    w("## 1d) VASS Monthly Breakdown")
    w()
    monthly_vass = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for s in vass_spreads:
        month = parse_timestamp(s["entry_time"]).strftime("%Y-%m")
        monthly_vass[month]["count"] += 1
        if s["is_win"]:
            monthly_vass[month]["wins"] += 1
        monthly_vass[month]["pnl"] += s["net_pnl"]

    w("| Month | Trades | Wins | Win% | Net P&L |")
    w("|-------|--------|------|------|---------|")
    for month in sorted(monthly_vass.keys()):
        d = monthly_vass[month]
        w(
            f"| {month} | {d['count']} | {d['wins']} | {d['wins']/d['count']*100:.0f}% | ${d['pnl']:+,.0f} |"
        )
    w()

    # 1e) Top 10 Worst VASS Trades
    w("## 1e) Top 10 Worst VASS Trades")
    w()
    worst_vass = sorted(vass_spreads, key=lambda x: x["net_pnl"])[:10]
    w("| # | Entry | Type | Net P&L | VIX | DTE | D/W% | Exit Trigger | Hold |")
    w("|---|-------|------|---------|-----|-----|------|--------------|------|")
    for idx, s in enumerate(worst_vass, 1):
        entry_short = parse_timestamp(s["entry_time"]).strftime("%m/%d")
        ts = s["spread_type"]
        type_short = (
            ts.replace("BULL_CALL_DEBIT", "BCD")
            .replace("BEAR_PUT_DEBIT", "BPD")
            .replace("BEAR_CALL_CREDIT", "BCC")
            .replace("BULL_PUT_CREDIT", "BPC")
        )
        w(
            f"| {idx} | {entry_short} | {type_short} | ${s['net_pnl']:+,.0f} | {s['vix']} | {s['dte']} | {s['dw_pct']:.0f}% | {s['exit_trigger']} | {s['hold']} |"
        )
    w()

    # 1f) By Spread Type
    w("## 1f) VASS By Spread Type")
    w()
    type_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for s in vass_spreads:
        type_stats[s["spread_type"]]["count"] += 1
        if s["is_win"]:
            type_stats[s["spread_type"]]["wins"] += 1
        type_stats[s["spread_type"]]["pnl"] += s["net_pnl"]

    w("| Spread Type | Count | Win% | Total P&L | Avg P&L |")
    w("|-------------|-------|------|-----------|---------|")
    for t, d in sorted(type_stats.items(), key=lambda x: x[1]["pnl"]):
        w(
            f"| {t} | {d['count']} | {d['wins']/d['count']*100:.0f}% | ${d['pnl']:+,.0f} | ${d['pnl']/d['count']:+,.0f} |"
        )
    w()

    # =================== PART 2: MICRO INTRADAY ===================
    w("---")
    w()
    w("# Part 2: MICRO Intraday Trade-by-Trade")
    w()
    w("All trades use ITM_MOMENTUM strategy (the only active MICRO strategy in V10.8).")
    w()
    w(
        "| # | Date | Entry | Exit | Dir | Micro Regime | VIX | VIX Dir | Exit Trigger | Hold | DTE | P&L $ | P&L % | W/L |"
    )
    w(
        "|---|------|-------|------|-----|-------------|-----|---------|--------------|------|-----|-------|-------|-----|"
    )

    for idx, m in enumerate(micro_details, 1):
        entry_dt = parse_timestamp(m["entry_time"])
        date_str = entry_dt.strftime("%m/%d")
        entry_str = entry_dt.strftime("%H:%M")
        exit_dt = parse_timestamp(m["exit_time"])
        exit_str = exit_dt.strftime("%m/%d %H:%M")
        wl = "W" if m["is_win"] else "L"
        w(
            f"| {idx} | {date_str} | {entry_str} | {exit_str} | {m['direction']} | {m['micro_regime']} | {m['vix']} | {m['vix_dir']} | {m['exit_trigger']} | {m['hold']} | {m['dte']} | ${m['pnl']:+,.0f} | {m['pnl_pct']:+.1f}% | {wl} |"
        )

    w()

    # 2a) MICRO Summary
    w("## 2a) MICRO Summary")
    w()
    micro_wins = [m for m in micro_details if m["is_win"]]
    micro_losses = [m for m in micro_details if not m["is_win"]]
    total_micro_pnl_calc = sum(m["pnl"] for m in micro_details)

    w("| Metric | Value |")
    w("|--------|-------|")
    w(f"| Total Trades | {len(micro_details)} |")
    w(f"| Wins | {len(micro_wins)} ({len(micro_wins)/max(len(micro_details),1)*100:.0f}%) |")
    w(f"| Losses | {len(micro_losses)} ({len(micro_losses)/max(len(micro_details),1)*100:.0f}%) |")
    w(f"| Total P&L | ${total_micro_pnl_calc:+,.0f} |")
    if micro_wins:
        w(f"| Avg Win | ${sum(m['pnl'] for m in micro_wins)/len(micro_wins):+,.0f} |")
    if micro_losses:
        w(f"| Avg Loss | ${sum(m['pnl'] for m in micro_losses)/len(micro_losses):+,.0f} |")
    w(f"| Largest Win | ${max(m['pnl'] for m in micro_details):+,.0f} |")
    w(f"| Largest Loss | ${min(m['pnl'] for m in micro_details):+,.0f} |")
    w(f"| Total Fees | ${sum(m['fees'] for m in micro_details):,.0f} |")
    w()

    # 2b) By Strategy
    w("## 2b) MICRO By Strategy")
    w()
    strat_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for m in micro_details:
        strat_stats[m["strategy"]]["count"] += 1
        if m["is_win"]:
            strat_stats[m["strategy"]]["wins"] += 1
        strat_stats[m["strategy"]]["pnl"] += m["pnl"]

    w("| Strategy | Count | Win% | Total P&L | Avg P&L |")
    w("|----------|-------|------|-----------|---------|")
    for s, d in sorted(strat_stats.items(), key=lambda x: x[1]["pnl"]):
        w(
            f"| {s} | {d['count']} | {d['wins']/d['count']*100:.0f}% | ${d['pnl']:+,.0f} | ${d['pnl']/d['count']:+,.0f} |"
        )
    w()

    # 2c) By Micro Regime (MOST IMPORTANT)
    w("## 2c) MICRO By Regime (Sorted by Total P&L, Worst First)")
    w()
    regime_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0, "trades": []})
    for m in micro_details:
        r = m["micro_regime"]
        regime_stats[r]["count"] += 1
        if m["is_win"]:
            regime_stats[r]["wins"] += 1
        regime_stats[r]["pnl"] += m["pnl"]
        regime_stats[r]["trades"].append(m)

    w("| Micro Regime | Count | Win% | Total P&L | Avg P&L | Worst Trade |")
    w("|-------------|-------|------|-----------|---------|-------------|")
    for r, d in sorted(regime_stats.items(), key=lambda x: x[1]["pnl"]):
        worst = min(d["trades"], key=lambda x: x["pnl"])
        worst_str = (
            f"${worst['pnl']:+,.0f} ({parse_timestamp(worst['entry_time']).strftime('%m/%d')})"
        )
        win_pct = d["wins"] / d["count"] * 100
        w(
            f"| {r} | {d['count']} | {win_pct:.0f}% | ${d['pnl']:+,.0f} | ${d['pnl']/d['count']:+,.0f} | {worst_str} |"
        )
    w()

    # 2d) By Direction
    w("## 2d) MICRO By Direction")
    w()
    dir_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for m in micro_details:
        dir_stats[m["direction"]]["count"] += 1
        if m["is_win"]:
            dir_stats[m["direction"]]["wins"] += 1
        dir_stats[m["direction"]]["pnl"] += m["pnl"]

    w("| Direction | Count | Win% | Total P&L | Avg P&L |")
    w("|-----------|-------|------|-----------|---------|")
    for d, data in sorted(dir_stats.items(), key=lambda x: x[1]["pnl"]):
        w(
            f"| {d} | {data['count']} | {data['wins']/data['count']*100:.0f}% | ${data['pnl']:+,.0f} | ${data['pnl']/data['count']:+,.0f} |"
        )
    w()

    # 2e) Exit Reason Distribution
    w("## 2e) MICRO Exit Reason Distribution")
    w()
    micro_exit_counts = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for m in micro_details:
        micro_exit_counts[m["exit_trigger"]]["count"] += 1
        micro_exit_counts[m["exit_trigger"]]["pnl"] += m["pnl"]
        if m["is_win"]:
            micro_exit_counts[m["exit_trigger"]]["wins"] += 1

    w("| Exit Reason | Count | Win% | Total P&L | Avg P&L |")
    w("|-------------|-------|------|-----------|---------|")
    for reason, data in sorted(micro_exit_counts.items(), key=lambda x: x[1]["pnl"]):
        win_pct = data["wins"] / data["count"] * 100
        avg_pnl = data["pnl"] / data["count"]
        w(
            f"| {reason} | {data['count']} | {win_pct:.0f}% | ${data['pnl']:+,.0f} | ${avg_pnl:+,.0f} |"
        )
    w()

    # 2f) Orphan Analysis
    w("## 2f) Orphan Trade Analysis")
    w()
    if orphan_details:
        orphan_total = sum(o["pnl"] for o in orphan_details)
        w(f"Total orphan trades: {len(orphan_details)}")
        w(f"Total orphan P&L: ${orphan_total:+,.0f}")
        w()
        w(
            "These are trades with `RECON_ORPHAN_OPTION` tag -- the system re-buying options it sold via EOD sweep"
        )
        w(
            "that were not properly closed, then selling again. The P&L reflects the loss on the orphan re-acquisition."
        )
        w()
        w("| # | Date | Symbol | Direction | Entry | Exit | P&L |")
        w("|---|------|--------|-----------|-------|------|-----|")
        for idx, o in enumerate(orphan_details, 1):
            dt = parse_timestamp(o["entry_time"])
            w(
                f"| {idx} | {dt.strftime('%m/%d')} | ...{o['symbol'][-15:]} | {o['direction']} | ${o['entry_price']:.2f} | ${o['exit_price']:.2f} | ${o['pnl']:+,.0f} |"
            )
    else:
        w("No orphan trades found. All orphan positions are accounted for within MICRO trades")
        w(
            "(EOD_SWEEP -> ORPHAN_RECON -> re-entry sequences are tracked as consecutive MICRO trades)."
        )
    w()

    # 2g) Regime x Direction Heatmap
    w("## 2g) Regime x Direction Heatmap (P&L)")
    w()
    regimes_seen = sorted(set(m["micro_regime"] for m in micro_details))
    dirs_seen = sorted(set(m["direction"] for m in micro_details))

    heatmap = defaultdict(lambda: defaultdict(float))
    heatmap_count = defaultdict(lambda: defaultdict(int))
    for m in micro_details:
        heatmap[m["micro_regime"]][m["direction"]] += m["pnl"]
        heatmap_count[m["micro_regime"]][m["direction"]] += 1

    header = "| Regime | " + " | ".join(dirs_seen) + " | Total |"
    sep = "|" + "---|" * (len(dirs_seen) + 2)
    w(header)
    w(sep)
    for r in sorted(regimes_seen, key=lambda x: sum(heatmap[x].values())):
        cells = []
        for d in dirs_seen:
            pnl = heatmap[r][d]
            cnt = heatmap_count[r][d]
            if cnt > 0:
                cells.append(f"${pnl:+,.0f} ({cnt})")
            else:
                cells.append("-")
        total = sum(heatmap[r].values())
        w(f"| {r} | " + " | ".join(cells) + f" | ${total:+,.0f} |")
    w()

    # 2h) Top 10 Worst MICRO Trades
    w("## 2h) Top 10 Worst MICRO Trades")
    w()
    worst_micro = sorted(micro_details, key=lambda x: x["pnl"])[:10]
    w("| # | Date | Dir | Regime | VIX | P&L | Exit Trigger | Hold | DTE |")
    w("|---|------|-----|--------|-----|-----|--------------|------|-----|")
    for idx, m in enumerate(worst_micro, 1):
        dt = parse_timestamp(m["entry_time"])
        w(
            f"| {idx} | {dt.strftime('%m/%d')} | {m['direction']} | {m['micro_regime']} | {m['vix']} | ${m['pnl']:+,.0f} | {m['exit_trigger']} | {m['hold']} | {m['dte']} |"
        )
    w()

    # =================== PART 3: ROOT CAUSE ANALYSIS ===================
    w("---")
    w()
    w("# Part 3: Combined Root Cause Analysis")
    w()

    # 3a) Loss Concentration
    w("## 3a) Loss Concentration")
    w()
    all_losses = []
    for s in vass_spreads:
        if not s["is_win"]:
            all_losses.append(
                {
                    "type": "VASS",
                    "pnl": s["net_pnl"],
                    "date": s["entry_time"],
                    "detail": s["spread_type"],
                }
            )
    for m in micro_details:
        if not m["is_win"]:
            all_losses.append(
                {
                    "type": "MICRO",
                    "pnl": m["pnl"],
                    "date": m["entry_time"],
                    "detail": m["direction"],
                }
            )

    all_losses.sort(key=lambda x: x["pnl"])
    total_loss_sum = sum(l["pnl"] for l in all_losses)

    w(f"Total losses: ${total_loss_sum:+,.0f} across {len(all_losses)} losing trades")
    w()

    if all_losses:
        top5_loss = sum(l["pnl"] for l in all_losses[:5])
        top10_loss = sum(l["pnl"] for l in all_losses[:10])
        w(
            f"- Top 5 worst trades: ${top5_loss:+,.0f} ({top5_loss/total_loss_sum*100:.0f}% of total losses)"
        )
        w(
            f"- Top 10 worst trades: ${top10_loss:+,.0f} ({top10_loss/total_loss_sum*100:.0f}% of total losses)"
        )
        w()
        w("### Top 5 Worst Trades (Combined)")
        w()
        w("| # | Type | Date | Detail | P&L |")
        w("|---|------|------|--------|-----|")
        for idx, l in enumerate(all_losses[:5], 1):
            dt = parse_timestamp(l["date"])
            w(
                f"| {idx} | {l['type']} | {dt.strftime('%m/%d')} | {l['detail']} | ${l['pnl']:+,.0f} |"
            )
    w()

    # Monthly combined breakdown
    w("### Monthly P&L Distribution (Combined)")
    w()
    monthly_combined = defaultdict(lambda: {"vass_w": 0, "vass_l": 0, "micro_w": 0, "micro_l": 0})
    for s in vass_spreads:
        month = parse_timestamp(s["entry_time"]).strftime("%Y-%m")
        if s["is_win"]:
            monthly_combined[month]["vass_w"] += s["net_pnl"]
        else:
            monthly_combined[month]["vass_l"] += s["net_pnl"]
    for m in micro_details:
        month = parse_timestamp(m["entry_time"]).strftime("%Y-%m")
        if m["is_win"]:
            monthly_combined[month]["micro_w"] += m["pnl"]
        else:
            monthly_combined[month]["micro_l"] += m["pnl"]

    all_months = sorted(monthly_combined.keys())
    w("| Month | VASS Win | VASS Loss | MICRO Win | MICRO Loss | Net |")
    w("|-------|----------|-----------|-----------|------------|-----|")
    for month in all_months:
        d = monthly_combined[month]
        net = d["vass_w"] + d["vass_l"] + d["micro_w"] + d["micro_l"]
        w(
            f"| {month} | ${d['vass_w']:+,.0f} | ${d['vass_l']:+,.0f} | ${d['micro_w']:+,.0f} | ${d['micro_l']:+,.0f} | ${net:+,.0f} |"
        )
    w()

    # 3b) Failure Mode Ranking
    w("## 3b) Failure Mode Ranking by $ Impact")
    w()
    failure_modes = defaultdict(lambda: {"count": 0, "pnl": 0})

    for s in vass_spreads:
        if not s["is_win"]:
            mode = f"VASS_{s['exit_trigger']}"
            failure_modes[mode]["count"] += 1
            failure_modes[mode]["pnl"] += s["net_pnl"]

    for m in micro_details:
        if not m["is_win"]:
            mode = f"MICRO_{m['exit_trigger']}_{m['direction']}"
            failure_modes[mode]["count"] += 1
            failure_modes[mode]["pnl"] += m["pnl"]

    w("| Rank | Failure Mode | Count | Total Loss | Avg Loss |")
    w("|------|-------------|-------|------------|----------|")
    for rank, (mode, data) in enumerate(
        sorted(failure_modes.items(), key=lambda x: x[1]["pnl"]), 1
    ):
        if rank <= 15:
            w(
                f"| {rank} | {mode} | {data['count']} | ${data['pnl']:+,.0f} | ${data['pnl']/data['count']:+,.0f} |"
            )
    w()

    # 3c) Regime Gate Simulation
    w("## 3c) Regime Gate Simulation")
    w()
    w(
        "What if we blocked MICRO trades in certain regimes? (Only regimes with negative total P&L shown)"
    )
    w()

    regime_savings = [(r, d["pnl"], d["count"]) for r, d in regime_stats.items() if d["pnl"] < 0]
    regime_savings.sort(key=lambda x: x[1])

    w("| Block Regime | Trades Avoided | P&L Saved | Cumulative Savings |")
    w("|-------------|----------------|-----------|-------------------|")
    cum = 0
    for r, pnl, count in regime_savings:
        cum += abs(pnl)
        w(f"| {r} | {count} | ${abs(pnl):,.0f} | ${cum:,.0f} |")
    w()

    # 3d) Min-Hold Impact
    w("## 3d) Min-Hold Impact Analysis")
    w()
    w("MICRO trades that hit OCO_STOP within minutes suggest immediate adverse move after entry.")
    w()

    oco_stops = [m for m in micro_details if m["exit_trigger"] == "OCO_STOP"]
    oco_stop_losses = [m for m in oco_stops if not m["is_win"]]

    def parse_hold_hours(h):
        try:
            if "d" in h:
                parts = h.split("d")
                days = float(parts[0])
                hours = float(parts[1].replace("h", ""))
                return days * 24 + hours
            return float(h.replace("h", ""))
        except:
            return 999

    short_holds = [m for m in micro_details if parse_hold_hours(m["hold"]) < 0.2]  # < 12 min

    w(
        f"- OCO_STOP exits total: {len(oco_stops)} trades, P&L: ${sum(m['pnl'] for m in oco_stops):+,.0f}"
    )
    w(
        f"- OCO_STOP losses: {len(oco_stop_losses)} trades, P&L: ${sum(m['pnl'] for m in oco_stop_losses):+,.0f}"
    )
    w(
        f"- Trades held < 12 min (instant stop): {len(short_holds)}, P&L: ${sum(m['pnl'] for m in short_holds):+,.0f}"
    )
    w()

    # 3e) Top 5 Actionable Fixes
    w("## 3e) Top 5 Actionable Fixes (Ranked by Estimated $ Impact)")
    w()

    fixes = []

    # Fix 1: Block worst regimes
    worst_regs = [(r, d["pnl"], d["count"]) for r, d in regime_stats.items() if d["pnl"] < -500]
    worst_regs.sort(key=lambda x: x[1])
    if worst_regs:
        save = sum(abs(pnl) for _, pnl, _ in worst_regs[:3])
        names = ", ".join(r for r, _, _ in worst_regs[:3])
        fixes.append(
            (
                save,
                f"Block MICRO entries in worst regimes: {names}",
                f"${save:,.0f} saved by avoiding {sum(c for _,_,c in worst_regs[:3])} trades",
            )
        )

    # Fix 2: VASS hard stop exits are massive losses
    hard_stop_spreads = [
        s
        for s in vass_spreads
        if s["exit_trigger"] in ("HARD_STOP", "HARD_STOP_WIDTH") and not s["is_win"]
    ]
    if hard_stop_spreads:
        hs_loss = abs(sum(s["net_pnl"] for s in hard_stop_spreads))
        fixes.append(
            (
                hs_loss * 0.4,
                f"Add VASS pre-entry momentum filter (avoid {len(hard_stop_spreads)} hard stop losses)",
                f"${hs_loss*0.4:,.0f} saved by filtering 40% of hard stop entries",
            )
        )

    # Fix 3: VASS FRIDAY_FIREWALL exits
    ff_spreads = [s for s in vass_spreads if s["exit_trigger"] == "FRIDAY_FIREWALL"]
    if ff_spreads:
        ff_loss = sum(s["net_pnl"] for s in ff_spreads if not s["is_win"])
        if ff_loss < 0:
            fixes.append(
                (
                    abs(ff_loss),
                    f"Skip VASS entries on Fridays with VIX >= 15 ({len([s for s in ff_spreads if not s['is_win']])} losing firewall exits)",
                    f"${abs(ff_loss):,.0f} saved",
                )
            )

    # Fix 4: MICRO OCO stop improvement
    if oco_stop_losses:
        stop_pnl = abs(sum(m["pnl"] for m in oco_stop_losses))
        fixes.append(
            (
                stop_pnl * 0.15,
                f"Widen MICRO OCO stops or add re-entry logic ({len(oco_stop_losses)} stopped out trades)",
                f"${stop_pnl*0.15:,.0f} recaptured from fewer whipsaws",
            )
        )

    # Fix 5: Orphan handling
    orphan_total_loss = abs(
        sum(
            m["pnl"]
            for m in micro_details
            if m["exit_trigger"] in ("EOD_SWEEP", "ORPHAN_RECON") and not m["is_win"]
        )
    )
    if orphan_total_loss > 200:
        fixes.append(
            (
                orphan_total_loss * 0.5,
                "Improve EOD sweep / orphan handling to reduce re-acquisition losses",
                f"${orphan_total_loss*0.5:,.0f} saved",
            )
        )

    # Fix 6: November 2024 catastrophic losses
    nov_loss = abs(monthly_combined.get("2024-11", {}).get("vass_l", 0))
    if nov_loss > 5000:
        fixes.append(
            (
                nov_loss * 0.5,
                "Add consecutive-loss circuit breaker for VASS (Nov had 3 hard stops in 1 day)",
                f"${nov_loss*0.5:,.0f} saved",
            )
        )

    fixes.sort(reverse=True, key=lambda x: x[0])

    for rank, (impact, fix, detail) in enumerate(fixes[:5], 1):
        w(f"**{rank}. {fix}**")
        w(f"   - Estimated impact: {detail}")
        w()

    # =================== APPENDIX ===================
    w("---")
    w()
    w("## Appendix A: MICRO 21-Regime Reference")
    w()
    w("```")
    w(
        "                    FALLING_FAST  FALLING   STABLE    RISING    RISING_FAST  SPIKING   WHIPSAW"
    )
    w(
        "VIX LOW (< 18)      PERFECT_MR    GOOD_MR   NORMAL    CAUTION   TRANSITION   RISK_OFF  CHOPPY"
    )
    w(
        "VIX MEDIUM (18-25)  RECOVERING    IMPROVING CAUTIOUS  WORSENING DETERIORATE  BREAKING  UNSTABLE"
    )
    w(
        "VIX HIGH (> 25)     PANIC_EASE    CALMING   ELEVATED  WORSE_HI  FULL_PANIC   CRASH     VOLATILE"
    )
    w("```")
    w()

    w("## Appendix B: Exit Trigger Glossary")
    w()
    w("### VASS Spread Exits")
    w("| Trigger | Description |")
    w("|---------|-------------|")
    w("| DAY4_EOD_CLOSE | Normal exit after 4-day hold period, at EOD |")
    w("| HARD_STOP | Spread lost > 50% of entry debit during hold period |")
    w("| HARD_STOP_WIDTH | Spread loss exceeded 35% of width |")
    w("| STOP_LOSS | Debit spread lost > 35% of entry; credit spread value exceeded stop |")
    w("| PROFIT_TARGET | Hit profit target (35-60% depending on regime and spread type) |")
    w("| TRAIL_STOP | Trailing stop triggered after profit was locked |")
    w("| DTE_EXIT | Mandatory exit when DTE <= 5 (assignment prevention) |")
    w("| FRIDAY_FIREWALL | Friday close: fresh trade + VIX >= 15 triggers protective exit |")
    w("| ASSIGNMENT_RISK | Margin buffer insufficient for spread max loss |")
    w("| CLOSE_RETRY | Spread close order was canceled, retried with market order |")
    w()
    w("### MICRO Exits")
    w("| Trigger | Description |")
    w("|---------|-------------|")
    w("| OCO_STOP | Stop-market leg of OCO pair triggered |")
    w("| OCO_PROFIT | Limit-profit leg of OCO pair triggered |")
    w("| EOD_SWEEP | Forced close via market order at end of day |")
    w("| EARLY_EXERCISE_GUARD | Closed when ITM with DTE <= 2 to prevent assignment |")
    w("| RETRY_CLOSE | MOO close failed, retried next day |")
    w("| EMERG_RETRY | Emergency retry after multiple close failures |")
    w("| MICRO_LIMIT | Limit exit order filled (non-OCO) |")
    w("| KILL_SWITCH | Kill switch triggered, position force-closed |")
    w()

    # Write report
    report = "\n".join(lines)
    with open(OUTPUT_FILE, "w") as f:
        f.write(report)

    print(f"\nReport written to: {OUTPUT_FILE}")
    print(f"Total lines: {len(lines)}")
    print(
        f"P&L check: trades.csv=${total_pnl_trades:,.0f}, reconstructed=${reconstructed:,.0f}, match={'YES' if pnl_match else 'NO'}"
    )


if __name__ == "__main__":
    main()
