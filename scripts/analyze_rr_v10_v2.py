#!/usr/bin/env python3
"""
V10 R:R Validation Analysis Script V2
Analyzes Apr-Jun 2023 backtest data for Risk:Reward validation
"""

import csv
import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple


def load_trades(filepath):
    trades = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return trades


def load_orders(filepath):
    orders = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            orders.append(row)
    return orders


def parse_strike_from_symbol(symbol: str) -> Optional[float]:
    """Extract strike from symbol like 'QQQ   230421C00321000'"""
    match = re.search(r"[CP](\d{8})", symbol)
    if match:
        strike_str = match.group(1)
        return float(strike_str) / 1000.0
    return None


def parse_option_type(symbol: str) -> Optional[str]:
    """Extract CALL or PUT from symbol"""
    if "C" in symbol[-9:]:
        return "CALL"
    elif "P" in symbol[-9:]:
        return "PUT"
    return None


def find_all_spreads_and_micro(trades, orders):
    """
    Classify ALL trades into VASS spreads or MICRO singles
    Returns (spreads, micro_trades, unclassified)
    """
    spreads = []
    micro_trades = []
    processed_indices = set()

    # Build order tag lookup by order ID
    order_tags_by_id = {}
    for order in orders:
        order_id = order.get("Order Ids", "").strip()
        if order_id:
            order_tags_by_id[order_id] = order.get("Tag", "")

    # Group trades by entry/exit times
    trade_groups = defaultdict(list)
    for i, trade in enumerate(trades):
        key = (trade["Entry Time"], trade["Exit Time"])
        trade_groups[key].append((i, trade))

    # Process spread pairs
    for key, group in trade_groups.items():
        if len(group) == 2:
            idx1, trade1 = group[0]
            idx2, trade2 = group[1]

            # Check if one is Buy and one is Sell (spread pattern)
            if (trade1["Direction"] == "Buy" and trade2["Direction"] == "Sell") or (
                trade1["Direction"] == "Sell" and trade2["Direction"] == "Buy"
            ):
                # This is a spread
                if trade1["Direction"] == "Buy":
                    long_leg = trade1
                    short_leg = trade2
                else:
                    long_leg = trade2
                    short_leg = trade1

                # Extract strikes
                long_strike = parse_strike_from_symbol(long_leg["Symbols"])
                short_strike = parse_strike_from_symbol(short_leg["Symbols"])

                if long_strike and short_strike:
                    entry_time = datetime.strptime(trade1["Entry Time"], "%Y-%m-%dT%H:%M:%SZ")
                    exit_time = datetime.strptime(trade1["Exit Time"], "%Y-%m-%dT%H:%M:%SZ")
                    hold_days = (exit_time - entry_time).total_seconds() / 86400.0

                    width = abs(short_strike - long_strike)
                    long_entry = float(long_leg["Entry Price"])
                    short_entry = float(short_leg["Entry Price"])
                    net_debit = long_entry - short_entry

                    long_exit = float(long_leg["Exit Price"])
                    short_exit = float(short_leg["Exit Price"])

                    pnl = float(long_leg["P&L"]) + float(short_leg["P&L"])
                    qty = int(long_leg["Quantity"])

                    max_profit = width - net_debit if net_debit > 0 else width

                    debit_width_ratio = (net_debit / width) if width > 0 else 0
                    pnl_pct_debit = (pnl / (net_debit * qty * 100)) if net_debit > 0 else 0
                    pnl_pct_max = (pnl / (max_profit * qty * 100)) if max_profit > 0 else 0

                    option_type = parse_option_type(long_leg["Symbols"])

                    # Check order tags for exit reason
                    order_ids = long_leg.get("Order Ids", "").split(",")
                    exit_reason = "UNKNOWN"
                    for oid in order_ids:
                        oid = oid.strip()
                        if oid in order_tags_by_id:
                            tag = order_tags_by_id[oid]
                            if "VASS" in tag and "EXIT" not in tag:
                                # This is entry tag, skip
                                continue
                            # Could extract exit reason from logs if needed
                            exit_reason = "VASS_EXIT"

                    spreads.append(
                        {
                            "entry_time": entry_time,
                            "exit_time": exit_time,
                            "hold_days": hold_days,
                            "long_strike": long_strike,
                            "short_strike": short_strike,
                            "width": width,
                            "long_entry": long_entry,
                            "short_entry": short_entry,
                            "net_debit": net_debit,
                            "long_exit": long_exit,
                            "short_exit": short_exit,
                            "max_profit": max_profit,
                            "pnl": pnl,
                            "qty": qty,
                            "debit_width_ratio": debit_width_ratio,
                            "pnl_pct_debit": pnl_pct_debit * 100,
                            "pnl_pct_max": pnl_pct_max * 100,
                            "option_type": option_type,
                            "is_win": pnl > 0,
                            "exit_reason": exit_reason,
                        }
                    )

                    processed_indices.add(idx1)
                    processed_indices.add(idx2)

    # Process single-leg trades (not part of spreads)
    for i, trade in enumerate(trades):
        if i in processed_indices:
            continue  # Already part of a spread

        # This is a single-leg trade
        entry_time = datetime.strptime(trade["Entry Time"], "%Y-%m-%dT%H:%M:%SZ")
        exit_time = datetime.strptime(trade["Exit Time"], "%Y-%m-%dT%H:%M:%SZ")
        hold_hours = (exit_time - entry_time).total_seconds() / 3600.0

        # Look up strategy from order tags
        order_ids = trade.get("Order Ids", "").split(",")
        strategy = "UNKNOWN"
        exit_type = "UNKNOWN"

        for oid in order_ids:
            oid = oid.strip()
            if oid in order_tags_by_id:
                tag = order_tags_by_id[oid]
                if "MICRO:ITM_MOMENTUM" in tag:
                    strategy = "ITM_MOMENTUM"
                elif "MICRO:DEBIT_FADE" in tag:
                    strategy = "DEBIT_FADE"
                elif "MICRO" in tag:
                    strategy = "MICRO"

                # Check exit type
                if "OCO_STOP" in tag:
                    exit_type = "OCO_STOP"
                elif "OCO_PROFIT" in tag:
                    exit_type = "OCO_PROFIT"
                elif "RECON_ORPHAN" in tag:
                    exit_type = "RECON_ORPHAN"

        # Also check exit time for force close
        if exit_time.hour == 19 and exit_time.minute >= 25:
            if exit_type == "UNKNOWN":
                exit_type = "FORCE_CLOSE_1926"

        # Check order type from orders table
        for order in orders:
            if (
                order.get("Symbol", "") == trade["Symbols"]
                and order.get("Time", "") == trade["Exit Time"]
            ):
                if "Stop Market" in order.get("Type", ""):
                    exit_type = "STOP_MARKET"
                elif "Market" in order.get("Type", "") and "RECON_ORPHAN" in order.get("Tag", ""):
                    exit_type = "RECON_ORPHAN"

        entry_price = float(trade["Entry Price"])
        exit_price = float(trade["Exit Price"])
        pnl = float(trade["P&L"])
        qty = int(trade["Quantity"])

        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

        option_type = parse_option_type(trade["Symbols"])

        # Only add if it's a MICRO trade
        if strategy in ["ITM_MOMENTUM", "DEBIT_FADE", "MICRO"]:
            micro_trades.append(
                {
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "hold_hours": hold_hours,
                    "strategy": strategy,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "qty": qty,
                    "pnl_pct": pnl_pct,
                    "exit_type": exit_type,
                    "option_type": option_type,
                    "is_win": pnl > 0,
                }
            )
            processed_indices.add(i)

    unclassified = []
    for i, trade in enumerate(trades):
        if i not in processed_indices:
            unclassified.append((i, trade))

    return spreads, micro_trades, unclassified


def bucket_debit_width(ratio):
    """Bucket debit/width ratio"""
    if ratio < 0.30:
        return "0-30%"
    elif ratio < 0.40:
        return "30-40%"
    elif ratio < 0.50:
        return "40-50%"
    elif ratio < 0.55:
        return "50-55%"
    else:
        return ">55%"


def bucket_pnl_pct(pnl_pct):
    """Bucket P&L percentage"""
    if pnl_pct >= 40:
        return ">+40% (big win)"
    elif pnl_pct >= 20:
        return "+20-40% (mod win)"
    elif pnl_pct >= 0:
        return "0-20% (small win)"
    elif pnl_pct >= -10:
        return "-10-0% (small loss)"
    elif pnl_pct >= -20:
        return "-20 to -10% (mod loss)"
    elif pnl_pct >= -30:
        return "-30 to -20% (stop area)"
    else:
        return "<-30% (beyond stop)"


def generate_report(spreads, micro_trades, unclassified):
    """Generate markdown report"""

    report = []
    report.append("# V10 Apr-Jun 2023 R:R Validation Report\n")
    report.append("**Period:** April 3, 2023 - June 30, 2023\n")
    report.append("**Analysis Date:** " + datetime.now().strftime("%Y-%m-%d") + "\n")
    report.append("\n---\n\n")

    # === EXECUTIVE SUMMARY ===
    report.append("## Executive Summary\n\n")

    total_vass = len(spreads)
    total_micro = len(micro_trades)

    vass_wins = sum(1 for s in spreads if s["is_win"])
    vass_losses = total_vass - vass_wins
    vass_wr = (vass_wins / total_vass * 100) if total_vass > 0 else 0

    micro_wins = sum(1 for m in micro_trades if m["is_win"])
    micro_losses = total_micro - micro_wins
    micro_wr = (micro_wins / total_micro * 100) if total_micro > 0 else 0

    vass_pnl = sum(s["pnl"] for s in spreads)
    micro_pnl = sum(m["pnl"] for m in micro_trades)

    report.append(
        f"- **VASS Spreads:** {total_vass} trades | {vass_wins}W-{vass_losses}L | {vass_wr:.1f}% WR | ${vass_pnl:,.0f} P&L\n"
    )
    report.append(
        f"- **MICRO Intraday:** {total_micro} trades | {micro_wins}W-{micro_losses}L | {micro_wr:.1f}% WR | ${micro_pnl:,.0f} P&L\n"
    )
    report.append(
        f"- **Total Classified:** {total_vass + total_micro} trades | ${vass_pnl + micro_pnl:,.0f} net P&L\n"
    )
    if unclassified:
        report.append(f"- **Unclassified:** {len(unclassified)} trades (see end of report)\n")
    report.append("\n")

    # === VASS ANALYSIS ===
    if total_vass > 0:
        report.append("## 1. VASS Spread Trade Analysis\n\n")

        # Table A: Debit/Width Distribution
        report.append("### Table A: VASS Debit/Width Ratio Distribution\n\n")

        buckets = {}
        for spread in spreads:
            bucket = bucket_debit_width(spread["debit_width_ratio"])
            if bucket not in buckets:
                buckets[bucket] = []
            buckets[bucket].append(spread)

        report.append(
            "| Debit/Width | Count | Wins | Losses | Win Rate | Avg P&L | Avg P&L % Debit | Avg P&L % Max |\n"
        )
        report.append(
            "|-------------|-------|------|--------|----------|---------|-----------------|---------------|\n"
        )

        for bucket_name in ["0-30%", "30-40%", "40-50%", "50-55%", ">55%"]:
            if bucket_name in buckets:
                bucket_spreads = buckets[bucket_name]
                count = len(bucket_spreads)
                wins = sum(1 for s in bucket_spreads if s["is_win"])
                losses = count - wins
                wr = (wins / count * 100) if count > 0 else 0
                avg_pnl = sum(s["pnl"] for s in bucket_spreads) / count if count > 0 else 0
                avg_pnl_pct_debit = (
                    sum(s["pnl_pct_debit"] for s in bucket_spreads) / count if count > 0 else 0
                )
                avg_pnl_pct_max = (
                    sum(s["pnl_pct_max"] for s in bucket_spreads) / count if count > 0 else 0
                )

                report.append(
                    f"| {bucket_name} | {count} | {wins} | {losses} | {wr:.1f}% | ${avg_pnl:,.0f} | {avg_pnl_pct_debit:+.1f}% | {avg_pnl_pct_max:+.1f}% |\n"
                )

        report.append("\n")

        # Table B: Stats
        report.append("### Table B: VASS Trade Statistics\n\n")

        avg_width = sum(s["width"] for s in spreads) / len(spreads) if spreads else 0
        avg_debit = sum(s["net_debit"] for s in spreads) / len(spreads) if spreads else 0
        avg_dw_ratio = sum(s["debit_width_ratio"] for s in spreads) / len(spreads) if spreads else 0
        avg_hold = sum(s["hold_days"] for s in spreads) / len(spreads) if spreads else 0

        report.append(f"- **Avg Spread Width:** ${avg_width:.2f}\n")
        report.append(f"- **Avg Net Debit:** ${avg_debit:.2f}\n")
        report.append(f"- **Avg Debit/Width:** {avg_dw_ratio*100:.1f}%\n")
        report.append(f"- **Avg Hold Time:** {avg_hold:.1f} days\n\n")

        # Table C: P&L Distribution
        report.append("### Table C: VASS P&L Distribution (Histogram)\n\n")

        pnl_buckets = {}
        for spread in spreads:
            bucket = bucket_pnl_pct(spread["pnl_pct_debit"])
            if bucket not in pnl_buckets:
                pnl_buckets[bucket] = []
            pnl_buckets[bucket].append(spread)

        report.append("| P&L % Range | Count | % of Total | Avg P&L | Total P&L |\n")
        report.append("|-------------|-------|------------|---------|----------|\n")

        for bucket_name in [
            ">+40% (big win)",
            "+20-40% (mod win)",
            "0-20% (small win)",
            "-10-0% (small loss)",
            "-20 to -10% (mod loss)",
            "-30 to -20% (stop area)",
            "<-30% (beyond stop)",
        ]:
            if bucket_name in pnl_buckets:
                bucket_spreads = pnl_buckets[bucket_name]
                count = len(bucket_spreads)
                pct_total = (count / len(spreads) * 100) if spreads else 0
                avg_pnl = sum(s["pnl"] for s in bucket_spreads) / count if count > 0 else 0
                total_pnl = sum(s["pnl"] for s in bucket_spreads)

                report.append(
                    f"| {bucket_name} | {count} | {pct_total:.1f}% | ${avg_pnl:,.0f} | ${total_pnl:,.0f} |\n"
                )

        report.append("\n")

        # CALL vs PUT
        report.append("### VASS: CALL vs PUT Performance\n\n")

        calls = [s for s in spreads if s["option_type"] == "CALL"]
        puts = [s for s in spreads if s["option_type"] == "PUT"]

        if calls:
            call_wins = sum(1 for s in calls if s["is_win"])
            call_wr = (call_wins / len(calls) * 100) if calls else 0
            call_pnl = sum(s["pnl"] for s in calls)
            call_avg_pnl = call_pnl / len(calls) if calls else 0
            report.append(
                f"**CALL Spreads:** {len(calls)} trades | {call_wins}W-{len(calls)-call_wins}L | {call_wr:.1f}% WR | ${call_pnl:,.0f} total (${call_avg_pnl:,.0f} avg)\n\n"
            )

        if puts:
            put_wins = sum(1 for s in puts if s["is_win"])
            put_wr = (put_wins / len(puts) * 100) if puts else 0
            put_pnl = sum(s["pnl"] for s in puts)
            put_avg_pnl = put_pnl / len(puts) if puts else 0
            report.append(
                f"**PUT Spreads:** {len(puts)} trades | {put_wins}W-{len(puts)-put_wins}L | {put_wr:.1f}% WR | ${put_pnl:,.0f} total (${put_avg_pnl:,.0f} avg)\n\n"
            )

    # === MICRO ANALYSIS ===
    if total_micro > 0:
        report.append("## 2. MICRO Intraday Trade Analysis\n\n")

        # Table D: Strategy Performance
        report.append("### Table D: MICRO Strategy Performance\n\n")

        strategies = {}
        for trade in micro_trades:
            strat = trade["strategy"]
            if strat not in strategies:
                strategies[strat] = []
            strategies[strat].append(trade)

        report.append(
            "| Strategy | Count | Wins | Losses | Win Rate | Avg Win % | Avg Loss % | Actual R:R | Avg Hold (hrs) |\n"
        )
        report.append(
            "|----------|-------|------|--------|----------|-----------|------------|------------|----------------|\n"
        )

        for strat_name in sorted(strategies.keys()):
            strat_trades = strategies[strat_name]
            count = len(strat_trades)
            wins = [t for t in strat_trades if t["is_win"]]
            losses = [t for t in strat_trades if not t["is_win"]]

            win_count = len(wins)
            loss_count = len(losses)
            wr = (win_count / count * 100) if count > 0 else 0

            avg_win_pct = sum(w["pnl_pct"] for w in wins) / len(wins) if wins else 0
            avg_loss_pct = sum(l["pnl_pct"] for l in losses) / len(losses) if losses else 0

            actual_rr = abs(avg_win_pct / avg_loss_pct) if avg_loss_pct != 0 else 0

            avg_hold = sum(t["hold_hours"] for t in strat_trades) / count if count > 0 else 0

            report.append(
                f"| {strat_name} | {count} | {win_count} | {loss_count} | {wr:.1f}% | {avg_win_pct:+.1f}% | {avg_loss_pct:+.1f}% | {actual_rr:.2f}:1 | {avg_hold:.1f} |\n"
            )

        report.append("\n")

        # Table E: Exit Mechanism
        report.append("### Table E: MICRO Exit Mechanism Distribution\n\n")

        exit_types = {}
        for trade in micro_trades:
            exit_type = trade["exit_type"]
            if exit_type not in exit_types:
                exit_types[exit_type] = []
            exit_types[exit_type].append(trade)

        report.append("| Exit Type | Count | % of Total | Wins | Losses | Win Rate | Avg P&L |\n")
        report.append("|-----------|-------|------------|------|--------|----------|---------|\n")

        for exit_name in sorted(exit_types.keys()):
            exit_trades = exit_types[exit_name]
            count = len(exit_trades)
            pct = (count / len(micro_trades) * 100) if micro_trades else 0
            wins = sum(1 for t in exit_trades if t["is_win"])
            losses = count - wins
            wr = (wins / count * 100) if count > 0 else 0
            avg_pnl = sum(t["pnl"] for t in exit_trades) / count if count > 0 else 0

            report.append(
                f"| {exit_name} | {count} | {pct:.1f}% | {wins} | {losses} | {wr:.1f}% | ${avg_pnl:,.0f} |\n"
            )

        report.append("\n")

        # CALL vs PUT
        report.append("### MICRO: CALL vs PUT Performance\n\n")

        micro_calls = [m for m in micro_trades if m["option_type"] == "CALL"]
        micro_puts = [m for m in micro_trades if m["option_type"] == "PUT"]

        if micro_calls:
            mcall_wins = sum(1 for m in micro_calls if m["is_win"])
            mcall_wr = (mcall_wins / len(micro_calls) * 100) if micro_calls else 0
            mcall_pnl = sum(m["pnl"] for m in micro_calls)
            mcall_avg_pnl = mcall_pnl / len(micro_calls) if micro_calls else 0
            report.append(
                f"**CALL Options:** {len(micro_calls)} trades | {mcall_wins}W-{len(micro_calls)-mcall_wins}L | {mcall_wr:.1f}% WR | ${mcall_pnl:,.0f} total (${mcall_avg_pnl:,.0f} avg)\n\n"
            )

        if micro_puts:
            mput_wins = sum(1 for m in micro_puts if m["is_win"])
            mput_wr = (mput_wins / len(micro_puts) * 100) if micro_puts else 0
            mput_pnl = sum(m["pnl"] for m in micro_puts)
            mput_avg_pnl = mput_pnl / len(micro_puts) if micro_puts else 0
            report.append(
                f"**PUT Options:** {len(micro_puts)} trades | {mput_wins}W-{len(micro_puts)-mput_wins}L | {mput_wr:.1f}% WR | ${mput_pnl:,.0f} total (${mput_avg_pnl:,.0f} avg)\n\n"
            )

    # === OVERALL R:R SUMMARY ===
    report.append("## 3. Overall R:R Summary\n\n")

    report.append(
        "| Strategy | Configured R:R | Observed Avg Win | Observed Avg Loss | Actual R:R | Breakeven WR | Actual WR | EV per trade |\n"
    )
    report.append(
        "|----------|----------------|------------------|-------------------|------------|--------------|-----------|-------------|\n"
    )

    # VASS
    if total_vass > 0:
        vass_wins_list = [s for s in spreads if s["is_win"]]
        vass_losses_list = [s for s in spreads if not s["is_win"]]
        vass_avg_win = (
            sum(s["pnl"] for s in vass_wins_list) / len(vass_wins_list) if vass_wins_list else 0
        )
        vass_avg_loss = (
            sum(s["pnl"] for s in vass_losses_list) / len(vass_losses_list)
            if vass_losses_list
            else 0
        )
        vass_actual_rr = abs(vass_avg_win / vass_avg_loss) if vass_avg_loss != 0 else 0
        vass_be_wr = (1 / (1 + vass_actual_rr) * 100) if vass_actual_rr > 0 else 50
        vass_ev = (vass_wr / 100 * vass_avg_win) + ((100 - vass_wr) / 100 * vass_avg_loss)

        report.append(
            f"| VASS Spreads | 1.33:1 (40%/30%) | ${vass_avg_win:,.0f} | ${vass_avg_loss:,.0f} | {vass_actual_rr:.2f}:1 | {vass_be_wr:.1f}% | {vass_wr:.1f}% | ${vass_ev:,.0f} |\n"
        )

    # MICRO
    if total_micro > 0 and "ITM_MOMENTUM" in strategies:
        itm_trades = strategies["ITM_MOMENTUM"]
        itm_wins_list = [t for t in itm_trades if t["is_win"]]
        itm_losses_list = [t for t in itm_trades if not t["is_win"]]
        itm_avg_win = (
            sum(t["pnl"] for t in itm_wins_list) / len(itm_wins_list) if itm_wins_list else 0
        )
        itm_avg_loss = (
            sum(t["pnl"] for t in itm_losses_list) / len(itm_losses_list) if itm_losses_list else 0
        )
        itm_actual_rr = abs(itm_avg_win / itm_avg_loss) if itm_avg_loss != 0 else 0
        itm_wr = (len(itm_wins_list) / len(itm_trades) * 100) if itm_trades else 0
        itm_be_wr = (1 / (1 + itm_actual_rr) * 100) if itm_actual_rr > 0 else 50
        itm_ev = (itm_wr / 100 * itm_avg_win) + ((100 - itm_wr) / 100 * itm_avg_loss)

        report.append(
            f"| MICRO ITM_MOM | 1.8:1 (45%/25%) | ${itm_avg_win:,.0f} | ${itm_avg_loss:,.0f} | {itm_actual_rr:.2f}:1 | {itm_be_wr:.1f}% | {itm_wr:.1f}% | ${itm_ev:,.0f} |\n"
        )

    if total_micro > 0 and "DEBIT_FADE" in strategies:
        fade_trades = strategies["DEBIT_FADE"]
        fade_wins_list = [t for t in fade_trades if t["is_win"]]
        fade_losses_list = [t for t in fade_trades if not t["is_win"]]
        fade_avg_win = (
            sum(t["pnl"] for t in fade_wins_list) / len(fade_wins_list) if fade_wins_list else 0
        )
        fade_avg_loss = (
            sum(t["pnl"] for t in fade_losses_list) / len(fade_losses_list)
            if fade_losses_list
            else 0
        )
        fade_actual_rr = abs(fade_avg_win / fade_avg_loss) if fade_avg_loss != 0 else 0
        fade_wr = (len(fade_wins_list) / len(fade_trades) * 100) if fade_trades else 0
        fade_be_wr = (1 / (1 + fade_actual_rr) * 100) if fade_actual_rr > 0 else 50
        fade_ev = (fade_wr / 100 * fade_avg_win) + ((100 - fade_wr) / 100 * fade_avg_loss)

        report.append(
            f"| MICRO FADE | 1.6:1 (40%/25%) | ${fade_avg_win:,.0f} | ${fade_avg_loss:,.0f} | {fade_actual_rr:.2f}:1 | {fade_be_wr:.1f}% | {fade_wr:.1f}% | ${fade_ev:,.0f} |\n"
        )

    report.append("\n")

    # === KEY QUESTIONS ===
    report.append("## 4. Key Questions Answered\n\n")

    if total_vass > 0:
        # Q1: Stop clustering
        report.append("### Q1: Are VASS exits clustering at the configured 30% stop level?\n\n")
        stop_area = [s for s in spreads if -30 <= s["pnl_pct_debit"] < -20]
        beyond_stop = [s for s in spreads if s["pnl_pct_debit"] < -30]
        report.append(
            f"- Trades exiting in stop area (-30% to -20%): **{len(stop_area)} ({len(stop_area)/len(spreads)*100:.1f}%)**\n"
        )
        report.append(
            f"- Trades beyond stop (<-30%): **{len(beyond_stop)} ({len(beyond_stop)/len(spreads)*100:.1f}%)**\n"
        )
        report.append(
            f"- **Finding:** {len(stop_area)} trades hit the stop area, {len(beyond_stop)} trades exceeded the 30% stop level.\n\n"
        )

        # Q2: Target capture
        report.append("### Q2: Are VASS exits capturing the configured 40% profit target?\n\n")
        big_wins = [s for s in spreads if s["pnl_pct_debit"] >= 40]
        mod_wins = [s for s in spreads if 20 <= s["pnl_pct_debit"] < 40]
        small_wins = [s for s in spreads if 0 < s["pnl_pct_debit"] < 20]
        report.append(
            f"- Trades reaching +40% target: **{len(big_wins)} ({len(big_wins)/len(spreads)*100:.1f}%)**\n"
        )
        report.append(
            f"- Trades exiting +20-40% (partial capture): **{len(mod_wins)} ({len(mod_wins)/len(spreads)*100:.1f}%)**\n"
        )
        report.append(
            f"- Trades exiting 0-20% (early exits): **{len(small_wins)} ({len(small_wins)/len(spreads)*100:.1f}%)**\n"
        )
        report.append(
            f"- **Finding:** {len(big_wins)} trades hit the 40% target, {len(mod_wins)} partial, {len(small_wins)} early.\n\n"
        )

        # Q3: R:R comparison
        report.append("### Q3: What is the actual R:R ratio vs configured?\n\n")
        report.append(f"- **VASS Configured:** 1.33:1 (40% target / 30% stop)\n")
        report.append(
            f"- **VASS Observed:** {vass_actual_rr:.2f}:1 (${vass_avg_win:,.0f} avg win / ${abs(vass_avg_loss):,.0f} avg loss)\n"
        )
        if total_micro > 0 and "ITM_MOMENTUM" in strategies:
            report.append(f"- **MICRO ITM Configured:** 1.8:1 (45% target / 25% stop)\n")
            report.append(f"- **MICRO ITM Observed:** {itm_actual_rr:.2f}:1\n")
        report.append("\n")

        # Q4: Debit/width impact
        report.append("### Q4: Do lower debit/width ratios produce better outcomes?\n\n")
        for bucket_name in ["0-30%", "30-40%", "40-50%", "50-55%", ">55%"]:
            if bucket_name in buckets:
                b_spreads = buckets[bucket_name]
                b_wr = (
                    (sum(1 for s in b_spreads if s["is_win"]) / len(b_spreads) * 100)
                    if b_spreads
                    else 0
                )
                b_avg_pnl = sum(s["pnl"] for s in b_spreads) / len(b_spreads) if b_spreads else 0
                report.append(f"- **{bucket_name}:** {b_wr:.1f}% WR, ${b_avg_pnl:,.0f} avg P&L\n")
        report.append("\n")

    if total_micro > 0:
        # Q5: System bugs
        report.append("### Q5: What % of MICRO losses are system bugs vs strategy exits?\n\n")
        orphan_exits = [m for m in micro_trades if m["exit_type"] == "RECON_ORPHAN"]
        orphan_losses = [m for m in orphan_exits if not m["is_win"]]
        total_micro_losses = [m for m in micro_trades if not m["is_win"]]

        report.append(
            f"- **Orphan exits (bugs):** {len(orphan_exits)} ({len(orphan_exits)/len(micro_trades)*100:.1f}% of all MICRO)\n"
        )
        report.append(
            f"- **Orphan losses:** {len(orphan_losses)} (${sum(m['pnl'] for m in orphan_losses):,.0f})\n"
        )
        report.append(
            f"- **Total MICRO losses:** {len(total_micro_losses)} (${sum(m['pnl'] for m in total_micro_losses):,.0f})\n"
        )
        bug_pct = (len(orphan_losses) / len(total_micro_losses) * 100) if total_micro_losses else 0
        report.append(f"- **% of losses from bugs:** {bug_pct:.1f}%\n\n")

        # Q6: ITM R:R
        if "ITM_MOMENTUM" in strategies:
            report.append("### Q6: Is the ITM_MOMENTUM R:R of 1.8:1 being realized?\n\n")
            report.append(f"- **Configured R:R:** 1.8:1 (45% target / 25% stop)\n")
            report.append(f"- **Observed R:R:** {itm_actual_rr:.2f}:1\n")
            report.append(
                f"- **Assessment:** {'YES - target met' if itm_actual_rr >= 1.7 else 'NO - falling short'}\n\n"
            )

        # Q7: CALL vs PUT
        report.append("### Q7: How do CALL vs PUT trades compare on R:R metrics?\n\n")
        if total_vass > 0:
            report.append("**VASS:**\n")
            if calls and puts:
                call_avg_win = (
                    sum(s["pnl"] for s in calls if s["is_win"])
                    / sum(1 for s in calls if s["is_win"])
                    if any(s["is_win"] for s in calls)
                    else 0
                )
                call_avg_loss = (
                    sum(s["pnl"] for s in calls if not s["is_win"])
                    / sum(1 for s in calls if not s["is_win"])
                    if any(not s["is_win"] for s in calls)
                    else 0
                )
                call_rr = abs(call_avg_win / call_avg_loss) if call_avg_loss != 0 else 0

                put_avg_win = (
                    sum(s["pnl"] for s in puts if s["is_win"]) / sum(1 for s in puts if s["is_win"])
                    if any(s["is_win"] for s in puts)
                    else 0
                )
                put_avg_loss = (
                    sum(s["pnl"] for s in puts if not s["is_win"])
                    / sum(1 for s in puts if not s["is_win"])
                    if any(not s["is_win"] for s in puts)
                    else 0
                )
                put_rr = abs(put_avg_win / put_avg_loss) if put_avg_loss != 0 else 0

                report.append(f"- CALL R:R: {call_rr:.2f}:1 | {call_wr:.1f}% WR\n")
                report.append(f"- PUT R:R: {put_rr:.2f}:1 | {put_wr:.1f}% WR\n\n")
            elif calls:
                call_avg_win = (
                    sum(s["pnl"] for s in calls if s["is_win"])
                    / sum(1 for s in calls if s["is_win"])
                    if any(s["is_win"] for s in calls)
                    else 0
                )
                call_avg_loss = (
                    sum(s["pnl"] for s in calls if not s["is_win"])
                    / sum(1 for s in calls if not s["is_win"])
                    if any(not s["is_win"] for s in calls)
                    else 0
                )
                call_rr = abs(call_avg_win / call_avg_loss) if call_avg_loss != 0 else 0
                report.append(f"- CALL R:R: {call_rr:.2f}:1 | {call_wr:.1f}% WR\n")
                report.append(f"- No PUT spreads\n\n")

        report.append("**MICRO:**\n")
        if micro_calls and micro_puts:
            mcall_avg_win = (
                sum(m["pnl"] for m in micro_calls if m["is_win"])
                / sum(1 for m in micro_calls if m["is_win"])
                if any(m["is_win"] for m in micro_calls)
                else 0
            )
            mcall_avg_loss = (
                sum(m["pnl"] for m in micro_calls if not m["is_win"])
                / sum(1 for m in micro_calls if not m["is_win"])
                if any(not m["is_win"] for m in micro_calls)
                else 0
            )
            mcall_rr = abs(mcall_avg_win / mcall_avg_loss) if mcall_avg_loss != 0 else 0

            mput_avg_win = (
                sum(m["pnl"] for m in micro_puts if m["is_win"])
                / sum(1 for m in micro_puts if m["is_win"])
                if any(m["is_win"] for m in micro_puts)
                else 0
            )
            mput_avg_loss = (
                sum(m["pnl"] for m in micro_puts if not m["is_win"])
                / sum(1 for m in micro_puts if not m["is_win"])
                if any(not m["is_win"] for m in micro_puts)
                else 0
            )
            mput_rr = abs(mput_avg_win / mput_avg_loss) if mput_avg_loss != 0 else 0

            report.append(f"- CALL R:R: {mcall_rr:.2f}:1 | {mcall_wr:.1f}% WR\n")
            report.append(f"- PUT R:R: {mput_rr:.2f}:1 | {mput_wr:.1f}% WR\n\n")
        elif micro_calls:
            mcall_avg_win = (
                sum(m["pnl"] for m in micro_calls if m["is_win"])
                / sum(1 for m in micro_calls if m["is_win"])
                if any(m["is_win"] for m in micro_calls)
                else 0
            )
            mcall_avg_loss = (
                sum(m["pnl"] for m in micro_calls if not m["is_win"])
                / sum(1 for m in micro_calls if not m["is_win"])
                if any(not m["is_win"] for m in micro_calls)
                else 0
            )
            mcall_rr = abs(mcall_avg_win / mcall_avg_loss) if mcall_avg_loss != 0 else 0
            report.append(f"- CALL R:R: {mcall_rr:.2f}:1 | {mcall_wr:.1f}% WR\n")
            report.append(f"- No PUT options\n\n")
        elif micro_puts:
            mput_avg_win = (
                sum(m["pnl"] for m in micro_puts if m["is_win"])
                / sum(1 for m in micro_puts if m["is_win"])
                if any(m["is_win"] for m in micro_puts)
                else 0
            )
            mput_avg_loss = (
                sum(m["pnl"] for m in micro_puts if not m["is_win"])
                / sum(1 for m in micro_puts if not m["is_win"])
                if any(not m["is_win"] for m in micro_puts)
                else 0
            )
            mput_rr = abs(mput_avg_win / mput_avg_loss) if mput_avg_loss != 0 else 0
            report.append(f"- No CALL options\n")
            report.append(f"- PUT R:R: {mput_rr:.2f}:1 | {mput_wr:.1f}% WR\n\n")

    # === TRADE DETAILS ===
    report.append("## 5. Detailed Trade Lists\n\n")

    if total_vass > 0:
        report.append("### VASS Spreads (All Trades)\n\n")
        report.append(
            "| Entry | Exit | Days | Long K | Short K | Width | Debit | D/W% | P&L | P&L%Debit | Type |\n"
        )
        report.append(
            "|-------|------|------|--------|---------|-------|-------|------|-----|-----------|------|\n"
        )

        for s in spreads:
            entry_str = s["entry_time"].strftime("%m/%d")
            exit_str = s["exit_time"].strftime("%m/%d")
            report.append(
                f"| {entry_str} | {exit_str} | {s['hold_days']:.1f} | {s['long_strike']:.0f} | {s['short_strike']:.0f} | ${s['width']:.0f} | ${s['net_debit']:.2f} | {s['debit_width_ratio']*100:.0f}% | ${s['pnl']:,.0f} | {s['pnl_pct_debit']:+.0f}% | {s['option_type']} |\n"
            )

        report.append("\n")

    if total_micro > 0:
        report.append("### MICRO Trades (All Trades)\n\n")
        report.append(
            "| Entry | Exit | Hours | Strategy | Entry | Exit | P&L | P&L% | Exit Type | Type |\n"
        )
        report.append(
            "|-------|------|-------|----------|-------|------|-----|------|-----------|------|\n"
        )

        for m in micro_trades:
            entry_str = m["entry_time"].strftime("%m/%d %H:%M")
            exit_str = m["exit_time"].strftime("%m/%d %H:%M")
            report.append(
                f"| {entry_str} | {exit_str} | {m['hold_hours']:.1f} | {m['strategy']} | ${m['entry_price']:.2f} | ${m['exit_price']:.2f} | ${m['pnl']:,.0f} | {m['pnl_pct']:+.0f}% | {m['exit_type']} | {m['option_type']} |\n"
            )

        report.append("\n")

    # === UNCLASSIFIED ===
    if unclassified:
        report.append("## 6. Unclassified Trades\n\n")
        report.append(
            f"The following {len(unclassified)} trades could not be classified as VASS spreads or MICRO singles:\n\n"
        )
        report.append("| Index | Entry Time | Symbol | Direction | P&L |\n")
        report.append("|-------|------------|--------|-----------|-----|\n")
        for idx, trade in unclassified[:20]:  # Show first 20
            report.append(
                f"| {idx} | {trade['Entry Time']} | {trade['Symbols'][:25]} | {trade['Direction']} | ${float(trade['P&L']):,.0f} |\n"
            )
        report.append("\n")

    # === CONCLUSION ===
    report.append("## 7. Conclusion\n\n")
    report.append(
        f"This analysis examined {total_vass} VASS spread trades and {total_micro} MICRO intraday trades "
    )
    report.append(f"from the V10 Apr-Jun 2023 backtest period.\n\n")

    if total_vass > 0 or total_micro > 0:
        report.append("**Key Findings:**\n")
        if total_vass > 0:
            report.append(f"1. VASS achieved {vass_actual_rr:.2f}:1 R:R (configured: 1.33:1)\n")
            report.append(
                f"2. {len(big_wins)} VASS trades ({len(big_wins)/len(spreads)*100:.0f}%) reached the 40% profit target\n"
            )
            report.append(
                f"3. {len(stop_area) + len(beyond_stop)} VASS trades ({(len(stop_area) + len(beyond_stop))/len(spreads)*100:.0f}%) hit or exceeded stops\n"
            )
        if total_micro > 0:
            report.append(
                f"4. MICRO orphan exits accounted for {bug_pct:.0f}% of MICRO losses (system bugs)\n"
            )
            if "ITM_MOMENTUM" in strategies:
                report.append(
                    f"5. ITM_MOMENTUM achieved {itm_actual_rr:.2f}:1 R:R (configured: 1.8:1)\n"
                )

    report.append("\n**Data Quality:** All metrics derived from trades.csv and orders.csv. ")
    report.append("Exit reasons inferred from order tags and timing patterns.\n")

    return "\n".join(report)


def main():
    trades_file = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10/V10_0_AprJun2023_v2_trades.csv"
    orders_file = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10/V10_0_AprJun2023_v2_orders.csv"
    output_file = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10/V10_0_AprJun2023_RR_Validation_Report.md"

    print("Loading trades and orders...")
    trades = load_trades(trades_file)
    orders = load_orders(orders_file)

    print(f"Loaded {len(trades)} trades and {len(orders)} orders")

    print("Classifying trades...")
    spreads, micro_trades, unclassified = find_all_spreads_and_micro(trades, orders)
    print(
        f"Found {len(spreads)} VASS spreads, {len(micro_trades)} MICRO trades, {len(unclassified)} unclassified"
    )

    print("Generating report...")
    report = generate_report(spreads, micro_trades, unclassified)

    print(f"Writing report to {output_file}")
    with open(output_file, "w") as f:
        f.write(report)

    print("✓ Report complete!")
    print(f"\nSummary:")
    print(f"  - VASS spreads: {len(spreads)}")
    print(f"  - MICRO trades: {len(micro_trades)}")
    print(f"  - Unclassified: {len(unclassified)}")
    print(f"  - Total classified: {len(spreads)*2 + len(micro_trades)} / {len(trades)} rows")


if __name__ == "__main__":
    main()
