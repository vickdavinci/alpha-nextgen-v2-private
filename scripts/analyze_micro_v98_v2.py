#!/usr/bin/env python3
"""
Analyze V9.8 Full Year 2017 MICRO intraday options performance.
"""

import re
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

# File paths
TRADES_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear_trades.csv"
ORDERS_CSV = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear_orders.csv"
LOGS_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear.txt"

# Load orders (has tags)
print("Loading orders...")
orders_df = pd.read_csv(ORDERS_CSV)
orders_df["Time"] = pd.to_datetime(orders_df["Time"], utc=True)
print(f"Total orders: {len(orders_df)}")

# Filter MICRO orders
micro_orders = orders_df[orders_df["Tag"].str.contains("MICRO", na=False)].copy()
print(f"MICRO orders: {len(micro_orders)}")


# Extract strategy from tag
def extract_strategy(tag):
    if pd.isna(tag):
        return None
    if "ITM_MOMENTUM" in tag:
        return "ITM_MOMENTUM"
    elif "DEBIT_FADE" in tag:
        return "DEBIT_FADE"
    elif "DEBIT_MOMENTUM" in tag:
        return "DEBIT_MOMENTUM"
    elif "MICRO" in tag:
        return "MICRO_GENERIC"
    return None


micro_orders["Strategy"] = micro_orders["Tag"].apply(extract_strategy)

print("\nMICRO order strategies:")
print(micro_orders["Strategy"].value_counts())

# Load trades
print("\nLoading trades...")
trades_df = pd.read_csv(TRADES_CSV)
trades_df["Entry Time"] = pd.to_datetime(trades_df["Entry Time"], utc=True)
trades_df["Exit Time"] = pd.to_datetime(trades_df["Exit Time"], utc=True)


# Parse Order Ids
def parse_order_ids(id_str):
    """Parse order IDs from string like '\t1,10'"""
    if pd.isna(id_str):
        return []
    # Remove tabs and whitespace, split by comma
    ids = str(id_str).replace("\t", "").split(",")
    return [int(x.strip()) for x in ids if x.strip().isdigit()]


trades_df["Entry Order ID"] = trades_df["Order Ids"].apply(
    lambda x: parse_order_ids(x)[0] if parse_order_ids(x) else None
)
trades_df["Exit Order ID"] = trades_df["Order Ids"].apply(
    lambda x: parse_order_ids(x)[1] if len(parse_order_ids(x)) > 1 else None
)

# Create order ID to tag mapping
order_tag_map = {}
for idx, row in orders_df.iterrows():
    # Order IDs in orders CSV should match
    # We need to match by time + symbol
    pass


# Match trades to MICRO orders by symbol and entry time
def match_micro_trade(row):
    """Check if trade entry matches a MICRO order."""
    entry_time = row["Entry Time"]
    symbol = row["Symbols"].strip()

    # Find micro orders with same symbol and close entry time (within 1 minute)
    matches = micro_orders[
        (micro_orders["Symbol"].str.strip() == symbol)
        & (abs((micro_orders["Time"] - entry_time).dt.total_seconds()) < 60)
        & (micro_orders["Quantity"] > 0)  # Entry orders only
    ]

    if len(matches) > 0:
        return matches.iloc[0]["Strategy"]
    return None


print("\nMatching trades to MICRO orders...")
trades_df["MICRO Strategy"] = trades_df.apply(match_micro_trade, axis=1)

micro_trades = trades_df[trades_df["MICRO Strategy"].notna()].copy()

print(f"\n{'='*80}")
print(f"MICRO INTRADAY TRADES ANALYSIS - V9.8 FULL YEAR 2017")
print(f"{'='*80}")
print(f"\nTotal MICRO trades: {len(micro_trades)}")

if len(micro_trades) == 0:
    print("\n⚠️ WARNING: NO MICRO TRADES MATCHED!")
    print("\nDebug info:")
    print(f"Micro orders count: {len(micro_orders)}")
    print(micro_orders[["Time", "Symbol", "Quantity", "Tag"]].head(10))
    exit(1)

# Calculate metrics
micro_trades["Duration Hours"] = (
    micro_trades["Exit Time"] - micro_trades["Entry Time"]
).dt.total_seconds() / 3600
micro_trades["Duration Days"] = micro_trades["Duration Hours"] / 24


# Parse DTE
def extract_dte_from_symbol(symbol, entry_time):
    """Extract DTE from symbol at entry time."""
    match = re.search(r"(\d{6})[CP]", symbol)
    if match:
        exp_date_str = match.group(1)
        try:
            exp_year = 2000 + int(exp_date_str[0:2])
            exp_month = int(exp_date_str[2:4])
            exp_day = int(exp_date_str[4:6])
            exp_date = pd.Timestamp(year=exp_year, month=exp_month, day=exp_day, tz="UTC")
            dte = (exp_date - entry_time).days
            return max(0, dte)
        except:
            return None
    return None


micro_trades["DTE"] = micro_trades.apply(
    lambda row: extract_dte_from_symbol(row["Symbols"], row["Entry Time"]), axis=1
)


# Parse option type
def get_option_type(symbol):
    if "C" in symbol:
        for i, char in enumerate(symbol):
            if char == "C":
                # Check if next chars are digits (it's the strike)
                if i + 1 < len(symbol) and symbol[i + 1].isdigit():
                    return "CALL"
    if "P" in symbol:
        for i, char in enumerate(symbol):
            if char == "P":
                if i + 1 < len(symbol) and symbol[i + 1].isdigit():
                    return "PUT"
    return "UNKNOWN"


micro_trades["Option Type"] = micro_trades["Symbols"].apply(get_option_type)

# Overall stats
wins = micro_trades[micro_trades["IsWin"] == 1]
losses = micro_trades[micro_trades["IsWin"] == 0]

print(f"\n{'='*80}")
print("OVERALL MICRO PERFORMANCE")
print(f"{'='*80}")
print(f"Total Trades: {len(micro_trades)}")
print(f"Wins: {len(wins)} ({len(wins)/len(micro_trades)*100:.1f}%)")
print(f"Losses: {len(losses)} ({len(losses)/len(micro_trades)*100:.1f}%)")
print(f"\nP&L:")
print(f"  Gross P&L: ${micro_trades['P&L'].sum():,.0f}")
print(f"  Total Fees: ${micro_trades['Fees'].sum():,.0f}")
print(f"  Net P&L: ${(micro_trades['P&L'].sum() - micro_trades['Fees'].sum()):,.0f}")
print(f"\nPer Trade:")
print(f"  Avg Win: ${wins['P&L'].mean():,.0f}" if len(wins) > 0 else "  Avg Win: N/A")
print(f"  Avg Loss: ${losses['P&L'].mean():,.0f}" if len(losses) > 0 else "  Avg Loss: N/A")
print(f"  Avg P&L: ${micro_trades['P&L'].mean():,.0f}")
print(f"  Avg Fees: ${micro_trades['Fees'].mean():.0f}")

# Strategy breakdown
print(f"\n{'='*80}")
print("BREAKDOWN BY STRATEGY")
print(f"{'='*80}")

for strategy in micro_trades["MICRO Strategy"].unique():
    if pd.isna(strategy):
        continue

    strat_trades = micro_trades[micro_trades["MICRO Strategy"] == strategy]
    strat_wins = strat_trades[strat_trades["IsWin"] == 1]
    strat_losses = strat_trades[strat_trades["IsWin"] == 0]

    print(f"\n{strategy}:")
    print(f"  Count: {len(strat_trades)}")
    print(f"  Wins: {len(strat_wins)} ({len(strat_wins)/len(strat_trades)*100:.1f}%)")
    print(f"  Losses: {len(strat_losses)} ({len(strat_losses)/len(strat_trades)*100:.1f}%)")
    print(f"  Total P&L: ${strat_trades['P&L'].sum():,.0f}")
    print(f"  Total Fees: ${strat_trades['Fees'].sum():,.0f}")
    print(f"  Net P&L: ${(strat_trades['P&L'].sum() - strat_trades['Fees'].sum()):,.0f}")
    if len(strat_wins) > 0:
        print(f"  Avg Win: ${strat_wins['P&L'].mean():,.0f}")
    if len(strat_losses) > 0:
        print(f"  Avg Loss: ${strat_losses['P&L'].mean():,.0f}")

# DTE distribution
print(f"\n{'='*80}")
print("DTE DISTRIBUTION")
print(f"{'='*80}")
dte_counts = micro_trades["DTE"].value_counts().sort_index()
print(dte_counts)
print(f"\nDTE Stats:")
print(f"  Min: {micro_trades['DTE'].min()}")
print(f"  Max: {micro_trades['DTE'].max()}")
print(f"  Mean: {micro_trades['DTE'].mean():.1f}")
print(f"  Median: {micro_trades['DTE'].median()}")

# Contract size and prices
print(f"\n{'='*80}")
print("CONTRACT SIZE & PRICING")
print(f"{'='*80}")
print(f"Contracts per trade:")
print(f"  Average: {micro_trades['Quantity'].mean():.0f}")
print(f"  Min: {micro_trades['Quantity'].min()}")
print(f"  Max: {micro_trades['Quantity'].max()}")
print(
    f"  Mode: {micro_trades['Quantity'].mode()[0] if len(micro_trades['Quantity'].mode()) > 0 else 'N/A'}"
)
print(f"\nOption Entry Prices:")
print(f"  Average: ${micro_trades['Entry Price'].mean():.2f}")
print(f"  Min: ${micro_trades['Entry Price'].min():.2f}")
print(f"  Max: ${micro_trades['Entry Price'].max():.2f}")
print(f"  Median: ${micro_trades['Entry Price'].median():.2f}")

# Cheap options analysis
cheap_threshold = 0.50
cheap_options = micro_trades[micro_trades["Entry Price"] < cheap_threshold]
print(f"\nCheap options (< ${cheap_threshold}):")
print(f"  Count: {len(cheap_options)} ({len(cheap_options)/len(micro_trades)*100:.1f}%)")
print(f"  Avg contracts: {cheap_options['Quantity'].mean():.0f}")
print(
    f"  Win rate: {(cheap_options['IsWin'].sum()/len(cheap_options)*100):.1f}%"
    if len(cheap_options) > 0
    else "  Win rate: N/A"
)

# Fee analysis
print(f"\n{'='*80}")
print("FEE ANALYSIS")
print(f"{'='*80}")
gross_pnl = micro_trades["P&L"].sum()
total_fees = micro_trades["Fees"].sum()
fee_pct = (total_fees / abs(gross_pnl) * 100) if gross_pnl != 0 else 0

print(f"Total Fees: ${total_fees:,.0f}")
print(f"Gross P&L: ${gross_pnl:,.0f}")
print(f"Fees as % of Gross P&L: {fee_pct:.1f}%")
print(f"Average fee per trade: ${micro_trades['Fees'].mean():.0f}")

# Monthly breakdown
print(f"\n{'='*80}")
print("MONTHLY BREAKDOWN")
print(f"{'='*80}")

micro_trades["Month"] = micro_trades["Entry Time"].dt.to_period("M")
monthly_stats = []

for month in sorted(micro_trades["Month"].unique()):
    month_trades = micro_trades[micro_trades["Month"] == month]
    month_wins = month_trades[month_trades["IsWin"] == 1]

    monthly_stats.append(
        {
            "Month": str(month),
            "Trades": len(month_trades),
            "Wins": len(month_wins),
            "Win Rate %": f"{len(month_wins)/len(month_trades)*100:.1f}",
            "Gross P&L": month_trades["P&L"].sum(),
            "Fees": month_trades["Fees"].sum(),
            "Net P&L": month_trades["P&L"].sum() - month_trades["Fees"].sum(),
        }
    )

monthly_df = pd.DataFrame(monthly_stats)
print(monthly_df.to_string(index=False))

# Direction analysis
print(f"\n{'='*80}")
print("DIRECTION ANALYSIS (CALL vs PUT)")
print(f"{'='*80}")

for opt_type in ["CALL", "PUT"]:
    type_trades = micro_trades[micro_trades["Option Type"] == opt_type]
    if len(type_trades) == 0:
        continue
    type_wins = type_trades[type_trades["IsWin"] == 1]
    print(f"\n{opt_type}:")
    print(f"  Count: {len(type_trades)} ({len(type_trades)/len(micro_trades)*100:.1f}%)")
    print(f"  Win Rate: {len(type_wins)/len(type_trades)*100:.1f}%")
    print(f"  Net P&L: ${(type_trades['P&L'].sum() - type_trades['Fees'].sum()):,.0f}")

# Duration analysis
print(f"\n{'='*80}")
print("DURATION ANALYSIS")
print(f"{'='*80}")
print(
    f"Average hold time: {micro_trades['Duration Hours'].mean():.1f} hours ({micro_trades['Duration Days'].mean():.1f} days)"
)
print(f"Median hold time: {micro_trades['Duration Hours'].median():.1f} hours")
print(f"Min hold time: {micro_trades['Duration Hours'].min():.1f} hours")
print(f"Max hold time: {micro_trades['Duration Hours'].max():.1f} hours")

# Same-day exits
same_day = micro_trades[micro_trades["Duration Hours"] < 24]
print(f"\nSame-day exits: {len(same_day)} ({len(same_day)/len(micro_trades)*100:.1f}%)")

# Compare to V9.6 and V9.7
print(f"\n{'='*80}")
print("COMPARISON TO PREVIOUS VERSIONS")
print(f"{'='*80}")
print("V9.6 MICRO: -$12,829")
print("V9.7 MICRO: -$43,084 (DEBIT_MOMENTUM enabled)")
print(f"V9.8 MICRO: ${(micro_trades['P&L'].sum() - micro_trades['Fees'].sum()):,.0f}")

# Search logs for rejections
print(f"\n{'='*80}")
print("SEARCHING LOGS FOR REJECTION PATTERNS")
print(f"{'='*80}")

rejection_patterns = {
    "MICRO_SIGNAL_REJECTED": 0,
    "MICRO_SIGNAL_BLOCKED": 0,
    "MICRO_SIGNAL_DROPPED": 0,
    "INTRADAY_SIGNAL_REJECTED": 0,
    "INTRADAY_SIGNAL_BLOCKED": 0,
    "CHOPPY_MARKET": 0,
    "VIX_TOO_LOW": 0,
    "REGIME_BLOCK": 0,
}

with open(LOGS_FILE, "r") as f:
    for line in f:
        for pattern in rejection_patterns:
            if pattern in line:
                rejection_patterns[pattern] += 1

print("Rejection counts:")
for pattern, count in rejection_patterns.items():
    if count > 0:
        print(f"  {pattern}: {count}")

# Sample trades
print(f"\n{'='*80}")
print("SAMPLE MICRO TRADES (First 10)")
print(f"{'='*80}")
sample_cols = [
    "Entry Time",
    "Symbols",
    "MICRO Strategy",
    "DTE",
    "Entry Price",
    "Quantity",
    "P&L",
    "Fees",
    "IsWin",
    "Duration Hours",
]
print(micro_trades[sample_cols].head(10).to_string(index=False))

# Summary for report
print(f"\n{'='*80}")
print("SUMMARY FOR REPORT")
print(f"{'='*80}")

net_pnl = micro_trades["P&L"].sum() - micro_trades["Fees"].sum()
print(
    f"""
CRITICAL FINDINGS:

1. MICRO Trade Count: {len(micro_trades)}
2. Overall Win Rate: {len(wins)/len(micro_trades)*100:.1f}%
3. Net P&L: ${net_pnl:,.0f}
4. Average Trade: ${net_pnl/len(micro_trades):.0f}

5. DEBIT_MOMENTUM Performance:
   - Enabled: {'YES' if len(micro_trades[micro_trades['MICRO Strategy'] == 'DEBIT_MOMENTUM']) > 0 else 'NO'}
   - Count: {len(micro_trades[micro_trades['MICRO Strategy'] == 'DEBIT_MOMENTUM'])}
   - Net P&L: ${(micro_trades[micro_trades['MICRO Strategy'] == 'DEBIT_MOMENTUM']['P&L'].sum() - micro_trades[micro_trades['MICRO Strategy'] == 'DEBIT_MOMENTUM']['Fees'].sum()):,.0f}

6. Contract sizing: Avg {micro_trades['Quantity'].mean():.0f} contracts @ ${micro_trades['Entry Price'].mean():.2f}

7. DTE usage: {micro_trades['DTE'].min()}-{micro_trades['DTE'].max()} (mean {micro_trades['DTE'].mean():.1f})
"""
)
