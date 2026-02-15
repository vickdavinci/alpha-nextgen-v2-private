#!/usr/bin/env python3
"""
Stage10 bull-regime RCA generator.

Consumes stage10 logs/orders/trades and writes:
  - RCA_Bull_Regimes_Stage10.md
  - RCA_Bull_Regimes_Stage10_metrics.csv
  - RCA_Bull_Regimes_Stage10_funnel.csv
  - RCA_Bull_Regimes_Stage10_param_sensitivity.csv
  - RCA_Bull_Regimes_Stage10_code_map.md
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

BASE_DIR = Path("docs/audits/logs/stage10")
RUNS = [
    {
        "run": "2017_Q3",
        "label": "Jul-Sep 2017",
        "prefix": "V10_0_Smoke_JulSep2017",
        "market_note": "Bull grind-up",
    },
    {
        "run": "2021_Q2",
        "label": "Apr-Jun 2021",
        "prefix": "V10_AprJun2021",
        "market_note": "Bull with May drawdown shock",
    },
    {
        "run": "2023_Q2",
        "label": "Apr-Jun 2023",
        "prefix": "V10_0_AprJun2023_v2",
        "market_note": "AI-led bull rally",
    },
]


EVENT_PATTERNS = [
    "SPREAD: ENTRY_SIGNAL",
    "SPREAD: POSITION_REGISTERED",
    "SPREAD: EXIT_SIGNAL",
    "SPREAD: POSITION_REMOVED",
    "INTRADAY_SIGNAL",
    "OPT: INTRADAY position registered",
    "OPT: INTRADAY_POSITION_REMOVED",
    "VETO: VASS conviction (BEARISH) overrides Macro (BULLISH)",
    "VETO: MICRO conviction (BEARISH) overrides Macro (BULLISH)",
    "CREDIT_SPREAD: Entry blocked - CREDIT_TO_WIDTH",
    "Order Error:",
    "OPT_MACRO_RECOVERY: Pending spread entry cancelled",
    "INTRADAY_FORCE_EXIT",
    "PREMARKET_ITM_CHECK",
]


EVIDENCE_KEYS = [
    "SPREAD: ENTRY_SIGNAL",
    "SPREAD: POSITION_REGISTERED",
    "SPREAD: EXIT_SIGNAL",
    "INTRADAY_SIGNAL",
    "OPT: INTRADAY position registered",
    "VETO: VASS conviction (BEARISH) overrides Macro (BULLISH)",
    "VETO: MICRO conviction (BEARISH) overrides Macro (BULLISH)",
    "CREDIT_SPREAD: Entry blocked - CREDIT_TO_WIDTH",
    "Order Error:",
    "OPT_MACRO_RECOVERY: Pending spread entry cancelled",
    "INTRADAY_FORCE_EXIT",
]


ENTRY_DEBIT_RE = re.compile(
    r"SPREAD: ENTRY_SIGNAL \| (?P<strategy>[A-Z_]+): Regime=(?P<regime>\d+)"
    r" \| VIX=(?P<vix>[\d.]+) \| Long=(?P<long>[\d.]+) Short=(?P<short>[\d.]+)"
    r" \| Debit=\$(?P<debit>[\d.]+) MaxProfit=\$(?P<max_profit>[\d.]+)"
    r" \| x(?P<qty>\d+) \| DTE=(?P<dte>\d+)"
)

ENTRY_CREDIT_RE = re.compile(
    r"CREDIT_SPREAD: ENTRY_SIGNAL \| (?P<strategy>[A-Z_]+): Regime=(?P<regime>\d+)"
    r" \| VIX=(?P<vix>[\d.]+) \| Sell (?P<short>[\d.]+) Buy (?P<long>[\d.]+)"
    r" \| Credit=\$(?P<credit>[\d.]+) Width=\$(?P<width>[\d.]+)"
    r" \| x(?P<qty>\d+) \| DTE=(?P<dte>\d+)"
)

INTRADAY_SIGNAL_RE = re.compile(
    r"INTRADAY_SIGNAL: (?P<strategy>[A-Z_]+): Regime=(?P<regime>[A-Z_]+)"
    r" \| Score=(?P<score>-?[\d.]+) \| VIX=(?P<vix>[\d.]+)"
    r".* \| (?P<direction>CALL|PUT) x(?P<qty>\d+)"
    r".* \| Stop=(?P<stop>\d+)%"
)

SPREAD_KEY_RE = re.compile(
    r"Key=(?P<long_sym>[^|]+)\|(?P<short_sym>[^|]+)\|(?P<entry_ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
)

PCT_RE = re.compile(r"P&L=(?P<pct>[+-]?[\d.]+)%")
OPTION_RE = re.compile(r"([CP])(\d{8})$")


@dataclass
class TradeRecord:
    engine: str
    strategy: str
    option_type: str
    pnl: float
    fees: float
    net: float
    is_win: bool
    entry_tag: str
    exit_tag: str
    exit_type: str
    exit_class: str
    entry_time: datetime
    exit_time: datetime


@dataclass
class SpreadEntry:
    timestamp: datetime
    strategy: str
    regime: float
    vix: float
    long_strike: float
    short_strike: float
    qty: int
    dte: int
    spread_kind: str  # debit or credit
    ratio: float  # debit/width for debit, credit/width for credit


@dataclass
class SpreadExit:
    timestamp: datetime
    entry_time: datetime
    long_strike: float
    short_strike: float
    reason: str
    pnl_pct: Optional[float]


def parse_ts(line: str) -> datetime:
    return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")


def parse_utc(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


def parse_order_ids(raw: str) -> List[int]:
    return [int(x) for x in re.findall(r"\d+", raw or "")]


def parse_option_type(symbol: str) -> str:
    m = OPTION_RE.search(symbol.strip())
    if not m:
        return "UNKNOWN"
    return "CALL" if m.group(1) == "C" else "PUT"


def parse_strike(symbol: str) -> Optional[float]:
    m = OPTION_RE.search(symbol.strip())
    if not m:
        return None
    return float(m.group(2)) / 1000.0


def bucket_ratio(ratio: float) -> str:
    if ratio < 0.35:
        return "<35%"
    if ratio < 0.45:
        return "35-45%"
    if ratio < 0.55:
        return "45-55%"
    return ">=55%"


def bucket_dte(dte: int) -> str:
    if dte <= 10:
        return "<=10"
    if dte <= 20:
        return "11-20"
    if dte <= 30:
        return "21-30"
    return ">30"


def bucket_regime(score: float) -> str:
    if score < 55:
        return "<55"
    if score < 65:
        return "55-64"
    if score < 75:
        return "65-74"
    return ">=75"


def summarize_records(records: Iterable[TradeRecord]) -> Dict[str, float]:
    rows = list(records)
    n = len(rows)
    wins = sum(1 for r in rows if r.is_win)
    losses = n - wins
    gross = sum(r.pnl for r in rows)
    fees = sum(r.fees for r in rows)
    net = gross - fees
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": (100.0 * wins / n) if n else 0.0,
        "gross_pnl": gross,
        "fees": fees,
        "net_pnl": net,
    }


def classify_vass_strategy(tag: str) -> str:
    if "VASS:BULL_CALL_DEBIT" in tag:
        return "BULL_CALL_DEBIT"
    if "VASS:BEAR_PUT_DEBIT" in tag:
        return "BEAR_PUT_DEBIT"
    if "VASS:BULL_PUT_CREDIT" in tag:
        return "BULL_PUT_CREDIT"
    if "VASS:BEAR_CALL_CREDIT" in tag:
        return "BEAR_CALL_CREDIT"
    return "VASS_OTHER"


def classify_micro_strategy(tag: str) -> str:
    if "MICRO:ITM_MOMENTUM" in tag:
        return "ITM_MOMENTUM"
    if "MICRO:DEBIT_FADE" in tag:
        return "DEBIT_FADE"
    if "MICRO:DEBIT_MOMENTUM" in tag:
        return "DEBIT_MOMENTUM"
    return "MICRO_OTHER"


def classify_micro_exit(exit_tag: str, exit_type: str, exit_time: datetime) -> str:
    if "RECON_ORPHAN_OPTION" in exit_tag:
        return "RECON_ORPHAN"
    if exit_tag.startswith("OCO_STOP") or exit_type == "Stop Market":
        return "STOP"
    if exit_tag in {"MICRO", "MICRO_EOD_SWEEP"} or (
        exit_time.hour == 19 and exit_time.minute >= 25
    ):
        return "FORCE_EOD"
    if exit_tag == "" and exit_type == "Limit":
        return "LIMIT_OR_OCO_PROFIT"
    if exit_tag == "" and exit_type == "Market":
        return "MARKET_EXIT"
    if exit_tag == "":
        return "EMPTY_TAG"
    return "OTHER"


def load_orders(path: Path) -> Tuple[Dict[int, Dict[str, str]], List[Dict[str, str]]]:
    by_id: Dict[int, Dict[str, str]] = {}
    rows: List[Dict[str, str]] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            by_id[i] = row
            rows.append(row)
    return by_id, rows


def load_trades(path: Path, orders_by_id: Dict[int, Dict[str, str]]) -> List[TradeRecord]:
    records: List[TradeRecord] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            ids = parse_order_ids(row["Order Ids"])
            if not ids:
                continue
            entry_order = orders_by_id.get(ids[0], {})
            exit_order = orders_by_id.get(ids[-1], {})
            entry_tag = entry_order.get("Tag", "").strip().strip('"')
            exit_tag = exit_order.get("Tag", "").strip().strip('"')
            exit_type = exit_order.get("Type", "").strip()

            symbol = row["Symbols"].strip().strip('"')
            option_type = parse_option_type(symbol)
            pnl = float(row["P&L"] or 0.0)
            fees = float(row["Fees"] or 0.0)
            entry_time = parse_utc(row["Entry Time"])
            exit_time = parse_utc(row["Exit Time"])

            if "VASS" in entry_tag:
                engine = "VASS"
                strategy = classify_vass_strategy(entry_tag)
                exit_class = "N/A"
            elif "MICRO" in entry_tag:
                engine = "MICRO"
                strategy = classify_micro_strategy(entry_tag)
                exit_class = classify_micro_exit(exit_tag, exit_type, exit_time)
            else:
                engine = "OTHER"
                strategy = "OTHER"
                exit_class = "N/A"

            records.append(
                TradeRecord(
                    engine=engine,
                    strategy=strategy,
                    option_type=option_type,
                    pnl=pnl,
                    fees=fees,
                    net=pnl - fees,
                    is_win=(pnl > 0.0),
                    entry_tag=entry_tag,
                    exit_tag=exit_tag,
                    exit_type=exit_type,
                    exit_class=exit_class,
                    entry_time=entry_time,
                    exit_time=exit_time,
                )
            )
    return records


def parse_spread_entry_from_line(line: str) -> Optional[SpreadEntry]:
    ts = parse_ts(line)
    debit_match = ENTRY_DEBIT_RE.search(line)
    if debit_match:
        long_s = float(debit_match.group("long"))
        short_s = float(debit_match.group("short"))
        width = abs(short_s - long_s)
        debit = float(debit_match.group("debit"))
        ratio = (debit / width) if width > 0 else 0.0
        return SpreadEntry(
            timestamp=ts,
            strategy=debit_match.group("strategy"),
            regime=float(debit_match.group("regime")),
            vix=float(debit_match.group("vix")),
            long_strike=long_s,
            short_strike=short_s,
            qty=int(debit_match.group("qty")),
            dte=int(debit_match.group("dte")),
            spread_kind="debit",
            ratio=ratio,
        )

    credit_match = ENTRY_CREDIT_RE.search(line)
    if credit_match:
        long_s = float(credit_match.group("long"))
        short_s = float(credit_match.group("short"))
        width = float(credit_match.group("width"))
        credit = float(credit_match.group("credit"))
        ratio = (credit / width) if width > 0 else 0.0
        return SpreadEntry(
            timestamp=ts,
            strategy=credit_match.group("strategy"),
            regime=float(credit_match.group("regime")),
            vix=float(credit_match.group("vix")),
            long_strike=long_s,
            short_strike=short_s,
            qty=int(credit_match.group("qty")),
            dte=int(credit_match.group("dte")),
            spread_kind="credit",
            ratio=ratio,
        )
    return None


def parse_spread_exit_from_line(line: str) -> Optional[SpreadExit]:
    ts = parse_ts(line)
    parts = line.strip().split(" | ")
    if len(parts) < 3:
        return None
    key_part = parts[1]
    reason_part = parts[2].strip()

    key_match = SPREAD_KEY_RE.search(key_part)
    if not key_match:
        return None

    long_sym = key_match.group("long_sym").strip()
    short_sym = key_match.group("short_sym").strip()
    entry_ts = datetime.strptime(key_match.group("entry_ts"), "%Y-%m-%d %H:%M:%S")

    reason = reason_part.split()[0]
    reason = reason.replace("SPREAD_HARD_STOP_TRIGGERED_PCT", "HARD_STOP")
    reason = reason.replace("SPREAD_HARD_STOP_TRIGGERED_WIDTH", "HARD_STOP_WIDTH")

    pnl_pct = None
    pct_match = PCT_RE.search(line)
    if pct_match:
        pnl_pct = float(pct_match.group("pct"))

    long_strike = parse_strike(long_sym)
    short_strike = parse_strike(short_sym)
    if long_strike is None or short_strike is None:
        return None

    return SpreadExit(
        timestamp=ts,
        entry_time=entry_ts,
        long_strike=long_strike,
        short_strike=short_strike,
        reason=reason,
        pnl_pct=pnl_pct,
    )


def parse_logs(path: Path) -> Dict[str, object]:
    lines = path.read_text().splitlines()
    counters = Counter()
    evidence: Dict[str, List[Tuple[int, str]]] = {k: [] for k in EVIDENCE_KEYS}
    spread_entries: List[SpreadEntry] = []
    spread_exits: List[SpreadExit] = []
    intraday_signals: List[Dict[str, object]] = []
    intraday_exec_unmatched = 0

    # Keep queue of unmatched signal indices for short-horizon signal->execution mapping.
    pending_signal_indices: List[int] = []

    for lineno, line in enumerate(lines, start=1):
        for event in EVENT_PATTERNS:
            if event in line:
                counters[event] += 1
        for key in EVIDENCE_KEYS:
            if key in line and len(evidence[key]) < 4:
                evidence[key].append((lineno, line))

        if "SPREAD: ENTRY_SIGNAL" in line or "CREDIT_SPREAD: ENTRY_SIGNAL" in line:
            entry = parse_spread_entry_from_line(line)
            if entry is not None:
                spread_entries.append(entry)

        if "SPREAD: EXIT_SIGNAL" in line:
            exit_rec = parse_spread_exit_from_line(line)
            if exit_rec is not None:
                spread_exits.append(exit_rec)

        if "INTRADAY_SIGNAL:" in line:
            m = INTRADAY_SIGNAL_RE.search(line)
            if m:
                signal = {
                    "timestamp": parse_ts(line),
                    "strategy": m.group("strategy"),
                    "regime": m.group("regime"),
                    "score": float(m.group("score")),
                    "vix": float(m.group("vix")),
                    "direction": m.group("direction"),
                    "qty": int(m.group("qty")),
                    "stop_pct": float(m.group("stop")),
                    "executed": False,
                }
                intraday_signals.append(signal)
                pending_signal_indices.append(len(intraday_signals) - 1)

        if "OPT: INTRADAY position registered" in line:
            exec_ts = parse_ts(line)
            matched = False
            for idx in reversed(pending_signal_indices):
                signal_ts = intraday_signals[idx]["timestamp"]
                if isinstance(signal_ts, datetime):
                    lag = exec_ts - signal_ts
                    if timedelta(minutes=0) <= lag <= timedelta(minutes=2):
                        intraday_signals[idx]["executed"] = True
                        pending_signal_indices.remove(idx)
                        matched = True
                        break
            if not matched:
                intraday_exec_unmatched += 1

    return {
        "counters": counters,
        "evidence": evidence,
        "spread_entries": spread_entries,
        "spread_exits": spread_exits,
        "intraday_signals": intraday_signals,
        "intraday_exec_unmatched": intraday_exec_unmatched,
    }


def key_for_spread_entry(
    entry_time: datetime, long_strike: float, short_strike: float
) -> Tuple[str, str, str]:
    return (
        entry_time.strftime("%Y-%m-%d %H:%M:%S"),
        f"{long_strike:.1f}",
        f"{short_strike:.1f}",
    )


def build_param_rows(
    run_id: str,
    trade_records: List[TradeRecord],
    log_data: Dict[str, object],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []

    def add_group_rows(engine: str, parameter: str, grouping: Dict[str, List[TradeRecord]]) -> None:
        for bucket, bucket_rows in sorted(grouping.items()):
            stats = summarize_records(bucket_rows)
            rows.append(
                {
                    "run": run_id,
                    "engine": engine,
                    "parameter": parameter,
                    "bucket": bucket,
                    "trades": stats["trades"],
                    "win_rate_pct": round(stats["win_rate_pct"], 1),
                    "gross_pnl": round(stats["gross_pnl"], 1),
                    "fees": round(stats["fees"], 1),
                    "net_pnl": round(stats["net_pnl"], 1),
                    "notes": "",
                }
            )

    micro = [r for r in trade_records if r.engine == "MICRO"]
    vass = [r for r in trade_records if r.engine == "VASS"]

    by_micro_strategy: Dict[str, List[TradeRecord]] = defaultdict(list)
    by_micro_direction: Dict[str, List[TradeRecord]] = defaultdict(list)
    by_micro_exit: Dict[str, List[TradeRecord]] = defaultdict(list)
    for r in micro:
        by_micro_strategy[r.strategy].append(r)
        by_micro_direction[r.option_type].append(r)
        by_micro_exit[r.exit_class].append(r)
    add_group_rows("MICRO", "strategy", by_micro_strategy)
    add_group_rows("MICRO", "direction", by_micro_direction)
    add_group_rows("MICRO", "exit_path", by_micro_exit)

    by_vass_strategy: Dict[str, List[TradeRecord]] = defaultdict(list)
    by_vass_direction: Dict[str, List[TradeRecord]] = defaultdict(list)
    for r in vass:
        by_vass_strategy[r.strategy].append(r)
        by_vass_direction[r.option_type].append(r)
    add_group_rows("VASS", "strategy", by_vass_strategy)
    add_group_rows("VASS", "direction", by_vass_direction)

    spread_entries: List[SpreadEntry] = log_data["spread_entries"]  # type: ignore[assignment]
    spread_exits: List[SpreadExit] = log_data["spread_exits"]  # type: ignore[assignment]

    entry_map: Dict[Tuple[str, str, str], List[SpreadEntry]] = defaultdict(list)
    for entry in spread_entries:
        entry_map[
            key_for_spread_entry(entry.timestamp, entry.long_strike, entry.short_strike)
        ].append(entry)

    matched: List[Tuple[SpreadEntry, SpreadExit]] = []
    for exit_rec in spread_exits:
        k = key_for_spread_entry(exit_rec.entry_time, exit_rec.long_strike, exit_rec.short_strike)
        if entry_map.get(k):
            matched.append((entry_map[k].pop(0), exit_rec))

    # VASS entry-quality buckets (log-derived, matched exits only)
    ratio_groups: Dict[str, List[Tuple[SpreadEntry, SpreadExit]]] = defaultdict(list)
    dte_groups: Dict[str, List[Tuple[SpreadEntry, SpreadExit]]] = defaultdict(list)
    regime_groups: Dict[str, List[Tuple[SpreadEntry, SpreadExit]]] = defaultdict(list)
    reason_groups: Dict[str, List[Tuple[SpreadEntry, SpreadExit]]] = defaultdict(list)
    for pair in matched:
        entry, exit_rec = pair
        ratio_groups[bucket_ratio(entry.ratio)].append(pair)
        dte_groups[bucket_dte(entry.dte)].append(pair)
        regime_groups[bucket_regime(entry.regime)].append(pair)
        reason_groups[exit_rec.reason].append(pair)

    def add_log_match_rows(
        parameter: str, groups: Dict[str, List[Tuple[SpreadEntry, SpreadExit]]]
    ) -> None:
        for bucket, pairs in sorted(groups.items()):
            valid_pct = [p[1].pnl_pct for p in pairs if p[1].pnl_pct is not None]
            wins = sum(1 for p in valid_pct if p > 0)
            n = len(valid_pct)
            avg_pct = (sum(valid_pct) / n) if n else 0.0
            rows.append(
                {
                    "run": run_id,
                    "engine": "VASS",
                    "parameter": parameter,
                    "bucket": bucket,
                    "trades": n,
                    "win_rate_pct": round((100.0 * wins / n) if n else 0.0, 1),
                    "gross_pnl": "",
                    "fees": "",
                    "net_pnl": "",
                    "notes": f"avg_pnl_pct={avg_pct:.1f}",
                }
            )

    add_log_match_rows("entry_ratio", ratio_groups)
    add_log_match_rows("entry_dte_bucket", dte_groups)
    add_log_match_rows("entry_regime_bucket", regime_groups)
    add_log_match_rows("exit_reason", reason_groups)

    intraday_signals: List[Dict[str, object]] = log_data["intraday_signals"]  # type: ignore[assignment]
    regime_counts: Dict[str, Tuple[int, int]] = defaultdict(lambda: (0, 0))
    for sig in intraday_signals:
        regime = str(sig["regime"])
        total, executed = regime_counts[regime]
        total += 1
        executed += 1 if bool(sig["executed"]) else 0
        regime_counts[regime] = (total, executed)
    for regime, (total, executed) in sorted(regime_counts.items()):
        conv = (100.0 * executed / total) if total else 0.0
        rows.append(
            {
                "run": run_id,
                "engine": "MICRO",
                "parameter": "signal_regime_conversion",
                "bucket": regime,
                "trades": total,
                "win_rate_pct": round(conv, 1),
                "gross_pnl": "",
                "fees": "",
                "net_pnl": "",
                "notes": f"signals={total},executed={executed}",
            }
        )

    return rows


def write_csv(path: Path, rows: List[Dict[str, object]], fieldnames: List[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def format_money(value: float) -> str:
    return f"${value:,.0f}"


def build_report(
    run_summaries: List[Dict[str, object]],
    evidence_by_run: Dict[str, Dict[str, List[Tuple[int, str]]]],
    out_metrics: Path,
    out_funnel: Path,
    out_params: Path,
) -> str:
    summary_by_run = {str(s["run"]): s for s in run_summaries}
    lines: List[str] = []
    lines.append("# RCA Bull Regimes - Stage10")
    lines.append("")
    lines.append(f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%SZ')}")
    lines.append("")
    lines.append("## Scope")
    lines.append("- Runs: Jul-Sep 2017, Apr-Jun 2021, Apr-Jun 2023")
    lines.append("- Data: stage10 logs + orders.csv + trades.csv")
    lines.append(
        "- Focus: why bull regimes are not producing portfolio wins, why ITM/VASS fail, why signal-to-trade conversion is low, and which parameters drive win-rate."
    )
    lines.append("")
    lines.append("## Portfolio Diagnosis")
    lines.append("| Run | Net P&L | VASS Net | MICRO Net | VASS Conv | MICRO Conv |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for s in run_summaries:
        lines.append(
            f"| {s['label']} | {format_money(float(s['total_net_pnl']))} | "
            f"{format_money(float(s['vass_net_pnl']))} | {format_money(float(s['micro_net_pnl']))} | "
            f"{float(s['vass_conversion_pct']):.1f}% | {float(s['micro_conversion_pct']):.1f}% |"
        )
    lines.append("")
    lines.append("## Core Findings")
    lines.append(
        "1. MICRO is the persistent drag in bull runs. In 2023 Q2 alone MICRO lost about $16.6k net while VASS made about $10.8k net, resulting in a net portfolio loss."
    )
    lines.append(
        "2. Signal conversion is asymmetric: VASS is heavily choked in 2017/2023 (49.1% and 17.1%), while MICRO still executes enough volume to realize losses."
    )
    lines.append(
        "3. ITM path failure in 2023 is mostly exit plumbing/stop-path dominated: RECON_ORPHAN + stop exits account for most MICRO drawdown."
    )
    lines.append(
        "4. VASS direction quality is better than MICRO in bull regimes, but still exposed to bearish-conviction overrides and hard-stop clusters."
    )
    lines.append(
        "5. 2021 conversion choke is strongly tied to credit quality gate (`CREDIT_TO_WIDTH < 35%` repeatedly), which suppresses spread entries."
    )
    lines.append("")
    lines.append("## MICRO Exit-Path Impact")
    lines.append("| Run | RECON_ORPHAN Net | STOP Net | FORCE_EOD Net |")
    lines.append("|---|---:|---:|---:|")
    for s in run_summaries:
        lines.append(
            f"| {s['label']} | {format_money(float(s['micro_recon_orphan_net']))} | "
            f"{format_money(float(s['micro_stop_net']))} | {format_money(float(s['micro_force_eod_net']))} |"
        )
    lines.append("")
    lines.append("## Evidence (Direct Log/Order References)")
    for run in RUNS:
        run_id = run["run"]
        lines.append(f"### {run['label']}")
        evidence = evidence_by_run[run_id]
        summary = summary_by_run.get(run_id, {})
        for key in [
            "Order Error:",
            "OPT_MACRO_RECOVERY: Pending spread entry cancelled",
            "VETO: VASS conviction (BEARISH) overrides Macro (BULLISH)",
            "VETO: MICRO conviction (BEARISH) overrides Macro (BULLISH)",
            "CREDIT_SPREAD: Entry blocked - CREDIT_TO_WIDTH",
            "SPREAD: EXIT_SIGNAL",
            "INTRADAY_FORCE_EXIT",
        ]:
            matches = evidence.get(key, [])
            if not matches:
                continue
            ln, text = matches[0]
            lines.append(f"- `{run['prefix']}_logs.txt:{ln}` {text}")
        orphan_line = summary.get("first_orphan_line")
        orphan_text = summary.get("first_orphan_text") or {}
        if orphan_line:
            lines.append(
                "- "
                f"`{run['prefix']}_orders.csv:{orphan_line}` "
                f"{orphan_text.get('Time','')} {orphan_text.get('Symbol','').strip()} "
                f"{orphan_text.get('Type','')} {orphan_text.get('Status','')} "
                f"Tag={orphan_text.get('Tag','').strip()}"
            )
        invalid_line = summary.get("first_invalid_line")
        invalid_text = summary.get("first_invalid_text") or {}
        if invalid_line:
            lines.append(
                "- "
                f"`{run['prefix']}_orders.csv:{invalid_line}` "
                f"{invalid_text.get('Time','')} {invalid_text.get('Symbol','').strip()} "
                f"{invalid_text.get('Type','')} {invalid_text.get('Status','')} "
                f"Tag={invalid_text.get('Tag','').strip()}"
            )
        lines.append("")
    lines.append("## Output Artifacts")
    lines.append(f"- Metrics: `{out_metrics}`")
    lines.append(f"- Funnel: `{out_funnel}`")
    lines.append(f"- Parameter sensitivity: `{out_params}`")
    lines.append("")
    lines.append("## Action Priority (Next Backtest)")
    lines.append(
        "1. Eliminate MICRO orphan/stop churn path first; this is the largest drag in bull runs."
    )
    lines.append(
        "2. Increase VASS conversion quality by reducing veto overuse in clear bull profile windows."
    )
    lines.append(
        "3. Re-tune credit quality gating in medium-IV bull periods to avoid total conversion collapse."
    )
    return "\n".join(lines) + "\n"


def build_code_map() -> str:
    return """# RCA Bull Regimes - Code Map

## Trade Selection and Gating
- `main.py:4727` VASS conviction resolution vs macro direction.
- `main.py:7034` Intraday VASS conviction resolution.
- `engines/satellite/options_engine.py:4749` `VASS_BULL_SPREAD_REGIME_MIN` floor check.
- `engines/satellite/options_engine.py:4759` `VASS_BULL_MA20_GATE_ENABLED` gate.
- `engines/satellite/options_engine.py:5599` `CREDIT_TO_WIDTH` quality block path.

## Spread Exit Logic
- `engines/satellite/options_engine.py:6661` Spread profit target base (`SPREAD_PROFIT_TARGET_PCT`).
- `engines/satellite/options_engine.py:6696` Spread trail activation/offset.
- `engines/satellite/options_engine.py:6723` Hard stop and adaptive stop handling.
- `engines/satellite/options_engine.py:6511` Spread min-hold guard log path.

## Intraday Exit and Plumbing
- `engines/satellite/options_engine.py:7135` Strategy-aware intraday trailing/exit logic.
- `main.py:2797` EOD MICRO force-exit sweep fallback from holdings.
- `main.py:8538` MICRO recovery/unmatched cancel handling.

## Margin and Retry Path
- `engines/satellite/options_engine.py:5174` Margin pre-sizing and rejection cap handling.
- `engines/satellite/options_engine.py:6486` Spread exit retry cooldown.
- `portfolio/portfolio_router.py:320` Spread margin registration and de-registration helpers.
"""


def main() -> None:
    metric_rows: List[Dict[str, object]] = []
    funnel_rows: List[Dict[str, object]] = []
    param_rows: List[Dict[str, object]] = []
    run_summaries: List[Dict[str, object]] = []
    evidence_by_run: Dict[str, Dict[str, List[Tuple[int, str]]]] = {}

    for run in RUNS:
        run_id = run["run"]
        prefix = run["prefix"]

        logs_path = BASE_DIR / f"{prefix}_logs.txt"
        orders_path = BASE_DIR / f"{prefix}_orders.csv"
        trades_path = BASE_DIR / f"{prefix}_trades.csv"

        orders_by_id, order_rows = load_orders(orders_path)
        trades = load_trades(trades_path, orders_by_id)
        log_data = parse_logs(logs_path)

        counters: Counter = log_data["counters"]  # type: ignore[assignment]
        evidence: Dict[str, List[Tuple[int, str]]] = log_data["evidence"]  # type: ignore[assignment]
        evidence_by_run[run_id] = evidence

        vass_records = [r for r in trades if r.engine == "VASS"]
        micro_records = [r for r in trades if r.engine == "MICRO"]
        all_stats = summarize_records(trades)
        vass_stats = summarize_records(vass_records)
        micro_stats = summarize_records(micro_records)

        invalid_orders = sum(1 for r in order_rows if r.get("Status", "").strip() == "Invalid")
        recon_orphan_orders = sum(
            1 for r in order_rows if "RECON_ORPHAN_OPTION" in r.get("Tag", "")
        )
        first_invalid_line = None
        first_invalid_text = None
        first_orphan_line = None
        first_orphan_text = None
        for idx, row in enumerate(order_rows, start=2):  # +1 for header row
            if first_invalid_line is None and row.get("Status", "").strip() == "Invalid":
                first_invalid_line = idx
                first_invalid_text = row
            if first_orphan_line is None and "RECON_ORPHAN_OPTION" in row.get("Tag", ""):
                first_orphan_line = idx
                first_orphan_text = row
            if first_invalid_line is not None and first_orphan_line is not None:
                break

        vass_signals = counters["SPREAD: ENTRY_SIGNAL"]
        vass_exec = counters["SPREAD: POSITION_REGISTERED"]
        micro_signals = counters["INTRADAY_SIGNAL"]
        micro_exec = counters["OPT: INTRADAY position registered"]
        vass_conv = (100.0 * vass_exec / vass_signals) if vass_signals else 0.0
        micro_conv = (100.0 * micro_exec / micro_signals) if micro_signals else 0.0

        summary = {
            "run": run_id,
            "label": run["label"],
            "market_note": run["market_note"],
            "total_net_pnl": all_stats["net_pnl"],
            "vass_net_pnl": vass_stats["net_pnl"],
            "micro_net_pnl": micro_stats["net_pnl"],
            "vass_conversion_pct": vass_conv,
            "micro_conversion_pct": micro_conv,
            "micro_recon_orphan_net": summarize_records(
                [r for r in micro_records if r.exit_class == "RECON_ORPHAN"]
            )["net_pnl"],
            "micro_stop_net": summarize_records(
                [r for r in micro_records if r.exit_class == "STOP"]
            )["net_pnl"],
            "micro_force_eod_net": summarize_records(
                [r for r in micro_records if r.exit_class == "FORCE_EOD"]
            )["net_pnl"],
            "first_invalid_line": first_invalid_line or "",
            "first_invalid_text": first_invalid_text or {},
            "first_orphan_line": first_orphan_line or "",
            "first_orphan_text": first_orphan_text or {},
        }
        run_summaries.append(summary)

        metric_rows.append(
            {
                "run": run_id,
                "label": run["label"],
                "market_note": run["market_note"],
                "total_trades": all_stats["trades"],
                "total_gross_pnl": round(all_stats["gross_pnl"], 1),
                "total_fees": round(all_stats["fees"], 1),
                "total_net_pnl": round(all_stats["net_pnl"], 1),
                "vass_trades": vass_stats["trades"],
                "vass_win_rate_pct": round(vass_stats["win_rate_pct"], 1),
                "vass_net_pnl": round(vass_stats["net_pnl"], 1),
                "micro_trades": micro_stats["trades"],
                "micro_win_rate_pct": round(micro_stats["win_rate_pct"], 1),
                "micro_net_pnl": round(micro_stats["net_pnl"], 1),
                "vass_signals": vass_signals,
                "vass_executed": vass_exec,
                "vass_conversion_pct": round(vass_conv, 1),
                "micro_signals": micro_signals,
                "micro_executed": micro_exec,
                "micro_conversion_pct": round(micro_conv, 1),
                "vass_bearish_veto_count": counters[
                    "VETO: VASS conviction (BEARISH) overrides Macro (BULLISH)"
                ],
                "micro_bearish_veto_count": counters[
                    "VETO: MICRO conviction (BEARISH) overrides Macro (BULLISH)"
                ],
                "credit_to_width_block_count": counters[
                    "CREDIT_SPREAD: Entry blocked - CREDIT_TO_WIDTH"
                ],
                "order_error_count": counters["Order Error:"],
                "macro_recovery_cancel_count": counters[
                    "OPT_MACRO_RECOVERY: Pending spread entry cancelled"
                ],
                "premarket_itm_check_count": counters["PREMARKET_ITM_CHECK"],
                "intraday_force_exit_count": counters["INTRADAY_FORCE_EXIT"],
                "recon_orphan_order_count": recon_orphan_orders,
                "invalid_order_count": invalid_orders,
            }
        )

        funnel_rows.extend(
            [
                {
                    "run": run_id,
                    "engine": "VASS",
                    "stage": "signals",
                    "count": vass_signals,
                },
                {
                    "run": run_id,
                    "engine": "VASS",
                    "stage": "executed",
                    "count": vass_exec,
                },
                {
                    "run": run_id,
                    "engine": "VASS",
                    "stage": "blocked",
                    "count": max(vass_signals - vass_exec, 0),
                },
                {
                    "run": run_id,
                    "engine": "MICRO",
                    "stage": "signals",
                    "count": micro_signals,
                },
                {
                    "run": run_id,
                    "engine": "MICRO",
                    "stage": "executed",
                    "count": micro_exec,
                },
                {
                    "run": run_id,
                    "engine": "MICRO",
                    "stage": "blocked",
                    "count": max(micro_signals - micro_exec, 0),
                },
            ]
        )

        param_rows.extend(build_param_rows(run_id, trades, log_data))

    out_metrics = BASE_DIR / "RCA_Bull_Regimes_Stage10_metrics.csv"
    out_funnel = BASE_DIR / "RCA_Bull_Regimes_Stage10_funnel.csv"
    out_params = BASE_DIR / "RCA_Bull_Regimes_Stage10_param_sensitivity.csv"
    out_report = BASE_DIR / "RCA_Bull_Regimes_Stage10.md"
    out_code_map = BASE_DIR / "RCA_Bull_Regimes_Stage10_code_map.md"

    write_csv(
        out_metrics,
        metric_rows,
        [
            "run",
            "label",
            "market_note",
            "total_trades",
            "total_gross_pnl",
            "total_fees",
            "total_net_pnl",
            "vass_trades",
            "vass_win_rate_pct",
            "vass_net_pnl",
            "micro_trades",
            "micro_win_rate_pct",
            "micro_net_pnl",
            "vass_signals",
            "vass_executed",
            "vass_conversion_pct",
            "micro_signals",
            "micro_executed",
            "micro_conversion_pct",
            "vass_bearish_veto_count",
            "micro_bearish_veto_count",
            "credit_to_width_block_count",
            "order_error_count",
            "macro_recovery_cancel_count",
            "premarket_itm_check_count",
            "intraday_force_exit_count",
            "recon_orphan_order_count",
            "invalid_order_count",
        ],
    )

    write_csv(out_funnel, funnel_rows, ["run", "engine", "stage", "count"])

    write_csv(
        out_params,
        param_rows,
        [
            "run",
            "engine",
            "parameter",
            "bucket",
            "trades",
            "win_rate_pct",
            "gross_pnl",
            "fees",
            "net_pnl",
            "notes",
        ],
    )

    report = build_report(run_summaries, evidence_by_run, out_metrics, out_funnel, out_params)
    out_report.write_text(report)
    out_code_map.write_text(build_code_map())

    print(f"Wrote {out_report}")
    print(f"Wrote {out_metrics}")
    print(f"Wrote {out_funnel}")
    print(f"Wrote {out_params}")
    print(f"Wrote {out_code_map}")


if __name__ == "__main__":
    main()
