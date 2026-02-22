#!/usr/bin/env python3
"""Generate run reports from QuantConnect logs/orders/trades artifacts.

Outputs three markdown files in the same folder:
  - <run_name>_REPORT.md
  - <run_name>_SIGNAL_FLOW_REPORT.md
  - <run_name>_TRADE_DETAIL_REPORT.md
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

STARTING_CAPITAL = 100000.0
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) ")
OPTION_TYPE_RE = re.compile(r"[CP](\d{8})$")
REASON_CODE_RE = re.compile(r"\b(?:[ER]_[A-Z0-9_]+|[A-Z]{2,}_[A-Z0-9_]{3,})\b")


@dataclass
class TradeRow:
    entry_time: str
    exit_time: str
    symbol: str
    pnl: float
    fees: float
    is_win: int
    duration: str
    engine: str
    strategy: str
    direction: str
    order_ids: List[int]
    raw_tag: str


REJECTION_EXCLUDES = {
    "BULL_CALL",
    "BEAR_PUT",
    "BULL_PUT",
    "BEAR_CALL",
    "BULL_CALL_DEBIT",
    "BEAR_PUT_DEBIT",
    "BULL_PUT_CREDIT",
    "BEAR_CALL_CREDIT",
    "ITM_MOMENTUM",
    "MICRO_OTM_MOMENTUM",
    "INTRADAY_SIGNAL_CANDIDATE",
    "ORDER_TAG_RESOLVE",
    "EXEC_EXTERNAL",
    "OPTIONS_DIAG_SUMMARY",
    "SWING_FILTER",
}


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


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def parse_order_ids(raw: str) -> List[int]:
    return [int(x) for x in re.findall(r"\d+", raw or "")]


def parse_option_type(symbol: str) -> str:
    text = (symbol or "").strip()
    m = OPTION_TYPE_RE.search(text)
    if m:
        return "CALL" if "C" + m.group(1) in text else "PUT"
    # Fallback for malformed symbols.
    if "C" in text[-12:]:
        return "CALL"
    if "P" in text[-12:]:
        return "PUT"
    return "UNKNOWN"


def classify_engine_strategy(tag: str) -> Tuple[str, str]:
    cleaned = (tag or "").strip()
    if not cleaned:
        return "UNKNOWN", "UNKNOWN"
    prefix = cleaned.split("|", 1)[0]
    if prefix.startswith("VASS:"):
        return "VASS", prefix.split(":", 1)[1] or "VASS_UNKNOWN"
    if prefix.startswith("MICRO:"):
        return "MICRO", prefix.split(":", 1)[1] or "MICRO_UNKNOWN"
    if prefix.startswith("ITM:"):
        return "MICRO", prefix.split(":", 1)[1] or "ITM"
    if "VASS" in prefix:
        return "VASS", prefix
    if "MICRO" in prefix or "ITM" in prefix:
        return "MICRO", prefix
    return "UNKNOWN", prefix


def derive_direction(engine: str, strategy: str, symbol: str) -> str:
    s = strategy.upper()
    if engine == "VASS":
        if "BULL_CALL" in s or "BULL_PUT" in s:
            return "BULLISH"
        if "BEAR_PUT" in s or "BEAR_CALL" in s:
            return "BEARISH"
    opt_type = parse_option_type(symbol)
    if engine == "MICRO":
        if opt_type in {"CALL", "PUT"}:
            return opt_type
        return "UNKNOWN"
    if opt_type == "CALL":
        return "BULLISH"
    if opt_type == "PUT":
        return "BEARISH"
    return "UNKNOWN"


def read_orders(orders_path: Path) -> Dict[int, Dict[str, str]]:
    if not orders_path.exists():
        return {}
    orders: Dict[int, Dict[str, str]] = {}
    with orders_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            oid = safe_int(row.get("ID", ""))
            if not oid:
                continue
            orders[oid] = row
    return orders


def find_primary_tag(order_ids: List[int], orders: Dict[int, Dict[str, str]]) -> str:
    for oid in order_ids:
        order = orders.get(oid)
        if not order:
            continue
        tag = (order.get("Tag", "") or "").strip()
        if tag:
            return tag
    return ""


def read_trades(trades_path: Path, orders: Dict[int, Dict[str, str]]) -> List[TradeRow]:
    if not trades_path.exists():
        return []
    rows: List[TradeRow] = []
    with trades_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry_time = row.get("Entry Time", "") or row.get("EntryTime", "")
            exit_time = row.get("Exit Time", "") or row.get("ExitTime", "")
            symbols = row.get("Symbols", "") or row.get("Symbol", "")
            symbol = (symbols.split(";")[0] if symbols else "").strip().strip('"')
            order_ids = parse_order_ids(row.get("Order IDs", "") or row.get("Order Ids", ""))
            tag = find_primary_tag(order_ids, orders)
            engine, strategy = classify_engine_strategy(tag)
            direction = derive_direction(engine, strategy, symbol)
            rows.append(
                TradeRow(
                    entry_time=entry_time.strip(),
                    exit_time=exit_time.strip(),
                    symbol=symbol,
                    pnl=safe_float(row.get("P&L", "") or row.get("PnL", "")),
                    fees=safe_float(row.get("Fees", "")),
                    is_win=safe_int(row.get("IsWin", "")),
                    duration=(row.get("Duration", "") or "").strip(),
                    engine=engine,
                    strategy=strategy,
                    direction=direction,
                    order_ids=order_ids,
                    raw_tag=tag,
                )
            )
    return rows


def read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    with path.open() as f:
        return [line.rstrip("\n") for line in f]


def parse_date_from_iso(value: str) -> str:
    if not value:
        return ""
    return value[:10]


def infer_date_range_from_logs(lines: Iterable[str]) -> Tuple[str, str]:
    first = ""
    last = ""
    for line in lines:
        m = TS_RE.match(line)
        if not m:
            continue
        d = m.group(1)
        if not first:
            first = d
        last = d
    return first, last


def infer_regime_bucket(score: float) -> str:
    if score >= 70:
        return "RISK_ON"
    if score >= 60:
        return "UPPER_NEUTRAL"
    if score >= 50:
        return "LOWER_NEUTRAL"
    if score >= 40:
        return "CAUTIOUS"
    if score >= 30:
        return "DEFENSIVE"
    return "RISK_OFF"


def parse_logs(lines: List[str]) -> Dict[str, object]:
    regime_scores: List[float] = []
    vass_generated = Counter()
    micro_generated = Counter()
    vass_reasons = Counter()
    micro_reasons = Counter()
    anomalies: List[str] = []

    for line in lines:
        if not line:
            continue

        # Regime score parsing.
        m = re.search(r"Score=([0-9]+(?:\.[0-9]+)?)", line)
        if "REGIME:" in line and m:
            regime_scores.append(float(m.group(1)))

        # VASS signal generation.
        if "SPREAD: ENTRY_SIGNAL" in line or "VASS: ENTRY" in line:
            if "BULL_CALL" in line or "BULL_PUT" in line:
                vass_generated["BULLISH"] += 1
            elif "BEAR_PUT" in line or "BEAR_CALL" in line:
                vass_generated["BEARISH"] += 1
            else:
                vass_generated["UNKNOWN"] += 1

        # MICRO signal generation.
        if "INTRADAY_SIGNAL" in line:
            if " CALL " in f" {line} ":
                micro_generated["CALL"] += 1
            elif " PUT " in f" {line} ":
                micro_generated["PUT"] += 1
            else:
                micro_generated["UNKNOWN"] += 1

        # Rejection reasons.
        codes = {code for code in REASON_CODE_RE.findall(line) if is_rejection_code(code)}
        if codes:
            if any(
                k in line
                for k in ("VASS", "SPREAD", "BEAR_PUT", "BULL_CALL", "BULL_PUT", "BEAR_CALL")
            ):
                for code in codes:
                    vass_reasons[code] += 1
            if any(k in line for k in ("MICRO", "INTRADAY", "ITM_")):
                for code in codes:
                    micro_reasons[code] += 1

        # Runtime anomalies.
        if any(
            k in line
            for k in ("Error invoking", "Runtime Error", "EXCEPTION", "Traceback", "Order Error:")
        ):
            anomalies.append(line)

    regime_counts = Counter()
    for score in regime_scores:
        regime_counts[infer_regime_bucket(score)] += 1

    return {
        "regime_scores": regime_scores,
        "regime_counts": regime_counts,
        "vass_generated": vass_generated,
        "micro_generated": micro_generated,
        "vass_reasons": vass_reasons,
        "micro_reasons": micro_reasons,
        "anomalies": anomalies,
    }


def is_rejection_code(code: str) -> bool:
    if not code or code in REJECTION_EXCLUDES:
        return False
    if code.startswith(("R_", "E_")):
        return True
    return any(
        k in code
        for k in (
            "BLOCK",
            "GATE",
            "REJECT",
            "CAP",
            "FAIL",
            "NO_TRADE",
            "TRADE_LIMIT",
            "TIME_WINDOW",
            "QUALITY",
            "ASSIGNMENT",
            "CREDIT_TO_WIDTH",
            "ABS_DEBIT",
            "THRESHOLD",
            "COOLDOWN",
            "MAX",
            "MIN",
        )
    )


def parse_overview_errors(overview_text: str) -> List[str]:
    if not overview_text:
        return []
    errors: List[str] = []
    capture = False
    for line in overview_text.splitlines():
        if line.startswith("## Error"):
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture and line.strip():
            errors.append(line.strip())
    return errors


def summarize_trades(trades: List[TradeRow]) -> Dict[str, object]:
    total = len(trades)
    wins = sum(1 for t in trades if t.is_win == 1)
    losses = total - wins
    pnl = sum(t.pnl for t in trades)
    fees = sum(t.fees for t in trades)
    win_rate = (100.0 * wins / total) if total else 0.0
    gross_return_pct = 100.0 * pnl / STARTING_CAPITAL

    by_engine: Dict[str, List[TradeRow]] = defaultdict(list)
    by_strategy: Dict[str, List[TradeRow]] = defaultdict(list)
    for trade in trades:
        by_engine[trade.engine].append(trade)
        by_strategy[trade.strategy].append(trade)

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl": pnl,
        "fees": fees,
        "gross_return_pct": gross_return_pct,
        "by_engine": by_engine,
        "by_strategy": by_strategy,
    }


def direction_summary(rows: Iterable[TradeRow]) -> Dict[str, Dict[str, float]]:
    bucket: Dict[str, List[TradeRow]] = defaultdict(list)
    for row in rows:
        bucket[row.direction].append(row)
    out: Dict[str, Dict[str, float]] = {}
    for direction, items in bucket.items():
        count = len(items)
        wins = sum(1 for t in items if t.is_win == 1)
        losses = count - wins
        pnl = sum(t.pnl for t in items)
        out[direction] = {
            "trades": count,
            "wins": wins,
            "losses": losses,
            "win_rate": (100.0 * wins / count) if count else 0.0,
            "pnl": pnl,
            "avg_pnl": (pnl / count) if count else 0.0,
        }
    return out


def md_table(headers: List[str], rows: List[List[str]]) -> str:
    table = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        table.append("| " + " | ".join(row) + " |")
    return "\n".join(table)


def write_performance_report(
    out_path: Path,
    run_name: str,
    trades: List[TradeRow],
    trade_summary: Dict[str, object],
    log_summary: Dict[str, object],
    log_dates: Tuple[str, str],
) -> None:
    first_log_date, last_log_date = log_dates
    trade_dates = [parse_date_from_iso(t.entry_time) for t in trades if t.entry_time]
    trade_start = min(trade_dates) if trade_dates else ""
    trade_end = max(trade_dates) if trade_dates else ""

    by_engine = trade_summary["by_engine"]
    regime_scores = log_summary["regime_scores"]
    regime_counts: Counter = log_summary["regime_counts"]

    lines: List[str] = []
    lines.append(f"# {run_name} REPORT")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(f"- Net P&L (trades.csv): **{fmt_money(trade_summary['pnl'])}**")
    lines.append(f"- Return on $100k baseline: **{fmt_pct(trade_summary['gross_return_pct'])}**")
    lines.append(
        f"- Trades / Win rate: **{trade_summary['total']}** / **{fmt_pct(trade_summary['win_rate'])}**"
    )
    lines.append("")

    lines.append("## Data Validation")
    checks = [
        ("trades.csv parsed", "x" if trade_summary["total"] >= 0 else " "),
        (
            "Win rate from IsWin column",
            "x"
            if trade_summary["total"] == (trade_summary["wins"] + trade_summary["losses"])
            else " ",
        ),
        ("Log date range parsed", "x" if first_log_date and last_log_date else " "),
        ("Trade date range parsed", "x" if trade_start and trade_end else " "),
        (
            "Date ranges overlap",
            "x"
            if (
                trade_start
                and first_log_date
                and last_log_date
                and first_log_date <= trade_start <= last_log_date
            )
            else " ",
        ),
    ]
    for text, mark in checks:
        lines.append(f"- [{mark}] {text}")
    lines.append("")
    lines.append(f"- Log date range: `{first_log_date}` to `{last_log_date}`")
    lines.append(f"- Trade date range: `{trade_start}` to `{trade_end}`")
    lines.append("")

    lines.append("## Core Metrics")
    lines.append(
        md_table(
            ["Metric", "Value"],
            [
                ["Total Trades", str(trade_summary["total"])],
                ["Wins", str(trade_summary["wins"])],
                ["Losses", str(trade_summary["losses"])],
                ["Win Rate", fmt_pct(trade_summary["win_rate"])],
                ["Gross P&L", fmt_money(trade_summary["pnl"])],
                ["Fees", fmt_money(trade_summary["fees"])],
                ["Gross Return (100k baseline)", fmt_pct(trade_summary["gross_return_pct"])],
            ],
        )
    )
    lines.append("")

    lines.append("## Engine Breakdown")
    engine_rows: List[List[str]] = []
    for engine in sorted(by_engine.keys()):
        items = by_engine[engine]
        wins = sum(1 for t in items if t.is_win == 1)
        losses = len(items) - wins
        pnl = sum(t.pnl for t in items)
        engine_rows.append(
            [
                engine,
                str(len(items)),
                str(wins),
                str(losses),
                fmt_pct(100.0 * wins / len(items) if items else 0.0),
                fmt_money(pnl),
            ]
        )
    lines.append(md_table(["Engine", "Trades", "Wins", "Losses", "Win Rate", "P&L"], engine_rows))
    lines.append("")

    lines.append("## Direction Breakdown")
    for engine in ("VASS", "MICRO"):
        items = by_engine.get(engine, [])
        if not items:
            continue
        lines.append(f"### {engine}")
        dsum = direction_summary(items)
        rows = [
            [
                d,
                str(v["trades"]),
                str(v["wins"]),
                str(v["losses"]),
                fmt_pct(v["win_rate"]),
                fmt_money(v["pnl"]),
                fmt_money(v["avg_pnl"]),
            ]
            for d, v in sorted(dsum.items())
        ]
        lines.append(
            md_table(["Direction", "Trades", "Wins", "Losses", "Win Rate", "P&L", "Avg P&L"], rows)
        )
        lines.append("")

    lines.append("## Regime Distribution")
    regime_rows = []
    total_regime = sum(regime_counts.values())
    for regime, count in regime_counts.most_common():
        pct = (100.0 * count / total_regime) if total_regime else 0.0
        regime_rows.append([regime, str(count), fmt_pct(pct)])
    if regime_rows:
        lines.append(md_table(["Regime Bucket", "Count", "Share"], regime_rows))
        if regime_scores:
            lines.append("")
            lines.append(
                f"- Average regime score: `{sum(regime_scores)/len(regime_scores):.2f}` from `{len(regime_scores)}` samples"
            )
    else:
        lines.append("- No regime lines parsed from logs.")
    lines.append("")

    anomalies: List[str] = log_summary["anomalies"]
    lines.append("## Runtime and Anomalies")
    if anomalies:
        lines.append("- Top anomalies detected:")
        for line in anomalies[:12]:
            lines.append(f"  - `{line}`")
    else:
        lines.append("- No runtime anomalies detected in parsed logs.")
    lines.append("")

    lines.append("## Recommendations")
    if trade_summary["total"] == 0:
        lines.append("- P0: resolve runtime/plumbing issue before evaluating alpha quality.")
        lines.append("- P1: rerun smoke window after runtime fix and regenerate reports.")
    else:
        if trade_summary["gross_return_pct"] < 2.0:
            lines.append("- P0: smoke performance is below the +2% QQQ reference; continue tuning.")
        if any("Error invoking" in x for x in anomalies):
            lines.append("- P0: runtime data-read errors must be treated as infra blockers.")
        lines.append("- P1: reduce dominant rejection reasons shown in signal-flow report.")

    out_path.write_text("\n".join(lines) + "\n")


def write_signal_flow_report(
    out_path: Path,
    run_name: str,
    trades: List[TradeRow],
    trade_summary: Dict[str, object],
    log_summary: Dict[str, object],
) -> None:
    by_engine = trade_summary["by_engine"]
    vass_exec = by_engine.get("VASS", [])
    micro_exec = by_engine.get("MICRO", [])
    vass_exec_dir = direction_summary(vass_exec)
    micro_exec_dir = direction_summary(micro_exec)
    vass_generated: Counter = log_summary["vass_generated"]
    micro_generated: Counter = log_summary["micro_generated"]
    vass_reasons: Counter = log_summary["vass_reasons"]
    micro_reasons: Counter = log_summary["micro_reasons"]

    vass_generated_total = sum(vass_generated.values())
    micro_generated_total = sum(micro_generated.values())
    vass_rejected = sum(vass_reasons.values())
    micro_rejected = sum(micro_reasons.values())

    lines: List[str] = []
    lines.append(f"# {run_name} SIGNAL FLOW REPORT")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(
        md_table(
            ["Engine", "Generated", "Rejected/Blocked", "Executed", "Execution Rate"],
            [
                [
                    "VASS",
                    str(vass_generated_total),
                    str(vass_rejected),
                    str(len(vass_exec)),
                    fmt_pct(
                        100.0 * len(vass_exec) / vass_generated_total
                        if vass_generated_total
                        else 0.0
                    ),
                ],
                [
                    "MICRO",
                    str(micro_generated_total),
                    str(micro_rejected),
                    str(len(micro_exec)),
                    fmt_pct(
                        100.0 * len(micro_exec) / micro_generated_total
                        if micro_generated_total
                        else 0.0
                    ),
                ],
            ],
        )
    )
    lines.append("")

    lines.append("## VASS Signal Flow")
    gen_rows = [[k, str(v)] for k, v in sorted(vass_generated.items())]
    lines.append("### Generation by Direction")
    lines.append(md_table(["Direction", "Generated"], gen_rows or [["N/A", "0"]]))
    lines.append("")
    lines.append("### Rejection/Block Reasons")
    reason_rows = [[k, str(v)] for k, v in vass_reasons.most_common(20)]
    lines.append(md_table(["Reason", "Count"], reason_rows or [["N/A", "0"]]))
    lines.append("")
    lines.append("### Executed Trades by Direction")
    exec_rows = [
        [
            d,
            str(v["trades"]),
            str(v["wins"]),
            str(v["losses"]),
            fmt_pct(v["win_rate"]),
            fmt_money(v["pnl"]),
        ]
        for d, v in sorted(vass_exec_dir.items())
    ]
    lines.append(
        md_table(
            ["Direction", "Trades", "Wins", "Losses", "Win Rate", "P&L"],
            exec_rows or [["N/A", "0", "0", "0", "0.00%", "$0.00"]],
        )
    )
    lines.append("")
    lines.append("### Visual Funnel")
    lines.append("```")
    lines.append(
        f"VASS Generated {vass_generated_total} -> Rejected {vass_rejected} -> Executed {len(vass_exec)}"
    )
    lines.append("```")
    lines.append("")

    lines.append("## MICRO Signal Flow")
    micro_gen_rows = [[k, str(v)] for k, v in sorted(micro_generated.items())]
    lines.append("### Generation by Direction")
    lines.append(md_table(["Direction", "Generated"], micro_gen_rows or [["N/A", "0"]]))
    lines.append("")
    lines.append("### Rejection/Block Reasons")
    micro_reason_rows = [[k, str(v)] for k, v in micro_reasons.most_common(20)]
    lines.append(md_table(["Reason", "Count"], micro_reason_rows or [["N/A", "0"]]))
    lines.append("")
    lines.append("### Executed Trades by Direction")
    micro_exec_rows = [
        [
            d,
            str(v["trades"]),
            str(v["wins"]),
            str(v["losses"]),
            fmt_pct(v["win_rate"]),
            fmt_money(v["pnl"]),
        ]
        for d, v in sorted(micro_exec_dir.items())
    ]
    lines.append(
        md_table(
            ["Direction", "Trades", "Wins", "Losses", "Win Rate", "P&L"],
            micro_exec_rows or [["N/A", "0", "0", "0", "0.00%", "$0.00"]],
        )
    )
    lines.append("")
    lines.append("### Visual Funnel")
    lines.append("```")
    lines.append(
        f"MICRO Generated {micro_generated_total} -> Rejected {micro_rejected} -> Executed {len(micro_exec)}"
    )
    lines.append("```")
    lines.append("")

    lines.append("## Key Findings and Recommendations")
    if len(trades) == 0:
        lines.append("- P0: no executed trades; treat as plumbing/runtime failure.")
    else:
        if vass_generated_total and (100.0 * len(vass_exec) / vass_generated_total) < 25.0:
            lines.append("- P0: VASS execution rate is low; relax the highest-impact gate reason.")
        if micro_generated_total and (100.0 * len(micro_exec) / micro_generated_total) < 25.0:
            lines.append("- P0: MICRO execution rate is low; inspect top rejection reasons.")
        lines.append(
            "- P1: prioritize rejection reasons with both high count and high opportunity cost."
        )

    out_path.write_text("\n".join(lines) + "\n")


def write_trade_detail_report(
    out_path: Path,
    run_name: str,
    trades: List[TradeRow],
    trade_summary: Dict[str, object],
) -> None:
    by_strategy: Dict[str, List[TradeRow]] = trade_summary["by_strategy"]
    losses = [t for t in trades if t.pnl < 0]
    avg_loss = (sum(abs(t.pnl) for t in losses) / len(losses)) if losses else 0.0
    tail_losses = [t for t in losses if abs(t.pnl) > 2.0 * avg_loss] if avg_loss else []

    monthly = defaultdict(float)
    for trade in trades:
        if not trade.entry_time:
            continue
        key = trade.entry_time[:7]
        monthly[key] += trade.pnl

    lines: List[str] = []
    lines.append(f"# {run_name} TRADE DETAIL REPORT")
    lines.append("")
    lines.append("## Topline")
    lines.append(f"- Total trades: **{trade_summary['total']}**")
    lines.append(f"- Wins/Losses: **{trade_summary['wins']} / {trade_summary['losses']}**")
    lines.append(f"- Win rate: **{fmt_pct(trade_summary['win_rate'])}**")
    lines.append(f"- Gross P&L: **{fmt_money(trade_summary['pnl'])}**")
    lines.append("")

    lines.append("## Strategy Breakdown")
    strategy_rows: List[List[str]] = []
    for strategy, items in sorted(
        by_strategy.items(), key=lambda kv: sum(t.pnl for t in kv[1]), reverse=True
    ):
        wins = sum(1 for t in items if t.is_win == 1)
        pnl = sum(t.pnl for t in items)
        strategy_rows.append(
            [
                strategy,
                items[0].engine if items else "UNKNOWN",
                str(len(items)),
                str(wins),
                str(len(items) - wins),
                fmt_pct(100.0 * wins / len(items) if items else 0.0),
                fmt_money(pnl),
            ]
        )
    lines.append(
        md_table(
            ["Strategy", "Engine", "Trades", "Wins", "Losses", "Win Rate", "P&L"],
            strategy_rows or [["N/A", "N/A", "0", "0", "0", "0.00%", "$0.00"]],
        )
    )
    lines.append("")

    lines.append("## Tail Risk")
    lines.append(f"- Average losing trade: **{fmt_money(-avg_loss)}**")
    lines.append(
        f"- Tail-loss count (>2x avg loss): **{len(tail_losses)}** / **{len(losses)}** losses"
    )
    tail_pct = (100.0 * len(tail_losses) / len(losses)) if losses else 0.0
    lines.append(f"- Tail-loss concentration: **{fmt_pct(tail_pct)}**")
    lines.append("")

    lines.append("## Worst Trades")
    worst = sorted(trades, key=lambda t: t.pnl)[:20]
    worst_rows = [
        [
            t.entry_time,
            t.symbol,
            t.engine,
            t.strategy,
            t.direction,
            fmt_money(t.pnl),
            str(t.is_win),
            t.duration,
        ]
        for t in worst
    ]
    lines.append(
        md_table(
            ["Entry", "Symbol", "Engine", "Strategy", "Direction", "P&L", "IsWin", "Duration"],
            worst_rows or [["N/A", "N/A", "N/A", "N/A", "N/A", "$0.00", "0", "N/A"]],
        )
    )
    lines.append("")

    lines.append("## Monthly P&L")
    month_rows = [[m, fmt_money(v)] for m, v in sorted(monthly.items())]
    lines.append(md_table(["Month", "P&L"], month_rows or [["N/A", "$0.00"]]))
    lines.append("")

    out_path.write_text("\n".join(lines) + "\n")


def resolve_run_name(stage_dir: Path, run_name: str | None) -> str:
    if run_name:
        return run_name
    trades = sorted(stage_dir.glob("*_trades.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not trades:
        raise SystemExit(f"No *_trades.csv found in {stage_dir}")
    return trades[0].name[: -len("_trades.csv")]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate run reports from QC artifacts")
    parser.add_argument("--stage-dir", required=True, help="Directory containing run artifacts")
    parser.add_argument("--run-name", help="Run prefix (without _logs/_orders/_trades suffix)")
    args = parser.parse_args()

    stage_dir = Path(args.stage_dir)
    if not stage_dir.exists():
        raise SystemExit(f"Stage dir not found: {stage_dir}")

    run_name = resolve_run_name(stage_dir, args.run_name)
    logs_path = stage_dir / f"{run_name}_logs.txt"
    orders_path = stage_dir / f"{run_name}_orders.csv"
    trades_path = stage_dir / f"{run_name}_trades.csv"
    overview_path = stage_dir / f"{run_name}_overview.txt"

    orders = read_orders(orders_path)
    trades = read_trades(trades_path, orders)
    lines = read_lines(logs_path)
    log_summary = parse_logs(lines)
    overview_errors = parse_overview_errors(
        overview_path.read_text() if overview_path.exists() else ""
    )
    if overview_errors:
        log_summary["anomalies"] = list(log_summary["anomalies"]) + overview_errors
    trade_summary = summarize_trades(trades)
    log_dates = infer_date_range_from_logs(lines)

    report_path = stage_dir / f"{run_name}_REPORT.md"
    flow_path = stage_dir / f"{run_name}_SIGNAL_FLOW_REPORT.md"
    detail_path = stage_dir / f"{run_name}_TRADE_DETAIL_REPORT.md"

    write_performance_report(report_path, run_name, trades, trade_summary, log_summary, log_dates)
    write_signal_flow_report(flow_path, run_name, trades, trade_summary, log_summary)
    write_trade_detail_report(detail_path, run_name, trades, trade_summary)

    print(f"Generated: {report_path}")
    print(f"Generated: {flow_path}")
    print(f"Generated: {detail_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
