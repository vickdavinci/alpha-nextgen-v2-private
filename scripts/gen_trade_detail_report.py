#!/usr/bin/env python3
"""Generate V10.3 R5 FullYear2023 Trade Detail Report."""
import csv
import re
from collections import defaultdict
from datetime import datetime, timedelta
from io import StringIO

BASE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.3"
TRADES_FILE = f"{BASE}/V10.3-FullYear2023-R5_trades.csv"
LOGS_FILE = f"{BASE}/V10.3-FullYear2023-R5_logs.txt"
OUTPUT_FILE = f"{BASE}/V10_3_R5_FullYear2023_TRADE_DETAIL_REPORT.md"


def parse_trades():
    """Parse trades.csv into list of dicts."""
    trades = []
    with open(TRADES_FILE) as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["P&L"] = float(row["P&L"])
            row["Quantity"] = int(row["Quantity"])
            row["Entry Price"] = float(row["Entry Price"])
            row["Exit Price"] = float(row["Exit Price"])
            row["Fees"] = float(row["Fees"])
            row["IsWin"] = int(row["IsWin"])
            row["Direction"] = int(row["Direction"])
            row["row_num"] = len(trades) + 2  # 1-indexed, header=1
            trades.append(row)
    return trades


def parse_logs():
    """Parse logs into list of (line_num, timestamp, text)."""
    logs = []
    with open(LOGS_FILE) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.+)$", line)
            if m:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                logs.append((i, ts, m.group(2)))
    return logs


def parse_dt(s):
    """Parse ISO datetime from trades.csv."""
    return datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")


def extract_symbol_info(sym):
    """Extract strike and direction from option symbol like QQQ   230109P00270000."""
    sym = sym.strip().strip('"')
    m = re.search(r"(\d{6})([CP])(\d{8})", sym)
    if m:
        exp_str = m.group(1)
        opt_type = "PUT" if m.group(2) == "P" else "CALL"
        strike = int(m.group(3)) / 1000.0
        return opt_type, strike, exp_str
    return None, None, None


def hold_duration(entry_dt, exit_dt):
    """Format hold duration."""
    delta = exit_dt - entry_dt
    total_sec = delta.total_seconds()
    if total_sec < 0:
        return "0m"
    if total_sec < 86400:
        hours = int(total_sec // 3600)
        mins = int((total_sec % 3600) // 60)
        return f"{hours}h {mins}m"
    days = total_sec / 86400
    return f"{days:.1f}d"


def classify_trades(trades, logs):
    """Classify each trade as ITM/MICRO/VASS and pair VASS spreads."""
    # Build FILL log index by symbol for order tag lookup
    fill_tags = {}  # symbol -> list of (timestamp, tag)
    for ln, ts, text in logs:
        m = re.match(
            r"FILL: (?:BUY|SELL) [\d.]+ (.+?) @ \$[\d.]+ \| OrderId=(\d+) .* Tag=(.+?)(?:\s*\||\s*$)",
            text,
        )
        if m:
            sym = m.group(1).strip()
            order_id = m.group(2)
            tag = m.group(3).strip()
            if sym not in fill_tags:
                fill_tags[sym] = []
            fill_tags[sym].append((ts, tag, order_id))

    # For each trade, find its tag from FILL logs
    for t in trades:
        sym = t["Symbols"].strip().strip('"')
        order_ids = t["Order IDs"].split(";")
        t["_sym"] = sym
        t["_entry_dt"] = parse_dt(t["Entry Time"])
        t["_exit_dt"] = parse_dt(t["Exit Time"])
        t["_opt_type"], t["_strike"], t["_exp"] = extract_symbol_info(sym)
        t["_tag"] = None
        t["_exit_tag"] = None

        # Try to match order IDs to fill tags
        if sym in fill_tags:
            for fts, ftag, foid in fill_tags[sym]:
                if foid in order_ids:
                    if "ITM:" in ftag:
                        t["_tag"] = "ITM"
                        t["_full_tag"] = ftag
                    elif "MICRO:" in ftag:
                        t["_tag"] = "MICRO"
                        t["_full_tag"] = ftag
                    elif "VASS:" in ftag or "SPREAD" in ftag:
                        t["_tag"] = "VASS"
                        t["_full_tag"] = ftag

        # If no tag found from fills, try inferring from entry signal logs
        if t["_tag"] is None:
            entry_date = t["_entry_dt"].strftime("%Y-%m-%d")
            # Check for INTRADAY_SIGNAL on entry date matching strike
            for ln, ts, text in logs:
                if ts.strftime("%Y-%m-%d") == entry_date:
                    if "INTRADAY_SIGNAL:" in text and f"K={t['_strike']}" in text:
                        t["_tag"] = "ITM" if "ITM" in text else "MICRO"
                        t["_full_tag"] = text
                        break
                    if "SPREAD: ENTRY_SIGNAL" in text or "CREDIT_SPREAD: ENTRY_SIGNAL" in text:
                        if t["Direction"] == 1:  # Short leg
                            t["_tag"] = "VASS"
                            t["_full_tag"] = text
                            break

        # Default: classify by direction in CSV — Direction 1 is short (VASS spread short leg)
        if t["_tag"] is None:
            # Direction 0 = long, check if it looks like a VASS long leg by pairing
            t["_tag"] = "UNKNOWN"
            t["_full_tag"] = ""

    return trades


def pair_vass_spreads(trades):
    """Pair VASS spread legs. Returns paired spreads + unpaired + MICRO + ITM trades."""
    # VASS trades have two consecutive rows with same entry date and same exit date
    # One Direction=0 (long), one Direction=1 (short)
    vass_trades = [t for t in trades if t["_tag"] == "VASS"]
    micro_trades = [t for t in trades if t["_tag"] == "MICRO"]
    itm_trades = [t for t in trades if t["_tag"] == "ITM"]
    unknown_trades = [t for t in trades if t["_tag"] == "UNKNOWN"]

    # Try to pair unknown trades with VASS by looking for Direction=1 near Direction=0
    # First pass: classify remaining unknowns
    for t in unknown_trades:
        # Reclassify only on matching evidence (opposite side, same entry+exit date).
        entry_date = t["_entry_dt"].strftime("%Y-%m-%d")
        exit_date = t["_exit_dt"].strftime("%Y-%m-%d")
        expected_opp_direction = 1 if t["Direction"] == 0 else 0
        for t2 in trades:
            if t2 is t or t2["Direction"] != expected_opp_direction:
                continue
            if (
                t2["_entry_dt"].strftime("%Y-%m-%d") == entry_date
                and t2["_exit_dt"].strftime("%Y-%m-%d") == exit_date
            ):
                t["_tag"] = "VASS"
                vass_trades.append(t)
                break

    # Sort VASS trades by entry time
    vass_trades.sort(key=lambda x: (x["_entry_dt"], x["row_num"]))

    # Pair by matching entry date and exit date
    paired = []
    used = set()
    for i, t1 in enumerate(vass_trades):
        if id(t1) in used:
            continue
        if t1["Direction"] == 0:  # Long leg
            for j, t2 in enumerate(vass_trades):
                if id(t2) in used or i == j:
                    continue
                if t2["Direction"] == 1:
                    # Same entry date and exit date (within tolerance)
                    if (
                        abs((t1["_entry_dt"] - t2["_entry_dt"]).total_seconds()) < 3600
                        and abs((t1["_exit_dt"] - t2["_exit_dt"]).total_seconds()) < 3600
                    ):
                        paired.append(
                            {
                                "long": t1,
                                "short": t2,
                                "net_pnl": t1["P&L"] + t2["P&L"],
                                "net_fees": t1["Fees"] + t2["Fees"],
                                "is_win": 1 if (t1["P&L"] + t2["P&L"]) > 0 else 0,
                            }
                        )
                        used.add(id(t1))
                        used.add(id(t2))
                        break

    # Unpaired VASS trades treated as single legs
    unpaired_vass = [t for t in vass_trades if id(t) not in used]

    return paired, unpaired_vass, micro_trades, itm_trades


def find_intraday_signal(logs, entry_date, strike=None, opt_type=None):
    """Find INTRADAY_SIGNAL log for a micro trade."""
    results = []
    for ln, ts, text in logs:
        if ts.strftime("%Y-%m-%d") != entry_date:
            continue
        if "INTRADAY_SIGNAL:" in text:
            results.append((ln, ts, text))

    if not results:
        return None

    # Try to match by strike
    if strike:
        for ln, ts, text in results:
            if f"K={strike}" in text or f"K={int(strike)}.0" in text:
                return text

    # Try to match by option type (PUT/CALL)
    if opt_type:
        direction_word = "PUT" if opt_type == "PUT" else "CALL"
        for ln, ts, text in results:
            if f"| {direction_word} x" in text:
                return text

    # Return first match
    return results[0][2] if results else None


def parse_intraday_signal(text):
    """Parse INTRADAY_SIGNAL log line into dict."""
    if not text:
        return {}
    result = {}

    # Strategy - look for the strategy name after "INTRADAY_SIGNAL: INTRADAY_"
    m = re.search(r"INTRADAY_SIGNAL:\s*INTRADAY_(\w+):", text)
    if m:
        strat = m.group(1)
        if strat == "ITM_MOM":
            result["strategy"] = "ITM_MOMENTUM"
        elif strat == "DEBIT_FADE":
            result["strategy"] = "DEBIT_FADE"
        elif strat == "DEBIT_MOMENTUM":
            result["strategy"] = "DEBIT_MOMENTUM"
        elif strat == "PROTECTIVE_PUT":
            result["strategy"] = "PROTECTIVE_PUT"
        else:
            result["strategy"] = strat
    else:
        # Fallback: try second INTRADAY_ pattern
        m = re.search(r"INTRADAY_(ITM_MOM|DEBIT_FADE|DEBIT_MOMENTUM|PROTECTIVE_PUT)", text)
        if m:
            strat = m.group(1)
            if strat == "ITM_MOM":
                result["strategy"] = "ITM_MOMENTUM"
            else:
                result["strategy"] = strat
        else:
            result["strategy"] = "ITM_MOMENTUM"  # default

    # Regime
    m = re.search(r"Regime=(\w+)", text)
    if m:
        result["micro_regime"] = m.group(1)

    # Score
    m = re.search(r"Score=(\d+)", text)
    if m:
        result["score"] = int(m.group(1))

    # VIX
    m = re.search(r"VIX=([\d.]+)\s*\((\w+)\)", text)
    if m:
        result["vix"] = float(m.group(1))
        result["vix_dir"] = m.group(2)

    # Direction
    m = re.search(r"\| (CALL|PUT) x(\d+)", text)
    if m:
        result["direction"] = m.group(1)
        result["qty"] = int(m.group(2))

    # QQQ move
    m = re.search(r"QQQ=(\w+)\s*\(([\+\-]?[\d.]+%)\)", text)
    if m:
        result["qqq_move"] = m.group(1)
        result["qqq_pct"] = m.group(2)

    # DTE
    m = re.search(r"DTE=(\d+)", text)
    if m:
        result["dte"] = int(m.group(1))

    # Stop
    m = re.search(r"Stop=(\d+)%", text)
    if m:
        result["stop_pct"] = int(m.group(1))

    return result


def find_micro_exit_trigger(logs, trade):
    """Find exit trigger for a MICRO trade."""
    exit_dt = trade["_exit_dt"]
    exit_date = exit_dt.strftime("%Y-%m-%d")
    entry_date = trade["_entry_dt"].strftime("%Y-%m-%d")
    sym = trade["_sym"]

    # Helper to classify a fill tag
    def classify_fill_tag(text):
        if "OCO_STOP" in text:
            return "OCO_STOP"
        if "OCO_PROFIT" in text:
            return "OCO_PROFIT"
        if "RECON_ORPHAN" in text:
            return "ORPHAN_RECON"
        if "EARLY_EXERCISE_GUARD" in text:
            return "EARLY_EXERCISE_GUARD"
        return None

    # Search for fill near exit time (within 5 min)
    for ln, ts, text in logs:
        if abs((ts - exit_dt).total_seconds()) < 300:
            if "FILL:" in text and sym in text:
                result = classify_fill_tag(text)
                if result:
                    return result

    # Wider search on exit date
    for ln, ts, text in logs:
        if ts.strftime("%Y-%m-%d") == exit_date:
            if "FILL:" in text and sym in text:
                result = classify_fill_tag(text)
                if result:
                    return result

    # Check entry date too (for same-day exits)
    for ln, ts, text in logs:
        if ts.strftime("%Y-%m-%d") == entry_date:
            if "FILL:" in text and sym in text:
                result = classify_fill_tag(text)
                if result:
                    return result

    # Check for force exit sweep (may happen days before actual close)
    for ln, ts, text in logs:
        if "INTRADAY_FORCE_EXIT_SWEEP" in text and sym in text:
            return "FORCE_CLOSE_SWEEP"

    # Check for force exit on exit date
    for ln, ts, text in logs:
        if ts.strftime("%Y-%m-%d") == exit_date:
            if "INTRADAY_FORCE_EXIT" in text and sym in text:
                return "FORCE_CLOSE"

    # Check for PREMARKET_STALE
    for ln, ts, text in logs:
        if ts.strftime("%Y-%m-%d") == exit_date:
            if "PREMARKET_STALE" in text and sym in text:
                return "PREMARKET_STALE"

    # Check for VASS spread exit (for misclassified spread legs)
    for ln, ts, text in logs:
        if ts.strftime("%Y-%m-%d") == exit_date:
            if ("SPREAD: EXIT" in text or "FILL:" in text) and sym in text:
                if "VASS" in text or "ComboMarket" in text:
                    return "VASS_EXIT"

    # Check across all dates between entry and exit for fills
    for ln, ts, text in logs:
        if "FILL:" in text and sym in text and "SELL" in text:
            if entry_date <= ts.strftime("%Y-%m-%d") <= exit_date:
                if "OCO_STOP" in text:
                    return "OCO_STOP"
                if "OCO_PROFIT" in text:
                    return "OCO_PROFIT"
                if "RECON_ORPHAN" in text:
                    return "ORPHAN_RECON"
                if "VASS" in text:
                    return "VASS_EXIT"

    # After hours
    if exit_dt.hour >= 20:
        return "AFTER_HOURS_EXIT"

    return "UNKNOWN"


def find_spread_entry_signal(
    logs, entry_date, long_strike=None, short_strike=None, is_credit=False
):
    """Find SPREAD: ENTRY_SIGNAL for a VASS spread."""
    results = []
    pattern = "CREDIT_SPREAD: ENTRY_SIGNAL" if is_credit else "SPREAD: ENTRY_SIGNAL"

    for ln, ts, text in logs:
        if ts.strftime("%Y-%m-%d") != entry_date:
            continue
        if (
            pattern in text
            or "SPREAD: ENTRY_SIGNAL" in text
            or "CREDIT_SPREAD: ENTRY_SIGNAL" in text
        ):
            results.append((ln, ts, text))

    if not results:
        return None

    # Match by strikes
    if long_strike and short_strike:
        for ln, ts, text in results:
            ls = f"Long={long_strike}" if "Long=" in text else f"Sell {short_strike}"
            if (f"Long={long_strike}" in text or f"Long={int(long_strike)}.0" in text) and (
                f"Short={short_strike}" in text or f"Short={int(short_strike)}.0" in text
            ):
                return text
            if (f"Sell {short_strike}" in text or f"Sell {int(short_strike)}.0" in text) and (
                f"Buy {long_strike}" in text or f"Buy {int(long_strike)}.0" in text
            ):
                return text

    # Return first match
    return results[0][2] if results else None


def parse_spread_entry(text):
    """Parse SPREAD: ENTRY_SIGNAL log line."""
    if not text:
        return {}
    result = {}

    # Type
    if "BULL_CALL" in text:
        result["spread_type"] = "BULL_CALL"
    elif "BEAR_PUT" in text:
        result["spread_type"] = "BEAR_PUT"
    elif "BEAR_CALL_CREDIT" in text or "BEAR_CALL_CREDI" in text:
        result["spread_type"] = "BEAR_CALL_CREDIT"
    elif "BULL_PUT_CREDIT" in text:
        result["spread_type"] = "BULL_PUT_CREDIT"
    else:
        result["spread_type"] = "UNKNOWN"

    # Regime
    m = re.search(r"Regime=(\d+)", text)
    if m:
        result["regime"] = int(m.group(1))

    # VIX
    m = re.search(r"VIX=([\d.]+)", text)
    if m:
        result["vix"] = float(m.group(1))

    # Strikes (debit)
    m = re.search(r"Long=([\d.]+)\s+Short=([\d.]+)", text)
    if m:
        result["long_strike"] = float(m.group(1))
        result["short_strike"] = float(m.group(2))
        result["width"] = abs(result["short_strike"] - result["long_strike"])

    # Strikes (credit)
    m = re.search(r"Sell\s+([\d.]+)\s+Buy\s+([\d.]+)", text)
    if m:
        result["short_strike"] = float(m.group(1))
        result["long_strike"] = float(m.group(2))
        result["width"] = abs(result["long_strike"] - result["short_strike"])

    # Debit
    m = re.search(r"Debit=\$([\d.]+)", text)
    if m:
        result["debit"] = float(m.group(1))
        if "width" in result and result["width"] > 0:
            result["dw_pct"] = result["debit"] / result["width"] * 100

    # Credit
    m = re.search(r"Credit=\$([\d.]+)", text)
    if m:
        result["credit"] = float(m.group(1))

    # Width from explicit field
    m = re.search(r"Width=\$(\d+)", text)
    if m:
        result["width"] = float(m.group(1))

    # DTE
    m = re.search(r"DTE=(\d+)", text)
    if m:
        result["dte"] = int(m.group(1))

    # Score
    m = re.search(r"Score=([\d.]+)", text)
    if m:
        result["score"] = float(m.group(1))

    # Quantity
    m = re.search(r"x(\d+)", text)
    if m:
        result["qty"] = int(m.group(1))

    return result


def find_spread_exit_trigger(logs, exit_date, long_strike=None, short_strike=None, entry_date=None):
    """Find spread exit trigger from logs."""
    # Search around exit date
    for ln, ts, text in logs:
        d = ts.strftime("%Y-%m-%d")
        if d != exit_date:
            continue
        if "SPREAD: EXIT" in text:
            if "STOP_LOSS" in text:
                return "STOP_LOSS"
            if "SPREAD_HARD_STOP" in text:
                return "HARD_STOP"
            if "PROFIT_TARGET" in text:
                return "PROFIT_TARGET"
            if "TRAIL_STOP" in text:
                return "TRAIL_STOP"
            if "DTE_EXIT" in text:
                return "DTE_EXIT"
            if "FRIDAY_FIREWALL" in text:
                return "FRIDAY_FIREWALL"
            if "FILL_CLOSE_RECONCILED" in text:
                return "RECONCILED"
            if "SPREAD_CLOSE_RETRY" in text:
                return "CLOSE_RETRY"
            if "CREDIT_STOP_LOSS" in text:
                return "CREDIT_STOP_LOSS"
        if "SPREAD_OVERLAY_EXIT" in text and "STRESS" in text:
            return "STRESS_EXIT"
        if "SPREAD_HARD_STOP_TRIGGERED" in text:
            return "HARD_STOP"

    # Wider search: day before and after
    exit_dt = datetime.strptime(exit_date, "%Y-%m-%d")
    for delta_days in [-1, 1]:
        search_date = (exit_dt + timedelta(days=delta_days)).strftime("%Y-%m-%d")
        for ln, ts, text in logs:
            if ts.strftime("%Y-%m-%d") != search_date:
                continue
            if "SPREAD: EXIT" in text:
                if entry_date and entry_date in text:
                    if "STOP_LOSS" in text:
                        return "STOP_LOSS"
                    if "SPREAD_HARD_STOP" in text:
                        return "HARD_STOP"
                    if "PROFIT_TARGET" in text:
                        return "PROFIT_TARGET"
                    if "TRAIL_STOP" in text:
                        return "TRAIL_STOP"
                    if "FILL_CLOSE_RECONCILED" in text:
                        return "RECONCILED"
                    if "SPREAD_CLOSE_RETRY" in text:
                        return "CLOSE_RETRY"
                    if "CREDIT_STOP_LOSS" in text:
                        return "CREDIT_STOP_LOSS"

    return "UNKNOWN"


def find_spread_exit_trigger_precise(logs, spread, entry_date_str):
    """More precise exit trigger search using spread keys."""
    long_t = spread["long"]
    short_t = spread["short"]
    exit_date = long_t["_exit_dt"].strftime("%Y-%m-%d")
    long_sym = long_t["_sym"]
    short_sym = short_t["_sym"]

    # Build key pattern: "long_sym|short_sym|entry_date" or similar
    for ln, ts, text in logs:
        d = ts.strftime("%Y-%m-%d")
        if d != exit_date:
            # Check day before for gap-open exits
            exit_dt = datetime.strptime(exit_date, "%Y-%m-%d")
            prev_date = (exit_dt - timedelta(days=1)).strftime("%Y-%m-%d")
            if d != prev_date and d != exit_date:
                continue

        if "SPREAD: EXIT" not in text and "SPREAD_HARD_STOP" not in text:
            continue

        # Check if this exit references our spread's symbols
        if long_sym in text or short_sym in text or entry_date_str in text:
            if "HARD_STOP" in text or "SPREAD_HARD_STOP" in text:
                return "HARD_STOP"
            if "STOP_LOSS" in text:
                return "STOP_LOSS"
            if "PROFIT_TARGET" in text:
                return "PROFIT_TARGET"
            if "TRAIL_STOP" in text:
                return "TRAIL_STOP"
            if "DTE_EXIT" in text:
                return "DTE_EXIT"
            if "FRIDAY_FIREWALL" in text:
                return "FRIDAY_FIREWALL"
            if "FILL_CLOSE_RECONCILED" in text:
                return "RECONCILED"
            if "SPREAD_CLOSE_RETRY" in text:
                return "CLOSE_RETRY"
            if "CREDIT_STOP_LOSS" in text:
                return "CREDIT_STOP_LOSS"

    # Check for RECON_ORPHAN exits on exit date
    for ln, ts, text in logs:
        d = ts.strftime("%Y-%m-%d")
        if d == exit_date:
            if "FILL:" in text and (long_sym in text or short_sym in text):
                if "RECON_ORPHAN" in text:
                    return "ORPHAN_RECON"
                if "EARLY_EXERCISE_GUARD" in text:
                    return "EARLY_EXERCISE_GUARD"

    # Fallback to date-only search
    return find_spread_exit_trigger(logs, exit_date, entry_date=entry_date_str)


def main():
    print("Parsing trades...")
    trades = parse_trades()
    print(f"  Found {len(trades)} trade rows")

    print("Parsing logs...")
    logs = parse_logs()
    print(f"  Found {len(logs)} log entries")

    print("Classifying trades...")
    trades = classify_trades(trades, logs)

    # Build fill index for quick lookup
    fill_index = {}
    for ln, ts, text in logs:
        m = re.match(
            r"FILL: (?:BUY|SELL) [\d.]+ (.+?) @ \$[\d.]+ \| OrderId=(\d+) .* Tag=(.+?)(?:\s*\||\s*$)",
            text,
        )
        if m:
            oid = m.group(2).strip()
            tag = m.group(3).strip()
            fill_index[oid] = (ts, tag, m.group(1).strip())

    # Re-classify using fill index and order IDs
    for t in trades:
        order_ids = t["Order IDs"].split(";")
        has_vass_entry_tag = False  # VASS: with colon = entry fill
        has_bare_vass_tag = False  # VASS without colon = exit/close fill
        for oid in order_ids:
            oid = oid.strip()
            if oid in fill_index:
                _, tag, _ = fill_index[oid]
                if "ITM:" in tag:
                    t["_tag"] = "ITM"
                    t["_full_tag"] = tag
                    has_vass_entry_tag = False
                    has_bare_vass_tag = False
                    break
                elif "MICRO:" in tag:
                    t["_tag"] = "MICRO"
                    t["_full_tag"] = tag
                    has_vass_entry_tag = False
                    has_bare_vass_tag = False
                    break
                elif "VASS:" in tag or "SPREAD" in tag:
                    t["_tag"] = "VASS"
                    t["_full_tag"] = tag
                    has_vass_entry_tag = True
                    break
                elif tag == "VASS":
                    has_bare_vass_tag = True
        # Bare VASS tags (without colon) indicate exit/close fills
        # These are VASS overlap artifacts -- mark as VASS but flag
        if (
            has_bare_vass_tag
            and not has_vass_entry_tag
            and t["_tag"] not in ("MICRO", "ITM", "VASS")
        ):
            t["_tag"] = "VASS"
            t["_full_tag"] = "VASS"
            t["_is_vass_overlap"] = True  # Flag as overlap artifact

    # Count still unknown — classify by pairing logic
    # Direction=1 with matching Direction=0 on same date = VASS
    unknown_long = [t for t in trades if t["_tag"] == "UNKNOWN" and t["Direction"] == 0]
    for t in unknown_long:
        entry_date = t["_entry_dt"].strftime("%Y-%m-%d")
        exit_date = t["_exit_dt"].strftime("%Y-%m-%d")
        for t2 in trades:
            if t2["Direction"] == 1 and t2["_tag"] in ("VASS", "UNKNOWN"):
                if (
                    t2["_entry_dt"].strftime("%Y-%m-%d") == entry_date
                    and t2["_exit_dt"].strftime("%Y-%m-%d") == exit_date
                ):
                    t["_tag"] = "VASS"
                    t2["_tag"] = "VASS"
                    break

    # Any remaining unknowns stay UNKNOWN for audit truthfulness.
    # Only evidence-based pairing above can reclassify to VASS.
    for t in trades:
        if t["_tag"] == "UNKNOWN":
            continue

    itm_count = sum(1 for t in trades if t["_tag"] == "ITM")
    micro_count = sum(1 for t in trades if t["_tag"] == "MICRO")
    vass_count = sum(1 for t in trades if t["_tag"] == "VASS")
    unknown_count = sum(1 for t in trades if t["_tag"] == "UNKNOWN")
    print(f"  ITM: {itm_count}, MICRO: {micro_count}, VASS: {vass_count}, UNKNOWN: {unknown_count}")

    # Pair VASS spreads (exclude overlap artifacts from pairing)
    vass_overlap_trades = [
        t for t in trades if t["_tag"] == "VASS" and t.get("_is_vass_overlap", False)
    ]
    vass_trades = [
        t for t in trades if t["_tag"] == "VASS" and not t.get("_is_vass_overlap", False)
    ]
    micro_trades = [t for t in trades if t["_tag"] == "MICRO"]
    itm_trades = [t for t in trades if t["_tag"] == "ITM"]
    unknown_trades = [t for t in trades if t["_tag"] == "UNKNOWN"]
    intraday_trades = micro_trades + itm_trades

    vass_trades.sort(key=lambda x: (x["_entry_dt"], x["row_num"]))

    spreads = []
    used_ids = set()
    # First pass: tight pairing (same entry time within 1hr, same exit time within 1hr)
    for i, t1 in enumerate(vass_trades):
        if id(t1) in used_ids:
            continue
        if t1["Direction"] == 0:
            best_match = None
            best_diff = float("inf")
            for j, t2 in enumerate(vass_trades):
                if id(t2) in used_ids or i == j:
                    continue
                if t2["Direction"] == 1:
                    entry_diff = abs((t1["_entry_dt"] - t2["_entry_dt"]).total_seconds())
                    exit_diff = abs((t1["_exit_dt"] - t2["_exit_dt"]).total_seconds())
                    if entry_diff < 3600 and exit_diff < 3600:
                        if entry_diff + exit_diff < best_diff:
                            best_diff = entry_diff + exit_diff
                            best_match = t2
            if best_match:
                spreads.append(
                    {
                        "long": t1,
                        "short": best_match,
                        "net_pnl": t1["P&L"] + best_match["P&L"],
                        "net_fees": t1["Fees"] + best_match["Fees"],
                    }
                )
                used_ids.add(id(t1))
                used_ids.add(id(best_match))

    # Second pass: wider pairing for remaining (same expiry, exit within 2 days)
    for i, t1 in enumerate(vass_trades):
        if id(t1) in used_ids:
            continue
        if t1["Direction"] == 0:
            best_match = None
            best_diff = float("inf")
            for j, t2 in enumerate(vass_trades):
                if id(t2) in used_ids or i == j:
                    continue
                if t2["Direction"] == 1:
                    # Same expiry series
                    if t1["_exp"] == t2["_exp"]:
                        exit_diff = abs((t1["_exit_dt"] - t2["_exit_dt"]).total_seconds())
                        entry_diff = abs((t1["_entry_dt"] - t2["_entry_dt"]).total_seconds())
                        if exit_diff < 172800:  # 2 days
                            score = entry_diff + exit_diff
                            if score < best_diff:
                                best_diff = score
                                best_match = t2
            if best_match:
                spreads.append(
                    {
                        "long": t1,
                        "short": best_match,
                        "net_pnl": t1["P&L"] + best_match["P&L"],
                        "net_fees": t1["Fees"] + best_match["Fees"],
                    }
                )
                used_ids.add(id(t1))
                used_ids.add(id(best_match))

    # Unpaired VASS = single legs
    unpaired_vass = [t for t in vass_trades if id(t) not in used_ids]
    # Add unpaired VASS long legs to micro if they look like single options
    # Mark them as VASS_OVERLAP artifacts so they're clearly annotated
    for t in unpaired_vass:
        if t["Direction"] == 0:
            t["_is_vass_overlap"] = True
            micro_trades.append(t)
        # Short legs alone don't make sense, skip them

    # Also add pre-flagged VASS overlap artifacts to micro
    for t in vass_overlap_trades:
        micro_trades.append(t)

    micro_trades.sort(key=lambda x: x["_entry_dt"])
    itm_trades.sort(key=lambda x: x["_entry_dt"])
    intraday_trades = sorted(intraday_trades, key=lambda x: x["_entry_dt"])

    print(
        "  VASS spreads: "
        f"{len(spreads)}, Unpaired VASS: {len(unpaired_vass)}, "
        f"MICRO: {len(micro_trades)}, ITM: {len(itm_trades)}"
    )

    # === ENRICHMENT ===
    print("Enriching intraday trades with log context...")
    for t in intraday_trades:
        # Check if this is a VASS overlap artifact
        if t.get("_is_vass_overlap", False):
            t["_signal"] = {
                "strategy": "VASS_OVERLAP",
                "micro_regime": "VASS_OVERLAP",
                "direction": t["_opt_type"] or "N/A",
            }
            t["_exit_trigger"] = "VASS_EXIT"
            t["_hold"] = hold_duration(t["_entry_dt"], t["_exit_dt"])
            entry_val = abs(t["Entry Price"] * t["Quantity"] * 100)
            t["_pnl_pct"] = (t["P&L"] / entry_val * 100) if entry_val > 0 else 0
            continue

        entry_date = t["_entry_dt"].strftime("%Y-%m-%d")
        sig = find_intraday_signal(logs, entry_date, t["_strike"], t["_opt_type"])
        t["_signal"] = parse_intraday_signal(sig) if sig else {}
        t["_exit_trigger"] = find_micro_exit_trigger(logs, t)
        t["_hold"] = hold_duration(t["_entry_dt"], t["_exit_dt"])
        # P&L % based on entry
        entry_val = abs(t["Entry Price"] * t["Quantity"] * 100)
        t["_pnl_pct"] = (t["P&L"] / entry_val * 100) if entry_val > 0 else 0

    print("Enriching VASS spreads with log context...")
    for s in spreads:
        lt = s["long"]
        st = s["short"]
        entry_date = lt["_entry_dt"].strftime("%Y-%m-%d")
        is_credit = (
            lt["_opt_type"] == "CALL"
            and st["_opt_type"] == "CALL"
            and st["_strike"] < lt["_strike"]
        )
        if not is_credit:
            # Check bear call credit: sell lower strike call, buy higher strike call
            # In trades.csv: Direction=1 is the short (sold) leg, Direction=0 is the long (bought) leg
            # For BEAR_CALL_CREDIT: short leg (sell) strike < long leg (buy) strike
            if st["_strike"] and lt["_strike"] and st["_strike"] < lt["_strike"]:
                is_credit = True

        sig = find_spread_entry_signal(logs, entry_date, lt["_strike"], st["_strike"], is_credit)
        s["_entry_signal"] = parse_spread_entry(sig) if sig else {}
        entry_date_str = lt["_entry_dt"].strftime("%Y-%m-%d %H:%M:%S")
        s["_exit_trigger"] = find_spread_exit_trigger_precise(logs, s, entry_date_str)
        s["_hold"] = hold_duration(lt["_entry_dt"], lt["_exit_dt"])

        # Calculate net debit and P&L%
        net_debit = lt["Entry Price"] - st["Entry Price"]
        s["_net_debit"] = net_debit
        width = abs(lt["_strike"] - st["_strike"]) if lt["_strike"] and st["_strike"] else 0
        s["_width"] = width
        s["_dw_pct"] = (net_debit / width * 100) if width > 0 else 0
        qty = lt["Quantity"]
        entry_cost = abs(net_debit * qty * 100)
        s["_pnl_pct"] = (s["net_pnl"] / entry_cost * 100) if entry_cost > 0 else 0
        s["_is_win"] = 1 if s["net_pnl"] > 0 else 0

    # === GENERATE REPORT ===
    print("Generating report...")
    report = []
    r = report.append

    # Validation checklist
    total_trade_rows = len(trades)
    total_micro = len(micro_trades)
    total_itm = len(itm_trades)
    total_unknown = len(unknown_trades)
    total_spread_legs = len(spreads) * 2 + len([t for t in unpaired_vass if t["Direction"] == 1])
    total_covered = total_micro + total_itm + total_spread_legs

    r("# V10.3 R5 FullYear2023 - Trade Detail Report\n")
    r("## Validation Checklist\n")
    r(f"- Total trade rows in CSV: **{total_trade_rows}**")
    r(f"- MICRO trades: **{total_micro}**")
    r(f"- ITM trades: **{total_itm}**")
    r(f"- UNKNOWN unattributed rows: **{total_unknown}**")
    r(f"- VASS spread pairs: **{len(spreads)}** ({len(spreads)*2} legs)")
    r(f"- Unpaired VASS legs: **{len([t for t in unpaired_vass if t['Direction'] == 1])}**")
    r(f"- Total accounted: **{total_covered}** / {total_trade_rows}")
    r(f"- Coverage: **{total_covered/total_trade_rows*100:.1f}%**\n")

    # ==================== PART 1: VASS ====================
    r("---\n")
    r("# Part 1: VASS Spread Trade-by-Trade\n")

    r(
        "| # | Entry | Exit | Type | Regime | VIX | DTE | Debit | Width | D/W% | Exit Trigger | Hold | Net P&L | P&L% | W/L |"
    )
    r(
        "|---|-------|------|------|--------|-----|-----|-------|-------|------|-------------|------|---------|------|-----|"
    )

    for i, s in enumerate(sorted(spreads, key=lambda x: x["long"]["_entry_dt"]), 1):
        lt = s["long"]
        sig = s.get("_entry_signal", {})
        sp_type = sig.get("spread_type", "BULL_CALL")
        regime = sig.get("regime", "N/A")
        vix = sig.get("vix", "N/A")
        dte = sig.get("dte", "N/A")
        debit = f"${s['_net_debit']:.2f}" if s["_net_debit"] else "N/A"
        width = f"${s['_width']:.0f}" if s["_width"] else "N/A"
        dw = f"{s['_dw_pct']:.1f}%" if s["_dw_pct"] else "N/A"
        exit_t = s["_exit_trigger"]
        hold_d = s["_hold"]
        net_pnl = s["net_pnl"]
        pnl_pct = s["_pnl_pct"]
        wl = "W" if s["_is_win"] else "L"
        entry_d = lt["_entry_dt"].strftime("%Y-%m-%d")
        exit_d = lt["_exit_dt"].strftime("%Y-%m-%d")

        vix_str = f"{vix:.1f}" if isinstance(vix, float) else str(vix)
        regime_str = str(regime)

        r(
            f"| {i} | {entry_d} | {exit_d} | {sp_type} | {regime_str} | {vix_str} | {dte} | {debit} | {width} | {dw} | {exit_t} | {hold_d} | ${net_pnl:+,.0f} | {pnl_pct:+.1f}% | {wl} |"
        )

    # 1a Summary
    r("\n### 1a. VASS Summary\n")
    total_vass_pnl = sum(s["net_pnl"] for s in spreads)
    total_vass_fees = sum(s["net_fees"] for s in spreads)
    vass_wins = sum(1 for s in spreads if s["_is_win"])
    vass_losses = len(spreads) - vass_wins
    avg_win = sum(s["net_pnl"] for s in spreads if s["_is_win"]) / max(vass_wins, 1)
    avg_loss = sum(s["net_pnl"] for s in spreads if not s["_is_win"]) / max(vass_losses, 1)

    r(f"- Total Spreads: **{len(spreads)}**")
    r(f"- Wins: **{vass_wins}** ({vass_wins/len(spreads)*100:.1f}%)")
    r(f"- Losses: **{vass_losses}** ({vass_losses/len(spreads)*100:.1f}%)")
    r(f"- Net P&L: **${total_vass_pnl:+,.0f}**")
    r(f"- Total Fees: **${total_vass_fees:,.0f}**")
    r(f"- Avg Win: **${avg_win:+,.0f}**")
    r(f"- Avg Loss: **${avg_loss:+,.0f}**")
    r(
        f"- Win/Loss Ratio: **{abs(avg_win/avg_loss):.2f}**"
        if avg_loss != 0
        else "- Win/Loss Ratio: N/A"
    )

    # 1b Exit Reason Distribution
    r("\n### 1b. VASS Exit Reason Distribution\n")
    exit_dist = defaultdict(lambda: {"count": 0, "pnl": 0})
    for s in spreads:
        exit_dist[s["_exit_trigger"]]["count"] += 1
        exit_dist[s["_exit_trigger"]]["pnl"] += s["net_pnl"]

    r("| Exit Reason | Count | % | Total P&L | Avg P&L |")
    r("|-------------|-------|---|-----------|---------|")
    for reason, data in sorted(exit_dist.items(), key=lambda x: x[1]["count"], reverse=True):
        count = data["count"]
        pnl = data["pnl"]
        r(
            f"| {reason} | {count} | {count/len(spreads)*100:.1f}% | ${pnl:+,.0f} | ${pnl/count:+,.0f} |"
        )

    # 1c D/W% Analysis
    r("\n### 1c. Debit/Width % Analysis\n")
    dw_buckets = {"<40%": [], "40-45%": [], "45-50%": [], "50-55%": [], ">55%": []}
    for s in spreads:
        dw = s["_dw_pct"]
        if dw < 40:
            dw_buckets["<40%"].append(s)
        elif dw < 45:
            dw_buckets["40-45%"].append(s)
        elif dw < 50:
            dw_buckets["45-50%"].append(s)
        elif dw < 55:
            dw_buckets["50-55%"].append(s)
        else:
            dw_buckets[">55%"].append(s)

    r("| D/W% Range | Count | Win% | Avg P&L | Total P&L |")
    r("|------------|-------|------|---------|-----------|")
    for bucket, sl in dw_buckets.items():
        if sl:
            wins = sum(1 for s in sl if s["_is_win"])
            total = sum(s["net_pnl"] for s in sl)
            r(
                f"| {bucket} | {len(sl)} | {wins/len(sl)*100:.1f}% | ${total/len(sl):+,.0f} | ${total:+,.0f} |"
            )

    # 1d Monthly Breakdown
    r("\n### 1d. VASS Monthly Breakdown\n")
    monthly = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for s in spreads:
        month = s["long"]["_entry_dt"].strftime("%Y-%m")
        monthly[month]["count"] += 1
        monthly[month]["wins"] += s["_is_win"]
        monthly[month]["pnl"] += s["net_pnl"]

    r("| Month | Trades | Wins | Win% | Total P&L | Avg P&L |")
    r("|-------|--------|------|------|-----------|---------|")
    for month in sorted(monthly.keys()):
        d = monthly[month]
        r(
            f"| {month} | {d['count']} | {d['wins']} | {d['wins']/d['count']*100:.1f}% | ${d['pnl']:+,.0f} | ${d['pnl']/d['count']:+,.0f} |"
        )

    # 1e Top 10 Worst VASS
    r("\n### 1e. Top 10 Worst VASS Trades\n")
    worst_vass = sorted(spreads, key=lambda x: x["net_pnl"])[:10]
    r("| # | Entry | Type | Regime | VIX | DTE | D/W% | Exit Trigger | Net P&L | P&L% |")
    r("|---|-------|------|--------|-----|-----|------|-------------|---------|------|")
    for i, s in enumerate(worst_vass, 1):
        sig = s.get("_entry_signal", {})
        r(
            f"| {i} | {s['long']['_entry_dt'].strftime('%Y-%m-%d')} | {sig.get('spread_type', 'N/A')} | {sig.get('regime', 'N/A')} | {sig.get('vix', 'N/A')} | {sig.get('dte', 'N/A')} | {s['_dw_pct']:.1f}% | {s['_exit_trigger']} | ${s['net_pnl']:+,.0f} | {s['_pnl_pct']:+.1f}% |"
        )

    # ==================== PART 2: MICRO ====================
    r("\n---\n")
    r("# Part 2: MICRO Intraday Trade-by-Trade\n")

    r(
        "| # | Date | Entry | Exit | Strategy | Dir | Micro Regime | Score | VIX | VIX Dir | Exit Trigger | Hold | P&L $ | P&L% | W/L | Notes |"
    )
    r(
        "|---|------|-------|------|----------|-----|-------------|-------|-----|---------|-------------|------|-------|------|-----|-------|"
    )

    for i, t in enumerate(micro_trades, 1):
        sig = t.get("_signal", {})
        entry_time = t["_entry_dt"].strftime("%H:%M")
        exit_time = t["_exit_dt"].strftime("%H:%M")
        entry_date = t["_entry_dt"].strftime("%Y-%m-%d")
        strategy = sig.get("strategy", "ITM_MOMENTUM")
        direction = sig.get("direction", t["_opt_type"] or "N/A")
        micro_regime = sig.get("micro_regime", "N/A")
        score = sig.get("score", "N/A")
        vix = sig.get("vix", "N/A")
        vix_dir = sig.get("vix_dir", "N/A")
        vix_str = f"{vix:.1f}" if isinstance(vix, float) else str(vix)
        exit_trigger = t["_exit_trigger"]
        hold_d = t["_hold"]
        pnl = t["P&L"]
        pnl_pct = t["_pnl_pct"]
        wl = "W" if t["IsWin"] else "L"
        notes = ""
        if t.get("_is_vass_overlap", False):
            notes = "VASS_OVERLAP: spread leg artifact"
        elif exit_trigger == "PREMARKET_STALE":
            notes = "ORPHAN"
        elif t["_exit_dt"].hour >= 20:
            notes = "AFTER_HOURS"

        r(
            f"| {i} | {entry_date} | {entry_time} | {exit_time} | {strategy} | {direction} | {micro_regime} | {score} | {vix_str} | {vix_dir} | {exit_trigger} | {hold_d} | ${pnl:+,.0f} | {pnl_pct:+.1f}% | {wl} | {notes} |"
        )

    # 2a Summary
    r("\n### 2a. MICRO Summary\n")

    # Separate real MICRO trades from VASS overlap artifacts
    real_micro = [t for t in micro_trades if not t.get("_is_vass_overlap", False)]
    vass_overlaps = [t for t in micro_trades if t.get("_is_vass_overlap", False)]

    total_micro_pnl = sum(t["P&L"] for t in micro_trades)
    total_micro_fees = sum(t["Fees"] for t in micro_trades)
    micro_wins = sum(1 for t in micro_trades if t["IsWin"])
    micro_losses = len(micro_trades) - micro_wins

    real_micro_pnl = sum(t["P&L"] for t in real_micro)
    real_micro_wins = sum(1 for t in real_micro if t["IsWin"])
    real_micro_losses = len(real_micro) - real_micro_wins
    avg_micro_win = sum(t["P&L"] for t in real_micro if t["IsWin"]) / max(real_micro_wins, 1)
    avg_micro_loss = sum(t["P&L"] for t in real_micro if not t["IsWin"]) / max(real_micro_losses, 1)

    r(f"- Total Trades (incl. overlaps): **{len(micro_trades)}**")
    r(f"- VASS Overlap Artifacts: **{len(vass_overlaps)}** (excluded from stats below)")
    r(f"- Real MICRO Trades: **{len(real_micro)}**")
    r(f"- Wins: **{real_micro_wins}** ({real_micro_wins/len(real_micro)*100:.1f}%)")
    r(f"- Losses: **{real_micro_losses}** ({real_micro_losses/len(real_micro)*100:.1f}%)")
    r(f"- Net P&L (real): **${real_micro_pnl:+,.0f}**")
    r(f"- Net P&L (incl. overlaps): **${total_micro_pnl:+,.0f}**")
    r(f"- Total Fees: **${total_micro_fees:,.0f}**")
    r(f"- Avg Win: **${avg_micro_win:+,.0f}**")
    r(f"- Avg Loss: **${avg_micro_loss:+,.0f}**")
    r(
        f"- Win/Loss Ratio: **{abs(avg_micro_win/avg_micro_loss):.2f}**"
        if avg_micro_loss != 0
        else "- Win/Loss Ratio: N/A"
    )

    # 2b By Strategy (excludes VASS overlap artifacts)
    r("\n### 2b. MICRO by Strategy\n")
    by_strat = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for t in real_micro:
        strat = t.get("_signal", {}).get("strategy", "ITM_MOMENTUM")
        by_strat[strat]["count"] += 1
        by_strat[strat]["wins"] += t["IsWin"]
        by_strat[strat]["pnl"] += t["P&L"]

    r("| Strategy | Count | Wins | Win% | Total P&L | Avg P&L |")
    r("|----------|-------|------|------|-----------|---------|")
    for strat, d in sorted(by_strat.items(), key=lambda x: x[1]["pnl"]):
        r(
            f"| {strat} | {d['count']} | {d['wins']} | {d['wins']/d['count']*100:.1f}% | ${d['pnl']:+,.0f} | ${d['pnl']/d['count']:+,.0f} |"
        )

    # 2c By Micro Regime (MOST IMPORTANT - excludes VASS overlap artifacts)
    r("\n### 2c. MICRO by Micro Regime (KEY ANALYSIS)\n")
    by_regime = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0, "trades": []})
    for t in real_micro:
        regime = t.get("_signal", {}).get("micro_regime", "UNKNOWN")
        by_regime[regime]["count"] += 1
        by_regime[regime]["wins"] += t["IsWin"]
        by_regime[regime]["pnl"] += t["P&L"]
        by_regime[regime]["trades"].append(t)

    r("| Micro Regime | Count | Wins | Win% | Total P&L | Avg P&L | Verdict |")
    r("|-------------|-------|------|------|-----------|---------|---------|")
    for regime, d in sorted(by_regime.items(), key=lambda x: x[1]["pnl"]):
        win_pct = d["wins"] / d["count"] * 100
        avg_pnl = d["pnl"] / d["count"]
        if avg_pnl > 100:
            verdict = "PROFITABLE"
        elif avg_pnl > -100:
            verdict = "OK"
        else:
            verdict = "TOXIC"
        r(
            f"| {regime} | {d['count']} | {d['wins']} | {win_pct:.1f}% | ${d['pnl']:+,.0f} | ${avg_pnl:+,.0f} | **{verdict}** |"
        )

    # 2d By Direction (excludes VASS overlap artifacts)
    r("\n### 2d. MICRO by Direction\n")
    by_dir = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for t in real_micro:
        direction = t.get("_signal", {}).get("direction", t["_opt_type"] or "N/A")
        by_dir[direction]["count"] += 1
        by_dir[direction]["wins"] += t["IsWin"]
        by_dir[direction]["pnl"] += t["P&L"]

    r("| Direction | Count | Wins | Win% | Total P&L | Avg P&L |")
    r("|-----------|-------|------|------|-----------|---------|")
    for d_name, d in sorted(by_dir.items(), key=lambda x: x[1]["pnl"]):
        r(
            f"| {d_name} | {d['count']} | {d['wins']} | {d['wins']/d['count']*100:.1f}% | ${d['pnl']:+,.0f} | ${d['pnl']/d['count']:+,.0f} |"
        )

    # 2e Exit Reason Distribution (includes all, VASS_EXIT annotated)
    r("\n### 2e. MICRO Exit Reason Distribution\n")
    micro_exit_dist = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    for t in micro_trades:
        trigger = t["_exit_trigger"]
        if t.get("_is_vass_overlap", False):
            trigger = "VASS_OVERLAP (artifact)"
        micro_exit_dist[trigger]["count"] += 1
        micro_exit_dist[trigger]["wins"] += t["IsWin"]
        micro_exit_dist[trigger]["pnl"] += t["P&L"]

    r("| Exit Trigger | Count | Win% | Total P&L | Avg P&L |")
    r("|-------------|-------|------|-----------|---------|")
    for reason, d in sorted(micro_exit_dist.items(), key=lambda x: x[1]["count"], reverse=True):
        r(
            f"| {reason} | {d['count']} | {d['wins']/d['count']*100:.1f}% | ${d['pnl']:+,.0f} | ${d['pnl']/d['count']:+,.0f} |"
        )

    # 2f Orphan Analysis (excludes VASS overlap artifacts)
    r("\n### 2f. Orphan / Overnight Hold Analysis\n")
    overnight = [t for t in real_micro if t["_exit_dt"].date() > t["_entry_dt"].date()]
    r(f"- Trades held overnight: **{len(overnight)}**")
    overnight_pnl = sum(t["P&L"] for t in overnight)
    overnight_wins = sum(1 for t in overnight if t["IsWin"])
    r(f"- Overnight Win%: **{overnight_wins/max(len(overnight),1)*100:.1f}%**")
    r(f"- Overnight Total P&L: **${overnight_pnl:+,.0f}**")
    r(f"- Overnight Avg P&L: **${overnight_pnl/max(len(overnight),1):+,.0f}**")

    same_day = [t for t in real_micro if t["_exit_dt"].date() == t["_entry_dt"].date()]
    same_pnl = sum(t["P&L"] for t in same_day)
    same_wins = sum(1 for t in same_day if t["IsWin"])
    r(f"\n- Same-day exits: **{len(same_day)}**")
    r(f"- Same-day Win%: **{same_wins/max(len(same_day),1)*100:.1f}%**")
    r(f"- Same-day Total P&L: **${same_pnl:+,.0f}**")

    # 2g Regime x Direction Heatmap (excludes VASS overlap artifacts)
    r("\n### 2g. Micro Regime x Direction Heatmap\n")
    heatmap = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
    all_regimes = set()
    all_dirs = set()
    for t in real_micro:
        regime = t.get("_signal", {}).get("micro_regime", "UNKNOWN")
        direction = t.get("_signal", {}).get("direction", t["_opt_type"] or "N/A")
        key = (regime, direction)
        heatmap[key]["count"] += 1
        heatmap[key]["wins"] += t["IsWin"]
        heatmap[key]["pnl"] += t["P&L"]
        all_regimes.add(regime)
        all_dirs.add(direction)

    sorted_dirs = sorted(all_dirs)
    header = "| Regime | " + " | ".join([f"{d} (Count/Win%/P&L)" for d in sorted_dirs]) + " |"
    separator = "|--------|" + "|".join(["---" for _ in sorted_dirs]) + "|"
    r(header)
    r(separator)
    for regime in sorted(all_regimes):
        cols = []
        for d in sorted_dirs:
            key = (regime, d)
            if key in heatmap:
                dd = heatmap[key]
                wp = dd["wins"] / dd["count"] * 100
                cols.append(f"{dd['count']}/{wp:.0f}%/${dd['pnl']:+,.0f}")
            else:
                cols.append("-")
        r(f"| {regime} | " + " | ".join(cols) + " |")

    # 2h Top 10 Worst MICRO (real trades only, excludes VASS overlaps)
    r("\n### 2h. Top 10 Worst MICRO Trades\n")
    worst_micro = sorted(real_micro, key=lambda x: x["P&L"])[:10]
    r("| # | Date | Strategy | Dir | Regime | VIX | Exit Trigger | P&L | P&L% |")
    r("|---|------|----------|-----|--------|-----|-------------|-----|------|")
    for i, t in enumerate(worst_micro, 1):
        sig = t.get("_signal", {})
        r(
            f"| {i} | {t['_entry_dt'].strftime('%Y-%m-%d')} | {sig.get('strategy', 'ITM_MOM')} | {sig.get('direction', 'N/A')} | {sig.get('micro_regime', 'N/A')} | {sig.get('vix', 'N/A')} | {t['_exit_trigger']} | ${t['P&L']:+,.0f} | {t['_pnl_pct']:+.1f}% |"
        )

    # ==================== PART 3: ITM ====================
    r("\n---\n")
    r("# Part 3: ITM Intraday Trade-by-Trade\n")
    if not itm_trades:
        r("\n_No ITM trades found._\n")
    else:
        r("| # | Date | Entry | Exit | Strategy | Dir | Exit Trigger | Hold | P&L $ | P&L% | W/L |")
        r("|---|------|-------|------|----------|-----|-------------|------|-------|------|-----|")
        for i, t in enumerate(itm_trades, 1):
            sig = t.get("_signal", {})
            entry_time = t["_entry_dt"].strftime("%H:%M")
            exit_time = t["_exit_dt"].strftime("%H:%M")
            entry_date = t["_entry_dt"].strftime("%Y-%m-%d")
            strategy = sig.get("strategy", "ITM_MOMENTUM")
            direction = sig.get("direction", t["_opt_type"] or "N/A")
            exit_trigger = t["_exit_trigger"]
            hold_d = t["_hold"]
            pnl = t["P&L"]
            pnl_pct = t["_pnl_pct"]
            wl = "W" if t["IsWin"] else "L"
            r(
                f"| {i} | {entry_date} | {entry_time} | {exit_time} | {strategy} | "
                f"{direction} | {exit_trigger} | {hold_d} | ${pnl:+,.0f} | {pnl_pct:+.1f}% | {wl} |"
            )

        r("\n### 3a. ITM Summary\n")
        itm_total_pnl = sum(t["P&L"] for t in itm_trades)
        itm_wins = sum(1 for t in itm_trades if t["IsWin"])
        itm_losses = len(itm_trades) - itm_wins
        itm_total_fees = sum(t["Fees"] for t in itm_trades)
        itm_avg_win = sum(t["P&L"] for t in itm_trades if t["IsWin"]) / max(itm_wins, 1)
        itm_avg_loss = sum(t["P&L"] for t in itm_trades if not t["IsWin"]) / max(itm_losses, 1)
        r(f"- Total ITM Trades: **{len(itm_trades)}**")
        r(f"- Wins: **{itm_wins}** ({itm_wins/max(len(itm_trades), 1)*100:.1f}%)")
        r(f"- Losses: **{itm_losses}** ({itm_losses/max(len(itm_trades), 1)*100:.1f}%)")
        r(f"- Net P&L: **${itm_total_pnl:+,.0f}**")
        r(f"- Total Fees: **${itm_total_fees:,.0f}**")
        r(f"- Avg Win: **${itm_avg_win:+,.0f}**")
        r(f"- Avg Loss: **${itm_avg_loss:+,.0f}**")

    # ==================== PART 4: Combined Root Cause ====================
    r("\n---\n")
    r("# Part 4: Combined Root Cause Analysis\n")

    # 3a Loss Concentration
    r("\n### 3a. Loss Concentration\n")
    all_losses_vass = [
        (
            s["net_pnl"],
            "VASS",
            s["long"]["_entry_dt"].strftime("%Y-%m-%d"),
            s.get("_entry_signal", {}).get("spread_type", "N/A"),
        )
        for s in spreads
        if s["net_pnl"] < 0
    ]
    all_losses_intraday = [
        (
            t["P&L"],
            t.get("_tag", "UNKNOWN") if not t.get("_is_vass_overlap") else "VASS_OVERLAP",
            t["_entry_dt"].strftime("%Y-%m-%d"),
            t.get("_signal", {}).get("strategy", "N/A"),
        )
        for t in intraday_trades
        if t["P&L"] < 0
    ]
    all_losses = sorted(all_losses_vass + all_losses_intraday, key=lambda x: x[0])

    total_loss = sum(x[0] for x in all_losses)
    r(f"- Total losses: **${total_loss:+,.0f}** across **{len(all_losses)}** losing trades")

    top10_loss = sum(x[0] for x in all_losses[:10])
    r(
        f"- Top 10 losses: **${top10_loss:+,.0f}** ({top10_loss/total_loss*100:.1f}% of total losses)"
    )
    top20_loss = sum(x[0] for x in all_losses[:20])
    r(
        f"- Top 20 losses: **${top20_loss:+,.0f}** ({top20_loss/total_loss*100:.1f}% of total losses)"
    )

    r("\n**Top 10 Biggest Losses:**\n")
    r("| # | Type | Date | Strategy/Type | P&L |")
    r("|---|------|------|---------------|-----|")
    for i, (pnl, typ, date, strat) in enumerate(all_losses[:10], 1):
        r(f"| {i} | {typ} | {date} | {strat} | ${pnl:+,.0f} |")

    # 3b Failure Mode Ranking
    r("\n### 3b. Failure Mode Ranking\n")
    failure_modes = defaultdict(lambda: {"count": 0, "pnl": 0})

    for s in spreads:
        if s["net_pnl"] < 0:
            trigger = s["_exit_trigger"]
            failure_modes[f"VASS_{trigger}"]["count"] += 1
            failure_modes[f"VASS_{trigger}"]["pnl"] += s["net_pnl"]

    for t in intraday_trades:
        if t["P&L"] < 0:
            if t.get("_is_vass_overlap", False):
                failure_modes["VASS_OVERLAP_ARTIFACT"]["count"] += 1
                failure_modes["VASS_OVERLAP_ARTIFACT"]["pnl"] += t["P&L"]
            else:
                trigger = t["_exit_trigger"]
                if t.get("_tag") == "ITM":
                    failure_modes[f"ITM_{trigger}"]["count"] += 1
                    failure_modes[f"ITM_{trigger}"]["pnl"] += t["P&L"]
                else:
                    regime = t.get("_signal", {}).get("micro_regime", "UNKNOWN")
                    failure_modes[f"MICRO_{trigger}_{regime}"]["count"] += 1
                    failure_modes[f"MICRO_{trigger}_{regime}"]["pnl"] += t["P&L"]

    r("| Failure Mode | Count | Total P&L | Avg P&L |")
    r("|-------------|-------|-----------|---------|")
    for mode, d in sorted(failure_modes.items(), key=lambda x: x[1]["pnl"])[:15]:
        r(f"| {mode} | {d['count']} | ${d['pnl']:+,.0f} | ${d['pnl']/d['count']:+,.0f} |")

    # 3c Regime Gate Simulation
    r("\n### 3c. Regime Gate Simulation\n")
    r("*What if we blocked trades in specific micro regimes?*\n")

    for block_regime in ["WORSENING", "DETERIORATING", "CAUTIOUS", "TRANSITION", "CAUTION_LOW"]:
        blocked = [
            t for t in real_micro if t.get("_signal", {}).get("micro_regime") == block_regime
        ]
        blocked_pnl = sum(t["P&L"] for t in blocked)
        blocked_wins = sum(1 for t in blocked if t["IsWin"])
        r(
            f"- Block **{block_regime}**: Remove {len(blocked)} trades, save **${-blocked_pnl:+,.0f}** (was ${blocked_pnl:+,.0f}, {blocked_wins}/{len(blocked)} wins)"
        )

    # Also simulate blocking VASS in certain regime ranges
    r("\n*VASS regime gate simulation:*\n")
    for threshold in [60, 65, 70]:
        blocked = [s for s in spreads if s.get("_entry_signal", {}).get("regime", 100) < threshold]
        blocked_pnl = sum(s["net_pnl"] for s in blocked)
        blocked_wins = sum(1 for s in blocked if s["_is_win"])
        r(
            f"- Block VASS Regime < {threshold}: Remove {len(blocked)} trades, impact **${-blocked_pnl:+,.0f}** (was ${blocked_pnl:+,.0f}, {blocked_wins}/{len(blocked)} wins)"
        )

    # 3d Min-Hold Impact
    r("\n### 3d. Min-Hold Impact Analysis\n")
    r("*MICRO trades that exited within 30 min (likely stop-outs):*\n")

    quick_exits = [t for t in real_micro if (t["_exit_dt"] - t["_entry_dt"]).total_seconds() < 1800]
    quick_pnl = sum(t["P&L"] for t in quick_exits)
    r(f"- Trades exiting < 30 min: **{len(quick_exits)}**")
    r(f"- Total P&L: **${quick_pnl:+,.0f}**")
    r(f"- Avg P&L: **${quick_pnl/max(len(quick_exits),1):+,.0f}**")

    r("\n*MICRO trades held > 1 day (overnight holds):*\n")
    long_holds = [t for t in real_micro if (t["_exit_dt"] - t["_entry_dt"]).total_seconds() > 86400]
    long_pnl = sum(t["P&L"] for t in long_holds)
    long_wins = sum(1 for t in long_holds if t["IsWin"])
    r(f"- Trades held > 1 day: **{len(long_holds)}**")
    r(f"- Win%: **{long_wins/max(len(long_holds),1)*100:.1f}%**")
    r(f"- Total P&L: **${long_pnl:+,.0f}**")

    # 3e Top 5 Actionable Fixes
    r("\n### 3e. Top 5 Actionable Fixes\n")

    # Analyze data to determine top fixes
    toxic_regimes = [
        (regime, d)
        for regime, d in by_regime.items()
        if d["pnl"] / d["count"] < -100 and d["count"] >= 3
    ]
    toxic_regimes.sort(key=lambda x: x[1]["pnl"])

    r("**1. Block Toxic Micro Regimes**\n")
    for regime, d in toxic_regimes[:3]:
        r(
            f"   - Block `{regime}`: Would save **${-d['pnl']:+,.0f}** from {d['count']} trades ({d['wins']}/{d['count']} wins)"
        )

    # Find worst VASS exit patterns
    vass_stop_losses = [s for s in spreads if s["_exit_trigger"] in ("STOP_LOSS", "HARD_STOP")]
    vass_sl_pnl = sum(s["net_pnl"] for s in vass_stop_losses)
    r(f"\n**2. Improve VASS Entry Quality**")
    r(f"   - {len(vass_stop_losses)} spreads hit stops = **${vass_sl_pnl:+,.0f}**")
    r(f"   - Consider tighter D/W% gate or higher VASS score minimum")

    # OCO stop rate
    oco_stops = [t for t in real_micro if t["_exit_trigger"] == "OCO_STOP"]
    oco_stop_pnl = sum(t["P&L"] for t in oco_stops)
    r(f"\n**3. MICRO OCO Stop Optimization**")
    r(f"   - {len(oco_stops)} MICRO trades hit OCO_STOP = **${oco_stop_pnl:+,.0f}**")
    r(f"   - Stop hit rate: {len(oco_stops)/len(real_micro)*100:.1f}%")

    # Overnight hold impact
    r(f"\n**4. Overnight Hold Policy**")
    r(f"   - {len(overnight)} MICRO trades held overnight = **${overnight_pnl:+,.0f}**")
    r(
        f"   - Win%: {overnight_wins/max(len(overnight),1)*100:.1f}% vs same-day: {same_wins/max(len(same_day),1)*100:.1f}%"
    )

    # Monthly variance
    r(f"\n**5. Seasonal/Monthly Pattern**")
    best_month = max(monthly.items(), key=lambda x: x[1]["pnl"])
    worst_month = min(monthly.items(), key=lambda x: x[1]["pnl"])
    r(f"   - Best VASS month: **{best_month[0]}** (${best_month[1]['pnl']:+,.0f})")
    r(f"   - Worst VASS month: **{worst_month[0]}** (${worst_month[1]['pnl']:+,.0f})")

    # Overall Summary
    r("\n---\n")
    r("## Overall Portfolio Summary\n")
    r(
        f"*Note: {len(vass_overlaps)} VASS overlap artifact(s) excluded from MICRO stats (P&L=${sum(t['P&L'] for t in vass_overlaps):+,.0f}). These are spread leg transitions recorded by QC as separate trades.*\n"
    )
    real_itm_pnl = sum(t["P&L"] for t in itm_trades)
    itm_fees = sum(t["Fees"] for t in itm_trades)
    itm_wins = sum(1 for t in itm_trades if t["IsWin"])
    grand_pnl = total_vass_pnl + real_micro_pnl + real_itm_pnl
    real_micro_fees = sum(t["Fees"] for t in real_micro)
    grand_fees = total_vass_fees + real_micro_fees + itm_fees
    grand_trades = len(spreads) + len(real_micro) + len(itm_trades)
    grand_wins = vass_wins + real_micro_wins + itm_wins

    itm_avg_win_total = sum(t["P&L"] for t in itm_trades if t["IsWin"]) / max(itm_wins, 1)
    itm_avg_loss_total = sum(t["P&L"] for t in itm_trades if not t["IsWin"]) / max(
        len(itm_trades) - itm_wins, 1
    )

    r(f"| Metric | VASS | MICRO | ITM | Total |")
    r(f"|--------|------|-------|-----|-------|")
    r(f"| Trades | {len(spreads)} | {len(real_micro)} | {len(itm_trades)} | {grand_trades} |")
    r(
        f"| Wins | {vass_wins} ({vass_wins/len(spreads)*100:.1f}%) | {real_micro_wins} ({real_micro_wins/max(len(real_micro),1)*100:.1f}%) | {itm_wins} ({itm_wins/max(len(itm_trades),1)*100:.1f}%) | {grand_wins} ({grand_wins/max(grand_trades,1)*100:.1f}%) |"
    )
    r(
        f"| Net P&L | ${total_vass_pnl:+,.0f} | ${real_micro_pnl:+,.0f} | ${real_itm_pnl:+,.0f} | ${grand_pnl:+,.0f} |"
    )
    r(
        f"| Fees | ${total_vass_fees:,.0f} | ${real_micro_fees:,.0f} | ${itm_fees:,.0f} | ${grand_fees:,.0f} |"
    )
    r(f"| Avg Win | ${avg_win:+,.0f} | ${avg_micro_win:+,.0f} | ${itm_avg_win_total:+,.0f} | - |")
    r(
        f"| Avg Loss | ${avg_loss:+,.0f} | ${avg_micro_loss:+,.0f} | ${itm_avg_loss_total:+,.0f} | - |"
    )

    # Write report
    print(f"Writing report to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w") as f:
        f.write("\n".join(report))
    print(f"Done! {len(report)} lines written.")


if __name__ == "__main__":
    main()
