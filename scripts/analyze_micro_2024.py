#!/usr/bin/env python3
"""
Analyze MICRO (Protective Puts) performance in V12.18-FullYear2024 backtest.
Classifies trades by entry order tag and provides detailed MICRO breakdown.
"""

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(
    "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage12.18"
)
TRADES_FILE = BASE / "V12.18-FullYear2024_trades.csv"
ORDERS_FILE = BASE / "V12.18-FullYear2024_orders.csv"
LOGS_FILE = BASE / "V12.18-FullYear2024_logs.txt"


def load_orders(path):
    orders = {}
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            oid = row["ID"].strip()
            orders[oid] = row
    return orders


def load_trades(path):
    trades = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return trades


def classify_trade(trade, orders):
    order_ids = trade["Order IDs"].strip().split(";")
    entry_oid = order_ids[0].strip()
    entry_order = orders.get(entry_oid)
    if not entry_order:
        return "UNKNOWN", "NO_ENTRY_ORDER", "", ""
    tag = entry_order["Tag"]
    exit_tag = ""
    if len(order_ids) > 1:
        exit_oid = order_ids[1].strip()
        exit_order = orders.get(exit_oid)
        if exit_order:
            exit_tag = exit_order["Tag"]
    if "MICRO:PROTECTIVE_PUTS" in tag:
        return "MICRO", "PROTECTIVE_PUTS", tag, exit_tag
    elif "MICRO:MICRO_OTM_MOMENTUM" in tag:
        return "MICRO", "MICRO_OTM_MOMENTUM", tag, exit_tag
    elif "ITM:ITM_MOMENTUM" in tag:
        return "ITM", "ITM_MOMENTUM", tag, exit_tag
    elif "VASS:" in tag:
        return "VASS", tag.split("|")[0].replace("VASS:", ""), tag, exit_tag
    else:
        return "UNKNOWN", tag[:50], tag, exit_tag


def classify_exit(trade, orders):
    order_ids = trade["Order IDs"].strip().split(";")
    if len(order_ids) < 2:
        return "NO_EXIT_ORDER"
    exit_oid = order_ids[-1].strip()
    exit_order = orders.get(exit_oid)
    if not exit_order:
        return "NO_EXIT_ORDER"
    exit_tag = exit_order["Tag"]
    exit_type = exit_order["Type"].strip()
    exit_status = exit_order["Status"].strip()
    if "RETRY_CANCELED" in exit_tag:
        return "RETRY_CANCELED"
    elif "MICRO_EOD_SWEEP" in exit_tag or "FORCE" in exit_tag.upper():
        return "FORCE_CLOSE/EOD_SWEEP"
    elif "UNCLASSIFIED" in exit_tag:
        return "UNCLASSIFIED_CLOSE"
    elif "OCO_STOP" in exit_tag:
        return "OCO_STOP"
    elif "OCO_PROFIT" in exit_tag:
        return "OCO_PROFIT"
    elif exit_type == "Stop Market" and exit_status == "Filled":
        return "OCO_STOP"
    elif (
        exit_type == "Limit"
        and exit_status == "Filled"
        and float(exit_order.get("Quantity", "0")) < 0
    ):
        return "OCO_PROFIT"
    elif exit_type == "Market" and exit_status == "Filled":
        return "MARKET_CLOSE"
    else:
        return f"OTHER:{exit_type}/{exit_status}"


def parse_duration(dur_str):
    dur_str = dur_str.strip().strip('"')
    parts = dur_str.split(".")
    if len(parts) == 2:
        days = int(parts[0])
        time_parts = parts[1].split(":")
    elif len(parts) == 1:
        days = 0
        time_parts = parts[0].split(":")
    else:
        return 0
    hours = int(time_parts[0]) if len(time_parts) > 0 else 0
    minutes = int(time_parts[1]) if len(time_parts) > 1 else 0
    return days * 24 + hours + minutes / 60


def get_direction(symbol_str):
    symbol_str = symbol_str.strip().strip('"')
    if "C0" in symbol_str:
        return "CALL"
    elif "P0" in symbol_str:
        return "PUT"
    return "UNKNOWN"


def analyze_logs(path):
    signal_counts = Counter()
    block_reasons = Counter()
    approved_count = 0
    dropped_count = 0
    micro_vix_levels = []
    micro_regimes = []
    intraday_blocked_reasons = Counter()

    with open(path, "r") as f:
        for line in f:
            if "INTRADAY: Blocked" in line and "MICRO_BLOCK" in line:
                signal_counts["MICRO_BLOCKED_SCAN"] += 1
                m = re.search(r"MICRO_BLOCK:(\S+)", line)
                if m:
                    reason = m.group(1)
                    # Clean trailing parens
                    reason = re.sub(r"\s*\(.*$", "", reason)
                    block_reasons[reason] += 1
            if "INTRADAY_SIGNAL:" in line and "PROTECTIVE_PUTS" in line:
                signal_counts["MICRO_PP_SIGNAL"] += 1
                m = re.search(r"VIX=(\d+\.?\d*)", line)
                if m:
                    micro_vix_levels.append(float(m.group(1)))
                m = re.search(r"Regime=(\S+)", line)
                if m:
                    micro_regimes.append(m.group(1))
            if "INTRADAY_SIGNAL:" in line and "MICRO_OTM" in line:
                signal_counts["MICRO_OTM_SIGNAL"] += 1
            if "INTRADAY_SIGNAL_APPROVED" in line and "Strategy=PROTECTIVE_PUTS" in line:
                approved_count += 1
            if "INTRADAY_SIGNAL_APPROVED" in line and "Strategy=MICRO_OTM" in line:
                approved_count += 1
            if "INTRADAY_SIGNAL_DROPPED" in line and "Strategy=PROTECTIVE_PUTS" in line:
                dropped_count += 1
                m = re.search(r"Code=(\S+)", line)
                if m:
                    intraday_blocked_reasons[m.group(1)] += 1
            if "INTRADAY_SIGNAL_DROPPED" in line and "Strategy=MICRO_OTM" in line:
                dropped_count += 1
                m = re.search(r"Code=(\S+)", line)
                if m:
                    intraday_blocked_reasons[m.group(1)] += 1

    return {
        "signal_counts": signal_counts,
        "block_reasons": block_reasons,
        "approved": approved_count,
        "dropped": dropped_count,
        "dropped_reasons": intraday_blocked_reasons,
        "vix_levels": micro_vix_levels,
        "regimes": micro_regimes,
    }


def print_table(headers, rows, title=""):
    if title:
        print(f"\n{'='*90}")
        print(f"  {title}")
        print(f"{'='*90}")
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))
    header_str = " | ".join(str(h).ljust(col_widths[i]) for i, h in enumerate(headers))
    print(f"  {header_str}")
    print(f"  {'-+-'.join('-' * w for w in col_widths)}")
    for row in rows:
        row_str = " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))
        print(f"  {row_str}")


def main():
    print("=" * 90)
    print("  V12.18-FullYear2024: MICRO (Protective Puts) Deep Analysis")
    print("=" * 90)

    orders = load_orders(ORDERS_FILE)
    trades = load_trades(TRADES_FILE)

    # ---- SECTION 0: Overall trade classification ----
    category_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0, "fees": 0.0})
    micro_trades = []

    for trade in trades:
        cat, sub, entry_tag, exit_tag = classify_trade(trade, orders)
        pnl = float(trade["P&L"])
        fees = float(trade["Fees"])
        is_win = int(trade["IsWin"])
        category_stats[cat]["count"] += 1
        category_stats[cat]["wins"] += is_win
        category_stats[cat]["pnl"] += pnl
        category_stats[cat]["fees"] += fees
        key = f"{cat}:{sub}"
        category_stats[key]["count"] += 1
        category_stats[key]["wins"] += is_win
        category_stats[key]["pnl"] += pnl
        category_stats[key]["fees"] += fees
        if cat == "MICRO":
            micro_trades.append({**trade, "sub": sub, "entry_tag": entry_tag, "exit_tag": exit_tag})

    rows = []
    for cat in ["VASS", "ITM", "MICRO", "UNKNOWN"]:
        s = category_stats[cat]
        if s["count"] == 0:
            continue
        wr = f"{s['wins']/s['count']*100:.1f}%" if s["count"] > 0 else "N/A"
        rows.append(
            [
                cat,
                s["count"],
                s["wins"],
                s["count"] - s["wins"],
                wr,
                f"${s['pnl']:,.0f}",
                f"${s['fees']:,.0f}",
                f"${s['pnl'] - s['fees']:,.0f}",
            ]
        )
    print_table(
        ["Category", "Trades", "Wins", "Losses", "WR", "Gross P&L", "Fees", "Net P&L"],
        rows,
        "SECTION 0: Overall Trade Classification",
    )

    rows = []
    for key in sorted(category_stats.keys()):
        if ":" not in key:
            continue
        s = category_stats[key]
        if s["count"] == 0:
            continue
        wr = f"{s['wins']/s['count']*100:.1f}%"
        rows.append(
            [
                key,
                s["count"],
                s["wins"],
                s["count"] - s["wins"],
                wr,
                f"${s['pnl']:,.0f}",
                f"${s['pnl'] - s['fees']:,.0f}",
            ]
        )
    print_table(
        ["Sub-Strategy", "Trades", "Wins", "Losses", "WR", "Gross P&L", "Net P&L"],
        rows,
        "Sub-Strategy Breakdown",
    )

    if not micro_trades:
        print("\n  NO MICRO TRADES FOUND!")
        return

    # ---- SECTION 1: MICRO Monthly Breakdown ----
    monthly = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0, "fees": 0.0})
    for t in micro_trades:
        month = t["Entry Time"].strip()[:7]
        pnl = float(t["P&L"])
        fees = float(t["Fees"])
        is_win = int(t["IsWin"])
        monthly[month]["count"] += 1
        monthly[month]["wins"] += is_win
        monthly[month]["pnl"] += pnl
        monthly[month]["fees"] += fees

    rows = []
    total_trades = total_wins = 0
    total_pnl = total_fees = 0.0
    for month in sorted(monthly.keys()):
        m = monthly[month]
        wr = f"{m['wins']/m['count']*100:.1f}%"
        losses = m["count"] - m["wins"]
        net = m["pnl"] - m["fees"]
        avg = net / m["count"]
        rows.append(
            [
                month,
                m["count"],
                m["wins"],
                losses,
                wr,
                f"${m['pnl']:,.0f}",
                f"${m['fees']:,.1f}",
                f"${net:,.0f}",
                f"${avg:,.0f}",
            ]
        )
        total_trades += m["count"]
        total_wins += m["wins"]
        total_pnl += m["pnl"]
        total_fees += m["fees"]
    total_wr = f"{total_wins/total_trades*100:.1f}%"
    total_net = total_pnl - total_fees
    rows.append(
        [
            "TOTAL",
            total_trades,
            total_wins,
            total_trades - total_wins,
            total_wr,
            f"${total_pnl:,.0f}",
            f"${total_fees:,.1f}",
            f"${total_net:,.0f}",
            f"${total_net/total_trades:,.0f}",
        ]
    )
    print_table(
        ["Month", "Trades", "Wins", "Losses", "WR", "Gross P&L", "Fees", "Net P&L", "Avg/Trade"],
        rows,
        "SECTION 1: MICRO Monthly Breakdown",
    )

    # ---- SECTION 2: Exit Trigger Distribution ----
    exit_triggers = Counter()
    exit_pnl = defaultdict(float)
    exit_counts_win = defaultdict(int)
    for t in micro_trades:
        trigger = classify_exit(t, orders)
        exit_triggers[trigger] += 1
        exit_pnl[trigger] += float(t["P&L"]) - float(t["Fees"])
        if int(t["IsWin"]):
            exit_counts_win[trigger] += 1
    rows = []
    for trigger, count in exit_triggers.most_common():
        wr = f"{exit_counts_win[trigger]/count*100:.1f}%"
        avg_pnl = exit_pnl[trigger] / count
        rows.append(
            [
                trigger,
                count,
                f"{count/total_trades*100:.1f}%",
                wr,
                f"${exit_pnl[trigger]:,.0f}",
                f"${avg_pnl:,.0f}",
            ]
        )
    print_table(
        ["Exit Trigger", "Count", "% of Total", "WR", "Net P&L", "Avg P&L"],
        rows,
        "SECTION 2: Exit Trigger Distribution",
    )

    # ---- SECTION 3: P&L Distribution ----
    pnl_pcts = []
    for t in micro_trades:
        entry = float(t["Entry Price"])
        exit_p = float(t["Exit Price"])
        if entry > 0:
            pnl_pcts.append((exit_p - entry) / entry * 100)
    buckets = [
        ("<-50%", lambda x: x < -50),
        ("-50% to -30%", lambda x: -50 <= x < -30),
        ("-30% to -20%", lambda x: -30 <= x < -20),
        ("-20% to -10%", lambda x: -20 <= x < -10),
        ("-10% to 0%", lambda x: -10 <= x < 0),
        ("0% to 10%", lambda x: 0 <= x < 10),
        ("10% to 20%", lambda x: 10 <= x < 20),
        ("20% to 50%", lambda x: 20 <= x < 50),
        ("50% to 100%", lambda x: 50 <= x < 100),
        (">100%", lambda x: x >= 100),
    ]
    rows = []
    for label, pred in buckets:
        count = sum(1 for p in pnl_pcts if pred(p))
        if count > 0:
            avg = sum(p for p in pnl_pcts if pred(p)) / count
            bar = "#" * min(count, 40)
            rows.append([label, count, f"{count/len(pnl_pcts)*100:.1f}%", f"{avg:.1f}%", bar])
    print_table(
        ["P&L Bucket", "Count", "% of Total", "Avg P&L%", "Histogram"],
        rows,
        "SECTION 3: P&L Percentage Distribution",
    )
    if pnl_pcts:
        print(
            f"\n  Stats: Min={min(pnl_pcts):.1f}% | Max={max(pnl_pcts):.1f}% | "
            f"Median={sorted(pnl_pcts)[len(pnl_pcts)//2]:.1f}% | Mean={sum(pnl_pcts)/len(pnl_pcts):.1f}%"
        )

    # ---- SECTION 4: Hold Duration Distribution ----
    durations = [parse_duration(t["Duration"]) for t in micro_trades]
    dur_buckets = [
        ("<1h", lambda x: x < 1),
        ("1-2h", lambda x: 1 <= x < 2),
        ("2-4h", lambda x: 2 <= x < 4),
        ("4-8h", lambda x: 4 <= x < 8),
        ("8-24h", lambda x: 8 <= x < 24),
        ("1-2 days", lambda x: 24 <= x < 48),
        ("2-5 days", lambda x: 48 <= x < 120),
        (">5 days", lambda x: x >= 120),
    ]
    rows = []
    for label, pred in dur_buckets:
        count = sum(1 for d in durations if pred(d))
        if count > 0:
            wins = sum(int(micro_trades[i]["IsWin"]) for i, d in enumerate(durations) if pred(d))
            pnl_sum = sum(
                float(micro_trades[i]["P&L"]) - float(micro_trades[i]["Fees"])
                for i, d in enumerate(durations)
                if pred(d)
            )
            wr = f"{wins/count*100:.1f}%"
            bar = "#" * min(count, 40)
            rows.append(
                [label, count, f"{count/len(durations)*100:.1f}%", wr, f"${pnl_sum:,.0f}", bar]
            )
    print_table(
        ["Duration", "Count", "% of Total", "WR", "Net P&L", "Histogram"],
        rows,
        "SECTION 4: Hold Duration Distribution",
    )
    if durations:
        print(
            f"\n  Stats: Min={min(durations):.1f}h | Max={max(durations):.1f}h | "
            f"Median={sorted(durations)[len(durations)//2]:.1f}h | Mean={sum(durations)/len(durations):.1f}h"
        )

    # ---- SECTION 5: Win Rate by Direction ----
    dir_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0, "fees": 0.0, "sizes": []})
    for t in micro_trades:
        direction = get_direction(t["Symbols"])
        pnl = float(t["P&L"])
        fees = float(t["Fees"])
        is_win = int(t["IsWin"])
        qty = abs(float(t["Quantity"]))
        entry = float(t["Entry Price"])
        size = qty * entry * 100
        dir_stats[direction]["count"] += 1
        dir_stats[direction]["wins"] += is_win
        dir_stats[direction]["pnl"] += pnl
        dir_stats[direction]["fees"] += fees
        dir_stats[direction]["sizes"].append(size)
    rows = []
    for direction in ["CALL", "PUT", "UNKNOWN"]:
        s = dir_stats[direction]
        if s["count"] == 0:
            continue
        wr = f"{s['wins']/s['count']*100:.1f}%"
        avg_size = sum(s["sizes"]) / len(s["sizes"])
        net = s["pnl"] - s["fees"]
        avg_pnl = net / s["count"]
        rows.append(
            [
                direction,
                s["count"],
                s["wins"],
                s["count"] - s["wins"],
                wr,
                f"${net:,.0f}",
                f"${avg_pnl:,.0f}",
                f"${avg_size:,.0f}",
            ]
        )
    print_table(
        ["Direction", "Trades", "Wins", "Losses", "WR", "Net P&L", "Avg P&L", "Avg Size"],
        rows,
        "SECTION 5: Win Rate by Direction (CALL vs PUT)",
    )

    # ---- SECTION 6: Trade Size Statistics ----
    sizes = [abs(float(t["Quantity"])) * float(t["Entry Price"]) * 100 for t in micro_trades]
    print_table(
        ["Metric", "Value"],
        [
            ["Total MICRO trades", str(total_trades)],
            [
                "Avg contracts/trade",
                f"{sum(abs(float(t['Quantity'])) for t in micro_trades)/len(micro_trades):.1f}",
            ],
            [
                "Avg entry price",
                f"${sum(float(t['Entry Price']) for t in micro_trades)/len(micro_trades):.2f}",
            ],
            ["Avg trade size (notional)", f"${sum(sizes)/len(sizes):,.0f}"],
            ["Min trade size", f"${min(sizes):,.0f}"],
            ["Max trade size", f"${max(sizes):,.0f}"],
            ["Total capital deployed", f"${sum(sizes):,.0f}"],
            ["Total net P&L", f"${total_net:,.0f}"],
            ["Return on capital deployed", f"{total_net/sum(sizes)*100:.2f}%"],
        ],
        "SECTION 6: Trade Size Statistics",
    )

    # ---- SECTION 7: Individual MICRO Trade Detail ----
    rows = []
    running_pnl = 0
    for t in micro_trades:
        entry_time = t["Entry Time"][:10]
        symbol = t["Symbols"].strip().strip('"')
        direction = get_direction(symbol)
        entry_p = float(t["Entry Price"])
        exit_p = float(t["Exit Price"])
        qty = abs(float(t["Quantity"]))
        pnl = float(t["P&L"])
        fees = float(t["Fees"])
        net = pnl - fees
        running_pnl += net
        is_win = "W" if int(t["IsWin"]) else "L"
        pnl_pct = (exit_p - entry_p) / entry_p * 100 if entry_p > 0 else 0
        exit_trigger = classify_exit(t, orders)
        dur = parse_duration(t["Duration"])
        m = re.search(r"(\d{6})([CP])(\d{8})", symbol)
        short_sym = f"{m.group(2)}{int(m.group(3))/1000:.0f}" if m else symbol[-15:]
        rows.append(
            [
                entry_time,
                direction,
                short_sym,
                f"{qty:.0f}",
                f"${entry_p:.2f}",
                f"${exit_p:.2f}",
                f"{pnl_pct:+.1f}%",
                f"${net:+,.0f}",
                f"${running_pnl:+,.0f}",
                is_win,
                f"{dur:.1f}h",
                exit_trigger[:22],
            ]
        )
    print_table(
        [
            "Date",
            "Dir",
            "Strike",
            "Qty",
            "Entry",
            "Exit",
            "P&L%",
            "Net$",
            "Cumul$",
            "W/L",
            "Hold",
            "Exit Trigger",
        ],
        rows,
        "SECTION 7: Individual MICRO Trade Detail",
    )

    # ---- SECTION 8: Signal Flow Analysis ----
    log_analysis = analyze_logs(LOGS_FILE)
    total_signals = (
        log_analysis["signal_counts"]["MICRO_PP_SIGNAL"]
        + log_analysis["signal_counts"]["MICRO_OTM_SIGNAL"]
    )
    approval_rate = (
        f"{log_analysis['approved']/total_signals*100:.1f}%" if total_signals > 0 else "N/A"
    )
    print_table(
        ["Metric", "Value"],
        [
            [
                "MICRO scans blocked (30-min level)",
                str(log_analysis["signal_counts"]["MICRO_BLOCKED_SCAN"]),
            ],
            [
                "Protective Puts signals generated",
                str(log_analysis["signal_counts"]["MICRO_PP_SIGNAL"]),
            ],
            [
                "OTM Momentum signals generated",
                str(log_analysis["signal_counts"]["MICRO_OTM_SIGNAL"]),
            ],
            ["MICRO signals approved (filled)", str(log_analysis["approved"])],
            ["MICRO signals dropped post-candidate", str(log_analysis["dropped"])],
            ["Approval rate (approved/generated)", approval_rate],
        ],
        "SECTION 8: Signal Flow Analysis",
    )

    rows = [(reason, count) for reason, count in log_analysis["block_reasons"].most_common(15)]
    if rows:
        print_table(
            ["Block Reason (scan level)", "Count"], rows, "Scan-Level Block Reasons (MICRO_BLOCK:*)"
        )

    rows = [(reason, count) for reason, count in log_analysis["dropped_reasons"].most_common(15)]
    if rows:
        print_table(
            ["Drop Reason (candidate level)", "Count"], rows, "Candidate-Level Drop Reasons"
        )

    if log_analysis["vix_levels"]:
        vix = log_analysis["vix_levels"]
        print_table(
            ["Metric", "Value"],
            [
                ["Signals with VIX data", str(len(vix))],
                ["Min VIX", f"{min(vix):.1f}"],
                ["Max VIX", f"{max(vix):.1f}"],
                ["Mean VIX", f"{sum(vix)/len(vix):.1f}"],
                ["Median VIX", f"{sorted(vix)[len(vix)//2]:.1f}"],
                ["VIX < 15", str(sum(1 for v in vix if v < 15))],
                ["VIX 15-20", str(sum(1 for v in vix if 15 <= v < 20))],
                ["VIX 20-25", str(sum(1 for v in vix if 20 <= v < 25))],
                ["VIX 25-35", str(sum(1 for v in vix if 25 <= v < 35))],
                ["VIX > 35", str(sum(1 for v in vix if v >= 35))],
            ],
            "VIX Levels at MICRO Signal Generation",
        )

    if log_analysis["regimes"]:
        regime_counts = Counter(log_analysis["regimes"])
        rows = [
            [r, c, f"{c/len(log_analysis['regimes'])*100:.1f}%"]
            for r, c in regime_counts.most_common()
        ]
        print_table(
            ["Regime", "Count", "% of Signals"],
            rows,
            "Regime Distribution at MICRO Signal Generation",
        )

    # ---- SECTION 9: Key Insights ----
    print(f"\n{'='*90}")
    print(f"  SECTION 9: Key Insights - Why MICRO Was Profitable in 2024")
    print(f"{'='*90}")

    put_stats = dir_stats["PUT"]
    call_stats = dir_stats["CALL"]
    win_pnls = [float(t["P&L"]) - float(t["Fees"]) for t in micro_trades if int(t["IsWin"])]
    loss_pnls = [float(t["P&L"]) - float(t["Fees"]) for t in micro_trades if not int(t["IsWin"])]
    avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
    avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0

    put_wr = f"{put_stats['wins']/put_stats['count']*100:.1f}%" if put_stats["count"] > 0 else "N/A"
    put_net = put_stats["pnl"] - put_stats["fees"] if put_stats["count"] > 0 else 0
    call_wr = (
        f"{call_stats['wins']/call_stats['count']*100:.1f}%" if call_stats["count"] > 0 else "N/A"
    )
    call_net = call_stats["pnl"] - call_stats["fees"] if call_stats["count"] > 0 else 0

    print(
        f"""
  1. DIRECTION TILT:
     - PUT trades: {put_stats['count']} ({put_stats['wins']} wins, WR={put_wr}, Net=${put_net:+,.0f})
     - CALL trades: {call_stats['count']} ({call_stats['wins']} wins, WR={call_wr}, Net=${call_net:+,.0f})"""
    )

    wl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    print(
        f"""
  2. WIN/LOSS ASYMMETRY:
     - Avg winning trade: ${avg_win:+,.0f}
     - Avg losing trade:  ${avg_loss:+,.0f}
     - Win/Loss ratio:    {wl_ratio:.2f}x (need > 1.0 for edge)
     - Total wins P&L:    ${sum(win_pnls):+,.0f} ({len(win_pnls)} trades)
     - Total loss P&L:    ${sum(loss_pnls):+,.0f} ({len(loss_pnls)} trades)"""
    )

    print(f"\n  3. TOP 5 BEST TRADES:")
    for t in sorted(micro_trades, key=lambda t: float(t["P&L"]) - float(t["Fees"]), reverse=True)[
        :5
    ]:
        net = float(t["P&L"]) - float(t["Fees"])
        sym = t["Symbols"].strip().strip('"')
        direction = get_direction(sym)
        entry_d = t["Entry Time"][:10]
        pnl_pct = (float(t["Exit Price"]) - float(t["Entry Price"])) / float(t["Entry Price"]) * 100
        exit_tr = classify_exit(t, orders)
        print(f"     {entry_d} | {direction} | ${net:+,.0f} | {pnl_pct:+.1f}% | {exit_tr}")

    print(f"\n  4. TOP 5 WORST TRADES:")
    for t in sorted(micro_trades, key=lambda t: float(t["P&L"]) - float(t["Fees"]))[:5]:
        net = float(t["P&L"]) - float(t["Fees"])
        sym = t["Symbols"].strip().strip('"')
        direction = get_direction(sym)
        entry_d = t["Entry Time"][:10]
        pnl_pct = (float(t["Exit Price"]) - float(t["Entry Price"])) / float(t["Entry Price"]) * 100
        exit_tr = classify_exit(t, orders)
        print(f"     {entry_d} | {direction} | ${net:+,.0f} | {pnl_pct:+.1f}% | {exit_tr}")

    win_months = sum(1 for m in monthly.values() if m["pnl"] - m["fees"] > 0)
    loss_months = sum(1 for m in monthly.values() if m["pnl"] - m["fees"] <= 0)
    months_with_trades = len(monthly)

    oco_stop_pnl = exit_pnl.get("OCO_STOP", 0)
    oco_profit_pnl = exit_pnl.get("OCO_PROFIT", 0)
    retry_pnl = exit_pnl.get("RETRY_CANCELED", 0)
    eod_pnl = exit_pnl.get("FORCE_CLOSE/EOD_SWEEP", 0)
    unclass_pnl = exit_pnl.get("UNCLASSIFIED_CLOSE", 0)

    print(
        f"""
  5. MONTHLY CONSISTENCY:
     - Months with trades: {months_with_trades}
     - Winning months: {win_months} / {months_with_trades}
     - Losing months:  {loss_months} / {months_with_trades}

  6. EXIT EFFECTIVENESS:
     - OCO_STOP exits P&L:          ${oco_stop_pnl:+,.0f} ({exit_triggers.get('OCO_STOP', 0)} trades)
     - OCO_PROFIT exits P&L:        ${oco_profit_pnl:+,.0f} ({exit_triggers.get('OCO_PROFIT', 0)} trades)
     - RETRY_CANCELED exits P&L:    ${retry_pnl:+,.0f} ({exit_triggers.get('RETRY_CANCELED', 0)} trades)
     - FORCE_CLOSE/EOD_SWEEP P&L:   ${eod_pnl:+,.0f} ({exit_triggers.get('FORCE_CLOSE/EOD_SWEEP', 0)} trades)
     - UNCLASSIFIED_CLOSE P&L:      ${unclass_pnl:+,.0f} ({exit_triggers.get('UNCLASSIFIED_CLOSE', 0)} trades)

  7. SELECTIVITY:
     - Scan-level blocks:  {log_analysis['signal_counts']['MICRO_BLOCKED_SCAN']} (high = selective)
     - Signals generated:  {total_signals}
     - Signals approved:   {log_analysis['approved']}
     - Signals dropped:    {log_analysis['dropped']}
     - Filter ratio:       {log_analysis['signal_counts']['MICRO_BLOCKED_SCAN'] / max(log_analysis['approved'], 1):.0f}:1 blocked-per-approved
"""
    )


if __name__ == "__main__":
    main()
