#!/usr/bin/env python3
"""
Analyze V9.8 Full Year 2017 MICRO intraday options performance.
"""

import re
from datetime import datetime

import numpy as np
import pandas as pd

# File paths
TRADES_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear_trades.csv"
ORDERS_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear_orders.csv"
LOGS_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear.txt"

# Load trades
print("Loading trades...")
trades_df = pd.read_csv(TRADES_CSV)
trades_df["Entry Time"] = pd.to_datetime(trades_df["Entry Time"])
trades_df["Exit Time"] = pd.to_datetime(trades_df["Exit Time"])

print(f"Total trades: {len(trades_df)}")
print(f"Columns: {trades_df.columns.tolist()}")

# Load orders to get tags
print("\nLoading orders...")
orders_df = pd.read_csv(ORDERS_CSV)
print(f"Total orders: {len(orders_df)}")
print(f"Orders columns: {orders_df.columns.tolist()}")


# Parse symbol to extract DTE
def extract_dte_from_symbol(symbol):
    """Extract DTE from symbol at entry time."""
    # Symbol format: "QQQ   170120C00119500"
    # Date is positions 6-11: YYMMDD
    match = re.search(r"(\d{6})[CP]", symbol)
    if match:
        exp_date_str = match.group(1)
        try:
            # Parse as YYMMDD
            exp_year = 2000 + int(exp_date_str[0:2])
            exp_month = int(exp_date_str[2:4])
            exp_day = int(exp_date_str[4:6])
            return pd.Timestamp(year=exp_year, month=exp_month, day=exp_day)
        except:
            return None
    return None


def calculate_dte(entry_time, symbol):
    """Calculate DTE at entry."""
    exp_date = extract_dte_from_symbol(symbol)
    if exp_date and entry_time:
        dte = (exp_date - entry_time).days
        return max(0, dte)
    return None


# Calculate DTE for all trades
trades_df["Expiration"] = trades_df["Symbols"].apply(extract_dte_from_symbol)
trades_df["DTE"] = trades_df.apply(
    lambda row: calculate_dte(row["Entry Time"], row["Symbols"]), axis=1
)


# Parse option type (CALL/PUT)
def get_option_type(symbol):
    if "C" in symbol and "P" not in symbol:
        return "CALL"
    elif "P" in symbol:
        return "PUT"
    return "UNKNOWN"


trades_df["Option Type"] = trades_df["Symbols"].apply(get_option_type)

# Identify VASS spreads (paired entries at same time)
trades_df["Is Spread"] = False
trades_df["Spread Type"] = None

# Group by entry time to find spreads
for entry_time, group in trades_df.groupby("Entry Time"):
    if len(group) == 2:
        # Check if both legs expire same date
        if group["Expiration"].nunique() == 1:
            # Mark as spread
            trades_df.loc[group.index, "Is Spread"] = True
            # Determine spread type
            if all(group["Direction"] == "Buy"):
                # Debit spread - lower price = long, higher price = short (sold for less)
                # Actually need to check strikes
                pass

# Calculate duration
trades_df["Duration Days"] = (
    trades_df["Exit Time"] - trades_df["Entry Time"]
).dt.total_seconds() / 86400
trades_df["Duration Hours"] = (
    trades_df["Exit Time"] - trades_df["Entry Time"]
).dt.total_seconds() / 3600

# Identify MICRO trades (DTE 1-5, single leg, held < 2 days typically)
# MICRO = intraday single-leg options
micro_mask = (
    (trades_df["DTE"] >= 0)
    & (trades_df["DTE"] <= 5)
    & (~trades_df["Is Spread"])  # DTE 0-5 (some might be 0 DTE)
    & (  # Not part of spread
        trades_df["Duration Days"] < 3
    )  # Held less than 3 days (intraday style)
)

micro_trades = trades_df[micro_mask].copy()

print(f"\n{'='*80}")
print(f"MICRO INTRADAY TRADES ANALYSIS")
print(f"{'='*80}")
print(f"\nTotal MICRO trades identified: {len(micro_trades)}")

if len(micro_trades) == 0:
    print("\n⚠️ WARNING: NO MICRO TRADES FOUND!")
    print("\nLet's check what trades we have:")
    print(f"\nDTE distribution:")
    print(trades_df["DTE"].value_counts().sort_index())
    print(f"\nDuration distribution (in days):")
    print(trades_df["Duration Days"].describe())

# Merge with orders to get tags
# First, let's check orders structure
print("\n" + "=" * 80)
print("CHECKING ORDERS DATA")
print("=" * 80)
print(orders_df.head(20))

# Try to find MICRO patterns in logs
print("\n" + "=" * 80)
print("SEARCHING LOGS FOR MICRO PATTERNS")
print("=" * 80)

micro_log_patterns = [
    "MICRO:",
    "INTRADAY:",
    "ITM_MOMENTUM",
    "DEBIT_FADE",
    "DEBIT_MOMENTUM",
    "MICRO_REGIME",
]

for pattern in micro_log_patterns:
    count = 0
    with open(LOGS_FILE, "r") as f:
        for line in f:
            if pattern in line:
                count += 1
                if count <= 3:  # Print first 3 examples
                    print(f"[{pattern}] {line.strip()}")
    print(f"Total '{pattern}' occurrences: {count}\n")

print("\n" + "=" * 80)
print("ANALYZING ALL TRADES BY DTE AND DURATION")
print("=" * 80)

# Show DTE distribution
print("\nDTE Distribution:")
print(trades_df["DTE"].value_counts().sort_index().head(20))

# Show short duration trades
print("\nTrades with duration < 2 days:")
short_duration = trades_df[trades_df["Duration Days"] < 2]
print(f"Count: {len(short_duration)}")
print(f"DTE range: {short_duration['DTE'].min()} to {short_duration['DTE'].max()}")

# Calculate basic stats if we have micro trades
if len(micro_trades) > 0:
    print("\n" + "=" * 80)
    print("MICRO TRADES STATISTICS")
    print("=" * 80)

    # Win rate
    wins = micro_trades[micro_trades["IsWin"] == 1]
    losses = micro_trades[micro_trades["IsWin"] == 0]

    print(f"\nWin Rate:")
    print(f"  Wins: {len(wins)}")
    print(f"  Losses: {len(losses)}")
    print(f"  Win Rate: {len(wins)/len(micro_trades)*100:.1f}%")

    # P&L stats
    print(f"\nP&L Statistics:")
    print(f"  Total P&L: ${micro_trades['P&L'].sum():,.0f}")
    print(f"  Total Fees: ${micro_trades['Fees'].sum():,.0f}")
    print(f"  Net P&L: ${(micro_trades['P&L'].sum() - micro_trades['Fees'].sum()):,.0f}")
    print(f"  Avg Win: ${wins['P&L'].mean():,.0f}" if len(wins) > 0 else "  Avg Win: N/A")
    print(f"  Avg Loss: ${losses['P&L'].mean():,.0f}" if len(losses) > 0 else "  Avg Loss: N/A")

    # DTE distribution
    print(f"\nDTE Distribution:")
    print(micro_trades["DTE"].value_counts().sort_index())

    # Contract size
    print(f"\nContract Size:")
    print(f"  Avg: {micro_trades['Quantity'].mean():.0f}")
    print(f"  Min: {micro_trades['Quantity'].min():.0f}")
    print(f"  Max: {micro_trades['Quantity'].max():.0f}")

    # Option prices
    print(f"\nOption Entry Prices:")
    print(f"  Avg: ${micro_trades['Entry Price'].mean():.2f}")
    print(f"  Min: ${micro_trades['Entry Price'].min():.2f}")
    print(f"  Max: ${micro_trades['Entry Price'].max():.2f}")

    # Monthly breakdown
    micro_trades["Month"] = micro_trades["Entry Time"].dt.to_period("M")
    monthly_pnl = micro_trades.groupby("Month").agg({"P&L": ["sum", "count"], "IsWin": "sum"})
    monthly_pnl.columns = ["Total P&L", "Trade Count", "Wins"]
    monthly_pnl["Win Rate %"] = (monthly_pnl["Wins"] / monthly_pnl["Trade Count"] * 100).round(1)

    print(f"\n" + "=" * 80)
    print("MONTHLY BREAKDOWN")
    print("=" * 80)
    print(monthly_pnl)

print("\n" + "=" * 80)
print("SAMPLE TRADES (First 10)")
print("=" * 80)
sample_cols = [
    "Entry Time",
    "Symbols",
    "Exit Time",
    "DTE",
    "Entry Price",
    "Quantity",
    "P&L",
    "IsWin",
    "Duration Hours",
]
print(trades_df[sample_cols].head(10).to_string())

print("\n" + "=" * 80)
print("SHORT DURATION TRADES (< 1 day)")
print("=" * 80)
intraday = trades_df[trades_df["Duration Days"] < 1]
if len(intraday) > 0:
    print(f"Count: {len(intraday)}")
    print(intraday[sample_cols].head(20).to_string())
else:
    print("No trades with duration < 1 day")
