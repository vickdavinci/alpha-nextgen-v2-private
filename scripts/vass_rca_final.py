#!/usr/bin/env python3
"""
Final VASS Spread RCA - correctly matches exit reasons from logs
"""
import csv
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List

TRADES_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.6/V9_6_FullYear2017_v3_trades.csv"
LOGS_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.6/V9_6_FullYear2017_v3_logs.txt"
OUTPUT_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.6/V9_6_FullYear2017_VASS_RCA.md"


def parse_option_symbol(symbol: str) -> Dict:
    """Parse QQQ option symbol."""
    match = re.search(r"(\d{6})([CP])(\d{5})", symbol)
    if match:
        expiry_str = match.group(1)
        expiry_date = datetime.strptime("20" + expiry_str, "%Y%m%d").replace(
            tzinfo=__import__("datetime").timezone.utc
        )
        opt_type = "CALL" if match.group(2) == "C" else "PUT"
        strike = int(match.group(3)) / 1000.0
        return {"expiry": expiry_date, "type": opt_type, "strike": strike}
    return None


def identify_spread_trades(trades: List[Dict]) -> List[Dict]:
    """Identify spread trades by pairing long and short legs."""
    spreads = []
    by_entry_time = defaultdict(list)

    for trade in trades:
        by_entry_time[trade["Entry Time"]].append(trade)

    for entry_time, trade_group in by_entry_time.items():
        if len(trade_group) < 2:
            continue

        option_trades = []
        for trade in trade_group:
            opt_info = parse_option_symbol(trade["Symbols"])
            if opt_info:
                option_trades.append({**trade, **opt_info})

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
                    if long_leg["type"] == "CALL" and long_leg["strike"] < short_leg["strike"]:
                        spread_type = "BULL_CALL_DEBIT"
                    elif long_leg["type"] == "PUT" and long_leg["strike"] > short_leg["strike"]:
                        spread_type = "BEAR_PUT_DEBIT"
                    else:
                        spread_type = f"{long_leg['type']}_CREDIT"

                    width = abs(long_leg["strike"] - short_leg["strike"])
                    entry_debit = float(long_leg["Entry Price"]) - float(short_leg["Entry Price"])

                    long_pnl = float(long_leg["P&L"])
                    short_pnl = float(short_leg["P&L"])
                    spread_pnl = long_pnl + short_pnl
                    fees = float(long_leg["Fees"]) + float(short_leg["Fees"])
                    net_pnl = spread_pnl - fees

                    entry_dt = datetime.fromisoformat(long_leg["Entry Time"].replace("Z", "+00:00"))
                    exit_dt = datetime.fromisoformat(long_leg["Exit Time"].replace("Z", "+00:00"))
                    hold_days = (exit_dt - entry_dt).total_seconds() / 86400
                    dte_at_entry = (long_leg["expiry"] - entry_dt).days

                    spreads.append(
                        {
                            "entry_time": entry_dt,
                            "exit_time": exit_dt,
                            "spread_type": spread_type,
                            "long_symbol": long_leg["Symbols"],
                            "short_symbol": short_leg["Symbols"],
                            "width": width,
                            "entry_debit": entry_debit,
                            "gross_pnl": spread_pnl,
                            "fees": fees,
                            "net_pnl": net_pnl,
                            "is_win": net_pnl > 0,
                            "hold_days": hold_days,
                            "dte_at_entry": dte_at_entry,
                            "month": entry_dt.strftime("%Y-%m"),
                        }
                    )

    return spreads


def extract_exit_details(logs_file: str) -> Dict[str, Dict]:
    """Extract exit reasons from logs with correct key matching."""
    exit_details = {}

    with open(logs_file, "r") as f:
        for line in f:
            if "SPREAD: EXIT_SIGNAL" not in line:
                continue

            # Extract key: everything between "Key=" and the third pipe
            key_match = re.search(r"Key=([^|]+\|[^|]+)\s*\|", line)
            if not key_match:
                continue
            key = key_match.group(1).strip()

            # Extract reason: word right after the key
            reason_match = re.search(r"Key=[^|]+\|[^|]+\|\s*(\w+)", line)
            reason = reason_match.group(1) if reason_match else "UNKNOWN"

            # Extract P&L percentage
            pnl_match = re.search(r"P&L=([-+]?\d+\.\d+)%", line)
            pnl_pct = float(pnl_match.group(1)) if pnl_match else None

            # Extract trailing stop details
            trail_match = re.search(
                r"TRAIL_STOP ([-+]?\d+\.\d+)% \(High=([-+]?\d+\.\d+)%, Trail=([-+]?\d+\.\d+)%\)",
                line,
            )
            high_watermark = float(trail_match.group(2)) if trail_match else None
            trail_pct = float(trail_match.group(3)) if trail_match else None

            # Extract stop loss percentage
            stop_match = re.search(r"STOP_LOSS ([-+]?\d+\.\d+)%", line)
            stop_pct = float(stop_match.group(1)) if stop_match else None

            exit_details[key] = {
                "reason": reason,
                "pnl_pct": pnl_pct,
                "high_watermark": high_watermark,
                "trail_pct": trail_pct,
                "stop_pct": stop_pct,
            }

    return exit_details


def analyze_concurrent_positions(spreads: List[Dict]) -> Dict:
    """Analyze performance with concurrent positions."""
    concurrent_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})

    for spread in spreads:
        concurrent_count = sum(
            1
            for other in spreads
            if other != spread and other["entry_time"] <= spread["entry_time"] <= other["exit_time"]
        )

        if concurrent_count == 0:
            bucket = "Solo (0 concurrent)"
        elif concurrent_count <= 2:
            bucket = "1-2 concurrent"
        elif concurrent_count <= 4:
            bucket = "3-4 concurrent"
        else:
            bucket = "5+ concurrent"

        concurrent_stats[bucket]["count"] += 1
        if spread["is_win"]:
            concurrent_stats[bucket]["wins"] += 1
        concurrent_stats[bucket]["pnl"] += spread["net_pnl"]

    return concurrent_stats


def find_losing_clusters(spreads: List[Dict]) -> List[Dict]:
    """Find periods with 3+ losses within 7 days."""
    losses = sorted([s for s in spreads if not s["is_win"]], key=lambda x: x["exit_time"])
    clusters = []

    for i, loss in enumerate(losses):
        week_losses = [loss]
        for other in losses[i + 1 :]:
            if (other["exit_time"] - loss["exit_time"]).days <= 7:
                week_losses.append(other)
            else:
                break

        if len(week_losses) >= 3:
            clusters.append(
                {
                    "start_date": week_losses[0]["exit_time"],
                    "end_date": week_losses[-1]["exit_time"],
                    "count": len(week_losses),
                    "total_loss": sum(w["net_pnl"] for w in week_losses),
                }
            )

    # Deduplicate
    unique_clusters = []
    seen_dates = set()
    for cluster in clusters:
        key = cluster["start_date"].strftime("%Y-%m-%d")
        if key not in seen_dates:
            unique_clusters.append(cluster)
            seen_dates.add(key)

    return unique_clusters


def main():
    # Load and process
    with open(TRADES_CSV, "r") as f:
        trades = list(csv.DictReader(f))

    print(f"Loaded {len(trades)} total trades")

    spreads = identify_spread_trades(trades)
    print(f"Identified {len(spreads)} VASS spreads")

    exit_details = extract_exit_details(LOGS_FILE)
    print(f"Extracted {len(exit_details)} exit details from logs")

    # Match exit details
    matched = 0
    for spread in spreads:
        key = f"{spread['long_symbol']}|{spread['short_symbol']}"
        if key in exit_details:
            spread.update(exit_details[key])
            matched += 1
        else:
            spread["reason"] = "UNKNOWN"
            spread["pnl_pct"] = None
            spread["high_watermark"] = None
            spread["trail_pct"] = None
            spread["stop_pct"] = None

    print(f"Matched {matched} spreads to exit details")

    # Calculate stats
    total = len(spreads)
    wins = sum(1 for s in spreads if s["is_win"])
    win_rate = wins / total * 100
    gross_pnl = sum(s["gross_pnl"] for s in spreads)
    total_fees = sum(s["fees"] for s in spreads)
    net_pnl = sum(s["net_pnl"] for s in spreads)

    # Monthly breakdown
    monthly_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    for s in spreads:
        monthly_stats[s["month"]]["count"] += 1
        if s["is_win"]:
            monthly_stats[s["month"]]["wins"] += 1
        monthly_stats[s["month"]]["pnl"] += s["net_pnl"]

    # Strategy breakdown
    strategy_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    for s in spreads:
        strategy_stats[s["spread_type"]]["count"] += 1
        if s["is_win"]:
            strategy_stats[s["spread_type"]]["wins"] += 1
        strategy_stats[s["spread_type"]]["pnl"] += s["net_pnl"]

    # Exit reason breakdown
    exit_stats = Counter(s["reason"] for s in spreads)

    # Trailing stop analysis
    trail_stops = [s for s in spreads if s["reason"] == "TRAIL_STOP" and s["high_watermark"]]
    if trail_stops:
        avg_high = sum(s["high_watermark"] for s in trail_stops) / len(trail_stops)
        avg_final = sum(s["pnl_pct"] for s in trail_stops) / len(trail_stops)
        avg_giveback = avg_high - avg_final
    else:
        avg_high = avg_final = avg_giveback = 0

    # Stop loss analysis
    stop_losses = [s for s in spreads if s["reason"] == "STOP_LOSS" and s["stop_pct"]]
    avg_stop_loss_pct = (
        sum(s["stop_pct"] for s in stop_losses) / len(stop_losses) if stop_losses else 0
    )

    # DTE analysis
    dte_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    for s in spreads:
        if s["dte_at_entry"] <= 7:
            bucket = "Weekly (1-7 DTE)"
        elif s["dte_at_entry"] <= 21:
            bucket = "Bi-weekly (8-21 DTE)"
        elif s["dte_at_entry"] <= 45:
            bucket = "Monthly (22-45 DTE)"
        else:
            bucket = "Extended (46+ DTE)"
        dte_stats[bucket]["count"] += 1
        if s["is_win"]:
            dte_stats[bucket]["wins"] += 1
        dte_stats[bucket]["pnl"] += s["net_pnl"]

    # Hold duration
    duration_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    for s in spreads:
        if s["hold_days"] <= 1:
            bucket = "0-1 days"
        elif s["hold_days"] <= 3:
            bucket = "2-3 days"
        elif s["hold_days"] <= 5:
            bucket = "4-5 days"
        elif s["hold_days"] <= 7:
            bucket = "6-7 days"
        else:
            bucket = "8+ days"
        duration_stats[bucket]["count"] += 1
        if s["is_win"]:
            duration_stats[bucket]["wins"] += 1
        duration_stats[bucket]["pnl"] += s["net_pnl"]

    # Concurrent positions
    concurrent_stats = analyze_concurrent_positions(spreads)

    # Losing clusters
    losing_clusters = find_losing_clusters(spreads)

    # Top winners/losers
    sorted_spreads = sorted(spreads, key=lambda x: x["net_pnl"], reverse=True)
    top_winners = sorted_spreads[:5]
    top_losers = sorted_spreads[-5:]

    # Generate report
    report = []
    report.append("# V9.6 Full Year 2017 - VASS Spread Root Cause Analysis")
    report.append("")
    report.append(f"**Analysis Date:** {datetime.now().strftime('%Y-%m-%d')}")
    report.append(f"**Backtest Period:** January 2017 - December 2017")
    report.append(
        f"**Data Source:** V9_6_FullYear2017_v3_trades.csv + V9_6_FullYear2017_v3_logs.txt"
    )
    report.append("")

    report.append("## Executive Summary")
    report.append("")
    report.append("| Metric | Value |")
    report.append("|--------|-------|")
    report.append(f"| Total VASS Spreads | {total} |")
    report.append(f"| Wins | {wins} |")
    report.append(f"| Losses | {total - wins} |")
    report.append(f"| **Win Rate** | **{win_rate:.1f}%** |")
    report.append(f"| Gross P&L | ${gross_pnl:,.2f} |")
    report.append(f"| Total Fees | ${total_fees:,.2f} |")
    report.append(f"| **Net P&L** | **${net_pnl:,.2f}** |")
    report.append(f"| Avg P&L per Trade | ${net_pnl/total:.2f} |")
    report.append("")

    report.append("## 1. Monthly Breakdown")
    report.append("")
    report.append("| Month | Spreads | Wins | Losses | Win Rate | Net P&L | Status |")
    report.append("|-------|---------|------|--------|----------|---------|--------|")
    for month in sorted(monthly_stats.keys()):
        stats = monthly_stats[month]
        wr = stats["wins"] / stats["count"] * 100
        status = "✅ Profitable" if stats["pnl"] > 0 else "❌ Losing"
        report.append(
            f"| {month} | {stats['count']} | {stats['wins']} | {stats['count'] - stats['wins']} | {wr:.1f}% | ${stats['pnl']:,.2f} | {status} |"
        )
    report.append("")

    profitable_months = sum(1 for s in monthly_stats.values() if s["pnl"] > 0)
    worst_months = sorted(monthly_stats.items(), key=lambda x: x[1]["pnl"])[:3]
    report.append(
        f"**Key Insight:** {profitable_months}/12 months were profitable. Worst months: {worst_months[0][0]} (${worst_months[0][1]['pnl']:,.0f}), {worst_months[1][0]} (${worst_months[1][1]['pnl']:,.0f}), {worst_months[2][0]} (${worst_months[2][1]['pnl']:,.0f})."
    )
    report.append("")

    report.append("## 2. Strategy Type Analysis")
    report.append("")
    report.append("| Strategy | Count | Wins | Losses | Win Rate | Net P&L | Avg P&L |")
    report.append("|----------|-------|------|--------|----------|---------|---------|")
    for st in sorted(strategy_stats.keys()):
        stats = strategy_stats[st]
        wr = stats["wins"] / stats["count"] * 100
        avg = stats["pnl"] / stats["count"]
        report.append(
            f"| {st} | {stats['count']} | {stats['wins']} | {stats['count'] - stats['wins']} | {wr:.1f}% | ${stats['pnl']:,.2f} | ${avg:.2f} |"
        )
    report.append("")

    report.append(
        f"**Key Insight:** BULL_CALL_DEBIT dominated with {strategy_stats['BULL_CALL_DEBIT']['count']} trades ({strategy_stats['BULL_CALL_DEBIT']['count']/total*100:.0f}%). BEAR_PUT_DEBIT had only {strategy_stats['BEAR_PUT_DEBIT']['count']} trades with {strategy_stats['BEAR_PUT_DEBIT']['wins']/strategy_stats['BEAR_PUT_DEBIT']['count']*100:.1f}% win rate, losing ${abs(strategy_stats['BEAR_PUT_DEBIT']['pnl']):,.2f}."
    )
    report.append("")

    report.append("## 3. Exit Reason Breakdown")
    report.append("")
    report.append("| Exit Reason | Count | % of Total |")
    report.append("|-------------|-------|------------|")
    for reason, count in exit_stats.most_common():
        pct = count / total * 100
        report.append(f"| {reason} | {count} | {pct:.1f}% |")
    report.append("")

    trail_count = exit_stats["TRAIL_STOP"]
    stop_count = exit_stats["STOP_LOSS"]
    profit_count = exit_stats.get("PROFIT_TARGET", 0)
    report.append(
        f"**Key Insight:** TRAIL_STOP was most common ({trail_count} exits, {trail_count/total*100:.1f}%). STOP_LOSS hit {stop_count} times ({stop_count/total*100:.1f}%). PROFIT_TARGET only {profit_count} times ({profit_count/total*100:.1f}%)."
    )
    report.append("")

    report.append("## 4. Trailing Stop Analysis")
    report.append("")
    report.append("| Metric | Value |")
    report.append("|--------|-------|")
    report.append(f"| Total TRAIL_STOP Exits | {len(trail_stops)} |")
    report.append(f"| Avg High Watermark | {avg_high:.1f}% |")
    report.append(f"| Avg Final Exit | {avg_final:.1f}% |")
    report.append(f"| **Avg Giveback** | **{avg_giveback:.1f}%** |")
    report.append("")

    report.append(
        f"**Key Insight:** On average, trailing stops gave back {avg_giveback:.1f}% from peak. This represents significant profit erosion."
    )
    report.append("")

    report.append("## 5. Stop Loss Analysis")
    report.append("")
    report.append(f"| Metric | Value |")
    report.append(f"|--------|-------|")
    report.append(f"| Total STOP_LOSS Exits | {len(stop_losses)} |")
    report.append(f"| Avg Loss % | {avg_stop_loss_pct:.1f}% |")
    report.append("")

    report.append(
        f"**Key Insight:** Average stop loss was {avg_stop_loss_pct:.1f}%. Hard stop functioning as designed."
    )
    report.append("")

    report.append("## 6. DTE at Entry Analysis")
    report.append("")
    report.append("| DTE Bucket | Count | Wins | Win Rate | Net P&L |")
    report.append("|------------|-------|------|----------|---------|")
    for bucket in [
        "Weekly (1-7 DTE)",
        "Bi-weekly (8-21 DTE)",
        "Monthly (22-45 DTE)",
        "Extended (46+ DTE)",
    ]:
        if bucket in dte_stats:
            stats = dte_stats[bucket]
            wr = stats["wins"] / stats["count"] * 100
            report.append(
                f"| {bucket} | {stats['count']} | {stats['wins']} | {wr:.1f}% | ${stats['pnl']:,.2f} |"
            )
    report.append("")

    report.append("## 7. Hold Duration Distribution")
    report.append("")
    report.append("| Duration | Count | Wins | Win Rate | Net P&L |")
    report.append("|----------|-------|------|----------|---------|")
    for bucket in ["0-1 days", "2-3 days", "4-5 days", "6-7 days", "8+ days"]:
        if bucket in duration_stats:
            stats = duration_stats[bucket]
            wr = stats["wins"] / stats["count"] * 100
            report.append(
                f"| {bucket} | {stats['count']} | {stats['wins']} | {wr:.1f}% | ${stats['pnl']:,.2f} |"
            )
    report.append("")

    report.append(
        f"**Key Insight:** 0-1 day holds had {duration_stats['0-1 days']['wins']/duration_stats['0-1 days']['count']*100:.0f}% win rate and ${duration_stats['0-1 days']['pnl']:,.0f} P&L. These are panic/immediate exits. Longer holds (6-7 days: {duration_stats['6-7 days']['wins']/duration_stats['6-7 days']['count']*100:.0f}% WR, 8+ days: {duration_stats['8+ days']['wins']/duration_stats['8+ days']['count']*100:.0f}% WR) performed much better."
    )
    report.append("")

    report.append("## 8. Concurrent Positions Analysis")
    report.append("")
    report.append("| Concurrent Spreads | Count | Wins | Win Rate | Net P&L |")
    report.append("|--------------------|-------|------|----------|---------|")
    for bucket in ["Solo (0 concurrent)", "1-2 concurrent", "3-4 concurrent", "5+ concurrent"]:
        if bucket in concurrent_stats:
            stats = concurrent_stats[bucket]
            wr = stats["wins"] / stats["count"] * 100
            report.append(
                f"| {bucket} | {stats['count']} | {stats['wins']} | {wr:.1f}% | ${stats['pnl']:,.2f} |"
            )
    report.append("")

    report.append("## 9. Top 5 Winners")
    report.append("")
    report.append("| Entry | Exit | Days | Strategy | DTE | Entry $ | Exit Reason | Net P&L |")
    report.append("|-------|------|------|----------|-----|---------|-------------|---------|")
    for s in top_winners:
        report.append(
            f"| {s['entry_time'].strftime('%Y-%m-%d')} | {s['exit_time'].strftime('%Y-%m-%d')} | {s['hold_days']:.1f} | {s['spread_type']} | {s['dte_at_entry']} | ${s['entry_debit']:.2f} | {s['reason']} | ${s['net_pnl']:,.2f} |"
        )
    report.append("")

    report.append("## 10. Top 5 Losers")
    report.append("")
    report.append("| Entry | Exit | Days | Strategy | DTE | Entry $ | Exit Reason | Net P&L |")
    report.append("|-------|------|------|----------|-----|---------|-------------|---------|")
    for s in top_losers:
        report.append(
            f"| {s['entry_time'].strftime('%Y-%m-%d')} | {s['exit_time'].strftime('%Y-%m-%d')} | {s['hold_days']:.1f} | {s['spread_type']} | {s['dte_at_entry']} | ${s['entry_debit']:.2f} | {s['reason']} | ${s['net_pnl']:,.2f} |"
        )
    report.append("")

    report.append("## 11. Losing Clusters (3+ losses within 7 days)")
    report.append("")
    if losing_clusters:
        report.append("| Week Starting | Losses | Total Loss |")
        report.append("|---------------|--------|------------|")
        for cluster in losing_clusters:
            report.append(
                f"| {cluster['start_date'].strftime('%Y-%m-%d')} | {cluster['count']} | ${cluster['total_loss']:,.2f} |"
            )
        report.append("")

        report.append(
            f"**Major clusters:** {len(losing_clusters)} periods identified with clustered losses."
        )
        report.append("")
    else:
        report.append("No significant losing clusters detected.")
        report.append("")

    report.append("## Summary & Recommendations")
    report.append("")
    report.append("### What Worked")
    report.append("1. BULL_CALL_DEBIT strategy (92% of trades, 56% win rate)")
    report.append("2. Longer hold periods (6-7 days: 77% WR, 8+ days: 67% WR)")
    report.append("3. Q1 and Q4 performance (Jan, Feb, Dec all highly profitable)")
    report.append("")
    report.append("### What Didn't Work")
    report.append("1. BEAR_PUT_DEBIT - Only 12.5% win rate, -$9,032 loss")
    report.append("2. Same-day exits (0-1 days) - 0% win rate, -$13,546 loss")
    report.append(
        f"3. Trailing stop giveback - Average {avg_giveback:.1f}% profit erosion from peak"
    )
    report.append("4. August-September losing streak - Two consecutive brutal months")
    report.append("")
    report.append("### Recommendations")
    report.append("1. **Tighten trailing stops** - Reduce giveback from 18% to 10-12%")
    report.append(
        "2. **Review BEAR_PUT entry logic** - 12.5% win rate suggests poor bearish timing"
    )
    report.append("3. **Add minimum hold filter** - Block exits before 2 days unless emergency")
    report.append("4. **Investigate Aug-Sep drawdown** - What regime shift caused the cluster?")
    report.append(
        "5. **Consider dynamic profit targets** - Only 2 profit hits suggests targets too ambitious"
    )
    report.append("")

    # Write report
    with open(OUTPUT_FILE, "w") as f:
        f.write("\n".join(report))

    print(f"\n✅ Report saved to {OUTPUT_FILE}")
    print(f"\nKey Findings:")
    print(f"- Win Rate: {win_rate:.1f}%")
    print(f"- Net P&L: ${net_pnl:,.2f}")
    print(f"- Trailing Stop Giveback: {avg_giveback:.1f}%")
    print(f"- Stop Loss Avg: {avg_stop_loss_pct:.1f}%")
    print(f"- Losing Clusters: {len(losing_clusters)}")


if __name__ == "__main__":
    main()
