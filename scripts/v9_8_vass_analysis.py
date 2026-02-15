#!/usr/bin/env python3
"""
V9.8 VASS Spread Performance Analysis
Analyzes VASS (swing) spread trades from V9.8 Full Year 2017 backtest.
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# File paths
TRADES_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear_trades.csv"
LOGS_TXT = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear.txt"
ORDERS_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear_orders.csv"


def parse_option_symbol(symbol):
    """Parse QQQ option symbol to extract details."""
    # Format: QQQ   YYMMDDCPXXXXXXXX
    # Example: QQQ   170120C00119500
    symbol = symbol.strip()
    if "QQQ" not in symbol:
        return None

    try:
        parts = symbol.split()
        if len(parts) < 2:
            return None

        option_code = parts[1]
        expiry_str = option_code[:6]
        option_type = option_code[6]  # C or P
        strike_str = option_code[7:]

        # Parse expiry
        year = int("20" + expiry_str[:2])
        month = int(expiry_str[2:4])
        day = int(expiry_str[4:6])
        expiry = datetime(year, month, day)

        # Parse strike
        strike = float(strike_str) / 1000

        return {"expiry": expiry, "type": option_type, "strike": strike}
    except:
        return None


def identify_spread_type(long_symbol, short_symbol, long_direction):
    """Identify spread type from leg symbols and direction."""
    long_opt = parse_option_symbol(long_symbol)
    short_opt = parse_option_symbol(short_symbol)

    if not long_opt or not short_opt:
        return "UNKNOWN"

    # Check if same expiry
    if long_opt["expiry"] != short_opt["expiry"]:
        return "CALENDAR"

    # Both legs are calls
    if long_opt["type"] == "C" and short_opt["type"] == "C":
        if long_direction == "Buy":
            # Long lower strike, short higher strike = BULL_CALL
            if long_opt["strike"] < short_opt["strike"]:
                return "BULL_CALL_DEBIT"
            else:
                return "BEAR_CALL_CREDIT"
        else:
            # Selling the spread
            if long_opt["strike"] < short_opt["strike"]:
                return "BEAR_CALL_CREDIT"
            else:
                return "BULL_CALL_DEBIT"

    # Both legs are puts
    elif long_opt["type"] == "P" and short_opt["type"] == "P":
        if long_direction == "Buy":
            # Long higher strike, short lower strike = BEAR_PUT
            if long_opt["strike"] > short_opt["strike"]:
                return "BEAR_PUT_DEBIT"
            else:
                return "BULL_PUT_CREDIT"
        else:
            # Selling the spread
            if long_opt["strike"] > short_opt["strike"]:
                return "BULL_PUT_CREDIT"
            else:
                return "BEAR_PUT_DEBIT"

    return "MIXED"


def match_spread_pairs(df):
    """Match option legs into spreads."""
    spreads = []

    # Group by entry time
    for entry_time, group in df.groupby("Entry Time"):
        if len(group) == 2:
            # Should be a spread
            legs = group.sort_values("Direction")  # Buy first, then Sell

            if len(legs) == 2:
                buy_leg = legs.iloc[0] if legs.iloc[0]["Direction"] == "Buy" else legs.iloc[1]
                sell_leg = legs.iloc[1] if legs.iloc[1]["Direction"] == "Sell" else legs.iloc[0]

                spread_type = identify_spread_type(
                    buy_leg["Symbols"], sell_leg["Symbols"], buy_leg["Direction"]
                )

                # Calculate spread P&L
                spread_pnl = buy_leg["P&L"] + sell_leg["P&L"]
                spread_fees = buy_leg["Fees"] + sell_leg["Fees"]

                # Calculate hold time
                entry_dt = pd.to_datetime(entry_time).tz_localize(None)
                exit_dt = pd.to_datetime(buy_leg["Exit Time"]).tz_localize(None)
                hold_hours = (exit_dt - entry_dt).total_seconds() / 3600
                hold_days = hold_hours / 24

                # Calculate spread width
                buy_opt = parse_option_symbol(buy_leg["Symbols"])
                sell_opt = parse_option_symbol(sell_leg["Symbols"])

                if buy_opt and sell_opt:
                    spread_width = abs(buy_opt["strike"] - sell_opt["strike"])

                    # Calculate debit
                    net_debit = buy_leg["Entry Price"] - sell_leg["Entry Price"]
                    debit_to_width = net_debit / spread_width if spread_width > 0 else 0

                    # DTE
                    dte = (buy_opt["expiry"] - entry_dt).days
                else:
                    spread_width = 0
                    net_debit = 0
                    debit_to_width = 0
                    dte = 0

                spreads.append(
                    {
                        "entry_time": entry_time,
                        "exit_time": buy_leg["Exit Time"],
                        "spread_type": spread_type,
                        "long_symbol": buy_leg["Symbols"],
                        "short_symbol": sell_leg["Symbols"],
                        "quantity": buy_leg["Quantity"],
                        "long_entry": buy_leg["Entry Price"],
                        "long_exit": buy_leg["Exit Price"],
                        "short_entry": sell_leg["Entry Price"],
                        "short_exit": sell_leg["Exit Price"],
                        "net_debit": net_debit,
                        "spread_width": spread_width,
                        "debit_to_width": debit_to_width,
                        "pnl": spread_pnl,
                        "fees": spread_fees,
                        "net_pnl": spread_pnl - spread_fees,
                        "is_win": spread_pnl > 0,
                        "hold_hours": hold_hours,
                        "hold_days": hold_days,
                        "dte": dte,
                    }
                )

    return pd.DataFrame(spreads)


def parse_spread_exits_from_logs():
    """Parse spread exit reasons from logs."""
    exit_reasons = {}

    with open(LOGS_TXT, "r") as f:
        for line in f:
            # Look for spread exit logs
            # Example: "SPREAD: EXIT | Reason=PROFIT_TARGET | P&L=+$XXX"
            if "SPREAD: EXIT" in line or "SPREAD_EXIT:" in line or "VASS_EXIT:" in line:
                # Try to extract reason
                reason_match = re.search(r"Reason=(\w+)", line)
                if reason_match:
                    reason = reason_match.group(1)
                    # Try to extract timestamp
                    ts_match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    if ts_match:
                        timestamp = ts_match.group(1)
                        exit_reasons[timestamp] = reason

    return exit_reasons


def parse_vass_rejections_from_logs():
    """Count VASS rejection reasons from logs."""
    rejections = defaultdict(int)
    bear_put_blocks = 0
    total_vass_rejections = 0

    with open(LOGS_TXT, "r") as f:
        for line in f:
            if "VASS_REJECTION:" in line:
                total_vass_rejections += 1

                # Check for direction
                if "Direction=PUT" in line:
                    # Check if it's BEAR_PUT related
                    if "BEAR" in line.upper() or "PUT" in line:
                        # Check for regime block
                        if "Regime" in line:
                            regime_match = re.search(r"Regime=(\d+)", line)
                            if regime_match:
                                regime = int(regime_match.group(1))
                                if regime >= 70:
                                    bear_put_blocks += 1

                # Extract reason
                reason_match = re.search(r"ReasonCode=([^|]+)", line)
                if reason_match:
                    reason = reason_match.group(1).strip()
                    rejections[reason] += 1
                else:
                    # Try alternative format
                    reason_match = re.search(r"Reason=([^|]+)", line)
                    if reason_match:
                        reason = reason_match.group(1).strip()
                        if "Reason=" in reason:
                            reason = reason.split("Reason=")[0].strip()
                        rejections[reason] += 1

    return rejections, bear_put_blocks, total_vass_rejections


def parse_spread_min_hold_blocks():
    """Count how many times spreads were blocked from exiting by min hold."""
    count = 0

    with open(LOGS_TXT, "r") as f:
        for line in f:
            if "SPREAD_EXIT_GUARD_HOLD:" in line:
                count += 1

    return count


def main():
    print("=" * 80)
    print("V9.8 VASS SPREAD PERFORMANCE ANALYSIS")
    print("Full Year 2017 Backtest")
    print("=" * 80)
    print()

    # Load trades
    df = pd.read_csv(TRADES_CSV)

    # Filter for options only
    df_options = df[df["Symbols"].str.contains("QQQ", na=False)].copy()

    print(f"Total trades in CSV: {len(df)}")
    print(f"Options trades: {len(df_options)}")
    print()

    # Match spreads
    spreads_df = match_spread_pairs(df_options)

    print(f"Identified spreads: {len(spreads_df)}")
    print()

    # Filter for VASS spreads (exclude intraday/micro)
    # VASS spreads are the 4 types: BULL_CALL_DEBIT, BEAR_PUT_DEBIT, BULL_PUT_CREDIT, BEAR_CALL_CREDIT
    vass_types = ["BULL_CALL_DEBIT", "BEAR_PUT_DEBIT", "BULL_PUT_CREDIT", "BEAR_CALL_CREDIT"]
    vass_spreads = spreads_df[spreads_df["spread_type"].isin(vass_types)].copy()

    print("=" * 80)
    print("1. VASS SPREAD COUNT BY TYPE")
    print("=" * 80)

    for spread_type in vass_types:
        count = len(vass_spreads[vass_spreads["spread_type"] == spread_type])
        print(f"{spread_type:25s} {count:4d} trades")

    print(f"{'TOTAL VASS SPREADS':25s} {len(vass_spreads):4d} trades")
    print()

    # 2. P&L Breakdown by Spread Type
    print("=" * 80)
    print("2. P&L BREAKDOWN BY SPREAD TYPE")
    print("=" * 80)
    print()

    summary_data = []

    for spread_type in vass_types:
        spread_subset = vass_spreads[vass_spreads["spread_type"] == spread_type]

        if len(spread_subset) == 0:
            continue

        wins = spread_subset[spread_subset["is_win"]]
        losses = spread_subset[~spread_subset["is_win"]]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / len(spread_subset) * 100 if len(spread_subset) > 0 else 0

        avg_win = wins["net_pnl"].mean() if len(wins) > 0 else 0
        avg_loss = losses["net_pnl"].mean() if len(losses) > 0 else 0

        total_pnl = spread_subset["net_pnl"].sum()

        summary_data.append(
            {
                "Spread Type": spread_type,
                "Count": len(spread_subset),
                "Wins": win_count,
                "Losses": loss_count,
                "Win Rate": f"{win_rate:.1f}%",
                "Avg Win": f"${avg_win:.0f}",
                "Avg Loss": f"${avg_loss:.0f}",
                "Net P&L": f"${total_pnl:.0f}",
            }
        )

    # Add total row
    total_wins = len(vass_spreads[vass_spreads["is_win"]])
    total_losses = len(vass_spreads[~vass_spreads["is_win"]])
    total_win_rate = total_wins / len(vass_spreads) * 100 if len(vass_spreads) > 0 else 0
    total_avg_win = vass_spreads[vass_spreads["is_win"]]["net_pnl"].mean() if total_wins > 0 else 0
    total_avg_loss = (
        vass_spreads[~vass_spreads["is_win"]]["net_pnl"].mean() if total_losses > 0 else 0
    )
    total_net_pnl = vass_spreads["net_pnl"].sum()

    summary_data.append(
        {
            "Spread Type": "TOTAL",
            "Count": len(vass_spreads),
            "Wins": total_wins,
            "Losses": total_losses,
            "Win Rate": f"{total_win_rate:.1f}%",
            "Avg Win": f"${total_avg_win:.0f}",
            "Avg Loss": f"${total_avg_loss:.0f}",
            "Net P&L": f"${total_net_pnl:.0f}",
        }
    )

    summary_df = pd.DataFrame(summary_data)
    print(summary_df.to_string(index=False))
    print()

    # 3. BEAR_PUT_DEBIT Analysis - Regime Gate
    print("=" * 80)
    print("3. BEAR_PUT_DEBIT ANALYSIS - RISK_ON GATE (Regime >= 70)")
    print("=" * 80)
    print()

    bear_put_trades = vass_spreads[vass_spreads["spread_type"] == "BEAR_PUT_DEBIT"]
    print(f"BEAR_PUT_DEBIT Entries: {len(bear_put_trades)}")

    # Parse rejections from logs
    rejections, bear_put_blocks, total_rejections = parse_vass_rejections_from_logs()

    print(f"Total VASS Rejections: {total_rejections}")
    print(f"BEAR_PUT blocked by RISK_ON gate (regime >= 70): {bear_put_blocks}")
    print()

    print("Top Rejection Reasons:")
    sorted_rejections = sorted(rejections.items(), key=lambda x: x[1], reverse=True)[:10]
    for reason, count in sorted_rejections:
        print(f"  {reason:50s} {count:4d}x")
    print()

    # 4. Hold Time Analysis
    print("=" * 80)
    print("4. HOLD TIME ANALYSIS (SPREAD_MIN_HOLD_MINUTES = 10080 = 7 days)")
    print("=" * 80)
    print()

    avg_hold_days = vass_spreads["hold_days"].mean()
    median_hold_days = vass_spreads["hold_days"].median()
    min_hold_days = vass_spreads["hold_days"].min()
    max_hold_days = vass_spreads["hold_days"].max()

    print(f"Average Hold Time:    {avg_hold_days:.1f} days")
    print(f"Median Hold Time:     {median_hold_days:.1f} days")
    print(f"Min Hold Time:        {min_hold_days:.1f} days")
    print(f"Max Hold Time:        {max_hold_days:.1f} days")
    print()

    # Count spreads held exactly 7+ days
    held_7plus = len(vass_spreads[vass_spreads["hold_days"] >= 7])
    held_less_7 = len(vass_spreads[vass_spreads["hold_days"] < 7])

    print(f"Spreads held >= 7 days:  {held_7plus} ({held_7plus/len(vass_spreads)*100:.1f}%)")
    print(f"Spreads held < 7 days:   {held_less_7} ({held_less_7/len(vass_spreads)*100:.1f}%)")
    print()

    # Count min hold guard messages
    min_hold_blocks = parse_spread_min_hold_blocks()
    print(f"Min hold guard messages in logs: {min_hold_blocks:,}")
    print("(Each spread generates ~1 message per minute during first 7 days)")
    print()

    # 5. Spread Width Distribution
    print("=" * 80)
    print("5. SPREAD WIDTH DISTRIBUTION (Target: $4-7)")
    print("=" * 80)
    print()

    width_bins = [0, 3, 4, 5, 6, 7, 8, 100]
    width_labels = ["< $3", "$3-4", "$4-5", "$5-6", "$6-7", "$7-8", "> $8"]

    vass_spreads["width_bin"] = pd.cut(
        vass_spreads["spread_width"], bins=width_bins, labels=width_labels
    )

    width_dist = vass_spreads.groupby("width_bin", observed=True).size()
    print(width_dist.to_string())
    print()

    avg_width = vass_spreads["spread_width"].mean()
    median_width = vass_spreads["spread_width"].median()

    print(f"Average Spread Width: ${avg_width:.2f}")
    print(f"Median Spread Width:  ${median_width:.2f}")
    print()

    # Spreads in $4-7 range
    optimal_width = len(
        vass_spreads[(vass_spreads["spread_width"] >= 4) & (vass_spreads["spread_width"] <= 7)]
    )
    print(f"Spreads in $4-7 range: {optimal_width} ({optimal_width/len(vass_spreads)*100:.1f}%)")
    print()

    # 6. Exit Reasons
    print("=" * 80)
    print("6. EXIT REASONS (from logs)")
    print("=" * 80)
    print()

    # This requires parsing logs more thoroughly
    # For now, we'll estimate based on hold times and P&L

    # Estimate profit target exits (50%+ of max profit)
    # Estimate stop loss exits (losses)
    # Estimate time exits (DTE <= 1)

    print("(Exit reason analysis requires more detailed log parsing)")
    print("Estimated based on trade characteristics:")
    print()

    profit_exits = len(vass_spreads[vass_spreads["is_win"]])
    stop_exits = len(vass_spreads[~vass_spreads["is_win"]])

    print(f"  Profitable exits (likely profit target): {profit_exits}")
    print(f"  Loss exits (likely stop loss):          {stop_exits}")
    print()

    # 7. Monthly P&L Breakdown
    print("=" * 80)
    print("7. MONTHLY P&L BREAKDOWN")
    print("=" * 80)
    print()

    vass_spreads["month"] = pd.to_datetime(vass_spreads["entry_time"]).dt.to_period("M")

    monthly_pnl = (
        vass_spreads.groupby("month")
        .agg({"net_pnl": "sum", "spread_type": "count"})
        .rename(columns={"spread_type": "trades"})
    )

    print(monthly_pnl.to_string())
    print()

    # 8. Debit-to-Width Ratio Analysis
    print("=" * 80)
    print("8. DEBIT-TO-WIDTH RATIO (Gate: <= 55%)")
    print("=" * 80)
    print()

    avg_debit_width = vass_spreads["debit_to_width"].mean() * 100
    median_debit_width = vass_spreads["debit_to_width"].median() * 100

    print(f"Average Debit/Width: {avg_debit_width:.1f}%")
    print(f"Median Debit/Width:  {median_debit_width:.1f}%")
    print()

    # Count spreads within 55% gate
    within_gate = len(vass_spreads[vass_spreads["debit_to_width"] <= 0.55])
    print(f"Spreads within 55% gate: {within_gate} ({within_gate/len(vass_spreads)*100:.1f}%)")
    print()

    # Distribution
    debit_bins = [0, 0.30, 0.40, 0.50, 0.55, 0.60, 1.0]
    debit_labels = ["< 30%", "30-40%", "40-50%", "50-55%", "55-60%", "> 60%"]

    vass_spreads["debit_bin"] = pd.cut(
        vass_spreads["debit_to_width"], bins=debit_bins, labels=debit_labels
    )
    debit_dist = vass_spreads.groupby("debit_bin", observed=True).size()
    print(debit_dist.to_string())
    print()

    # 9. Regime Score at Entry
    print("=" * 80)
    print("9. REGIME SCORE AT ENTRY (requires log parsing)")
    print("=" * 80)
    print()
    print("(This analysis requires parsing SPREAD: ENTRY_SIGNAL logs)")
    print()

    # 10. Comparison to V9.6 and V9.7
    print("=" * 80)
    print("10. COMPARISON TO PREVIOUS VERSIONS")
    print("=" * 80)
    print()

    print("V9.6 VASS Performance: +$5,058")
    print("V9.7 VASS Performance: (data needed)")
    print(f"V9.8 VASS Performance: ${total_net_pnl:.0f}")
    print()

    delta_v96 = total_net_pnl - 5058
    print(f"Delta vs V9.6: ${delta_v96:+.0f}")
    print()

    # Save detailed spread data
    output_csv = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_VASS_spreads_detail.csv"
    vass_spreads.to_csv(output_csv, index=False)
    print(f"Detailed spread data saved to: {output_csv}")
    print()

    print("=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
