#!/usr/bin/env python3
"""
Comprehensive trade-by-trade analysis for V10.11 Full Year 2024
Distinguishes between MICRO, ITM_ENGINE, and VASS trades
VASS spreads are paired (2 legs = 1 trade)
"""

import csv
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

# File paths
TRADES_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.11/V10_11_FullYear2024_trades.csv"
ORDERS_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.11/V10_11_FullYear2024_orders.csv"
LOGS_TXT = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.11/V10_11_FullYear2024_logs.txt"
OUTPUT_MD = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.11/V10_11_FullYear2024_TRADE_DETAIL_REPORT.md"


def parse_symbol(symbol: str) -> Tuple[str, str, float]:
    """Extract direction (CALL/PUT), expiry, and strike from option symbol"""
    # QQQ   240103P00402780 -> PUT, 240103, 402.78
    match = re.search(r"(\d{6})([CP])(\d+)", symbol.strip())
    if match:
        expiry = match.group(1)
        direction = "CALL" if match.group(2) == "C" else "PUT"
        strike = int(match.group(3)) / 1000
        return direction, expiry, strike
    return "UNKNOWN", "", 0


def parse_time_duration(entry_time: str, exit_time: str) -> Tuple[str, float]:
    """Calculate hold duration in human-readable format and hours"""
    try:
        entry = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        exit = datetime.fromisoformat(exit_time.replace("Z", "+00:00"))
        duration = exit - entry

        total_hours = duration.total_seconds() / 3600
        days = duration.days
        hours = duration.seconds // 3600
        minutes = (duration.seconds % 3600) // 60

        if days > 0:
            return f"{days}d {hours}h", total_hours
        elif hours > 0:
            return f"{hours}h {minutes}m", total_hours
        else:
            return f"{minutes}m", total_hours
    except:
        return "UNKNOWN", 0


def identify_engine(order_tag: str) -> str:
    """Identify which engine placed the trade from order tag"""
    if "MICRO:" in order_tag:
        return "MICRO"
    elif "ITM:" in order_tag:
        return "ITM_ENGINE"
    elif "VASS:" in order_tag:
        return "VASS"
    return "UNKNOWN"


def get_vix_tier(vix: float) -> str:
    """Categorize VIX level"""
    if vix == 0:
        return "UNKNOWN"
    if vix < 16:
        return "LOW (<16)"
    elif vix < 25:
        return "MEDIUM (16-25)"
    else:
        return "HIGH (>25)"


def detect_exit_trigger(
    pnl: float, fees: float, mae: float, mfe: float, duration_str: str, logs_context: str
) -> str:
    """Infer exit trigger from trade metrics and log context"""
    # Check log context first
    if "OCO_PROFIT" in logs_context or "TARGET" in logs_context:
        return "OCO_PROFIT"
    if "OCO_STOP" in logs_context or "STOP" in logs_context:
        return "OCO_STOP"
    if "HARD_STOP" in logs_context or "ATR_STOP" in logs_context:
        return "HARD_STOP"
    if "TRAIL" in logs_context:
        return "TRAIL"
    if "EOD_SWEEP" in logs_context or "MICRO_EOD" in logs_context:
        return "EOD_SWEEP"
    if "EXPIRY" in logs_context or "DTE=0" in logs_context or "DTE=1" in logs_context:
        return "EXPIRY_FIREWALL"
    if "TIME_EXIT" in logs_context:
        return "TIME_EXIT"

    # Infer from metrics
    net_pnl = pnl + fees
    if abs(net_pnl) < 50:  # Near break-even
        return "TIME_EXIT"
    if mae != 0 and abs(mae - abs(net_pnl)) < 100:  # Hit max adverse
        return "HARD_STOP"
    if mfe != 0 and abs(mfe - net_pnl) < 100:  # Hit max favorable
        return "OCO_PROFIT"
    if "m" in duration_str and int(duration_str.split("m")[0].split()[-1]) < 60:  # < 1hr
        return "QUICK_EXIT"

    return "MANUAL/OTHER"


def find_consecutive_losses(trades: List[Dict]) -> List[List[Dict]]:
    """Find clusters of consecutive losses"""
    clusters = []
    current_cluster = []

    for trade in trades:
        if not trade["is_win"]:
            current_cluster.append(trade)
        else:
            if len(current_cluster) >= 3:  # 3+ consecutive losses
                clusters.append(current_cluster[:])
            current_cluster = []

    if len(current_cluster) >= 3:
        clusters.append(current_cluster)

    return clusters


def main():
    # Load trades
    trades_raw = []
    with open(TRADES_CSV, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades_raw.append(row)

    # Load orders to map engines
    order_map = {}  # order_id -> tag
    with open(ORDERS_CSV, "r") as f:
        reader = csv.DictReader(f)
        order_id = 0
        for row in reader:
            order_map[order_id] = row["Tag"]
            order_id += 1

    # Load key log lines for VIX/regime context
    vix_regime_map = {}  # date -> (vix, regime_score)
    exit_log_map = defaultdict(str)  # symbol+date -> exit context

    print(f"Loading logs from {LOGS_TXT}...")
    with open(LOGS_TXT, "r") as f:
        for line in f:
            # Extract VIX and regime - format: VIX_C=84(lvl=12.4)
            if "REGIME:" in line and "VIX_C=" in line:
                match = re.search(
                    r"(\d{4}-\d{2}-\d{2}).*?Score=([\d.]+).*?VIX_C=\d+\(lvl=([\d.]+)\)", line
                )
                if match:
                    date = match.group(1)
                    score = float(match.group(2))
                    vix = float(match.group(3))
                    vix_regime_map[date] = (vix, score)

            # Extract exit triggers
            if any(
                kw in line
                for kw in ["EXIT", "STOP", "PROFIT", "EOD_SWEEP", "TRAIL", "EXPIRY", "TARGET"]
            ):
                date_match = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                if date_match:
                    date = date_match.group(1)
                    symbol_match = re.search(r"(QQQ\s+\d{6}[CP]\d+)", line)
                    if symbol_match:
                        key = f"{date}_{symbol_match.group(1)}"
                        exit_log_map[key] += line + "\n"

    print(f"Loaded {len(trades_raw)} trade legs, {len(vix_regime_map)} regime snapshots")

    # Group VASS spreads (same entry/exit time = 1 spread trade)
    spread_groups = defaultdict(list)
    single_leg_trades = []

    for trade in trades_raw:
        entry_time = trade["Entry Time"]
        exit_time = trade["Exit Time"]
        symbol = trade["Symbols"]

        # Check if this is part of a spread (by checking order tags)
        order_ids = [
            int(x.strip()) for x in trade["Order Ids"].strip().split(",") if x.strip().isdigit()
        ]
        is_vass = False
        for oid in order_ids:
            if oid in order_map and "VASS:" in order_map[oid]:
                is_vass = True
                break

        if is_vass:
            # Group by entry+exit time (both legs have same time)
            key = f"{entry_time}_{exit_time}"
            spread_groups[key].append(trade)
        else:
            single_leg_trades.append(trade)

    print(f"Found {len(spread_groups)} VASS spread trades (2 legs each)")
    print(f"Found {len(single_leg_trades)} single-leg trades (MICRO + ITM)")

    # Analyze trades by engine
    engine_trades = defaultdict(list)

    # Process single-leg trades (MICRO + ITM)
    for trade in single_leg_trades:
        symbol = trade["Symbols"]
        entry_time = trade["Entry Time"]
        exit_time = trade["Exit Time"]
        direction, expiry, strike = parse_symbol(symbol)

        # Map to engine using order IDs
        order_ids = [
            int(x.strip()) for x in trade["Order Ids"].strip().split(",") if x.strip().isdigit()
        ]
        engine = "UNKNOWN"
        for oid in order_ids:
            if oid in order_map:
                tag = order_map[oid]
                engine = identify_engine(tag)
                if engine != "UNKNOWN":
                    break

        # Get VIX/regime at entry
        entry_date = entry_time[:10]
        vix_at_entry, regime_at_entry = vix_regime_map.get(entry_date, (0, 0))

        # Calculate P&L
        pnl = float(trade["P&L"])
        fees = float(trade["Fees"])
        mae = float(trade["MAE"])
        mfe = float(trade["MFE"])

        # Duration
        duration_str, duration_hours = parse_time_duration(entry_time, exit_time)

        # Exit trigger
        log_key = f"{entry_date}_{symbol.strip()}"
        exit_context = exit_log_map.get(log_key, "")
        exit_trigger = detect_exit_trigger(pnl, fees, mae, mfe, duration_str, exit_context)

        trade_data = {
            "date": entry_date,
            "entry_time": entry_time[11:16],
            "exit_time": exit_time[11:16],
            "symbol": symbol.strip(),
            "direction": direction,
            "expiry": expiry,
            "strike": strike,
            "engine": engine,
            "quantity": int(trade["Quantity"]),
            "entry_price": float(trade["Entry Price"]),
            "exit_price": float(trade["Exit Price"]),
            "pnl": pnl,
            "fees": fees,
            "mae": mae,
            "mfe": mfe,
            "is_win": trade["IsWin"] == "1",
            "duration": duration_str,
            "duration_hours": duration_hours,
            "vix_at_entry": vix_at_entry,
            "vix_tier": get_vix_tier(vix_at_entry),
            "regime_at_entry": regime_at_entry,
            "exit_trigger": exit_trigger,
            "is_spread": False,
        }

        engine_trades[engine].append(trade_data)

    # Process VASS spreads (combine both legs)
    for spread_key, legs in spread_groups.items():
        if len(legs) != 2:
            print(f"WARNING: Spread with {len(legs)} legs: {spread_key}")
            continue

        # Combine both legs
        leg1, leg2 = legs[0], legs[1]

        entry_time = leg1["Entry Time"]
        exit_time = leg1["Exit Time"]
        entry_date = entry_time[:10]

        # Get spread direction from the BUY leg
        buy_leg = leg1 if leg1["Direction"] == "Buy" else leg2
        sell_leg = leg2 if leg1["Direction"] == "Buy" else leg1

        buy_dir, buy_exp, buy_strike = parse_symbol(buy_leg["Symbols"])
        sell_dir, sell_exp, sell_strike = parse_symbol(sell_leg["Symbols"])

        # Net P&L and metrics
        net_pnl = float(leg1["P&L"]) + float(leg2["P&L"])
        net_fees = float(leg1["Fees"]) + float(leg2["Fees"])
        net_mae = float(leg1["MAE"]) + float(leg2["MAE"])
        net_mfe = float(leg1["MFE"]) + float(leg2["MFE"])

        # Determine spread type and direction
        if buy_dir == sell_dir == "PUT":
            if buy_strike < sell_strike:  # Buy lower, sell higher
                spread_type = "BULL_PUT_CREDIT"
                direction = "BULLISH"
            else:
                spread_type = "BEAR_PUT_DEBIT"
                direction = "BEARISH"
        elif buy_dir == sell_dir == "CALL":
            if buy_strike < sell_strike:  # Buy lower, sell higher
                spread_type = "BULL_CALL_DEBIT"
                direction = "BULLISH"
            else:
                spread_type = "BEAR_CALL_CREDIT"
                direction = "BEARISH"
        else:
            spread_type = "UNKNOWN"
            direction = "UNKNOWN"

        # Calculate debit/width ratio
        buy_price = float(buy_leg["Entry Price"])
        sell_price = float(sell_leg["Entry Price"])
        net_debit = abs(buy_price - sell_price)
        width = abs(buy_strike - sell_strike)
        debit_width_ratio = net_debit / width if width > 0 else 0

        # Get VIX/regime at entry
        vix_at_entry, regime_at_entry = vix_regime_map.get(entry_date, (0, 0))

        # Duration
        duration_str, duration_hours = parse_time_duration(entry_time, exit_time)

        # Exit trigger (check both legs)
        log_key1 = f"{entry_date}_{buy_leg['Symbols'].strip()}"
        log_key2 = f"{entry_date}_{sell_leg['Symbols'].strip()}"
        exit_context = exit_log_map.get(log_key1, "") + exit_log_map.get(log_key2, "")
        exit_trigger = detect_exit_trigger(
            net_pnl, net_fees, net_mae, net_mfe, duration_str, exit_context
        )

        # Calculate DTE at entry
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            expiry_dt = datetime.strptime(buy_exp, "%y%m%d")
            dte = (expiry_dt - entry_dt.replace(tzinfo=None)).days
        except:
            dte = 0

        spread_data = {
            "date": entry_date,
            "entry_time": entry_time[11:16],
            "exit_time": exit_time[11:16],
            "symbol": f"{buy_leg['Symbols'].strip()} / {sell_leg['Symbols'].strip()}",
            "direction": direction,
            "spread_type": spread_type,
            "buy_strike": buy_strike,
            "sell_strike": sell_strike,
            "width": width,
            "debit_width_ratio": debit_width_ratio,
            "expiry": buy_exp,
            "strike": (buy_strike + sell_strike) / 2,  # Mid-strike
            "engine": "VASS",
            "quantity": int(buy_leg["Quantity"]),
            "entry_price": net_debit,
            "exit_price": abs(float(buy_leg["Exit Price"]) - float(sell_leg["Exit Price"])),
            "pnl": net_pnl,
            "fees": net_fees,
            "mae": net_mae,
            "mfe": net_mfe,
            "is_win": net_pnl > 0,
            "duration": duration_str,
            "duration_hours": duration_hours,
            "vix_at_entry": vix_at_entry,
            "vix_tier": get_vix_tier(vix_at_entry),
            "regime_at_entry": regime_at_entry,
            "exit_trigger": exit_trigger,
            "is_spread": True,
            "dte_at_entry": dte,
        }

        engine_trades["VASS"].append(spread_data)

    # Generate report
    print(f"Writing report to {OUTPUT_MD}...")
    with open(OUTPUT_MD, "w") as f:
        f.write("# V10.11 Full Year 2024 - Trade-by-Trade Analysis\n\n")
        f.write("**Analysis Date:** " + datetime.now().strftime("%Y-%m-%d %H:%M") + "\n\n")
        f.write("**Period:** January 2, 2024 - December 30, 2024\n\n")
        f.write("---\n\n")

        # Summary by engine
        f.write("## Executive Summary by Engine\n\n")
        f.write("This system has THREE distinct intraday engines:\n\n")
        f.write("- **MICRO**: OTM momentum + Debit Fade (1-5 DTE, single-leg options)\n")
        f.write("- **ITM_ENGINE**: ITM momentum plays (7-14 DTE, single-leg options)\n")
        f.write("- **VASS**: Spread trades (14-45 DTE, two-leg spreads)\n\n")

        f.write(
            "| Engine | Trades | Wins | Losses | Win Rate | Total P&L | Avg P&L | Total Fees | Net P&L |\n"
        )
        f.write(
            "|--------|--------|------|--------|----------|-----------|---------|------------|----------|\n"
        )

        total_trades = sum(len(engine_trades[e]) for e in engine_trades)
        total_wins = sum(sum(1 for t in engine_trades[e] if t["is_win"]) for e in engine_trades)
        total_pnl = sum(sum(t["pnl"] for t in engine_trades[e]) for e in engine_trades)
        total_fees = sum(sum(t["fees"] for t in engine_trades[e]) for e in engine_trades)

        for engine in ["MICRO", "ITM_ENGINE", "VASS"]:
            trades_list = engine_trades[engine]
            if not trades_list:
                continue

            count = len(trades_list)
            wins = sum(1 for t in trades_list if t["is_win"])
            losses = count - wins
            win_rate = wins / count * 100 if count > 0 else 0
            pnl = sum(t["pnl"] for t in trades_list)
            avg_pnl = pnl / count if count > 0 else 0
            fees = sum(t["fees"] for t in trades_list)
            net = pnl - fees

            f.write(
                f"| **{engine}** | {count} | {wins} | {losses} | {win_rate:.1f}% | ${pnl:,.0f} | ${avg_pnl:.0f} | ${fees:.0f} | ${net:,.0f} |\n"
            )

        net_total = total_pnl - total_fees
        f.write(
            f"| **TOTAL** | {total_trades} | {total_wins} | {total_trades - total_wins} | {total_wins/total_trades*100:.1f}% | ${total_pnl:,.0f} | ${total_pnl/total_trades:.0f} | ${total_fees:.0f} | **${net_total:,.0f}** |\n"
        )
        f.write("\n---\n\n")

        # Detailed analysis per engine (reusing existing code for MICRO and ITM)
        for engine in ["MICRO", "ITM_ENGINE"]:
            trades_list = engine_trades[engine]
            if not trades_list:
                continue

            f.write(f"## {engine} Engine - Detailed Analysis\n\n")
            f.write(f"**Total Trades:** {len(trades_list)}\n\n")

            # By direction
            call_trades = [t for t in trades_list if t["direction"] == "CALL"]
            put_trades = [t for t in trades_list if t["direction"] == "PUT"]

            f.write("### Performance by Direction\n\n")
            f.write(
                "| Direction | Trades | Win Rate | Total P&L | Avg P&L | Avg Win | Avg Loss |\n"
            )
            f.write(
                "|-----------|--------|----------|-----------|---------|---------|----------|\n"
            )

            for dir_name, dir_trades in [("CALL", call_trades), ("PUT", put_trades)]:
                if not dir_trades:
                    continue
                wins = sum(1 for t in dir_trades if t["is_win"])
                wr = wins / len(dir_trades) * 100
                pnl = sum(t["pnl"] for t in dir_trades)
                avg = pnl / len(dir_trades)

                win_pnl = [t["pnl"] for t in dir_trades if t["is_win"]]
                loss_pnl = [t["pnl"] for t in dir_trades if not t["is_win"]]
                avg_win = sum(win_pnl) / len(win_pnl) if win_pnl else 0
                avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0

                f.write(
                    f"| {dir_name} | {len(dir_trades)} | {wr:.1f}% | ${pnl:,.0f} | ${avg:.0f} | ${avg_win:.0f} | ${avg_loss:.0f} |\n"
                )

            f.write("\n")

            # By VIX tier
            f.write("### Performance by VIX Environment\n\n")
            f.write("| VIX Tier | Trades | Win Rate | Total P&L | Avg P&L |\n")
            f.write("|----------|--------|----------|-----------|----------|\n")

            for tier in ["LOW (<16)", "MEDIUM (16-25)", "HIGH (>25)"]:
                tier_trades = [t for t in trades_list if t["vix_tier"] == tier]
                if not tier_trades:
                    continue
                wins = sum(1 for t in tier_trades if t["is_win"])
                wr = wins / len(tier_trades) * 100
                pnl = sum(t["pnl"] for t in tier_trades)
                avg = pnl / len(tier_trades)
                f.write(f"| {tier} | {len(tier_trades)} | {wr:.1f}% | ${pnl:,.0f} | ${avg:.0f} |\n")

            f.write("\n")

            # By hold duration
            f.write("### Performance by Hold Duration\n\n")
            f.write("| Duration | Trades | Win Rate | Total P&L | Avg P&L |\n")
            f.write("|----------|--------|----------|-----------|----------|\n")

            duration_buckets = {
                "< 1 hour": [t for t in trades_list if t["duration_hours"] < 1],
                "1-4 hours": [t for t in trades_list if 1 <= t["duration_hours"] < 4],
                "4-8 hours": [t for t in trades_list if 4 <= t["duration_hours"] < 8],
                "8-24 hours": [t for t in trades_list if 8 <= t["duration_hours"] < 24],
                "> 1 day": [t for t in trades_list if t["duration_hours"] >= 24],
            }

            for bucket_name, bucket_trades in duration_buckets.items():
                if not bucket_trades:
                    continue
                wins = sum(1 for t in bucket_trades if t["is_win"])
                wr = wins / len(bucket_trades) * 100 if bucket_trades else 0
                pnl = sum(t["pnl"] for t in bucket_trades)
                avg = pnl / len(bucket_trades) if bucket_trades else 0
                f.write(
                    f"| {bucket_name} | {len(bucket_trades)} | {wr:.1f}% | ${pnl:,.0f} | ${avg:.0f} |\n"
                )

            f.write("\n")

            # Exit trigger distribution
            f.write("### Exit Trigger Distribution\n\n")
            f.write("| Trigger | Count | Win Rate | Total P&L |\n")
            f.write("|---------|-------|----------|----------|\n")

            trigger_groups = defaultdict(list)
            for t in trades_list:
                trigger_groups[t["exit_trigger"]].append(t)

            for trigger in sorted(trigger_groups.keys()):
                trig_trades = trigger_groups[trigger]
                wins = sum(1 for t in trig_trades if t["is_win"])
                wr = wins / len(trig_trades) * 100
                pnl = sum(t["pnl"] for t in trig_trades)
                f.write(f"| {trigger} | {len(trig_trades)} | {wr:.1f}% | ${pnl:,.0f} |\n")

            f.write("\n")

            # Best and worst trades
            sorted_by_pnl = sorted(trades_list, key=lambda t: t["pnl"], reverse=True)

            f.write("### Top 10 Best Trades\n\n")
            f.write(
                "| Date | Dir | Strike | Entry | Exit | Qty | P&L | MFE | Duration | VIX | Exit Trigger |\n"
            )
            f.write(
                "|------|-----|--------|-------|------|-----|-----|-----|----------|-----|---------------|\n"
            )
            for t in sorted_by_pnl[:10]:
                f.write(
                    f"| {t['date']} | {t['direction']} | ${t['strike']:.0f} | ${t['entry_price']:.2f} | ${t['exit_price']:.2f} | {t['quantity']} | **${t['pnl']:,.0f}** | ${t['mfe']:,.0f} | {t['duration']} | {t['vix_at_entry']:.1f} | {t['exit_trigger']} |\n"
                )

            f.write("\n### Top 10 Worst Trades\n\n")
            f.write(
                "| Date | Dir | Strike | Entry | Exit | Qty | P&L | MAE | Duration | VIX | Exit Trigger |\n"
            )
            f.write(
                "|------|-----|--------|-------|------|-----|-----|-----|----------|-----|---------------|\n"
            )
            for t in sorted_by_pnl[-10:]:
                f.write(
                    f"| {t['date']} | {t['direction']} | ${t['strike']:.0f} | ${t['entry_price']:.2f} | ${t['exit_price']:.2f} | {t['quantity']} | **${t['pnl']:,.0f}** | ${t['mae']:,.0f} | {t['duration']} | {t['vix_at_entry']:.1f} | {t['exit_trigger']} |\n"
                )

            f.write("\n")

            # Profit left on table
            profit_left = [
                t for t in trades_list if not t["is_win"] and t["mfe"] > abs(t["pnl"]) * 1.5
            ]
            if profit_left:
                f.write(f"### Profit Left on Table ({len(profit_left)} trades)\n\n")
                f.write(
                    "Trades where MFE was >1.5x larger than the final loss (poor exit timing):\n\n"
                )
                f.write("| Date | Dir | P&L | MFE | MFE/Loss | Exit Trigger | Duration |\n")
                f.write("|------|-----|-----|-----|----------|--------------|----------|\n")
                for t in sorted(profit_left, key=lambda x: x["mfe"], reverse=True)[:15]:
                    ratio = abs(t["mfe"] / t["pnl"]) if t["pnl"] != 0 else 0
                    f.write(
                        f"| {t['date']} | {t['direction']} | ${t['pnl']:,.0f} | ${t['mfe']:,.0f} | {ratio:.1f}x | {t['exit_trigger']} | {t['duration']} |\n"
                    )
                f.write("\n")

            # Consecutive loss patterns
            sorted_chronologically = sorted(trades_list, key=lambda t: t["date"])
            loss_clusters = find_consecutive_losses(sorted_chronologically)
            if loss_clusters:
                f.write(f"### Consecutive Loss Patterns\n\n")
                f.write(f"Found {len(loss_clusters)} clusters of 3+ consecutive losses:\n\n")

                for idx, cluster in enumerate(loss_clusters[:5], 1):
                    total_loss = sum(t["pnl"] for t in cluster)
                    f.write(
                        f"**Cluster {idx}:** {len(cluster)} consecutive losses, Total: ${total_loss:,.0f}\n\n"
                    )
                    f.write("| Date | Dir | P&L | VIX | Exit Trigger |\n")
                    f.write("|------|-----|-----|-----|---------------|\n")
                    for t in cluster:
                        f.write(
                            f"| {t['date']} | {t['direction']} | ${t['pnl']:,.0f} | {t['vix_at_entry']:.1f} | {t['exit_trigger']} |\n"
                        )
                    f.write("\n")

            f.write("\n---\n\n")

        # VASS-specific detailed analysis
        vass_trades = engine_trades["VASS"]
        if vass_trades:
            f.write(f"## VASS Engine - Detailed Analysis\n\n")
            f.write(f"**Total Spread Trades:** {len(vass_trades)}\n\n")

            # By spread type
            f.write("### Performance by Spread Type\n\n")
            f.write("| Spread Type | Trades | Win Rate | Total P&L | Avg P&L |\n")
            f.write("|-------------|--------|----------|-----------|----------|\n")

            spread_types = defaultdict(list)
            for t in vass_trades:
                spread_types[t["spread_type"]].append(t)

            for stype in sorted(spread_types.keys()):
                stype_trades = spread_types[stype]
                wins = sum(1 for t in stype_trades if t["is_win"])
                wr = wins / len(stype_trades) * 100
                pnl = sum(t["pnl"] for t in stype_trades)
                avg = pnl / len(stype_trades)
                f.write(
                    f"| {stype} | {len(stype_trades)} | {wr:.1f}% | ${pnl:,.0f} | ${avg:.0f} |\n"
                )

            f.write("\n")

            # By VIX environment
            f.write("### Performance by VIX Environment (IV Tier)\n\n")
            f.write("| VIX Tier | Trades | Win Rate | Total P&L | Avg P&L | Avg D/W Ratio |\n")
            f.write("|----------|--------|----------|-----------|---------|----------------|\n")

            for tier in ["LOW (<16)", "MEDIUM (16-25)", "HIGH (>25)"]:
                tier_trades = [t for t in vass_trades if t["vix_tier"] == tier]
                if not tier_trades:
                    continue
                wins = sum(1 for t in tier_trades if t["is_win"])
                wr = wins / len(tier_trades) * 100
                pnl = sum(t["pnl"] for t in tier_trades)
                avg = pnl / len(tier_trades)
                avg_dw = sum(t["debit_width_ratio"] for t in tier_trades) / len(tier_trades)
                f.write(
                    f"| {tier} | {len(tier_trades)} | {wr:.1f}% | ${pnl:,.0f} | ${avg:.0f} | {avg_dw:.2%} |\n"
                )

            f.write("\n")

            # By DTE at entry
            f.write("### Performance by DTE at Entry\n\n")
            f.write("| DTE Range | Trades | Win Rate | Total P&L | Avg P&L |\n")
            f.write("|-----------|--------|----------|-----------|----------|\n")

            dte_buckets = {
                "0-7 days": [t for t in vass_trades if 0 <= t["dte_at_entry"] < 7],
                "7-14 days": [t for t in vass_trades if 7 <= t["dte_at_entry"] < 14],
                "14-21 days": [t for t in vass_trades if 14 <= t["dte_at_entry"] < 21],
                "21-30 days": [t for t in vass_trades if 21 <= t["dte_at_entry"] < 30],
                "> 30 days": [t for t in vass_trades if t["dte_at_entry"] >= 30],
            }

            for bucket_name, bucket_trades in dte_buckets.items():
                if not bucket_trades:
                    continue
                wins = sum(1 for t in bucket_trades if t["is_win"])
                wr = wins / len(bucket_trades) * 100
                pnl = sum(t["pnl"] for t in bucket_trades)
                avg = pnl / len(bucket_trades)
                f.write(
                    f"| {bucket_name} | {len(bucket_trades)} | {wr:.1f}% | ${pnl:,.0f} | ${avg:.0f} |\n"
                )

            f.write("\n")

            # Best and worst spreads
            sorted_by_pnl = sorted(vass_trades, key=lambda t: t["pnl"], reverse=True)

            f.write("### Top 10 Best Spreads\n\n")
            f.write("| Date | Type | Strikes | Width | D/W | P&L | Duration | VIX | DTE |\n")
            f.write("|------|------|---------|-------|-----|-----|----------|-----|-----|\n")
            for t in sorted_by_pnl[:10]:
                f.write(
                    f"| {t['date']} | {t['spread_type']} | ${t['buy_strike']:.0f}/${t['sell_strike']:.0f} | ${t['width']:.0f} | {t['debit_width_ratio']:.1%} | **${t['pnl']:,.0f}** | {t['duration']} | {t['vix_at_entry']:.1f} | {t['dte_at_entry']} |\n"
                )

            f.write("\n### Top 10 Worst Spreads\n\n")
            f.write("| Date | Type | Strikes | Width | D/W | P&L | Duration | VIX | DTE |\n")
            f.write("|------|------|---------|-------|-----|-----|----------|-----|-----|\n")
            for t in sorted_by_pnl[-10:]:
                f.write(
                    f"| {t['date']} | {t['spread_type']} | ${t['buy_strike']:.0f}/${t['sell_strike']:.0f} | ${t['width']:.0f} | {t['debit_width_ratio']:.1%} | **${t['pnl']:,.0f}** | {t['duration']} | {t['vix_at_entry']:.1f} | {t['dte_at_entry']} |\n"
                )

            f.write("\n---\n\n")

        # Cross-engine comparison
        f.write("## Cross-Engine Comparison\n\n")
        f.write("### Win Rate by Engine and Direction\n\n")
        f.write("| Engine | CALL/BULL WR | PUT/BEAR WR | Overall WR |\n")
        f.write("|--------|--------------|-------------|------------|\n")

        for engine in ["MICRO", "ITM_ENGINE", "VASS"]:
            trades_list = engine_trades[engine]
            if not trades_list:
                continue

            if engine == "VASS":
                bullish = [t for t in trades_list if t["direction"] == "BULLISH"]
                bearish = [t for t in trades_list if t["direction"] == "BEARISH"]
                bull_wr = (
                    sum(1 for t in bullish if t["is_win"]) / len(bullish) * 100 if bullish else 0
                )
                bear_wr = (
                    sum(1 for t in bearish if t["is_win"]) / len(bearish) * 100 if bearish else 0
                )
            else:
                calls = [t for t in trades_list if t["direction"] == "CALL"]
                puts = [t for t in trades_list if t["direction"] == "PUT"]
                bull_wr = sum(1 for t in calls if t["is_win"]) / len(calls) * 100 if calls else 0
                bear_wr = sum(1 for t in puts if t["is_win"]) / len(puts) * 100 if puts else 0

            overall_wr = sum(1 for t in trades_list if t["is_win"]) / len(trades_list) * 100

            f.write(f"| {engine} | {bull_wr:.1f}% | {bear_wr:.1f}% | {overall_wr:.1f}% |\n")

        f.write("\n---\n\n")
        f.write("**End of Report**\n\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    print(f"✓ Report written successfully to {OUTPUT_MD}")
    print(f"✓ Total analysis: {total_trades} trades")
    print(f"   - MICRO: {len(engine_trades['MICRO'])} trades")
    print(f"   - ITM_ENGINE: {len(engine_trades['ITM_ENGINE'])} trades")
    print(f"   - VASS: {len(engine_trades['VASS'])} spreads")


if __name__ == "__main__":
    main()
