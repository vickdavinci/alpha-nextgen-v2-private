#!/usr/bin/env python3
import csv
import re

# Load first spread from trades
with open("docs/audits/logs/stage9.6/V9_6_FullYear2017_v3_trades.csv", "r") as f:
    reader = csv.DictReader(f)
    row1 = next(reader)
    row2 = next(reader)

print("Trade CSV symbols:")
print(f"Row 1: '{row1['Symbols']}'")
print(f"Row 2: '{row2['Symbols']}'")
print(f"Combined key: '{row1['Symbols']}|{row2['Symbols']}'")
print()

# Load first exit signal from logs
with open("docs/audits/logs/stage9.6/V9_6_FullYear2017_v3_logs.txt", "r") as f:
    for line in f:
        if "SPREAD: EXIT_SIGNAL" in line:
            key_match = re.search(r"Key=([^|]+\|[^|]+)\s*\|", line)
            if key_match:
                key = key_match.group(1).strip()
                print(f"Log key: '{key}'")
                break
