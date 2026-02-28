#!/usr/bin/env python3
"""
Simulate replacing MICRO_OTM_MOMENTUM trades with synthetic ITM delta-0.60 trades.

Design goals:
- Use trades.csv as source of truth for realized P&L and timestamps.
- Keep original entry/exit timestamps (instrument substitution only).
- Apply conservative option repricing:
  1) estimate underlying move from observed OTM option move and observed delta proxy
  2) reprice synthetic ITM option at fixed target delta
  3) subtract theta decay and slippage/friction
- Produce trade-level CSV + markdown summary report.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

OPTION_SYMBOL_RE = re.compile(r"^\s*([A-Z]+)\s+(\d{6})([CP])(\d{8})\s*$")
INTRADAY_SIGNAL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) INTRADAY_SIGNAL: .*?"
    r"\| (?P<direction>CALL|PUT) x(?P<qty>\d+) "
    r"\| Δ=(?P<delta>\d+(?:\.\d+)?) K=(?P<strike>\d+(?:\.\d+)?) DTE=(?P<dte>-?\d+)"
)
NY_TZ = ZoneInfo("America/New_York")


@dataclass
class OptionSymbol:
    underlying: str
    expiry: datetime
    right: str
    strike: float


@dataclass
class IntradaySignal:
    ts: datetime
    direction: str
    qty: int
    delta: float
    strike: float
    dte: int


@dataclass
class TradeRow:
    entry_time: datetime
    exit_time: datetime
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    fees: float
    is_win: int
    order_ids: List[str]
    entry_tag: str
    option: OptionSymbol


@dataclass
class SimResult:
    entry_time: datetime
    exit_time: datetime
    symbol: str
    contracts_used: int
    hold_minutes: int
    actual_otm_pnl: float
    sim_itm_pnl: float
    pnl_delta: float
    actual_return_pct: float
    sim_return_pct: float
    theta_haircut: float
    slippage_haircut: float
    confidence: str
    observed_delta: float
    delta_source: str
    direction: str
    strike: float
    expiry: str
    dte_bucket: str
    month: str
    actual_pnl_1c: float
    sim_pnl_1c: float


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_option_symbol(symbol: str) -> Optional[OptionSymbol]:
    m = OPTION_SYMBOL_RE.match(symbol)
    if not m:
        return None
    underlying = m.group(1)
    expiry = datetime.strptime(m.group(2), "%y%m%d").replace(tzinfo=timezone.utc)
    right = "CALL" if m.group(3) == "C" else "PUT"
    strike = int(m.group(4)) / 1000.0
    return OptionSymbol(underlying=underlying, expiry=expiry, right=right, strike=strike)


def parse_order_ids(raw: str) -> List[str]:
    return [x.strip() for x in re.split(r"[;,]", raw or "") if x.strip()]


def load_orders(orders_file: Path) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    with orders_file.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            oid = str(row.get("ID", "")).strip()
            if oid:
                out[oid] = row
    return out


def load_micro_otm_trades(
    trades_file: Path,
    orders_by_id: Dict[str, Dict[str, str]],
    replacement_scope: str,
) -> List[TradeRow]:
    rows: List[TradeRow] = []
    with trades_file.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            order_ids = parse_order_ids(str(row.get("Order IDs", "")))
            if not order_ids:
                continue
            entry_id = order_ids[0]
            entry_tag = str(orders_by_id.get(entry_id, {}).get("Tag", "") or "")
            if replacement_scope.upper() not in entry_tag.upper():
                continue

            option = parse_option_symbol(str(row.get("Symbols", "")).strip().strip('"'))
            if option is None:
                continue

            qty = int(abs(float(row.get("Quantity", "0") or 0)))
            if qty <= 0:
                continue

            rows.append(
                TradeRow(
                    entry_time=parse_iso(str(row.get("Entry Time", "")).strip()),
                    exit_time=parse_iso(str(row.get("Exit Time", "")).strip()),
                    symbol=str(row.get("Symbols", "")).strip().strip('"'),
                    direction=option.right,
                    entry_price=float(row.get("Entry Price", "0") or 0),
                    exit_price=float(row.get("Exit Price", "0") or 0),
                    quantity=qty,
                    pnl=float(row.get("P&L", "0") or 0),
                    fees=float(row.get("Fees", "0") or 0),
                    is_win=int(float(row.get("IsWin", "0") or 0)),
                    order_ids=order_ids,
                    entry_tag=entry_tag,
                    option=option,
                )
            )
    rows.sort(key=lambda r: r.entry_time)
    return rows


def load_intraday_signals(log_file: Path) -> List[IntradaySignal]:
    out: List[IntradaySignal] = []
    if not log_file.exists():
        return out
    with log_file.open(errors="ignore") as f:
        for line in f:
            m = INTRADAY_SIGNAL_RE.search(line.strip())
            if not m:
                continue
            # Log timestamps are in exchange-local time; normalize to UTC for joins.
            ts = (
                datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=NY_TZ)
                .astimezone(timezone.utc)
            )
            out.append(
                IntradaySignal(
                    ts=ts,
                    direction=m.group("direction"),
                    qty=int(m.group("qty")),
                    delta=float(m.group("delta")),
                    strike=float(m.group("strike")),
                    dte=int(m.group("dte")),
                )
            )
    return out


def build_signals_by_day(signals: Iterable[IntradaySignal]) -> Dict[str, List[IntradaySignal]]:
    out: Dict[str, List[IntradaySignal]] = defaultdict(list)
    for s in signals:
        out[s.ts.strftime("%Y-%m-%d")].append(s)
    return out


def match_signal(
    trade: TradeRow,
    signals_by_day: Dict[str, List[IntradaySignal]],
    max_minutes: int = 15,
) -> Tuple[Optional[IntradaySignal], str]:
    day = trade.entry_time.strftime("%Y-%m-%d")
    cands = signals_by_day.get(day, [])
    if not cands:
        return None, "LOW"

    best: Optional[IntradaySignal] = None
    best_score = float("inf")
    for s in cands:
        if s.direction != trade.direction:
            continue
        if s.qty != trade.quantity:
            continue
        minutes = abs((s.ts - trade.entry_time).total_seconds()) / 60.0
        if minutes > max_minutes:
            continue
        strike_diff = abs(s.strike - trade.option.strike)
        score = minutes + (strike_diff * 8.0)
        if score < best_score:
            best_score = score
            best = s

    if best is None:
        return None, "LOW"

    minutes = abs((best.ts - trade.entry_time).total_seconds()) / 60.0
    strike_diff = abs(best.strike - trade.option.strike)
    if minutes <= 3.0 and strike_diff <= 0.75:
        return best, "HIGH"
    return best, "MEDIUM"


def infer_observed_delta(trade: TradeRow, matched: Optional[IntradaySignal]) -> Tuple[float, str]:
    if matched is not None:
        return max(0.10, min(0.95, float(matched.delta))), "LOG_MATCH"

    # Strategy-aware fallback: MICRO_OTM is configured around 0.35-0.50.
    # Use a mildly conservative midpoint.
    return 0.425, "FALLBACK_MICRO_OTM_MID"


def classify_dte_bucket(trade: TradeRow, matched: Optional[IntradaySignal]) -> Tuple[str, int]:
    if matched is not None:
        dte = matched.dte
    else:
        # Calendar-day proxy only as fallback.
        dte = int((trade.option.expiry.date() - trade.entry_time.date()).days)

    if dte <= 0:
        return "0DTE", dte
    if dte == 1:
        return "1DTE", dte
    return "2DTE_PLUS", dte


def theta_rate_per_hour(dte_bucket: str) -> float:
    # Conservative rates for intraday options:
    # 0DTE highest decay, 1DTE moderate, 2DTE+ lower.
    if dte_bucket == "0DTE":
        return 0.060
    if dte_bucket == "1DTE":
        return 0.040
    return 0.025


def _max_drawdown_from_pnl_sequence(values: List[float]) -> float:
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
        dd = cum - peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _simulate_trade(
    trade: TradeRow,
    observed_delta: float,
    dte_bucket: str,
    target_delta: float,
    theta_mult: float,
    slip_mult: float,
    entry_scale: float,
) -> Tuple[float, float, float, float, float]:
    hold_minutes = max(1, int(round((trade.exit_time - trade.entry_time).total_seconds() / 60.0)))
    hold_hours = hold_minutes / 60.0

    # First-order underlying move estimate from observed option move.
    observed_option_move = trade.exit_price - trade.entry_price
    underlying_move_est = observed_option_move / max(0.20, observed_delta)

    # Synthetic ITM entry premium proxy.
    itm_entry = max(
        0.25,
        trade.entry_price * (target_delta / max(0.20, observed_delta)) * entry_scale,
    )

    # Delta-driven move.
    itm_move_raw = target_delta * underlying_move_est

    # Theta decay haircut.
    theta_rate = theta_rate_per_hour(dte_bucket) * theta_mult
    theta_abs = itm_entry * theta_rate * hold_hours

    itm_exit = max(0.01, itm_entry + itm_move_raw - theta_abs)
    sim_return_pct = (itm_exit - itm_entry) / itm_entry if itm_entry > 0 else 0.0

    gross_per_contract = (itm_exit - itm_entry) * 100.0
    gross_total = gross_per_contract * trade.quantity

    fees_per_contract = (trade.fees / trade.quantity) if trade.quantity > 0 else 0.0
    fees_total = fees_per_contract * trade.quantity

    # Round-trip slippage haircut (entry + exit), conservative floor.
    per_leg_slip = max(0.05, itm_entry * 0.03 * slip_mult)
    slippage_per_contract = per_leg_slip * 2.0 * 100.0
    slippage_total = slippage_per_contract * trade.quantity

    sim_pnl = gross_total - fees_total - slippage_total
    return sim_pnl, sim_return_pct, theta_abs, (slippage_total / trade.quantity), itm_entry


def run_simulation(
    trades: List[TradeRow],
    signals_by_day: Dict[str, List[IntradaySignal]],
    target_delta: float,
    theta_mult: float,
    slip_mult: float,
    entry_scale: float = 1.08,
) -> List[SimResult]:
    out: List[SimResult] = []
    for t in trades:
        matched, confidence = match_signal(t, signals_by_day)
        obs_delta, delta_source = infer_observed_delta(t, matched)
        dte_bucket, _ = classify_dte_bucket(t, matched)
        sim_pnl, sim_ret, theta_abs, slip_per_contract, _ = _simulate_trade(
            trade=t,
            observed_delta=obs_delta,
            dte_bucket=dte_bucket,
            target_delta=target_delta,
            theta_mult=theta_mult,
            slip_mult=slip_mult,
            entry_scale=entry_scale,
        )

        hold_minutes = max(1, int(round((t.exit_time - t.entry_time).total_seconds() / 60.0)))
        actual_return = (t.exit_price - t.entry_price) / t.entry_price if t.entry_price > 0 else 0.0
        actual_1c = t.pnl / t.quantity if t.quantity > 0 else 0.0
        sim_1c = sim_pnl / t.quantity if t.quantity > 0 else 0.0

        out.append(
            SimResult(
                entry_time=t.entry_time,
                exit_time=t.exit_time,
                symbol=t.symbol,
                contracts_used=t.quantity,
                hold_minutes=hold_minutes,
                actual_otm_pnl=t.pnl,
                sim_itm_pnl=sim_pnl,
                pnl_delta=(sim_pnl - t.pnl),
                actual_return_pct=actual_return,
                sim_return_pct=sim_ret,
                theta_haircut=theta_abs,
                slippage_haircut=slip_per_contract,
                confidence=confidence,
                observed_delta=obs_delta,
                delta_source=delta_source,
                direction=t.direction,
                strike=t.option.strike,
                expiry=t.option.expiry.strftime("%Y-%m-%d"),
                dte_bucket=dte_bucket,
                month=t.entry_time.strftime("%Y-%m"),
                actual_pnl_1c=actual_1c,
                sim_pnl_1c=sim_1c,
            )
        )

    out.sort(key=lambda r: r.entry_time)
    return out


def aggregate(results: List[SimResult]) -> Dict[str, float]:
    actual = [r.actual_otm_pnl for r in results]
    sim = [r.sim_itm_pnl for r in results]
    delta = [r.pnl_delta for r in results]
    wins_actual = sum(1 for v in actual if v > 0)
    wins_sim = sum(1 for v in sim if v > 0)
    n = len(results)
    return {
        "count": float(n),
        "actual_total": sum(actual),
        "sim_total": sum(sim),
        "delta_total": sum(delta),
        "actual_win_rate": (wins_actual / n) if n else 0.0,
        "sim_win_rate": (wins_sim / n) if n else 0.0,
        "actual_avg": (sum(actual) / n) if n else 0.0,
        "sim_avg": (sum(sim) / n) if n else 0.0,
        "actual_median": median(actual) if n else 0.0,
        "sim_median": median(sim) if n else 0.0,
        "actual_max_dd": _max_drawdown_from_pnl_sequence(actual),
        "sim_max_dd": _max_drawdown_from_pnl_sequence(sim),
    }


def grouped_totals(results: List[SimResult], key_fn) -> List[Tuple[str, int, float, float, float]]:
    buckets: Dict[str, List[SimResult]] = defaultdict(list)
    for r in results:
        buckets[key_fn(r)].append(r)
    out = []
    for k in sorted(buckets.keys()):
        rows = buckets[k]
        actual = sum(x.actual_otm_pnl for x in rows)
        sim = sum(x.sim_itm_pnl for x in rows)
        out.append((k, len(rows), actual, sim, sim - actual))
    return out


def _fmt_money(v: float) -> str:
    return f"${v:,.0f}"


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def write_csv(out_file: Path, results: List[SimResult], delta_target: float) -> None:
    fieldnames = [
        "entry_time",
        "exit_time",
        "symbol",
        "contracts_used",
        "hold_minutes",
        "actual_otm_pnl",
        f"sim_itm{int(round(delta_target * 100))}_pnl",
        "pnl_delta",
        "actual_return_pct",
        "sim_return_pct",
        "theta_haircut",
        "slippage_haircut",
        "confidence",
        "observed_delta",
        "delta_source",
        "direction",
        "strike",
        "expiry",
        "dte_bucket",
        "month",
        "actual_pnl_1c",
        "sim_pnl_1c",
    ]
    with out_file.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            w.writerow(
                {
                    "entry_time": r.entry_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "exit_time": r.exit_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "symbol": r.symbol,
                    "contracts_used": r.contracts_used,
                    "hold_minutes": r.hold_minutes,
                    "actual_otm_pnl": f"{r.actual_otm_pnl:.2f}",
                    f"sim_itm{int(round(delta_target * 100))}_pnl": f"{r.sim_itm_pnl:.2f}",
                    "pnl_delta": f"{r.pnl_delta:.2f}",
                    "actual_return_pct": f"{r.actual_return_pct:.6f}",
                    "sim_return_pct": f"{r.sim_return_pct:.6f}",
                    "theta_haircut": f"{r.theta_haircut:.6f}",
                    "slippage_haircut": f"{r.slippage_haircut:.6f}",
                    "confidence": r.confidence,
                    "observed_delta": f"{r.observed_delta:.4f}",
                    "delta_source": r.delta_source,
                    "direction": r.direction,
                    "strike": f"{r.strike:.3f}",
                    "expiry": r.expiry,
                    "dte_bucket": r.dte_bucket,
                    "month": r.month,
                    "actual_pnl_1c": f"{r.actual_pnl_1c:.4f}",
                    "sim_pnl_1c": f"{r.sim_pnl_1c:.4f}",
                }
            )


def write_report(
    out_file: Path,
    results: List[SimResult],
    baseline: Dict[str, float],
    sensitivities: Dict[str, Dict[str, float]],
    delta_target: float,
) -> None:
    top_up = sorted(results, key=lambda r: r.pnl_delta, reverse=True)[:10]
    top_down = sorted(results, key=lambda r: r.pnl_delta)[:10]

    by_month = grouped_totals(results, key_fn=lambda r: r.month)
    by_dte = grouped_totals(results, key_fn=lambda r: r.dte_bucket)
    by_hold = grouped_totals(
        results,
        key_fn=lambda r: (
            "0-30m"
            if r.hold_minutes <= 30
            else "31-60m"
            if r.hold_minutes <= 60
            else "61-120m"
            if r.hold_minutes <= 120
            else ">120m"
        ),
    )
    by_conf = grouped_totals(results, key_fn=lambda r: r.confidence)

    with out_file.open("w") as f:
        f.write(f"# MICRO OTM -> ITM Δ={delta_target:.2f} Simulation Report\n\n")
        f.write("## Executive Summary\n")
        f.write(
            f"- Trades simulated: **{int(baseline['count'])}** (`MICRO_OTM_MOMENTUM` only, stage12.7)\n"
        )
        f.write(
            f"- Actual OTM P&L: **{_fmt_money(baseline['actual_total'])}** | "
            f"Sim ITM P&L: **{_fmt_money(baseline['sim_total'])}** | "
            f"Delta: **{_fmt_money(baseline['delta_total'])}**\n"
        )
        f.write(
            f"- Win rate: Actual **{_fmt_pct(baseline['actual_win_rate'])}** vs "
            f"Sim **{_fmt_pct(baseline['sim_win_rate'])}**\n"
        )
        f.write(
            f"- Max drawdown proxy (trade-sequence cumulative): "
            f"Actual **{_fmt_money(baseline['actual_max_dd'])}** vs "
            f"Sim **{_fmt_money(baseline['sim_max_dd'])}**\n\n"
        )

        f.write("## Method and Assumptions\n")
        f.write("- Entry/exit timestamps are unchanged from realized trades.\n")
        f.write("- Observed underlying move estimate: `dS ~= dOption / observed_delta`.\n")
        f.write(f"- Synthetic ITM repricing uses fixed `delta={delta_target:.2f}`.\n")
        f.write("- Conservative theta decay (per hour): 0DTE=6.0%, 1DTE=4.0%, 2DTE+=2.5%.\n")
        f.write("- Slippage haircut: 3% per leg with minimum $0.05 per leg.\n")
        f.write("- Fees use per-trade realized fee ratio from trades.csv.\n")
        f.write("- Confidence uses log-match quality for observed delta (HIGH/MEDIUM/LOW).\n\n")

        f.write("## Portfolio Metrics\n\n")
        f.write("| Metric | Actual OTM | Sim ITM | Delta |\n")
        f.write("|---|---:|---:|---:|\n")
        f.write(
            f"| Total P&L | {_fmt_money(baseline['actual_total'])} | "
            f"{_fmt_money(baseline['sim_total'])} | {_fmt_money(baseline['delta_total'])} |\n"
        )
        f.write(
            f"| Avg P&L / trade | {_fmt_money(baseline['actual_avg'])} | "
            f"{_fmt_money(baseline['sim_avg'])} | {_fmt_money(baseline['sim_avg'] - baseline['actual_avg'])} |\n"
        )
        f.write(
            f"| Median P&L / trade | {_fmt_money(baseline['actual_median'])} | "
            f"{_fmt_money(baseline['sim_median'])} | {_fmt_money(baseline['sim_median'] - baseline['actual_median'])} |\n"
        )
        f.write(
            f"| Win Rate | {_fmt_pct(baseline['actual_win_rate'])} | "
            f"{_fmt_pct(baseline['sim_win_rate'])} | {_fmt_pct(baseline['sim_win_rate'] - baseline['actual_win_rate'])} |\n\n"
        )

        f.write("## Sensitivity (No timing change)\n\n")
        f.write("| Scenario | Sim Total P&L | Delta vs Actual |\n")
        f.write("|---|---:|---:|\n")
        for name, m in sensitivities.items():
            f.write(f"| {name} | {_fmt_money(m['sim_total'])} | {_fmt_money(m['delta_total'])} |\n")
        f.write("\n")

        def write_group_table(title: str, rows: List[Tuple[str, int, float, float, float]]) -> None:
            f.write(f"## {title}\n\n")
            f.write("| Bucket | Trades | Actual OTM | Sim ITM | Delta |\n")
            f.write("|---|---:|---:|---:|---:|\n")
            for k, n, a, s, d in rows:
                f.write(f"| {k} | {n} | {_fmt_money(a)} | {_fmt_money(s)} | {_fmt_money(d)} |\n")
            f.write("\n")

        write_group_table("Monthly Breakdown", by_month)
        write_group_table("DTE Bucket Breakdown", by_dte)
        write_group_table("Hold-Time Breakdown", by_hold)
        write_group_table("Confidence Breakdown", by_conf)

        f.write("## Top Positive Contributors (Sim - Actual)\n\n")
        f.write("| Entry Time | Symbol | Contracts | Actual | Sim | Delta | Confidence |\n")
        f.write("|---|---|---:|---:|---:|---:|---|\n")
        for r in top_up:
            f.write(
                f"| {r.entry_time.strftime('%Y-%m-%d %H:%M')} | {r.symbol} | {r.contracts_used} | "
                f"{_fmt_money(r.actual_otm_pnl)} | {_fmt_money(r.sim_itm_pnl)} | "
                f"{_fmt_money(r.pnl_delta)} | {r.confidence} |\n"
            )
        f.write("\n")

        f.write("## Top Negative Contributors (Sim - Actual)\n\n")
        f.write("| Entry Time | Symbol | Contracts | Actual | Sim | Delta | Confidence |\n")
        f.write("|---|---|---:|---:|---:|---:|---|\n")
        for r in top_down:
            f.write(
                f"| {r.entry_time.strftime('%Y-%m-%d %H:%M')} | {r.symbol} | {r.contracts_used} | "
                f"{_fmt_money(r.actual_otm_pnl)} | {_fmt_money(r.sim_itm_pnl)} | "
                f"{_fmt_money(r.pnl_delta)} | {r.confidence} |\n"
            )
        f.write("\n")


def default_output_paths(stage_dir: Path, run_prefix: str, delta: float) -> Tuple[Path, Path]:
    delta_tag = int(round(delta * 100))
    csv_out = stage_dir / f"{run_prefix}_MICRO_ITM{delta_tag}_SIM.csv"
    md_out = stage_dir / f"{run_prefix}_MICRO_ITM{delta_tag}_SIM_REPORT.md"
    return csv_out, md_out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate replacing MICRO_OTM_MOMENTUM trades with ITM delta target."
    )
    parser.add_argument(
        "--stage-dir",
        default="docs/audits/logs/stage12.7",
        help="Folder containing <run_prefix>_{trades,orders,logs}.",
    )
    parser.add_argument(
        "--run-prefix",
        default="V12.7-FullYear2024-R1",
        help="Base file prefix for trades/orders/logs artifacts.",
    )
    parser.add_argument(
        "--delta",
        type=float,
        default=0.60,
        help="Target ITM delta for synthetic repricing.",
    )
    parser.add_argument(
        "--replacement-scope",
        default="MICRO:MICRO_OTM_MOMENTUM",
        help="Entry order tag filter to select trades for replacement.",
    )
    parser.add_argument("--output-csv", default="", help="Output CSV path.")
    parser.add_argument("--output-report", default="", help="Output markdown report path.")
    args = parser.parse_args()

    stage_dir = Path(args.stage_dir)
    trades_file = stage_dir / f"{args.run_prefix}_trades.csv"
    orders_file = stage_dir / f"{args.run_prefix}_orders.csv"
    logs_file = stage_dir / f"{args.run_prefix}_logs.txt"

    if not trades_file.exists():
        raise SystemExit(f"Missing trades file: {trades_file}")
    if not orders_file.exists():
        raise SystemExit(f"Missing orders file: {orders_file}")

    default_csv, default_report = default_output_paths(stage_dir, args.run_prefix, args.delta)
    output_csv = Path(args.output_csv) if args.output_csv else default_csv
    output_report = Path(args.output_report) if args.output_report else default_report

    orders_by_id = load_orders(orders_file)
    trades = load_micro_otm_trades(trades_file, orders_by_id, args.replacement_scope)
    signals = load_intraday_signals(logs_file)
    signals_by_day = build_signals_by_day(signals)

    if not trades:
        raise SystemExit("No trades matched replacement scope.")

    baseline_results = run_simulation(
        trades=trades,
        signals_by_day=signals_by_day,
        target_delta=float(args.delta),
        theta_mult=1.0,
        slip_mult=1.0,
    )
    baseline = aggregate(baseline_results)

    sensitivities = {
        "Baseline": baseline,
        "Theta -25%": aggregate(
            run_simulation(
                trades, signals_by_day, float(args.delta), theta_mult=0.75, slip_mult=1.0
            )
        ),
        "Theta +25%": aggregate(
            run_simulation(
                trades, signals_by_day, float(args.delta), theta_mult=1.25, slip_mult=1.0
            )
        ),
        "Slippage -25%": aggregate(
            run_simulation(
                trades, signals_by_day, float(args.delta), theta_mult=1.0, slip_mult=0.75
            )
        ),
        "Slippage +25%": aggregate(
            run_simulation(
                trades, signals_by_day, float(args.delta), theta_mult=1.0, slip_mult=1.25
            )
        ),
        "Delta=0.55": aggregate(
            run_simulation(trades, signals_by_day, 0.55, theta_mult=1.0, slip_mult=1.0)
        ),
        "Delta=0.65": aggregate(
            run_simulation(trades, signals_by_day, 0.65, theta_mult=1.0, slip_mult=1.0)
        ),
    }

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    write_csv(output_csv, baseline_results, float(args.delta))
    write_report(output_report, baseline_results, baseline, sensitivities, float(args.delta))

    conf_counts = defaultdict(int)
    for r in baseline_results:
        conf_counts[r.confidence] += 1

    print(f"Selected trades: {len(trades)}")
    print(
        f"Actual OTM total: {baseline['actual_total']:.2f} | "
        f"Sim ITM total: {baseline['sim_total']:.2f} | "
        f"Delta: {baseline['delta_total']:.2f}"
    )
    print(
        "Confidence mix: "
        + ", ".join(f"{k}={v}" for k, v in sorted(conf_counts.items(), key=lambda x: x[0]))
    )
    print(f"Wrote CSV: {output_csv}")
    print(f"Wrote report: {output_report}")


if __name__ == "__main__":
    main()
