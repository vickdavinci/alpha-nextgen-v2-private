#!/usr/bin/env python3
"""
VASS Spread Root Cause Analysis for V9.6 Full Year 2017
Analyzes VASS spread trades from trades.csv, orders.csv, and logs.
"""

import csv
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

# File paths
TRADES_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.6/V9_6_FullYear2017_v3_trades.csv"
ORDERS_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.6/V9_6_FullYear2017_v3_orders.csv"
LOGS_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.6/V9_6_FullYear2017_v3_logs.txt"
OUTPUT_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.6/V9_6_FullYear2017_VASS_RCA.md"


def parse_option_symbol(symbol: str) -> Dict:
    """Parse QQQ option symbol to extract strike and type."""
    # Format: QQQ   YYMMDDCXXXXX000 or QQQ   YYMMDDPXXXXX000
    match = re.search(r"(\d{6})([CP])(\d{5})", symbol)
    if match:
        expiry = match.group(1)
        opt_type = "CALL" if match.group(2) == "C" else "PUT"
        strike = int(match.group(3)) / 1000.0
        return {"expiry": expiry, "type": opt_type, "strike": strike}
    return None


def identify_spread_trades(trades: List[Dict]) -> List[Dict]:
    """Identify spread trades by pairing long and short legs with same expiry."""
    spreads = []

    # Group trades by entry time
    by_entry_time = defaultdict(list)
    for trade in trades:
        by_entry_time[trade["Entry Time"]].append(trade)

    for entry_time, trade_group in by_entry_time.items():
        if len(trade_group) < 2:
            continue

        # Parse all options
        option_trades = []
        for trade in trade_group:
            opt_info = parse_option_symbol(trade["Symbols"])
            if opt_info:
                option_trades.append({**trade, **opt_info})

        # Find pairs with same expiry
        for i, long_leg in enumerate(option_trades):
            if long_leg["Direction"] != "Buy":
                continue
            for short_leg in option_trades[i + 1 :]:
                if short_leg["Direction"] != "Sell":
                    continue
                if (
                    long_leg["expiry"] == short_leg["expiry"]
                    and long_leg["type"] == short_leg["type"]
                ):
                    # This is a spread
                    spread_type = (
                        f"{long_leg['type']}_DEBIT"
                        if long_leg["type"] == "CALL"
                        else "BEAR_PUT_DEBIT"
                    )
                    if long_leg["type"] == "CALL" and long_leg["strike"] < short_leg["strike"]:
                        spread_type = "BULL_CALL_DEBIT"
                    elif long_leg["type"] == "PUT" and long_leg["strike"] > short_leg["strike"]:
                        spread_type = "BEAR_PUT_DEBIT"

                    width = abs(long_leg["strike"] - short_leg["strike"])
                    entry_debit = float(long_leg["Entry Price"]) - float(short_leg["Entry Price"])
                    exit_debit = float(long_leg["Exit Price"]) - float(short_leg["Exit Price"])

                    long_pnl = float(long_leg["P&L"])
                    short_pnl = float(short_leg["P&L"])
                    spread_pnl = long_pnl + short_pnl

                    fees = float(long_leg["Fees"]) + float(short_leg["Fees"])
                    net_pnl = spread_pnl - fees

                    qty = int(long_leg["Quantity"])

                    is_win = net_pnl > 0

                    # Calculate hold duration
                    entry_dt = datetime.fromisoformat(long_leg["Entry Time"].replace("Z", "+00:00"))
                    exit_dt = datetime.fromisoformat(long_leg["Exit Time"].replace("Z", "+00:00"))
                    hold_days = (exit_dt - entry_dt).total_seconds() / 86400

                    spreads.append(
                        {
                            "entry_time": entry_dt,
                            "exit_time": exit_dt,
                            "spread_type": spread_type,
                            "long_symbol": long_leg["Symbols"],
                            "short_symbol": short_leg["Symbols"],
                            "long_strike": long_leg["strike"],
                            "short_strike": short_leg["strike"],
                            "width": width,
                            "entry_debit": entry_debit,
                            "exit_debit": exit_debit,
                            "quantity": qty,
                            "gross_pnl": spread_pnl,
                            "fees": fees,
                            "net_pnl": net_pnl,
                            "is_win": is_win,
                            "hold_days": hold_days,
                            "month": entry_dt.strftime("%Y-%m"),
                        }
                    )

    return spreads


def extract_exit_reasons(logs_file: str) -> Dict[str, str]:
    """Extract exit reasons from logs."""
    exit_reasons = {}

    with open(logs_file, "r") as f:
        for line in f:
            if "SPREAD: EXIT_SIGNAL" in line:
                # Extract spread key and reason
                match = re.search(r"Key=([^|]+)\s*\|\s*(\w+)", line)
                if match:
                    key = match.group(1).strip()
                    reason = match.group(2).strip()
                    exit_reasons[key] = reason

    return exit_reasons


def categorize_hold_duration(days: float) -> str:
    """Categorize hold duration into buckets."""
    if days <= 1:
        return "0-1 days"
    elif days <= 3:
        return "2-3 days"
    elif days <= 5:
        return "4-5 days"
    elif days <= 7:
        return "6-7 days"
    else:
        return "8+ days"


def categorize_width(width: float) -> str:
    """Categorize spread width."""
    if width < 4:
        return "$3-4"
    elif width < 5:
        return "$4-5"
    elif width < 6:
        return "$5-6"
    elif width < 7:
        return "$6-7"
    else:
        return "$7+"


def main():
    # Load trades
    trades = []
    with open(TRADES_CSV, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)

    print(f"Loaded {len(trades)} total trades")

    # Identify VASS spreads
    spreads = identify_spread_trades(trades)
    print(f"Identified {len(spreads)} VASS spreads")

    # Load exit reasons from logs
    exit_reasons = extract_exit_reasons(LOGS_FILE)
    print(f"Extracted {len(exit_reasons)} exit reasons from logs")

    # Match exit reasons to spreads
    for spread in spreads:
        key = f"{spread['long_symbol']}|{spread['short_symbol']}"
        if key in exit_reasons:
            spread["exit_reason"] = exit_reasons[key]
        else:
            spread["exit_reason"] = "UNKNOWN"

    # Analysis 1: Overall stats
    total_spreads = len(spreads)
    wins = sum(1 for s in spreads if s["is_win"])
    losses = total_spreads - wins
    win_rate = wins / total_spreads * 100 if total_spreads > 0 else 0
    gross_pnl = sum(s["gross_pnl"] for s in spreads)
    total_fees = sum(s["fees"] for s in spreads)
    net_pnl = sum(s["net_pnl"] for s in spreads)

    # Analysis 2: Monthly breakdown
    monthly_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    for spread in spreads:
        month = spread["month"]
        monthly_stats[month]["count"] += 1
        if spread["is_win"]:
            monthly_stats[month]["wins"] += 1
        monthly_stats[month]["pnl"] += spread["net_pnl"]

    # Analysis 3: Strategy type breakdown
    strategy_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    for spread in spreads:
        st = spread["spread_type"]
        strategy_stats[st]["count"] += 1
        if spread["is_win"]:
            strategy_stats[st]["wins"] += 1
        strategy_stats[st]["pnl"] += spread["net_pnl"]

    # Analysis 4: Exit reason breakdown
    exit_stats = defaultdict(lambda: {"count": 0})
    for spread in spreads:
        reason = spread["exit_reason"]
        exit_stats[reason]["count"] += 1

    # Analysis 5: Hold duration
    duration_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    for spread in spreads:
        bucket = categorize_hold_duration(spread["hold_days"])
        duration_stats[bucket]["count"] += 1
        if spread["is_win"]:
            duration_stats[bucket]["wins"] += 1
        duration_stats[bucket]["pnl"] += spread["net_pnl"]

    # Analysis 6: Spread width
    width_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    for spread in spreads:
        bucket = categorize_width(spread["width"])
        width_stats[bucket]["count"] += 1
        if spread["is_win"]:
            width_stats[bucket]["wins"] += 1
        width_stats[bucket]["pnl"] += spread["net_pnl"]

    # Analysis 7: Top winners and losers
    sorted_by_pnl = sorted(spreads, key=lambda x: x["net_pnl"], reverse=True)
    top_winners = sorted_by_pnl[:5]
    top_losers = sorted_by_pnl[-5:]

    # Generate report
    report = []
    report.append("# V9.6 Full Year 2017 - VASS Spread RCA")
    report.append("")
    report.append(f"**Analysis Date:** {datetime.now().strftime('%Y-%m-%d')}")
    report.append(f"**Period:** January 2017 - December 2017")
    report.append("")

    report.append("## 1. Overall Summary")
    report.append("")
    report.append(f"| Metric | Value |")
    report.append(f"|--------|-------|")
    report.append(f"| Total VASS Spreads | {total_spreads} |")
    report.append(f"| Wins | {wins} |")
    report.append(f"| Losses | {losses} |")
    report.append(f"| Win Rate | {win_rate:.1f}% |")
    report.append(f"| Gross P&L | ${gross_pnl:,.2f} |")
    report.append(f"| Total Fees | ${total_fees:,.2f} |")
    report.append(f"| Net P&L | ${net_pnl:,.2f} |")
    report.append("")

    report.append("## 2. Monthly Breakdown")
    report.append("")
    report.append("| Month | Spreads | Wins | Losses | Win Rate | Net P&L | Status |")
    report.append("|-------|---------|------|--------|----------|---------|--------|")
    for month in sorted(monthly_stats.keys()):
        stats = monthly_stats[month]
        wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
        status = "✅ Profitable" if stats["pnl"] > 0 else "❌ Losing"
        report.append(
            f"| {month} | {stats['count']} | {stats['wins']} | {stats['count'] - stats['wins']} | {wr:.1f}% | ${stats['pnl']:,.2f} | {status} |"
        )
    report.append("")

    report.append("## 3. Strategy Type Breakdown")
    report.append("")
    report.append("| Strategy | Count | Wins | Losses | Win Rate | Net P&L |")
    report.append("|----------|-------|------|--------|----------|---------|")
    for st in sorted(strategy_stats.keys()):
        stats = strategy_stats[st]
        wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
        report.append(
            f"| {st} | {stats['count']} | {stats['wins']} | {stats['count'] - stats['wins']} | {wr:.1f}% | ${stats['pnl']:,.2f} |"
        )
    report.append("")

    report.append("## 4. Exit Reason Breakdown")
    report.append("")
    report.append("| Exit Reason | Count | % of Total |")
    report.append("|-------------|-------|------------|")
    for reason in sorted(exit_stats.keys(), key=lambda x: exit_stats[x]["count"], reverse=True):
        stats = exit_stats[reason]
        pct = stats["count"] / total_spreads * 100 if total_spreads > 0 else 0
        report.append(f"| {reason} | {stats['count']} | {pct:.1f}% |")
    report.append("")

    report.append("## 5. Hold Duration Distribution")
    report.append("")
    report.append("| Duration | Count | Wins | Win Rate | Net P&L |")
    report.append("|----------|-------|------|----------|---------|")
    duration_order = ["0-1 days", "2-3 days", "4-5 days", "6-7 days", "8+ days"]
    for bucket in duration_order:
        if bucket in duration_stats:
            stats = duration_stats[bucket]
            wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
            report.append(
                f"| {bucket} | {stats['count']} | {stats['wins']} | {wr:.1f}% | ${stats['pnl']:,.2f} |"
            )
    report.append("")

    report.append("## 6. Spread Width Analysis")
    report.append("")
    report.append("| Width | Count | Wins | Win Rate | Net P&L |")
    report.append("|-------|-------|------|----------|---------|")
    width_order = ["$3-4", "$4-5", "$5-6", "$6-7", "$7+"]
    for bucket in width_order:
        if bucket in width_stats:
            stats = width_stats[bucket]
            wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
            report.append(
                f"| {bucket} | {stats['count']} | {stats['wins']} | {wr:.1f}% | ${stats['pnl']:,.2f} |"
            )
    report.append("")

    report.append("## 7. Top 5 Winners")
    report.append("")
    report.append(
        "| Entry Date | Exit Date | Strategy | Width | Entry Debit | Exit Reason | Net P&L |"
    )
    report.append(
        "|------------|-----------|----------|-------|-------------|-------------|---------|"
    )
    for spread in top_winners:
        report.append(
            f"| {spread['entry_time'].strftime('%Y-%m-%d')} | {spread['exit_time'].strftime('%Y-%m-%d')} | {spread['spread_type']} | ${spread['width']:.1f} | ${spread['entry_debit']:.2f} | {spread['exit_reason']} | ${spread['net_pnl']:,.2f} |"
        )
    report.append("")

    report.append("## 8. Top 5 Losers")
    report.append("")
    report.append(
        "| Entry Date | Exit Date | Strategy | Width | Entry Debit | Exit Reason | Net P&L |"
    )
    report.append(
        "|------------|-----------|----------|-------|-------------|-------------|---------|"
    )
    for spread in top_losers:
        report.append(
            f"| {spread['entry_time'].strftime('%Y-%m-%d')} | {spread['exit_time'].strftime('%Y-%m-%d')} | {spread['spread_type']} | ${spread['width']:.1f} | ${spread['entry_debit']:.2f} | {spread['exit_reason']} | ${spread['net_pnl']:,.2f} |"
        )
    report.append("")

    # Write report
    with open(OUTPUT_FILE, "w") as f:
        f.write("\n".join(report))

    print(f"\nReport saved to {OUTPUT_FILE}")
    print(f"\nKey Findings:")
    print(f"- Total VASS Spreads: {total_spreads}")
    print(f"- Win Rate: {win_rate:.1f}%")
    print(f"- Net P&L: ${net_pnl:,.2f}")


if __name__ == "__main__":
    main()
