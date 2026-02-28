#!/usr/bin/env python3
"""
Analyze V10.15 backtest logs to identify good signals choked by engine gates.
"""

import csv
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

LOG_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.15/V10_15_smoke_JulSep2024_v3_logs.txt"
TRADES_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.15/V10_15_smoke_JulSep2024_v3_trades.csv"
OUTPUT_FILE = "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.15/V10_15_CHOKED_SIGNAL_ANALYSIS.md"


class ChokedSignal:
    def __init__(
        self,
        timestamp,
        direction,
        qqq_price,
        block_code,
        engine,
        strategy=None,
        contract=None,
        reason=None,
    ):
        self.timestamp = timestamp
        self.direction = direction
        self.qqq_price = qqq_price
        self.block_code = block_code
        self.engine = engine
        self.strategy = strategy
        self.contract = contract
        self.reason = reason
        self.is_good_signal = None
        self.qqq_move = None

    def __repr__(self):
        return f"ChokedSignal({self.timestamp}, {self.direction}, QQQ={self.qqq_price}, {self.block_code})"


def parse_timestamp(ts_str):
    """Parse timestamp from log line."""
    try:
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    except:
        return None


def extract_qqq_price(line):
    """Extract QQQ price from log line."""
    match = re.search(r"QQQ[=:](\d+\.?\d*)", line)
    if match:
        return float(match.group(1))
    return None


def parse_itm_blocks(log_file):
    """Parse ITM engine blocks from log file."""
    blocks = []

    with open(log_file, "r") as f:
        for line in f:
            if "ITM_ENGINE_DECISION" in line and "BLOCK" in line:
                # Extract timestamp
                ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if not ts_match:
                    continue
                timestamp = parse_timestamp(ts_match.group(1))

                # Extract direction
                dir_match = re.search(r"Dir=(CALL|PUT)", line)
                direction = dir_match.group(1) if dir_match else None

                # Extract QQQ price
                qqq_price = extract_qqq_price(line)

                # Extract block code
                block_match = re.search(r"BLOCK\|(E_ITM_ENGINE_[A-Z_]+)", line)
                block_code = block_match.group(1) if block_match else "UNKNOWN"

                # Extract strategy
                strat_match = re.search(r"Strategy=(ITM_\w+)", line)
                strategy = strat_match.group(1) if strat_match else None

                if timestamp and direction and qqq_price:
                    blocks.append(
                        ChokedSignal(
                            timestamp, direction, qqq_price, block_code, "ITM", strategy=strategy
                        )
                    )

    return blocks


def parse_micro_blocks(log_file):
    """Parse MICRO engine blocks from log file."""
    blocks = []

    with open(log_file, "r") as f:
        for line in f:
            # VIX_STABLE_LOW_CONVICTION blocks
            if "MICRO_BLOCK:VIX_STABLE_LOW_CONVICTION" in line:
                ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if not ts_match:
                    continue
                timestamp = parse_timestamp(ts_match.group(1))

                # Extract QQQ move to infer direction
                move_match = re.search(r"QQQ ([+-]\d+\.?\d*)%", line)
                if move_match:
                    qqq_move_pct = float(move_match.group(1))
                    # Infer direction: if QQQ down, would be PUT; if up, CALL
                    direction = "PUT" if qqq_move_pct < 0 else "CALL"
                else:
                    direction = "UNKNOWN"

                # Get QQQ price from surrounding context (we'll need to scan)
                qqq_price = None

                if timestamp:
                    blocks.append(
                        ChokedSignal(
                            timestamp, direction, qqq_price, "VIX_STABLE_LOW_CONVICTION", "MICRO"
                        )
                    )

            # CONFIRMATION_FAIL blocks
            elif "CONFIRMATION_FAIL" in line:
                ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if not ts_match:
                    continue
                timestamp = parse_timestamp(ts_match.group(1))

                # Extract direction if present
                dir_match = re.search(r"Dir=(CALL|PUT)", line)
                direction = dir_match.group(1) if dir_match else "UNKNOWN"

                qqq_price = extract_qqq_price(line)

                if timestamp:
                    blocks.append(
                        ChokedSignal(timestamp, direction, qqq_price, "CONFIRMATION_FAIL", "MICRO")
                    )

            # E_CALL_GATE_MA20 blocks
            elif "E_CALL_GATE_MA20" in line:
                ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if not ts_match:
                    continue
                timestamp = parse_timestamp(ts_match.group(1))

                direction = "CALL"  # This gate only applies to CALLs
                qqq_price = extract_qqq_price(line)

                if timestamp:
                    blocks.append(
                        ChokedSignal(timestamp, direction, qqq_price, "E_CALL_GATE_MA20", "MICRO")
                    )

            # E_NO_CONTRACT_SELECTED blocks
            elif "E_NO_CONTRACT_SELECTED" in line:
                ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if not ts_match:
                    continue
                timestamp = parse_timestamp(ts_match.group(1))

                dir_match = re.search(r"Dir=(CALL|PUT)", line)
                direction = dir_match.group(1) if dir_match else "UNKNOWN"

                qqq_price = extract_qqq_price(line)

                if timestamp:
                    blocks.append(
                        ChokedSignal(
                            timestamp, direction, qqq_price, "E_NO_CONTRACT_SELECTED", "MICRO"
                        )
                    )

    return blocks


def parse_vass_blocks(log_file):
    """Parse VASS engine blocks from log file."""
    blocks = []

    with open(log_file, "r") as f:
        for line in f:
            if "DEBIT_TO_WIDTH_TOO_HIGH" in line:
                ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if not ts_match:
                    continue
                timestamp = parse_timestamp(ts_match.group(1))

                # Extract direction
                dir_match = re.search(r"Direction=(CALL|PUT)", line)
                direction = dir_match.group(1) if dir_match else None

                # Extract VIX as proxy for market context
                vix_match = re.search(r"VIX=(\d+\.?\d*)", line)
                vix = float(vix_match.group(1)) if vix_match else None

                if timestamp and direction:
                    blocks.append(
                        ChokedSignal(timestamp, direction, None, "DEBIT_TO_WIDTH_TOO_HIGH", "VASS")
                    )

    return blocks


def build_qqq_price_timeline(log_file):
    """Build a timeline of QQQ prices from the log file."""
    prices = {}  # timestamp -> price

    with open(log_file, "r") as f:
        for line in f:
            ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if ts_match:
                timestamp = parse_timestamp(ts_match.group(1))
                qqq_price = extract_qqq_price(line)
                if timestamp and qqq_price:
                    prices[timestamp] = qqq_price

    return prices


def evaluate_signal_quality(signal: ChokedSignal, qqq_timeline: Dict):
    """
    Evaluate if a choked signal was "good" by checking QQQ price movement.

    Good signal criteria:
    - CALL: QQQ rose >0.3% within 1-4 hours
    - PUT: QQQ fell >0.3% within 1-4 hours
    """
    if signal.qqq_price is None:
        # Try to find QQQ price at signal time from timeline
        closest_time = min(
            qqq_timeline.keys(),
            key=lambda t: abs((t - signal.timestamp).total_seconds()),
            default=None,
        )
        if (
            closest_time and abs((closest_time - signal.timestamp).total_seconds()) < 300
        ):  # within 5 min
            signal.qqq_price = qqq_timeline[closest_time]
        else:
            return  # Can't evaluate without price

    # Look for price movement 1-4 hours later
    future_times = [signal.timestamp + timedelta(hours=h) for h in [1, 2, 3, 4]]
    max_move = 0

    for future_time in future_times:
        # Find closest price point
        closest = min(
            qqq_timeline.keys(), key=lambda t: abs((t - future_time).total_seconds()), default=None
        )

        if closest and abs((closest - future_time).total_seconds()) < 900:  # within 15 min
            future_price = qqq_timeline[closest]
            move_pct = ((future_price - signal.qqq_price) / signal.qqq_price) * 100

            if abs(move_pct) > abs(max_move):
                max_move = move_pct

    signal.qqq_move = max_move

    # Evaluate if signal was good
    if signal.direction == "CALL":
        signal.is_good_signal = max_move > 0.3
    elif signal.direction == "PUT":
        signal.is_good_signal = max_move < -0.3
    else:
        signal.is_good_signal = None


def load_actual_trades(trades_file):
    """Load actual executed trades to compare win rates."""
    trades = []

    with open(trades_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(
                {
                    "entry_time": row["Entry Time"],
                    "symbols": row["Symbols"],
                    "direction": row["Direction"],
                    "pnl": float(row["P&L"]),
                    "is_win": row["IsWin"] == "1",
                }
            )

    return trades


def generate_report(itm_blocks, micro_blocks, vass_blocks, trades):
    """Generate comprehensive markdown report."""

    report = []
    report.append("# V10.15 Choked Signal Analysis")
    report.append("\n## Executive Summary\n")
    report.append(f"**Backtest Period:** Jul-Sep 2024 (V3 smoke test)")
    report.append(f"**Total Executed Trades:** {len(trades)}")
    report.append(
        f"**Actual Win Rate:** {sum(1 for t in trades if t['is_win']) / len(trades) * 100:.1f}%"
    )
    report.append(f"\n**Total Signals Choked:**")
    report.append(f"- ITM Engine: {len(itm_blocks)}")
    report.append(f"- MICRO Engine: {len(micro_blocks)}")
    report.append(f"- VASS Engine: {len(vass_blocks)}")
    report.append(f"- **Total: {len(itm_blocks) + len(micro_blocks) + len(vass_blocks)}**")

    # ITM Analysis
    report.append("\n---\n")
    report.append("## ITM Engine Choked Signals\n")

    itm_by_code = defaultdict(list)
    for sig in itm_blocks:
        itm_by_code[sig.block_code].append(sig)

    report.append("### Choker Breakdown by Block Code\n")
    report.append("| Block Code | Count | % of Total |")
    report.append("|------------|------:|----------:|")

    total_itm = len(itm_blocks)
    for code in sorted(itm_by_code.keys(), key=lambda k: len(itm_by_code[k]), reverse=True):
        count = len(itm_by_code[code])
        pct = (count / total_itm * 100) if total_itm > 0 else 0
        report.append(f"| {code} | {count} | {pct:.1f}% |")

    # Good signals analysis
    report.append("\n### Good Signals Choked\n")
    report.append("| Block Code | Total Choked | Good Signals | % Good | Avg QQQ Move |")
    report.append("|------------|-------------:|-------------:|-------:|-------------:|")

    for code in sorted(itm_by_code.keys(), key=lambda k: len(itm_by_code[k]), reverse=True):
        signals = itm_by_code[code]
        good_signals = [s for s in signals if s.is_good_signal is True]
        total = len(signals)
        good = len(good_signals)
        pct_good = (good / total * 100) if total > 0 else 0

        # Average move for good signals
        moves = [s.qqq_move for s in good_signals if s.qqq_move is not None]
        avg_move = sum(moves) / len(moves) if moves else 0

        report.append(f"| {code} | {total} | {good} | {pct_good:.1f}% | {avg_move:+.2f}% |")

    # Time of day analysis
    report.append("\n### Choke Rate by Time of Day (ITM)\n")
    report.append("| Hour | Total Blocks | Good Signals | % Good |")
    report.append("|------|-------------:|-------------:|-------:|")

    by_hour = defaultdict(list)
    for sig in itm_blocks:
        hour = sig.timestamp.hour
        by_hour[hour].append(sig)

    for hour in sorted(by_hour.keys()):
        signals = by_hour[hour]
        good = sum(1 for s in signals if s.is_good_signal is True)
        total = len(signals)
        pct = (good / total * 100) if total > 0 else 0
        report.append(f"| {hour:02d}:00 | {total} | {good} | {pct:.1f}% |")

    # MICRO Analysis
    report.append("\n---\n")
    report.append("## MICRO Engine Choked Signals\n")

    micro_by_code = defaultdict(list)
    for sig in micro_blocks:
        micro_by_code[sig.block_code].append(sig)

    report.append("### Choker Breakdown by Block Code\n")
    report.append("| Block Code | Count | % of Total |")
    report.append("|------------|------:|----------:|")

    total_micro = len(micro_blocks)
    for code in sorted(micro_by_code.keys(), key=lambda k: len(micro_by_code[k]), reverse=True):
        count = len(micro_by_code[code])
        pct = (count / total_micro * 100) if total_micro > 0 else 0
        report.append(f"| {code} | {count} | {pct:.1f}% |")

    report.append("\n### Good Signals Choked\n")
    report.append("| Block Code | Total Choked | Good Signals | % Good | Avg QQQ Move |")
    report.append("|------------|-------------:|-------------:|-------:|-------------:|")

    for code in sorted(micro_by_code.keys(), key=lambda k: len(micro_by_code[k]), reverse=True):
        signals = micro_by_code[code]
        good_signals = [s for s in signals if s.is_good_signal is True]
        total = len(signals)
        good = len(good_signals)
        pct_good = (good / total * 100) if total > 0 else 0

        moves = [s.qqq_move for s in good_signals if s.qqq_move is not None]
        avg_move = sum(moves) / len(moves) if moves else 0

        report.append(f"| {code} | {total} | {good} | {pct_good:.1f}% | {avg_move:+.2f}% |")

    # VASS Analysis
    report.append("\n---\n")
    report.append("## VASS Engine Choked Signals\n")

    vass_by_code = defaultdict(list)
    for sig in vass_blocks:
        vass_by_code[sig.block_code].append(sig)

    report.append("### Choker Breakdown by Block Code\n")
    report.append("| Block Code | Count | % of Total |")
    report.append("|------------|------:|----------:|")

    total_vass = len(vass_blocks)
    for code in sorted(vass_by_code.keys(), key=lambda k: len(vass_by_code[k]), reverse=True):
        count = len(vass_by_code[code])
        pct = (count / total_vass * 100) if total_vass > 0 else 0
        report.append(f"| {code} | {count} | {pct:.1f}% |")

    # Summary
    report.append("\n---\n")
    report.append("## Overall Summary\n")

    total_blocks = len(itm_blocks) + len(micro_blocks) + len(vass_blocks)
    total_good = sum(1 for s in itm_blocks + micro_blocks + vass_blocks if s.is_good_signal is True)

    report.append(f"**Total Signals Blocked:** {total_blocks}")
    report.append(f"**Total Good Signals Choked:** {total_good}")
    report.append(
        f"**Good Signal Rate:** {(total_good / total_blocks * 100) if total_blocks > 0 else 0:.1f}%"
    )

    # Most destructive block codes
    report.append("\n### Top 10 Most Destructive Block Codes\n")
    report.append("*(Choking the most good signals)*\n")
    report.append("| Rank | Engine | Block Code | Good Signals | Total Blocks |")
    report.append("|------|--------|------------|-------------:|-------------:|")

    all_codes = []
    for code, sigs in itm_by_code.items():
        good = sum(1 for s in sigs if s.is_good_signal is True)
        all_codes.append(("ITM", code, good, len(sigs)))
    for code, sigs in micro_by_code.items():
        good = sum(1 for s in sigs if s.is_good_signal is True)
        all_codes.append(("MICRO", code, good, len(sigs)))
    for code, sigs in vass_by_code.items():
        good = sum(1 for s in sigs if s.is_good_signal is True)
        all_codes.append(("VASS", code, good, len(sigs)))

    all_codes.sort(key=lambda x: x[2], reverse=True)

    for i, (engine, code, good, total) in enumerate(all_codes[:10], 1):
        report.append(f"| {i} | {engine} | {code} | {good} | {total} |")

    # Estimated P&L opportunity
    report.append("\n### Estimated P&L Opportunity Cost\n")

    avg_win_pnl = (
        sum(t["pnl"] for t in trades if t["is_win"]) / sum(1 for t in trades if t["is_win"])
        if any(t["is_win"] for t in trades)
        else 0
    )
    estimated_opportunity = total_good * avg_win_pnl

    report.append(f"- **Average winning trade P&L:** ${avg_win_pnl:.2f}")
    report.append(f"- **Good signals choked:** {total_good}")
    report.append(f"- **Estimated opportunity cost:** ${estimated_opportunity:,.2f}")
    report.append(
        f"\n*Note: This assumes choked good signals would have won at the same rate as executed trades.*"
    )

    return "\n".join(report)


def main():
    print("Parsing ITM blocks...")
    itm_blocks = parse_itm_blocks(LOG_FILE)
    print(f"  Found {len(itm_blocks)} ITM blocks")

    print("Parsing MICRO blocks...")
    micro_blocks = parse_micro_blocks(LOG_FILE)
    print(f"  Found {len(micro_blocks)} MICRO blocks")

    print("Parsing VASS blocks...")
    vass_blocks = parse_vass_blocks(LOG_FILE)
    print(f"  Found {len(vass_blocks)} VASS blocks")

    print("Building QQQ price timeline...")
    qqq_timeline = build_qqq_price_timeline(LOG_FILE)
    print(f"  Found {len(qqq_timeline)} price points")

    print("Evaluating signal quality...")
    for sig in itm_blocks + micro_blocks + vass_blocks:
        evaluate_signal_quality(sig, qqq_timeline)

    print("Loading actual trades...")
    trades = load_actual_trades(TRADES_FILE)
    print(f"  Loaded {len(trades)} trades")

    print("Generating report...")
    report = generate_report(itm_blocks, micro_blocks, vass_blocks, trades)

    with open(OUTPUT_FILE, "w") as f:
        f.write(report)

    print(f"\nReport written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
