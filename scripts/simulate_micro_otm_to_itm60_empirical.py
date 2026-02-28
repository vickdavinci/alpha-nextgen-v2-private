#!/usr/bin/env python3
"""
Empirical-calibrated MICRO->ITM simulation.

Calibration source:
- ITM_MOMENTUM trades from the same run (trades.csv + entry-order tag).

Simulation target:
- MICRO_OTM_MOMENTUM trades from the same run.

Method:
- Build empirical per-contract P&L distributions from ITM trades by:
  (direction, hold_bucket)
- For each MICRO trade, map to the closest empirical bucket and scale by
  its actual contract count.
- Produce median/p25/p75 scenarios and markdown summary.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Dict, Iterable, List, Optional, Tuple

OPTION_SYMBOL_RE = re.compile(r"^\s*([A-Z]+)\s+(\d{6})([CP])(\d{8})\s*$")


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    symbol: str
    direction: str
    quantity: int
    pnl: float
    is_win: int
    entry_tag: str
    hold_minutes: int
    hold_bucket: str


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_symbol_direction(symbol: str) -> Optional[str]:
    m = OPTION_SYMBOL_RE.match(symbol.strip().strip('"'))
    if not m:
        return None
    return "CALL" if m.group(3) == "C" else "PUT"


def parse_order_ids(raw: str) -> List[str]:
    return [x.strip() for x in re.split(r"[;,]", raw or "") if x.strip()]


def hold_bucket(minutes: int) -> str:
    if minutes <= 30:
        return "0-30m"
    if minutes <= 60:
        return "31-60m"
    if minutes <= 120:
        return "61-120m"
    return ">120m"


def load_orders_by_id(path: Path) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            oid = str(row.get("ID", "")).strip()
            if oid:
                out[oid] = row
    return out


def load_trades(path: Path, orders_by_id: Dict[str, Dict[str, str]]) -> List[Trade]:
    out: List[Trade] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            order_ids = parse_order_ids(str(row.get("Order IDs", "")))
            if not order_ids:
                continue
            entry_tag = str(orders_by_id.get(order_ids[0], {}).get("Tag", "") or "")
            symbol = str(row.get("Symbols", "")).strip().strip('"')
            direction = parse_symbol_direction(symbol)
            if direction is None:
                continue

            entry_time = parse_iso(str(row.get("Entry Time", "")))
            exit_time = parse_iso(str(row.get("Exit Time", "")))
            qty = int(abs(float(row.get("Quantity", "0") or 0)))
            if qty <= 0:
                continue

            hm = max(1, int(round((exit_time - entry_time).total_seconds() / 60.0)))
            out.append(
                Trade(
                    entry_time=entry_time,
                    exit_time=exit_time,
                    symbol=symbol,
                    direction=direction,
                    quantity=qty,
                    pnl=float(row.get("P&L", "0") or 0),
                    is_win=int(float(row.get("IsWin", "0") or 0)),
                    entry_tag=entry_tag,
                    hold_minutes=hm,
                    hold_bucket=hold_bucket(hm),
                )
            )
    out.sort(key=lambda t: t.entry_time)
    return out


def percentile(sorted_values: List[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = max(0.0, min(1.0, q)) * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def build_itm_calibration(itm_trades: Iterable[Trade]) -> Dict[Tuple[str, str], Dict[str, float]]:
    per_contract: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    per_contract_dir: Dict[str, List[float]] = defaultdict(list)
    per_contract_bucket: Dict[str, List[float]] = defaultdict(list)
    global_vals: List[float] = []

    for t in itm_trades:
        v = t.pnl / t.quantity if t.quantity > 0 else 0.0
        per_contract[(t.direction, t.hold_bucket)].append(v)
        per_contract_dir[t.direction].append(v)
        per_contract_bucket[t.hold_bucket].append(v)
        global_vals.append(v)

    def stat(values: List[float]) -> Dict[str, float]:
        if not values:
            return {"n": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "mean": 0.0}
        s = sorted(values)
        return {
            "n": float(len(values)),
            "p25": percentile(s, 0.25),
            "p50": percentile(s, 0.50),
            "p75": percentile(s, 0.75),
            "mean": sum(values) / len(values),
        }

    out: Dict[Tuple[str, str], Dict[str, float]] = {}
    for k, vals in per_contract.items():
        out[k] = stat(vals)

    # Fallback keys encoded as pseudo tuples.
    for direction, vals in per_contract_dir.items():
        out[(direction, "__ALL_HOLDS__")] = stat(vals)
    for bucket, vals in per_contract_bucket.items():
        out[("__ALL_DIRECTIONS__", bucket)] = stat(vals)
    out[("__ALL_DIRECTIONS__", "__ALL_HOLDS__")] = stat(global_vals)
    return out


def pick_calibration(
    calib: Dict[Tuple[str, str], Dict[str, float]], direction: str, bucket: str
) -> Tuple[Dict[str, float], str]:
    exact = (direction, bucket)
    if exact in calib and calib[exact]["n"] >= 3:
        return calib[exact], "HIGH"
    d_all = (direction, "__ALL_HOLDS__")
    if d_all in calib and calib[d_all]["n"] >= 5:
        return calib[d_all], "MEDIUM"
    b_all = ("__ALL_DIRECTIONS__", bucket)
    if b_all in calib and calib[b_all]["n"] >= 5:
        return calib[b_all], "MEDIUM"
    g = ("__ALL_DIRECTIONS__", "__ALL_HOLDS__")
    return calib.get(g, {"n": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "mean": 0.0}), "LOW"


def fmt_money(v: float) -> str:
    return f"${v:,.0f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage-dir", default="docs/audits/logs/stage12.7")
    ap.add_argument("--run-prefix", default="V12.7-FullYear2024-R1")
    ap.add_argument("--output-csv", default="")
    ap.add_argument("--output-report", default="")
    args = ap.parse_args()

    stage_dir = Path(args.stage_dir)
    prefix = args.run_prefix
    trades_file = stage_dir / f"{prefix}_trades.csv"
    orders_file = stage_dir / f"{prefix}_orders.csv"

    orders = load_orders_by_id(orders_file)
    trades = load_trades(trades_file, orders)
    micro = [t for t in trades if "MICRO:MICRO_OTM_MOMENTUM" in t.entry_tag.upper()]
    itm = [t for t in trades if "ITM:ITM_MOMENTUM" in t.entry_tag.upper()]

    if not micro:
        raise SystemExit("No MICRO OTM trades found.")
    if not itm:
        raise SystemExit("No ITM calibration trades found.")

    calib = build_itm_calibration(itm)

    out_csv = (
        Path(args.output_csv)
        if args.output_csv
        else stage_dir / f"{prefix}_MICRO_ITM60_EMPIRICAL_CAL_SIM.csv"
    )
    out_md = (
        Path(args.output_report)
        if args.output_report
        else stage_dir / f"{prefix}_MICRO_ITM60_EMPIRICAL_CAL_SIM_REPORT.md"
    )

    rows: List[Dict[str, str]] = []
    actual_total = 0.0
    sim_p50_total = 0.0
    sim_p25_total = 0.0
    sim_p75_total = 0.0
    actual_wins = 0
    sim_wins = 0

    for t in micro:
        stats, conf = pick_calibration(calib, t.direction, t.hold_bucket)
        p25_1c = stats["p25"]
        p50_1c = stats["p50"]
        p75_1c = stats["p75"]
        sim_p25 = p25_1c * t.quantity
        sim_p50 = p50_1c * t.quantity
        sim_p75 = p75_1c * t.quantity

        actual_total += t.pnl
        sim_p25_total += sim_p25
        sim_p50_total += sim_p50
        sim_p75_total += sim_p75
        actual_wins += 1 if t.pnl > 0 else 0
        sim_wins += 1 if sim_p50 > 0 else 0

        rows.append(
            {
                "entry_time": t.entry_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "exit_time": t.exit_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "symbol": t.symbol,
                "direction": t.direction,
                "contracts_used": str(t.quantity),
                "hold_minutes": str(t.hold_minutes),
                "hold_bucket": t.hold_bucket,
                "actual_otm_pnl": f"{t.pnl:.2f}",
                "calib_n": str(int(stats["n"])),
                "itm_empirical_p25_1c": f"{p25_1c:.4f}",
                "itm_empirical_p50_1c": f"{p50_1c:.4f}",
                "itm_empirical_p75_1c": f"{p75_1c:.4f}",
                "sim_itm_empirical_p25_pnl": f"{sim_p25:.2f}",
                "sim_itm_empirical_p50_pnl": f"{sim_p50:.2f}",
                "sim_itm_empirical_p75_pnl": f"{sim_p75:.2f}",
                "delta_p50_vs_actual": f"{(sim_p50 - t.pnl):.2f}",
                "confidence": conf,
            }
        )

    with out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    by_conf: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    by_bucket: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_conf[r["confidence"]].append(r)
        by_bucket[r["hold_bucket"]].append(r)

    def sum_col(rr: List[Dict[str, str]], col: str) -> float:
        return sum(float(x[col]) for x in rr)

    with out_md.open("w") as f:
        f.write("# MICRO OTM -> ITM (Empirical Calibrated) Simulation Report\n\n")
        f.write("## Executive Summary\n")
        f.write(f"- Calibration set (actual ITM_MOMENTUM trades): **{len(itm)}**\n")
        f.write(f"- Target set (MICRO_OTM_MOMENTUM trades): **{len(micro)}**\n")
        f.write(f"- Actual OTM total: **{fmt_money(actual_total)}**\n")
        f.write(f"- Sim ITM empirical (P50) total: **{fmt_money(sim_p50_total)}**\n")
        f.write(f"- Delta (P50 - Actual): **{fmt_money(sim_p50_total - actual_total)}**\n")
        f.write(
            f"- P25/P50/P75 envelope: "
            f"**{fmt_money(sim_p25_total)} / {fmt_money(sim_p50_total)} / {fmt_money(sim_p75_total)}**\n"
        )
        f.write(
            f"- Win rate (actual vs empirical P50): "
            f"**{(actual_wins/len(micro))*100:.1f}% vs {(sim_wins/len(micro))*100:.1f}%**\n\n"
        )

        f.write("## Confidence Breakdown\n\n")
        f.write("| Confidence | Trades | Actual OTM | Sim P50 | Delta |\n")
        f.write("|---|---:|---:|---:|---:|\n")
        for k in sorted(by_conf.keys()):
            group = by_conf[k]
            a = sum_col(group, "actual_otm_pnl")
            s = sum_col(group, "sim_itm_empirical_p50_pnl")
            f.write(
                f"| {k} | {len(group)} | {fmt_money(a)} | {fmt_money(s)} | {fmt_money(s-a)} |\n"
            )
        f.write("\n")

        f.write("## Hold-Bucket Breakdown\n\n")
        f.write("| Hold Bucket | Trades | Actual OTM | Sim P50 | Delta |\n")
        f.write("|---|---:|---:|---:|---:|\n")
        for k in sorted(by_bucket.keys()):
            group = by_bucket[k]
            a = sum_col(group, "actual_otm_pnl")
            s = sum_col(group, "sim_itm_empirical_p50_pnl")
            f.write(
                f"| {k} | {len(group)} | {fmt_money(a)} | {fmt_money(s)} | {fmt_money(s-a)} |\n"
            )
        f.write("\n")

        top_up = sorted(rows, key=lambda r: float(r["delta_p50_vs_actual"]), reverse=True)[:10]
        top_dn = sorted(rows, key=lambda r: float(r["delta_p50_vs_actual"]))[:10]

        f.write("## Top Positive Contributors (P50 - Actual)\n\n")
        f.write("| Entry Time | Symbol | Qty | Actual | Sim P50 | Delta |\n")
        f.write("|---|---|---:|---:|---:|---:|\n")
        for r in top_up:
            f.write(
                f"| {r['entry_time']} | {r['symbol']} | {r['contracts_used']} | "
                f"{fmt_money(float(r['actual_otm_pnl']))} | "
                f"{fmt_money(float(r['sim_itm_empirical_p50_pnl']))} | "
                f"{fmt_money(float(r['delta_p50_vs_actual']))} |\n"
            )
        f.write("\n")

        f.write("## Top Negative Contributors (P50 - Actual)\n\n")
        f.write("| Entry Time | Symbol | Qty | Actual | Sim P50 | Delta |\n")
        f.write("|---|---|---:|---:|---:|---:|\n")
        for r in top_dn:
            f.write(
                f"| {r['entry_time']} | {r['symbol']} | {r['contracts_used']} | "
                f"{fmt_money(float(r['actual_otm_pnl']))} | "
                f"{fmt_money(float(r['sim_itm_empirical_p50_pnl']))} | "
                f"{fmt_money(float(r['delta_p50_vs_actual']))} |\n"
            )

    print(f"Calibration ITM trades: {len(itm)}")
    print(f"Target MICRO trades: {len(micro)}")
    print(f"Actual OTM total: {actual_total:.2f}")
    print(f"Sim ITM empirical P50 total: {sim_p50_total:.2f}")
    print(f"Delta P50 vs Actual: {sim_p50_total - actual_total:.2f}")
    print(f"Envelope P25/P50/P75: {sim_p25_total:.2f} / {sim_p50_total:.2f} / {sim_p75_total:.2f}")
    print(f"Wrote CSV: {out_csv}")
    print(f"Wrote report: {out_md}")


if __name__ == "__main__":
    main()
