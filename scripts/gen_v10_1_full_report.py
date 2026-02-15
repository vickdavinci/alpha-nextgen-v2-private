#!/usr/bin/env python3
"""Generate comprehensive V10.1 trade detail report with full context"""

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    fees: float
    is_win: bool
    order_ids: str

    # Classification
    engine: str = "UNKNOWN"
    strategy: str = ""

    # MICRO fields
    micro_regime: str = ""
    regime_score: int = 0
    vix: float = 0
    vix_dir: str = ""
    qqq_move: str = ""
    dir: str = ""  # CALL/PUT
    dte: int = 0
    stop_pct: str = ""
    entry_trigger: str = ""
    exit_trigger: str = ""

    # VASS fields
    spread_type: str = ""
    vass_dir: str = ""  # BULLISH/BEARISH
    regime: int = 0
    vass_vix: float = 0
    vass_dte: int = 0
    debit: float = 0
    width: float = 0
    notes: List[str] = field(default_factory=list)


def parse_dt(s: str) -> datetime:
    return datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")


def load_trades(csv_path: Path) -> List[Trade]:
    trades = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            if not row["Entry Time"]:
                continue
            trades.append(
                Trade(
                    entry_time=parse_dt(row["Entry Time"]),
                    exit_time=parse_dt(row["Exit Time"]),
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
            )
    return trades


def extract_strike(symbol: str) -> Optional[float]:
    """Extract strike from option symbol: QQQ   230109P00270000 → 270.0"""
    match = re.search(r"[CP]0*(\d+)0{3,4}$", symbol)
    return float(match.group(1)) if match else None


def classify_trades(trades: List[Trade]) -> Tuple[List[Trade], List[Tuple[Trade, Trade]]]:
    """Separate MICRO (single-leg) from VASS (paired spreads)"""
    spreads_map = defaultdict(list)
    micros = []

    for t in trades:
        if "QQQ" not in t.symbol:
            continue
        entry_date = t.entry_time.date()
        exit_date = t.exit_time.date()
        key = (entry_date, exit_date)
        spreads_map[key].append(t)

    # Group spreads (2 legs with same entry/exit date)
    paired_spreads = []
    used = set()

    for key, group in spreads_map.items():
        if len(group) >= 2:
            # Sort by direction (Buy first)
            group.sort(key=lambda x: x.direction == "Sell")
            for i in range(0, len(group) - 1, 2):
                if i + 1 < len(group):
                    paired_spreads.append((group[i], group[i + 1]))
                    used.add(id(group[i]))
                    used.add(id(group[i + 1]))

    # Remaining are MICRO
    for key, group in spreads_map.items():
        for t in group:
            if id(t) not in used:
                micros.append(t)

    return micros, paired_spreads


def search_logs(
    log_lines: List[str], pattern: str, start_dt: datetime, end_dt: datetime
) -> List[str]:
    """Search for pattern in log lines within date range"""
    results = []
    for line in log_lines:
        if pattern not in line:
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m:
            log_dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            if start_dt <= log_dt <= end_dt:
                results.append(line)
    return results


def extract_micro_context(trade: Trade, logs: List[str]) -> None:
    """Extract MICRO context from logs"""
    strike = extract_strike(trade.symbol)

    # Search for INTRADAY_SIGNAL around entry (±3 hours)
    search_start = trade.entry_time - timedelta(hours=6)
    search_end = trade.entry_time + timedelta(hours=1)

    for line in logs:
        if "INTRADAY_SIGNAL:" not in line:
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if not m:
            continue
        log_dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")

        if not (search_start <= log_dt <= search_end):
            continue

        # Check if strike matches
        if strike:
            strike_match = re.search(r"K=([\d.]+)", line)
            if strike_match and abs(float(strike_match.group(1)) - strike) > 1:
                continue

        trade.engine = "MICRO"

        if "ITM_MOM" in line:
            trade.strategy = "ITM_MOMENTUM"
        elif "DEBIT_FADE" in line:
            trade.strategy = "DEBIT_FADE"

        # Extract fields
        if m := re.search(r"Regime=(\w+)", line):
            trade.micro_regime = m.group(1)
        if m := re.search(r"Score=(\d+)", line):
            trade.regime_score = int(m.group(1))
        if m := re.search(r"VIX=([\d.]+)\s+\((\w+)\)", line):
            trade.vix = float(m.group(1))
            trade.vix_dir = m.group(2)
        if m := re.search(r"QQQ=([A-Z_]+)\s+\(([\d.+%-]+)\)", line):
            trade.qqq_move = f"{m.group(1)}"
        if "PUT x" in line:
            trade.dir = "PUT"
        elif "CALL x" in line:
            trade.dir = "CALL"
        if m := re.search(r"DTE=(\d+)", line):
            trade.dte = int(m.group(1))
        if m := re.search(r"Stop=(\d+)%", line):
            trade.stop_pct = f"{m.group(1)}%"

        trade.entry_trigger = f"INTRADAY at {log_dt.strftime('%H:%M')}"
        break

    # Search for exit trigger
    exit_start = trade.exit_time - timedelta(minutes=10)
    exit_end = trade.exit_time + timedelta(minutes=10)

    for line in logs:
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if not m:
            continue
        log_dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")

        if exit_start <= log_dt <= exit_end:
            if "OCO_STOP" in line or "StopMarket" in line:
                trade.exit_trigger = "STOP"
                break
            elif "OCO_PROFIT" in line:
                trade.exit_trigger = "PROFIT"
                break
            elif "INTRADAY_FORCE_EXIT:" in line:
                trade.exit_trigger = "FORCE_15:25"
                break
            elif "PREMARKET_STALE" in line:
                trade.exit_trigger = "STALE"
                trade.notes.append("Orphan")
                break


def extract_vass_context(leg1: Trade, leg2: Trade, logs: List[str]) -> None:
    """Extract VASS context for a spread"""
    entry_date = leg1.entry_time.date()

    for line in logs:
        if "ENTRY_SIGNAL" not in line:
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", line)
        if not m:
            continue
        if datetime.strptime(m.group(1), "%Y-%m-%d").date() != entry_date:
            continue

        leg1.engine = leg2.engine = "VASS"

        if "BULL_CALL" in line:
            leg1.spread_type = leg2.spread_type = "BULL_CALL_DEBIT"
            leg1.vass_dir = leg2.vass_dir = "BULLISH"
        elif "BEAR_PUT" in line:
            leg1.spread_type = leg2.spread_type = "BEAR_PUT_DEBIT"
            leg1.vass_dir = leg2.vass_dir = "BEARISH"
        elif "BEAR_CALL" in line:
            leg1.spread_type = leg2.spread_type = "BEAR_CALL_CREDIT"
            leg1.vass_dir = leg2.vass_dir = "BEARISH"
        elif "BULL_PUT" in line:
            leg1.spread_type = leg2.spread_type = "BULL_PUT_CREDIT"
            leg1.vass_dir = leg2.vass_dir = "BULLISH"

        if m := re.search(r"Regime=(\d+)", line):
            leg1.regime = leg2.regime = int(m.group(1))
        if m := re.search(r"VIX=([\d.]+)", line):
            leg1.vass_vix = leg2.vass_vix = float(m.group(1))
        if m := re.search(r"DTE=(\d+)", line):
            leg1.vass_dte = leg2.vass_dte = int(m.group(1))
        if m := re.search(r"Debit=\$([\d.]+)", line):
            leg1.debit = leg2.debit = float(m.group(1))
        if m := re.search(r"MaxProfit=\$([\d.]+)", line):
            leg1.width = leg2.width = leg1.debit + float(m.group(1))
        if m := re.search(r"Width=\$([\d.]+)", line):
            leg1.width = leg2.width = float(m.group(1))

        leg1.entry_trigger = leg2.entry_trigger = "SPREAD_SIGNAL"
        break

    # Exit trigger
    exit_date = leg1.exit_time.date()
    for line in logs:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", line)
        if not m:
            continue
        if datetime.strptime(m.group(1), "%Y-%m-%d").date() != exit_date:
            continue

        if "STOP_LOSS" in line:
            leg1.exit_trigger = leg2.exit_trigger = "STOP"
            break
        elif "STRESS" in line:
            leg1.exit_trigger = leg2.exit_trigger = "STRESS"
            break
        elif "PROFIT_TARGET" in line:
            leg1.exit_trigger = leg2.exit_trigger = "PROFIT"
            break
        elif "TRAIL" in line:
            leg1.exit_trigger = leg2.exit_trigger = "TRAIL"
            break
        elif "DTE_EXIT" in line:
            leg1.exit_trigger = leg2.exit_trigger = "DTE"
            break
        elif "HARD_STOP" in line:
            leg1.exit_trigger = leg2.exit_trigger = "HARD_STOP"
            break
        elif "RECONCILED" in line:
            leg1.exit_trigger = leg2.exit_trigger = "RECONCILED"
            break


def calc_hold(entry: datetime, exit: datetime) -> str:
    delta = exit - entry
    if delta.total_seconds() < 24 * 3600:
        h = int(delta.total_seconds() // 3600)
        m = int((delta.total_seconds() % 3600) // 60)
        return f"{h}h{m}m"
    return f"{delta.total_seconds()/(24*3600):.1f}d"


def main():
    base = Path("docs/audits/logs/stage10.1")

    print("Loading trades...")
    trades = load_trades(base / "V10_1_Fullyear_2023_trades.csv")
    print(f"  {len(trades)} trades")

    print("Classifying...")
    micros, spreads = classify_trades(trades)
    print(f"  {len(micros)} MICRO trades")
    print(f"  {len(spreads)} VASS spreads")

    print("Loading logs...")
    with open(base / "V10_1_Fullyear_2023_logs.txt") as f:
        logs = [line.strip() for line in f]
    print(f"  {len(logs)} log lines")

    print("Extracting context...")
    for i, t in enumerate(micros, 1):
        if i % 20 == 0:
            print(f"  MICRO {i}/{len(micros)}...")
        extract_micro_context(t, logs)

    for i, (l1, l2) in enumerate(spreads, 1):
        if i % 20 == 0:
            print(f"  VASS {i}/{len(spreads)}...")
        extract_vass_context(l1, l2, logs)

    print("Generating report...")
    out = base / "V10_1_Fullyear_2023_TRADE_DETAIL_REPORT.md"

    with open(out, "w") as f:
        f.write("# V10.1 Full Year 2023 - Complete Trade Detail Report\n\n")
        f.write(f"**Period:** Jan-Dec 2023  \n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")

        # Part 1: VASS
        f.write("---\n\n# Part 1: VASS Spread Trades\n\n")
        f.write(
            "| # | Entry | Exit | Type | Regime | VIX | DTE | Debit | Width | D/W% | Entry | Exit | Hold | Net P&L | % | W/L |\n"
        )
        f.write(
            "|---|-------|------|------|--------|-----|-----|-------|-------|------|-------|------|------|---------|---|-----|\n"
        )

        for i, (l1, l2) in enumerate(spreads, 1):
            net_pnl = l1.pnl + l2.pnl
            hold = calc_hold(l1.entry_time, l1.exit_time)
            dw = (l1.debit / l1.width * 100) if l1.width > 0 else 0
            pnl_pct = (net_pnl / (l1.debit * l1.quantity * 100)) * 100 if l1.debit > 0 else 0
            wl = "W" if net_pnl > 0 else "L"

            f.write(
                f"| {i} | {l1.entry_time.strftime('%m/%d')} | {l1.exit_time.strftime('%m/%d')} | "
                f"{l1.spread_type} | {l1.regime} | {l1.vass_vix:.1f} | {l1.vass_dte} | "
                f"${l1.debit:.2f} | ${l1.width:.2f} | {dw:.0f}% | "
                f"{l1.entry_trigger} | {l1.exit_trigger} | {hold} | "
                f"${net_pnl:,.0f} | {pnl_pct:.0f}% | {wl} |\n"
            )

        # VASS stats
        wins = sum(1 for l1, l2 in spreads if (l1.pnl + l2.pnl) > 0)
        total_pnl = sum(l1.pnl + l2.pnl for l1, l2 in spreads)
        wr = wins / len(spreads) * 100 if spreads else 0

        f.write(
            f"\n**Summary:** {len(spreads)} spreads | {wins}W-{len(spreads)-wins}L | WR={wr:.1f}% | P&L=${total_pnl:,.0f}\n\n"
        )

        # Part 2: MICRO
        f.write("---\n\n# Part 2: MICRO Intraday Trades\n\n")
        f.write(
            "| # | Date | Entry | Exit | Strat | Dir | Regime | Sc | VIX | VDir | Entry | Exit | Hold | P&L | % | W/L |\n"
        )
        f.write(
            "|---|------|-------|------|-------|-----|--------|-----|-----|------|-------|------|------|-----|---|-----|\n"
        )

        for i, t in enumerate(micros, 1):
            hold = calc_hold(t.entry_time, t.exit_time)
            pnl_pct = (
                ((t.exit_price - t.entry_price) / t.entry_price * 100)
                if t.direction == "Buy"
                else 0
            )
            wl = "W" if t.is_win else "L"

            f.write(
                f"| {i} | {t.entry_time.strftime('%m/%d')} | {t.entry_time.strftime('%H:%M')} | "
                f"{t.exit_time.strftime('%H:%M')} | {t.strategy[:10]} | {t.dir} | "
                f"{t.micro_regime[:12]} | {t.regime_score} | {t.vix:.1f} | {t.vix_dir[:8]} | "
                f"{t.entry_trigger.split()[-1] if t.entry_trigger else 'N/A'} | "
                f"{t.exit_trigger[:8] if t.exit_trigger else 'N/A'} | {hold} | "
                f"${t.pnl:.0f} | {pnl_pct:.0f}% | {wl} |\n"
            )

        # MICRO stats
        m_wins = sum(1 for t in micros if t.is_win)
        m_pnl = sum(t.pnl for t in micros)
        m_wr = m_wins / len(micros) * 100 if micros else 0

        f.write(
            f"\n**Summary:** {len(micros)} trades | {m_wins}W-{len(micros)-m_wins}L | WR={m_wr:.1f}% | P&L=${m_pnl:,.0f}\n\n"
        )

        # Regime breakdown
        f.write("### By Regime\n\n")
        regime_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
        for t in micros:
            if t.micro_regime:
                regime_stats[t.micro_regime]["count"] += 1
                if t.is_win:
                    regime_stats[t.micro_regime]["wins"] += 1
                regime_stats[t.micro_regime]["pnl"] += t.pnl

        f.write("| Regime | Trades | Wins | WR% | P&L |\n")
        f.write("|--------|--------|------|-----|-----|\n")
        for regime in sorted(regime_stats.keys()):
            s = regime_stats[regime]
            wr = s["wins"] / s["count"] * 100 if s["count"] > 0 else 0
            f.write(f"| {regime} | {s['count']} | {s['wins']} | {wr:.0f}% | ${s['pnl']:.0f} |\n")

    print(f"\n✅ Done: {out}")
    print(
        f"  MICRO: {len(micros)} trades, {m_wins}W-{len(micros)-m_wins}L, {m_wr:.1f}% WR, ${m_pnl:,.0f}"
    )
    print(
        f"  VASS: {len(spreads)} spreads, {wins}W-{len(spreads)-wins}L, {wr:.1f}% WR, ${total_pnl:,.0f}"
    )


if __name__ == "__main__":
    main()
