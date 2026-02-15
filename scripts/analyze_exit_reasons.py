#!/usr/bin/env python3
"""
Analyze exit reasons for MICRO trades from logs.
"""

import re
from collections import Counter

LOGS_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage9.8/V9_8_2017fullyear.txt"

# Track MICRO-related patterns
micro_blocks = Counter()
micro_entries = []
micro_exits = []
exit_reasons = Counter()

print("Parsing logs for MICRO patterns...")

with open(LOGS_FILE, "r") as f:
    for line in f:
        # MICRO blocks
        if "MICRO_BLOCK:" in line:
            match = re.search(r"MICRO_BLOCK:(\w+)", line)
            if match:
                micro_blocks[match.group(1)] += 1

        # MICRO entries (from order fills)
        if "MICRO:" in line and "Market,Filled" in line:
            micro_entries.append(line.strip())

        # INTRADAY results (exits)
        if "INTRADAY_RESULT:" in line:
            micro_exits.append(line.strip())

        # Force exits
        if "INTRADAY_FORCE_EXIT" in line and "TIME_EXIT" in line:
            exit_reasons["TIME_EXIT_1525"] += 1
        elif "OCO: TRIGGERED" in line and "INTRADAY" in line:
            if "PROFIT" in line:
                exit_reasons["PROFIT_TARGET"] += 1
            elif "STOP" in line:
                exit_reasons["STOP_LOSS"] += 1

print(f"\n{'='*80}")
print("MICRO BLOCKING ANALYSIS")
print(f"{'='*80}")
print(f"\nTotal MICRO_BLOCK events: {sum(micro_blocks.values())}")
print(f"\nTop blocking reasons:")
for reason, count in micro_blocks.most_common(15):
    print(f"  {reason}: {count}")

print(f"\n{'='*80}")
print("MICRO ENTRY/EXIT COUNTS")
print(f"{'='*80}")
print(f"MICRO entry orders (from logs): {len(micro_entries)}")
print(f"INTRADAY_RESULT exits (from logs): {len(micro_exits)}")

print(f"\n{'='*80}")
print("EXIT REASON ANALYSIS")
print(f"{'='*80}")
print(f"Time exits (15:25 force close): {exit_reasons['TIME_EXIT_1525']}")
print(f"Profit targets: {exit_reasons['PROFIT_TARGET']}")
print(f"Stop losses: {exit_reasons['STOP_LOSS']}")
print(f"\nNote: Most exits appear to be discretionary/manual (no specific reason logged)")

# Analyze INTRADAY_RESULT details
wins = 0
losses = 0
total_pct = 0.0

for exit_line in micro_exits:
    if "WIN" in exit_line:
        wins += 1
        match = re.search(r"P&L=([+-]?\d+\.?\d*)%", exit_line)
        if match:
            total_pct += float(match.group(1))
    elif "LOSS" in exit_line:
        losses += 1
        match = re.search(r"P&L=([+-]?\d+\.?\d*)%", exit_line)
        if match:
            total_pct += float(match.group(1))

print(f"\n{'='*80}")
print("INTRADAY_RESULT BREAKDOWN (from logged exits)")
print(f"{'='*80}")
print(f"Wins: {wins}")
print(f"Losses: {losses}")
print(f"Win rate: {wins/(wins+losses)*100:.1f}%" if (wins + losses) > 0 else "Win rate: N/A")
print(f"Average % P&L: {total_pct/(wins+losses):.1f}%" if (wins + losses) > 0 else "Avg P&L: N/A")

# Check for ITM_MOMENTUM
print(f"\n{'='*80}")
print("STRATEGY DETECTION")
print(f"{'='*80}")

itm_count = 0
fade_count = 0
momentum_count = 0

for entry in micro_entries:
    if "ITM_MOMENTUM" in entry:
        itm_count += 1
    elif "DEBIT_FADE" in entry:
        fade_count += 1
    elif "DEBIT_MOMENTUM" in entry:
        momentum_count += 1

print(f"ITM_MOMENTUM: {itm_count}")
print(f"DEBIT_FADE: {fade_count}")
print(f"DEBIT_MOMENTUM: {momentum_count}")

# Check for choppy market
print(f"\n{'='*80}")
print("CHOPPY MARKET DETECTION")
print(f"{'='*80}")

choppy_count = 0
with open(LOGS_FILE, "r") as f:
    for line in f:
        if "CHOPPY" in line.upper():
            choppy_count += 1
            if choppy_count <= 5:
                print(line.strip())

print(f"\nTotal CHOPPY mentions: {choppy_count}")
