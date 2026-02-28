#!/usr/bin/env python3
"""Regime-conditioned full-year simulation for current code behavior.

This script does NOT run new backtests. It builds a probabilistic model from
existing calibration runs (V10.27, V10.28, V10.29), maps daily market regime
states for 2023/2024 using QQQ+VIX features, and Monte Carlo-simulates yearly
performance distributions.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import random
import re
import statistics
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

STARTING_CAPITAL = 100000.0
DEFAULT_SEED = 42
DEFAULT_ITERATIONS = 7000
KNN_K = 5

CALIBRATION_RUNS = [
    (
        "V10.27",
        Path("docs/audits/logs/stage10.27/V10.27_smoke_JulSep2024_R1_trades.csv"),
        Path("docs/audits/logs/stage10.27/V10.27_smoke_JulSep2024_R1_orders.csv"),
        Path("docs/audits/logs/stage10.27/V10.27_smoke_JulSep2024_R1_logs.txt"),
    ),
    (
        "V10.28",
        Path("docs/audits/logs/stage10.28/V10.28_smoke_JulSep2024_R1_trades.csv"),
        Path("docs/audits/logs/stage10.28/V10.28_smoke_JulSep2024_R1_orders.csv"),
        Path("docs/audits/logs/stage10.28/V10.28_smoke_JulSep2024_R1_logs.txt"),
    ),
    (
        "V10.29",
        Path("docs/audits/logs/stage10.29/V10.29_smoke_JulSep2024_R1_trades.csv"),
        Path("docs/audits/logs/stage10.29/V10.29_smoke_JulSep2024_R1_orders.csv"),
        Path("docs/audits/logs/stage10.29/V10.29_smoke_JulSep2024_R1_logs.txt"),
    ),
]


REGIME_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) REGIME: RegimeState\((?P<state>[^ |]+)"
)


@dataclass(frozen=True)
class TradeSample:
    run: str
    entry_dt: datetime
    exit_dt: datetime
    day: str
    state: str
    engine: str
    strategy: str
    direction: str
    pnl: float
    fee: float
    is_win: int


@dataclass(frozen=True)
class MarketPoint:
    date: str
    qqq_close: float
    qqq_ret_1d: float
    qqq_ret_5d: float
    vix_close: float
    vix_ret_5d: float


def safe_float(value: str) -> float:
    try:
        return float((value or "").strip())
    except Exception:
        return 0.0


def safe_int(value: str) -> int:
    try:
        return int(float((value or "").strip()))
    except Exception:
        return 0


def parse_order_ids(raw: str) -> List[int]:
    return [int(x) for x in re.findall(r"\d+", raw or "")]


def parse_trade_datetime(value: str) -> datetime:
    return datetime.strptime((value or "")[:19], "%Y-%m-%dT%H:%M:%S")


def classify_trade(tag: str, symbol: str) -> Tuple[str, str, str]:
    prefix = (tag or "").strip().split("|", 1)[0]
    if prefix.startswith("VASS:"):
        engine = "VASS"
        strategy = prefix.split(":", 1)[1] or "VASS_UNKNOWN"
    elif prefix.startswith("MICRO:"):
        engine = "MICRO"
        strategy = prefix.split(":", 1)[1] or "MICRO_UNKNOWN"
    elif prefix.startswith("ITM:"):
        engine = "ITM"
        strategy = prefix.split(":", 1)[1] or "ITM_UNKNOWN"
    elif prefix.startswith("HEDGE:"):
        engine = "HEDGE"
        strategy = prefix.split(":", 1)[1] or "HEDGE_UNKNOWN"
    else:
        upper = prefix.upper()
        if "VASS" in upper:
            engine = "VASS"
            strategy = prefix or "VASS_UNKNOWN"
        elif "ITM" in upper:
            engine = "ITM"
            strategy = prefix or "ITM_UNKNOWN"
        elif "MICRO" in upper:
            engine = "MICRO"
            strategy = prefix or "MICRO_UNKNOWN"
        elif "HEDGE" in upper or "PROTECTIVE" in upper:
            engine = "HEDGE"
            strategy = prefix or "HEDGE_UNKNOWN"
        else:
            engine = "UNKNOWN"
            strategy = prefix or "UNKNOWN"

    strategy_upper = strategy.upper()
    sym_tail = (symbol or "").upper()[-12:]
    if engine == "VASS":
        if "BEAR_" in strategy_upper:
            direction = "BEARISH"
        elif "BULL_" in strategy_upper:
            direction = "BULLISH"
        else:
            direction = "UNKNOWN"
    else:
        if "C" in sym_tail:
            direction = "CALL"
        elif "P" in sym_tail:
            direction = "PUT"
        else:
            direction = "UNKNOWN"
    return engine, strategy, direction


def parse_regime_timeline(
    log_path: Path,
) -> Tuple[Dict[str, List[Tuple[datetime, str]]], Dict[str, str]]:
    by_day: Dict[str, List[Tuple[datetime, str]]] = defaultdict(list)
    with log_path.open() as f:
        for line in f:
            m = REGIME_RE.match(line.strip())
            if not m:
                continue
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
            state = m.group("state").strip().upper()
            day = ts.strftime("%Y-%m-%d")
            by_day[day].append((ts, state))
    for day in by_day:
        by_day[day].sort(key=lambda x: x[0])

    daily_label: Dict[str, str] = {}
    for day, values in by_day.items():
        state_0935 = None
        for ts, state in values:
            if ts.strftime("%H:%M") == "09:35":
                state_0935 = state
                break
        daily_label[day] = state_0935 or values[0][1]
    return by_day, daily_label


def regime_at_entry(entry_dt: datetime, day_timeline: Dict[str, List[Tuple[datetime, str]]]) -> str:
    day = entry_dt.strftime("%Y-%m-%d")
    values = day_timeline.get(day)
    if not values:
        return "UNKNOWN"
    current = values[0][1]
    for ts, state in values:
        if ts <= entry_dt:
            current = state
        else:
            break
    return current


def load_trade_samples(
    run_name: str,
    trades_path: Path,
    orders_path: Path,
    log_path: Path,
) -> Tuple[List[TradeSample], Dict[str, str]]:
    day_timeline, day_labels = parse_regime_timeline(log_path)

    orders: Dict[int, Dict[str, str]] = {}
    with orders_path.open() as f:
        reader = csv.DictReader(f)
        has_id = "ID" in (reader.fieldnames or [])
        for idx, row in enumerate(reader, start=1):
            oid = safe_int(row.get("ID", "")) if has_id else idx
            if oid:
                orders[oid] = row

    out: List[TradeSample] = []
    with trades_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry = parse_trade_datetime(row.get("Entry Time", "") or row.get("EntryTime", ""))
            exit_dt = parse_trade_datetime(row.get("Exit Time", "") or row.get("ExitTime", ""))
            symbol = ((row.get("Symbols", "") or row.get("Symbol", "")).strip()).strip('"')
            pnl = safe_float(row.get("P&L", "") or row.get("PnL", ""))
            fee = safe_float(row.get("Fees", ""))
            is_win = safe_int(row.get("IsWin", ""))

            tag = ""
            order_ids = parse_order_ids(row.get("Order IDs", "") or row.get("Order Ids", ""))
            for oid in order_ids:
                order = orders.get(oid)
                if not order:
                    continue
                maybe = (order.get("Tag", "") or "").strip()
                if maybe:
                    tag = maybe
                    break

            engine, strategy, direction = classify_trade(tag, symbol)
            state = regime_at_entry(entry, day_timeline)
            out.append(
                TradeSample(
                    run=run_name,
                    entry_dt=entry,
                    exit_dt=exit_dt,
                    day=entry.strftime("%Y-%m-%d"),
                    state=state,
                    engine=engine,
                    strategy=strategy,
                    direction=direction,
                    pnl=pnl,
                    fee=fee,
                    is_win=is_win,
                )
            )
    return out, day_labels


def fetch_csv(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read().decode("utf-8", "ignore")


def fetch_qqq_stooq() -> Dict[str, float]:
    text = fetch_csv("https://stooq.com/q/d/l/?s=qqq.us&i=d")
    rows = csv.DictReader(io.StringIO(text))
    out: Dict[str, float] = {}
    for row in rows:
        date = row.get("Date", "")
        if not date:
            continue
        close = safe_float(row.get("Close", ""))
        if close > 0:
            out[date] = close
    return out


def fetch_vix_fred() -> Dict[str, float]:
    text = fetch_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS")
    rows = csv.DictReader(io.StringIO(text))
    out: Dict[str, float] = {}
    for row in rows:
        date = row.get("observation_date", "")
        raw = (row.get("VIXCLS", "") or "").strip()
        if not date or raw in {"", "."}:
            continue
        close = safe_float(raw)
        if close > 0:
            out[date] = close
    return out


def build_market_points(
    qqq_close: Dict[str, float], vix_close: Dict[str, float]
) -> Dict[str, MarketPoint]:
    dates = sorted(set(qqq_close) & set(vix_close))
    q_vals = [qqq_close[d] for d in dates]
    v_vals = [vix_close[d] for d in dates]

    points: Dict[str, MarketPoint] = {}
    for i, day in enumerate(dates):
        q = q_vals[i]
        v = v_vals[i]
        q_prev = q_vals[i - 1] if i >= 1 else q_vals[i]
        q_prev5 = q_vals[i - 5] if i >= 5 else q_vals[0]
        v_prev5 = v_vals[i - 5] if i >= 5 else v_vals[0]
        q_ret_1d = (q / q_prev - 1.0) if q_prev else 0.0
        q_ret_5d = (q / q_prev5 - 1.0) if q_prev5 else 0.0
        v_ret_5d = (v / v_prev5 - 1.0) if v_prev5 else 0.0
        points[day] = MarketPoint(
            date=day,
            qqq_close=q,
            qqq_ret_1d=q_ret_1d,
            qqq_ret_5d=q_ret_5d,
            vix_close=v,
            vix_ret_5d=v_ret_5d,
        )
    return points


def feature_vec(p: MarketPoint) -> Tuple[float, float, float, float]:
    return (p.qqq_ret_1d, p.qqq_ret_5d, p.vix_close, p.vix_ret_5d)


def zscore_params(
    vecs: Sequence[Tuple[float, float, float, float]]
) -> Tuple[List[float], List[float]]:
    means = [statistics.mean(v[i] for v in vecs) for i in range(4)]
    stds = []
    for i in range(4):
        st = statistics.pstdev(v[i] for v in vecs)
        stds.append(st if st > 1e-12 else 1.0)
    return means, stds


def zscore(
    vec: Tuple[float, float, float, float], means: Sequence[float], stds: Sequence[float]
) -> Tuple[float, ...]:
    return tuple((vec[i] - means[i]) / stds[i] for i in range(4))


def knn_predict_state(
    target: Tuple[float, ...],
    calib_z: Sequence[Tuple[float, ...]],
    calib_labels: Sequence[str],
    k: int = KNN_K,
) -> str:
    dists = []
    for i, vec in enumerate(calib_z):
        dist2 = sum((target[j] - vec[j]) ** 2 for j in range(4))
        dists.append((dist2, calib_labels[i]))
    dists.sort(key=lambda x: x[0])
    top = dists[: max(1, k)]
    score = defaultdict(float)
    for dist2, label in top:
        score[label] += 1.0 / (math.sqrt(dist2) + 1e-6)
    return max(score.items(), key=lambda x: x[1])[0]


def percentile(sorted_values: Sequence[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(round((len(sorted_values) - 1) * p))
    idx = max(0, min(idx, len(sorted_values) - 1))
    return sorted_values[idx]


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def simulate_year(
    year: int,
    year_days: List[str],
    year_states: Dict[str, str],
    per_state_day_counts: Dict[str, Dict[str, List[int]]],
    per_state_trades: Dict[str, Dict[str, List[TradeSample]]],
    global_trades: Dict[str, List[TradeSample]],
    qqq_return: float,
    iterations: int,
    seed: int,
) -> Dict[str, float]:
    rng = random.Random(seed + year)
    runs = sorted(global_trades.keys())

    net_returns = []
    gross_totals = []
    net_totals = []
    max_dds = []
    sep_totals = []
    outperf = []

    for _ in range(iterations):
        equity = STARTING_CAPITAL
        peak = STARTING_CAPITAL
        path_max_dd = 0.0
        gross_total = 0.0
        net_total = 0.0
        sep_total = 0.0

        for day in year_days:
            state = year_states[day]
            run = rng.choice(runs)

            count_pool = per_state_day_counts.get(state, {}).get(run, [])
            if not count_pool:
                count_pool = per_state_day_counts.get(state, {}).get("ALL", [])
            if not count_pool:
                count_pool = per_state_day_counts.get("GLOBAL", {}).get(run, [0])
            n_trades = rng.choice(count_pool) if count_pool else 0

            day_net = 0.0
            day_gross = 0.0
            for _n in range(n_trades):
                pool = per_state_trades.get(state, {}).get(run, [])
                if not pool:
                    pool = per_state_trades.get(state, {}).get("ALL", [])
                if not pool:
                    pool = global_trades[run]
                if not pool:
                    continue
                trade = rng.choice(pool)
                day_gross += trade.pnl
                day_net += trade.pnl - trade.fee

            gross_total += day_gross
            net_total += day_net
            if day[5:7] == "09":
                sep_total += day_net

            equity += day_net
            peak = max(peak, equity)
            drawdown = (peak - equity) / peak if peak > 0 else 0.0
            path_max_dd = max(path_max_dd, drawdown)

        gross_totals.append(gross_total)
        net_totals.append(net_total)
        ret = net_total / STARTING_CAPITAL
        net_returns.append(ret)
        max_dds.append(path_max_dd)
        sep_totals.append(sep_total)
        outperf.append(ret - qqq_return)

    gross_totals.sort()
    net_totals.sort()
    net_returns.sort()
    max_dds.sort()
    sep_totals.sort()
    outperf.sort()

    positive_prob = sum(1 for r in net_returns if r > 0) / len(net_returns)
    beat_by_1_prob = sum(1 for r in outperf if r > 0.01) / len(outperf)
    within_1_prob = sum(1 for r in outperf if -0.01 <= r <= 0.01) / len(outperf)
    under_by_1_prob = sum(1 for r in outperf if r < -0.01) / len(outperf)

    return {
        "year": year,
        "n_days": len(year_days),
        "qqq_return": qqq_return,
        "median_net_return": percentile(net_returns, 0.50),
        "p10_net_return": percentile(net_returns, 0.10),
        "p90_net_return": percentile(net_returns, 0.90),
        "median_net_pnl": percentile(net_totals, 0.50),
        "p10_net_pnl": percentile(net_totals, 0.10),
        "p90_net_pnl": percentile(net_totals, 0.90),
        "median_max_drawdown": percentile(max_dds, 0.50),
        "p90_max_drawdown": percentile(max_dds, 0.90),
        "positive_prob": positive_prob,
        "beat_qqq_by_1_prob": beat_by_1_prob,
        "within_qqq_plusminus_1_prob": within_1_prob,
        "under_qqq_by_1_prob": under_by_1_prob,
        "median_sep_net_pnl": percentile(sep_totals, 0.50),
        "p10_sep_net_pnl": percentile(sep_totals, 0.10),
        "p90_sep_net_pnl": percentile(sep_totals, 0.90),
    }


def pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def fmt_money(x: float) -> str:
    return f"${x:,.0f}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simulate full-year 2023/2024 from current-code calibration runs"
    )
    parser.add_argument(
        "--output-dir",
        default="docs/audits/logs/stage10.29/simulation_current_code",
        help="Directory for report/csv outputs",
    )
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_samples: List[TradeSample] = []
    day_labels_by_run: Dict[str, Dict[str, str]] = {}
    per_run_samples: Dict[str, List[TradeSample]] = {}

    for run, trades, orders, logs in CALIBRATION_RUNS:
        samples, day_labels = load_trade_samples(run, trades, orders, logs)
        all_samples.extend(samples)
        per_run_samples[run] = samples
        day_labels_by_run[run] = day_labels

    if not all_samples:
        raise RuntimeError("No calibration samples found")

    qqq = fetch_qqq_stooq()
    vix = fetch_vix_fred()
    market_points = build_market_points(qqq, vix)

    # Use V10.29 regime labels for feature->state supervision.
    label_run = "V10.29"
    calib_days = sorted(
        d
        for d in day_labels_by_run[label_run]
        if d in market_points and d.startswith("2024-") and "2024-07-01" <= d <= "2024-09-30"
    )
    calib_vecs = [feature_vec(market_points[d]) for d in calib_days]
    calib_labels = [day_labels_by_run[label_run][d] for d in calib_days]
    means, stds = zscore_params(calib_vecs)
    calib_z = [zscore(v, means, stds) for v in calib_vecs]

    years = [2023, 2024]
    year_days: Dict[int, List[str]] = {}
    year_states: Dict[int, Dict[str, str]] = {}
    year_qqq_returns: Dict[int, float] = {}

    for year in years:
        days = sorted(d for d in market_points if d.startswith(f"{year}-"))
        states: Dict[str, str] = {}
        for d in days:
            z = zscore(feature_vec(market_points[d]), means, stds)
            states[d] = knn_predict_state(z, calib_z, calib_labels, k=KNN_K)
        year_days[year] = days
        year_states[year] = states
        year_qqq_returns[year] = (
            market_points[days[-1]].qqq_close / market_points[days[0]].qqq_close
        ) - 1.0

    per_state_day_counts: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
    per_state_trades: Dict[str, Dict[str, List[TradeSample]]] = defaultdict(
        lambda: defaultdict(list)
    )
    global_trades: Dict[str, List[TradeSample]] = {}

    for run in per_run_samples:
        run_samples = per_run_samples[run]
        global_trades[run] = run_samples
        trades_per_day = Counter(s.day for s in run_samples)
        day_labels = day_labels_by_run[run]
        for day, state in day_labels.items():
            if not ("2024-07-01" <= day <= "2024-09-30"):
                continue
            per_state_day_counts[state][run].append(trades_per_day.get(day, 0))
        for s in run_samples:
            per_state_trades[s.state][run].append(s)

    # Build ALL/global fallback pools.
    for state, by_run in list(per_state_day_counts.items()):
        merged = []
        for run in per_run_samples:
            merged.extend(by_run.get(run, []))
        per_state_day_counts[state]["ALL"] = merged or [0]
    merged_global_counts = []
    for run in per_run_samples:
        merged_global_counts.extend(per_state_day_counts.get("RISK_ON", {}).get(run, []))
        merged_global_counts.extend(per_state_day_counts.get("NEUTRAL", {}).get(run, []))
        merged_global_counts.extend(per_state_day_counts.get("CAUTIOUS", {}).get(run, []))
        merged_global_counts.extend(per_state_day_counts.get("DEFENSIVE", {}).get(run, []))
    per_state_day_counts["GLOBAL"]["ALL"] = merged_global_counts or [0]

    for state, by_run in list(per_state_trades.items()):
        merged = []
        for run in per_run_samples:
            merged.extend(by_run.get(run, []))
        per_state_trades[state]["ALL"] = merged

    results = []
    for year in years:
        results.append(
            simulate_year(
                year=year,
                year_days=year_days[year],
                year_states=year_states[year],
                per_state_day_counts=per_state_day_counts,
                per_state_trades=per_state_trades,
                global_trades=global_trades,
                qqq_return=year_qqq_returns[year],
                iterations=args.iterations,
                seed=args.seed,
            )
        )

    # Diagnostics table: state x engine x direction from calibration.
    state_eng_dir_rows: List[Dict[str, object]] = []
    grouped: Dict[Tuple[str, str, str], List[TradeSample]] = defaultdict(list)
    for s in all_samples:
        grouped[(s.state, s.engine, s.direction)].append(s)
    for (state, eng, direction), arr in sorted(grouped.items()):
        wins = sum(x.is_win for x in arr)
        pnl_sum = sum(x.pnl for x in arr)
        state_eng_dir_rows.append(
            {
                "state": state,
                "engine": eng,
                "direction": direction,
                "trades": len(arr),
                "win_rate_pct": round(100.0 * wins / len(arr), 2),
                "avg_pnl": round(pnl_sum / len(arr), 2),
                "sum_pnl": round(pnl_sum, 2),
            }
        )

    # Predicted year state mix table.
    state_mix_rows: List[Dict[str, object]] = []
    for year in years:
        counts = Counter(year_states[year].values())
        total = sum(counts.values())
        for state, n in sorted(counts.items()):
            state_mix_rows.append(
                {
                    "year": year,
                    "state": state,
                    "days": n,
                    "share_pct": round(100.0 * n / total, 2) if total else 0.0,
                }
            )

    summary_rows = []
    for r in results:
        summary_rows.append(
            {
                "year": r["year"],
                "n_days": r["n_days"],
                "qqq_return_pct": round(r["qqq_return"] * 100, 3),
                "median_net_return_pct": round(r["median_net_return"] * 100, 3),
                "p10_net_return_pct": round(r["p10_net_return"] * 100, 3),
                "p90_net_return_pct": round(r["p90_net_return"] * 100, 3),
                "median_net_pnl": round(r["median_net_pnl"], 2),
                "p10_net_pnl": round(r["p10_net_pnl"], 2),
                "p90_net_pnl": round(r["p90_net_pnl"], 2),
                "median_max_drawdown_pct": round(r["median_max_drawdown"] * 100, 3),
                "p90_max_drawdown_pct": round(r["p90_max_drawdown"] * 100, 3),
                "positive_prob_pct": round(r["positive_prob"] * 100, 2),
                "beat_qqq_by_1_prob_pct": round(r["beat_qqq_by_1_prob"] * 100, 2),
                "within_qqq_pm1_prob_pct": round(r["within_qqq_plusminus_1_prob"] * 100, 2),
                "under_qqq_by_1_prob_pct": round(r["under_qqq_by_1_prob"] * 100, 2),
                "median_sep_net_pnl": round(r["median_sep_net_pnl"], 2),
                "p10_sep_net_pnl": round(r["p10_sep_net_pnl"], 2),
                "p90_sep_net_pnl": round(r["p90_sep_net_pnl"], 2),
            }
        )

    write_csv(
        output_dir / "calibration_state_engine_direction.csv",
        [
            "state",
            "engine",
            "direction",
            "trades",
            "win_rate_pct",
            "avg_pnl",
            "sum_pnl",
        ],
        state_eng_dir_rows,
    )
    write_csv(
        output_dir / "predicted_state_mix_2023_2024.csv",
        ["year", "state", "days", "share_pct"],
        state_mix_rows,
    )
    write_csv(
        output_dir / "simulation_summary_2023_2024.csv",
        list(summary_rows[0].keys()),
        summary_rows,
    )

    report = output_dir / "CurrentCode_RegimeSimulation_2023_2024.md"
    with report.open("w") as f:
        f.write("# Current-Code Regime Simulation (2023 + 2024)\n\n")
        f.write("## Method\n")
        f.write("- No new backtests were run.\n")
        f.write("- Calibration set: V10.27, V10.28, V10.29 smoke runs.\n")
        f.write("- Trade metrics source of truth: trades.csv; strategy tags from orders.csv.\n")
        f.write("- Regime labels from calibration logs (RegimeState lines).\n")
        f.write("- Market features for year mapping: QQQ daily closes (Stooq) + VIXCLS (FRED).\n")
        f.write(
            "- Regime classifier: k-NN (k=5) trained on Jul-Sep 2024 labeled days from V10.29.\n"
        )
        f.write(f"- Monte Carlo iterations per year: {args.iterations}.\n\n")

        f.write("## Calibration Coverage\n")
        total_trades = len(all_samples)
        gross = sum(s.pnl for s in all_samples)
        net = sum(s.pnl - s.fee for s in all_samples)
        win_rate = sum(s.is_win for s in all_samples) / total_trades if total_trades else 0.0
        f.write(f"- Trades: {total_trades}\n")
        f.write(f"- Gross P&L: {fmt_money(gross)}\n")
        f.write(f"- Net P&L: {fmt_money(net)}\n")
        f.write(f"- Win Rate: {pct(win_rate)}\n\n")

        f.write("## Simulated Year Outcomes\n")
        f.write(
            "| Year | QQQ Return | Sim Net Return (P10 / P50 / P90) | Sim Net PnL (P10 / P50 / P90) | Positive Prob | Beat QQQ by >1% |\n"
        )
        f.write("|---|---:|---:|---:|---:|---:|\n")
        for r in results:
            f.write(
                f"| {r['year']} | {pct(r['qqq_return'])} | "
                f"{pct(r['p10_net_return'])} / {pct(r['median_net_return'])} / {pct(r['p90_net_return'])} | "
                f"{fmt_money(r['p10_net_pnl'])} / {fmt_money(r['median_net_pnl'])} / {fmt_money(r['p90_net_pnl'])} | "
                f"{pct(r['positive_prob'])} | {pct(r['beat_qqq_by_1_prob'])} |\n"
            )
        f.write("\n")

        f.write("## Tail Focus (September)\n")
        f.write(
            "| Year | Sep Net PnL (P10 / P50 / P90) | Median Max Drawdown | P90 Max Drawdown |\n"
        )
        f.write("|---|---:|---:|---:|\n")
        for r in results:
            f.write(
                f"| {r['year']} | {fmt_money(r['p10_sep_net_pnl'])} / {fmt_money(r['median_sep_net_pnl'])} / {fmt_money(r['p90_sep_net_pnl'])} | "
                f"{pct(r['median_max_drawdown'])} | {pct(r['p90_max_drawdown'])} |\n"
            )
        f.write("\n")

        f.write("## Predicted Regime Mix (Daily)\n")
        f.write("| Year | State | Days | Share |\n")
        f.write("|---|---|---:|---:|\n")
        for row in state_mix_rows:
            f.write(
                f"| {row['year']} | {row['state']} | {row['days']} | {row['share_pct']:.2f}% |\n"
            )
        f.write("\n")

        f.write("## Notes\n")
        f.write("- This is a stochastic approximation, not an execution-identical QC replay.\n")
        f.write(
            "- Results are sensitive to calibration-window behavior; use as decision support only.\n"
        )
        f.write("- Core artifact tables are saved as CSV in this folder.\n")

    print(f"Simulation complete. Outputs written to: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
