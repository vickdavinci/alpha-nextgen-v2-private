#!/usr/bin/env python3
"""
Generate V10.1 Full Year 2023 Trade Detail Report
Cross-references trades.csv with log files to extract complete context.
"""

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class TradeContext:
    """Complete context for a single trade"""

    entry_time: datetime
    exit_time: datetime
    symbol: str
    direction: str  # Buy/Sell
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    fees: float
    is_win: bool
    order_ids: str

    # Context from logs (to be populated)
    engine: str = "UNKNOWN"
    strategy: str = "UNKNOWN"

    # MICRO-specific
    micro_regime: str = "N/A"
    regime_score: int = 0
    vix: float = 0.0
    vix_level: str = "N/A"
    vix_direction: str = "N/A"
    qqq_move: str = "N/A"
    micro_direction: str = "N/A"  # CALL/PUT
    stop_pct: str = "N/A"
    dte: int = 0
    entry_trigger: str = "NOT_FOUND"
    exit_trigger: str = "NOT_FOUND"

    # VASS-specific
    vass_direction: str = "N/A"  # BULLISH/BEARISH
    vass_spread_type: str = "N/A"  # BULL_CALL, BEAR_PUT, etc.
    vass_regime: int = 0
    vass_vix: float = 0.0
    vass_dte: int = 0
    debit: float = 0.0
    width: float = 0.0
    dw_pct: float = 0.0
    max_profit: float = 0.0

    notes: List[str] = field(default_factory=list)


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO datetime string"""
    return datetime.strptime(dt_str.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S%z").replace(
        tzinfo=None
    )


def parse_trades_csv(csv_path: Path) -> List[TradeContext]:
    """Parse trades.csv into TradeContext objects"""
    trades = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row["Entry Time"]:  # Skip empty rows
                continue

            trade = TradeContext(
                entry_time=parse_datetime(row["Entry Time"]),
                exit_time=parse_datetime(row["Exit Time"]),
                symbol=row["Symbols"].strip(),
                direction=row["Direction"],
                entry_price=float(row["Entry Price"]),
                exit_price=float(row["Exit Price"]),
                quantity=int(row["Quantity"]),
                pnl=float(row["P&L"]),
                fees=float(row["Fees"]),
                is_win=row["IsWin"] == "1",
                order_ids=row["Order Ids"],
            )
            trades.append(trade)

    return trades


def extract_micro_context(trade: TradeContext, log_lines: List[str]) -> None:
    """Extract MICRO intraday context from logs"""
    # Search for INTRADAY_SIGNAL within ±30 minutes of entry
    search_start = trade.entry_time - timedelta(minutes=30)
    search_end = trade.entry_time + timedelta(minutes=30)

    for line in log_lines:
        if "INTRADAY_SIGNAL:" not in line:
            continue

        # Parse log timestamp
        match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if not match:
            continue
        log_dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")

        if search_start <= log_dt <= search_end:
            # Extract all fields
            if "INTRADAY_ITM_MOM" in line:
                trade.strategy = "ITM_MOMENTUM"
            elif "INTRADAY_DEBIT_FADE" in line:
                trade.strategy = "DEBIT_FADE"
            else:
                trade.strategy = "UNKNOWN"

            trade.engine = "MICRO"

            # Regime
            regime_match = re.search(r"Regime=(\w+)", line)
            if regime_match:
                trade.micro_regime = regime_match.group(1)

            # Score
            score_match = re.search(r"Score=(\d+)", line)
            if score_match:
                trade.regime_score = int(score_match.group(1))

            # VIX
            vix_match = re.search(r"VIX=([\d.]+)\s+\((\w+)\)", line)
            if vix_match:
                trade.vix = float(vix_match.group(1))
                trade.vix_direction = vix_match.group(2)

            # VIX Level
            if trade.vix < 18:
                trade.vix_level = "LOW"
            elif trade.vix <= 25:
                trade.vix_level = "MEDIUM"
            else:
                trade.vix_level = "HIGH"

            # QQQ Move
            qqq_match = re.search(r"QQQ=([A-Z_]+)\s+\(([\d.+%-]+)\)", line)
            if qqq_match:
                trade.qqq_move = f"{qqq_match.group(1)} ({qqq_match.group(2)})"

            # Direction (CALL/PUT)
            if "PUT x" in line:
                trade.micro_direction = "PUT"
            elif "CALL x" in line:
                trade.micro_direction = "CALL"

            # Stop %
            stop_match = re.search(r"Stop=(\d+)%", line)
            if stop_match:
                trade.stop_pct = f"{stop_match.group(1)}%"

            # DTE
            dte_match = re.search(r"DTE=(\d+)", line)
            if dte_match:
                trade.dte = int(dte_match.group(1))

            trade.entry_trigger = f"INTRADAY_SIGNAL at {log_dt.strftime('%H:%M')}"
            break

    # Search for exit trigger
    exit_search_start = trade.exit_time - timedelta(minutes=5)
    exit_search_end = trade.exit_time + timedelta(minutes=5)

    for line in log_lines:
        match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if not match:
            continue
        log_dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")

        if exit_search_start <= log_dt <= exit_search_end:
            if "OCO_STOP:" in line or "Type=StopMarket" in line:
                trade.exit_trigger = "OCO_STOP"
                break
            elif "OCO_PROFIT:" in line:
                trade.exit_trigger = "OCO_PROFIT"
                break
            elif "INTRADAY_FORCE_EXIT:" in line:
                trade.exit_trigger = "FORCE_CLOSE_15:25"
                break
            elif "INTRADAY_FORCE_EXIT_FALLBACK:" in line:
                trade.exit_trigger = "FORCE_CLOSE_FALLBACK"
                break
            elif "PREMARKET_STALE_INTRADAY_CLOSE" in line:
                trade.exit_trigger = "PREMARKET_STALE_CLOSE"
                trade.notes.append("Orphan from previous day")
                break

    # Check for after-hours entry
    if trade.entry_time.hour >= 16:
        trade.notes.append(f"After-hours entry at {trade.entry_time.strftime('%H:%M')}")


def extract_vass_context(trade: TradeContext, log_lines: List[str]) -> None:
    """Extract VASS spread context from logs"""
    entry_date = trade.entry_time.date()

    for line in log_lines:
        if "SPREAD: ENTRY_SIGNAL" not in line and "CREDIT_SPREAD: ENTRY_SIGNAL" not in line:
            continue

        match = re.match(r"^(\d{4}-\d{2}-\d{2})", line)
        if not match:
            continue
        log_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()

        if log_date == entry_date:
            trade.engine = "VASS"

            # Spread type
            if "BULL_CALL" in line:
                trade.vass_spread_type = "BULL_CALL_DEBIT"
                trade.vass_direction = "BULLISH"
            elif "BEAR_PUT" in line:
                trade.vass_spread_type = "BEAR_PUT_DEBIT"
                trade.vass_direction = "BEARISH"
            elif "BEAR_CALL_CREDIT" in line:
                trade.vass_spread_type = "BEAR_CALL_CREDIT"
                trade.vass_direction = "BEARISH"
            elif "BULL_PUT_CREDIT" in line:
                trade.vass_spread_type = "BULL_PUT_CREDIT"
                trade.vass_direction = "BULLISH"

            # Regime
            regime_match = re.search(r"Regime=(\d+)", line)
            if regime_match:
                trade.vass_regime = int(regime_match.group(1))

            # VIX
            vix_match = re.search(r"VIX=([\d.]+)", line)
            if vix_match:
                trade.vass_vix = float(vix_match.group(1))

            # DTE
            dte_match = re.search(r"DTE=(\d+)", line)
            if dte_match:
                trade.vass_dte = int(dte_match.group(1))

            # Debit and MaxProfit
            debit_match = re.search(r"Debit=\$([\d.]+)\s+MaxProfit=\$([\d.]+)", line)
            if debit_match:
                trade.debit = float(debit_match.group(1))
                trade.max_profit = float(debit_match.group(2))
                trade.width = trade.debit + trade.max_profit
                trade.dw_pct = (trade.debit / trade.width * 100) if trade.width > 0 else 0

            # Credit spreads
            credit_match = re.search(r"Credit=\$([\d.]+)\s+Width=\$([\d.]+)", line)
            if credit_match:
                credit = float(credit_match.group(1))
                width = float(credit_match.group(2))
                trade.debit = width - credit
                trade.width = width
                trade.max_profit = credit
                trade.dw_pct = (trade.debit / trade.width * 100) if trade.width > 0 else 0

            trade.entry_trigger = f"SPREAD_ENTRY_SIGNAL"
            break

    # Search for exit trigger
    exit_date = trade.exit_time.date()

    for line in log_lines:
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", line)
        if not match:
            continue
        log_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()

        if log_date == exit_date:
            if "STOP_LOSS" in line and "SPREAD" in line:
                trade.exit_trigger = "STOP_LOSS"
                break
            elif "STRESS" in line and "SPREAD" in line:
                trade.exit_trigger = "STRESS_OVERLAY"
                break
            elif "RECONCILED" in line:
                trade.exit_trigger = "RECONCILED"
                break
            elif "HARD_STOP" in line:
                trade.exit_trigger = "HARD_STOP"
                break
            elif "PROFIT_TARGET" in line:
                trade.exit_trigger = "PROFIT_TARGET"
                break
            elif "TRAIL_STOP" in line:
                trade.exit_trigger = "TRAIL_STOP"
                break
            elif "DTE_EXIT" in line:
                trade.exit_trigger = "DTE_EXIT"
                break


def pair_vass_spreads(trades: List[TradeContext]) -> List[Tuple[TradeContext, TradeContext]]:
    """Pair VASS spread legs"""
    paired = []
    used_indices = set()

    for i, trade1 in enumerate(trades):
        if i in used_indices or trade1.engine != "VASS":
            continue

        for j, trade2 in enumerate(trades[i + 1 :], start=i + 1):
            if j in used_indices or trade2.engine != "VASS":
                continue

            if (
                trade1.entry_time.date() == trade2.entry_time.date()
                and trade1.exit_time.date() == trade2.exit_time.date()
            ):
                paired.append((trade1, trade2))
                used_indices.add(i)
                used_indices.add(j)
                break

    return paired


def calculate_hold_duration(entry: datetime, exit: datetime) -> str:
    """Calculate hold duration"""
    delta = exit - entry

    if delta.total_seconds() < 24 * 3600:
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        return f"{hours}h {minutes}m"
    else:
        days = delta.total_seconds() / (24 * 3600)
        return f"{days:.1f}d"


def generate_report(trades: List[TradeContext], output_path: Path) -> None:
    """Generate markdown report"""
    micro_trades = [t for t in trades if t.engine == "MICRO"]
    vass_trades = [t for t in trades if t.engine == "VASS"]
    vass_spreads = pair_vass_spreads(vass_trades)

    with open(output_path, "w") as f:
        f.write("# V10.1 Full Year 2023 - Trade Detail Report\n\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("**Period:** 2023-01-03 to 2023-12-29\n\n")
        f.write(f"**Total Trades:** {len(trades)}\n\n")

        # Validation
        f.write("## Data Validation\n\n")
        f.write(f"- [x] trades.csv parsed: {len(trades)} rows\n")
        f.write(f"- [x] MICRO trades identified: {len(micro_trades)}\n")
        f.write(
            f"- [x] VASS trades identified: {len(vass_trades)} legs → {len(vass_spreads)} spreads\n\n"
        )

        # Part 1: VASS
        f.write("---\n\n# Part 1: VASS Spread Trades\n\n")

        if vass_spreads:
            f.write(
                "| # | Entry | Exit | Spread | Regime | VIX | DTE | Debit | Width | D/W% | Entry Trigger | Exit Trigger | Hold | Net P&L | P&L% | W/L |\n"
            )
            f.write(
                "|---|-------|------|--------|--------|-----|-----|-------|-------|------|--------------|-------------|------|---------|-------|-----|\n"
            )

            for idx, (leg1, leg2) in enumerate(vass_spreads, 1):
                net_pnl = leg1.pnl + leg2.pnl
                hold = calculate_hold_duration(leg1.entry_time, leg1.exit_time)
                pnl_pct = (
                    (net_pnl / (leg1.debit * leg1.quantity * 100)) * 100 if leg1.debit > 0 else 0
                )
                wl = "W" if net_pnl > 0 else "L"

                f.write(
                    f"| {idx} | {leg1.entry_time.strftime('%m/%d')} | {leg1.exit_time.strftime('%m/%d')} | "
                    f"{leg1.vass_spread_type} | {leg1.vass_regime} | {leg1.vass_vix:.1f} | {leg1.vass_dte} | "
                    f"${leg1.debit:.2f} | ${leg1.width:.2f} | {leg1.dw_pct:.1f}% | "
                    f"{leg1.entry_trigger} | {leg1.exit_trigger} | {hold} | "
                    f"${net_pnl:,.0f} | {pnl_pct:.1f}% | {wl} |\n"
                )

        # VASS Summary
        total_spreads = len(vass_spreads)
        wins = sum(1 for l1, l2 in vass_spreads if (l1.pnl + l2.pnl) > 0)
        win_rate = (wins / total_spreads * 100) if total_spreads > 0 else 0
        total_pnl = sum(l1.pnl + l2.pnl for l1, l2 in vass_spreads)

        f.write(f"\n## VASS Summary\n\n")
        f.write(
            f"- **Spreads:** {total_spreads} | **Wins:** {wins} | **Losses:** {total_spreads - wins}\n"
        )
        f.write(f"- **Win Rate:** {win_rate:.1f}%\n")
        f.write(f"- **Net P&L:** ${total_pnl:,.0f}\n\n")

        # Part 2: MICRO
        f.write("---\n\n# Part 2: MICRO Intraday Trades\n\n")

        if micro_trades:
            f.write(
                "| # | Date | Entry | Exit | Strategy | Dir | Regime | Score | VIX | VIX Dir | Entry | Exit | Hold | P&L | % | W/L | Notes |\n"
            )
            f.write(
                "|---|------|-------|------|----------|-----|--------|-------|-----|---------|-------|------|------|-----|---|-----|-------|\n"
            )

            for idx, t in enumerate(micro_trades, 1):
                hold = calculate_hold_duration(t.entry_time, t.exit_time)
                pnl_pct = (
                    ((t.exit_price - t.entry_price) / t.entry_price * 100)
                    if t.direction == "Buy"
                    else 0
                )
                wl = "W" if t.is_win else "L"
                notes = "; ".join(t.notes[:2]) if t.notes else ""

                f.write(
                    f"| {idx} | {t.entry_time.strftime('%m/%d')} | {t.entry_time.strftime('%H:%M')} | "
                    f"{t.exit_time.strftime('%H:%M')} | {t.strategy} | {t.micro_direction} | "
                    f"{t.micro_regime} | {t.regime_score} | {t.vix:.1f} | {t.vix_direction} | "
                    f"{t.entry_trigger.split()[-1] if t.entry_trigger != 'NOT_FOUND' else 'N/A'} | "
                    f"{t.exit_trigger} | {hold} | ${t.pnl:.0f} | {pnl_pct:.1f}% | {wl} | {notes} |\n"
                )

        # MICRO Summary
        total_micro = len(micro_trades)
        micro_wins = sum(1 for t in micro_trades if t.is_win)
        micro_win_rate = (micro_wins / total_micro * 100) if total_micro > 0 else 0
        micro_pnl = sum(t.pnl for t in micro_trades)

        f.write(f"\n## MICRO Summary\n\n")
        f.write(
            f"- **Trades:** {total_micro} | **Wins:** {micro_wins} | **Losses:** {total_micro - micro_wins}\n"
        )
        f.write(f"- **Win Rate:** {micro_win_rate:.1f}%\n")
        f.write(f"- **Net P&L:** ${micro_pnl:,.0f}\n\n")

        # Regime breakdown
        f.write("### By Micro Regime\n\n")
        regime_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
        for t in micro_trades:
            regime_stats[t.micro_regime]["count"] += 1
            if t.is_win:
                regime_stats[t.micro_regime]["wins"] += 1
            regime_stats[t.micro_regime]["pnl"] += t.pnl

        f.write("| Regime | Trades | Wins | Losses | WR% | Net P&L |\n")
        f.write("|--------|--------|------|--------|-----|----------|\n")
        for regime in sorted(regime_stats.keys()):
            s = regime_stats[regime]
            wr = (s["wins"] / s["count"] * 100) if s["count"] > 0 else 0
            f.write(
                f"| {regime} | {s['count']} | {s['wins']} | {s['count']-s['wins']} | {wr:.1f}% | ${s['pnl']:.0f} |\n"
            )


def main():
    base_dir = Path(
        "/Users/vigneshwaranarumugam/Documents/Trading Github/alpha-nextgen-v2-private/docs/audits/logs/stage10.1"
    )
    trades_csv = base_dir / "V10_1_Fullyear_2023_trades.csv"
    log_file = base_dir / "V10_1_Fullyear_2023_logs.txt"
    output_file = base_dir / "V10_1_Fullyear_2023_TRADE_DETAIL_REPORT.md"

    print("Parsing trades.csv...")
    trades = parse_trades_csv(trades_csv)
    print(f"Loaded {len(trades)} trades")

    print("\nReading log file...")
    with open(log_file, "r") as f:
        all_log_lines = [line.strip() for line in f]
    print(f"Loaded {len(all_log_lines)} log lines")

    print("\nExtracting context...")
    for idx, trade in enumerate(trades, 1):
        if idx % 50 == 0:
            print(f"  {idx}/{len(trades)}...")

        log_start = trade.entry_time - timedelta(days=1)
        log_end = trade.exit_time + timedelta(days=1)
        relevant_logs = []

        for line in all_log_lines:
            match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if match:
                log_dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                if log_start <= log_dt <= log_end:
                    relevant_logs.append(line)

        if "QQQ" in trade.symbol and ("C0" in trade.symbol or "P0" in trade.symbol):
            extract_micro_context(trade, relevant_logs)
            if trade.engine == "UNKNOWN":
                extract_vass_context(trade, relevant_logs)

    print("\nGenerating report...")
    generate_report(trades, output_file)

    print(f"\n✅ Report generated: {output_file}")


if __name__ == "__main__":
    main()
