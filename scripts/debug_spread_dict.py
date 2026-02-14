#!/usr/bin/env python3
import csv
import re
from collections import defaultdict
from datetime import datetime


def parse_option_symbol(symbol: str):
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


# Load first two trades
with open("docs/audits/logs/stage9.6/V9_6_FullYear2017_v3_trades.csv", "r") as f:
    reader = csv.DictReader(f)
    trades = [next(reader), next(reader)]

# Group by entry time
by_entry_time = defaultdict(list)
for trade in trades:
    by_entry_time[trade["Entry Time"]].append(trade)

# Process first group
for entry_time, trade_group in by_entry_time.items():
    option_trades = []
    for trade in trade_group:
        opt_info = parse_option_symbol(trade["Symbols"])
        if opt_info:
            option_trades.append({**trade, **opt_info})

    # Check the spread
    for i, long_leg in enumerate(option_trades):
        if long_leg["Direction"] != "Buy":
            continue
        for short_leg in option_trades[i + 1 :]:
            if short_leg["Direction"] != "Sell":
                continue

            print("Long leg symbol:", repr(long_leg["Symbols"]))
            print("Short leg symbol:", repr(short_leg["Symbols"]))
            key = f"{long_leg['Symbols']}|{short_leg['Symbols']}"
            print("Generated key:", repr(key))
            break
        break
    break
