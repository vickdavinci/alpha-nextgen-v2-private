"""QuantConnect Research Object Store loader for Alpha NextGen observability artifacts.

Usage in QC Research notebook:
1) Upload this file to the project (or paste into a notebook cell).
2) Set RUN_NAME and BACKTEST_YEAR below.
3) Run the script cell.

It reads observability CSV artifacts directly from QC Object Store (no export/download),
handles single and sharded payloads, and prints detector/handoff + exit-plumbing summaries.
"""

from __future__ import annotations

import io
import json
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from QuantConnect.Research import QuantBook

# ---------- Runtime knobs ----------
RUN_NAME = "V12.4-JulSep2024-R2"
BACKTEST_YEAR = 2024
MAX_LEGACY_PARTS = 64
JOIN_TOLERANCE_HOURS = 6

ARTIFACT_PREFIXES = {
    "regime_decisions": "regime_observability",
    "regime_timeline": "regime_timeline_observability",
    "signal_lifecycle": "signal_lifecycle_observability",
    "router_rejections": "router_rejection_observability",
    "order_lifecycle": "order_lifecycle_observability",
}


def _safe_key_component(value: Any, default: str = "run") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or default


def _base_key(prefix: str, run_name: str, backtest_year: int) -> str:
    run_suffix = _safe_key_component(run_name, "run")
    year = _safe_key_component(backtest_year, "year")
    return f"{prefix}__{run_suffix}_{year}.csv"


def _key_candidates(key: str) -> List[str]:
    base = str(key or "").lstrip("/")
    out: List[str] = []
    for candidate in (base, f"/{base}"):
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def _read_text_from_store(qb: QuantBook, key: str) -> Tuple[Optional[str], Optional[str]]:
    for candidate in _key_candidates(key):
        if qb.object_store.contains_key(candidate):
            payload = qb.object_store.read(candidate)
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="ignore")
            return candidate, str(payload or "")
    return None, None


def _manifest_key(base_key: str) -> str:
    root = base_key[:-4] if base_key.endswith(".csv") else base_key
    return f"{root}__manifest.json"


def _part_key(base_key: str, idx: int) -> str:
    root = base_key[:-4] if base_key.endswith(".csv") else base_key
    return f"{root}__part{idx:03d}.csv"


def _merge_csv_parts(part_payloads: List[str]) -> str:
    lines_out: List[str] = []
    wrote_header = False
    for payload in part_payloads:
        lines = payload.splitlines()
        if not lines:
            continue
        if not wrote_header:
            lines_out.extend(lines)
            wrote_header = True
            continue
        lines_out.extend(lines[1:])
    return "\n".join(lines_out) + ("\n" if lines_out else "")


def _read_csv_artifact(qb: QuantBook, base_key: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    direct_key, direct_payload = _read_text_from_store(qb, base_key)
    if direct_payload:
        return pd.read_csv(io.StringIO(direct_payload)), {"mode": "single", "key": direct_key}

    manifest_store_key = None
    manifest_payload = None
    manifest_key = _manifest_key(base_key)
    manifest_store_key, manifest_payload = _read_text_from_store(qb, manifest_key)

    part_count = 0
    if manifest_payload:
        try:
            manifest = json.loads(manifest_payload)
            part_count = int(manifest.get("parts", 0) or 0)
        except Exception:
            part_count = 0

    part_payloads: List[str] = []
    if part_count > 0:
        indexes = range(1, part_count + 1)
    else:
        indexes = range(1, MAX_LEGACY_PARTS + 1)

    for idx in indexes:
        _, payload = _read_text_from_store(qb, _part_key(base_key, idx))
        if not payload:
            if part_count > 0:
                raise FileNotFoundError(f"Missing shard {idx} for {base_key}")
            break
        part_payloads.append(payload)

    if not part_payloads:
        raise FileNotFoundError(f"Object Store artifact not found: {base_key}")

    merged = _merge_csv_parts(part_payloads)
    if not merged.strip():
        raise ValueError(f"Merged shard payload is empty: {base_key}")

    return pd.read_csv(io.StringIO(merged)), {
        "mode": "sharded",
        "key": manifest_store_key or base_key,
        "parts": len(part_payloads),
    }


def _parse_time_column(df: pd.DataFrame) -> pd.DataFrame:
    if "time" not in df.columns:
        return df
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], errors="coerce")
    out = out.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    return out


def _pick_col(df: pd.DataFrame, names: List[str]) -> Optional[str]:
    for name in names:
        if name in df.columns:
            return name
    return None


def _row_text(df: pd.DataFrame) -> pd.Series:
    cols = [c for c in df.columns if str(df[c].dtype) == "object"]
    if not cols:
        cols = list(df.columns)
    return df[cols].astype(str).agg(" | ".join, axis=1).str.upper()


def load_objectstore_artifacts(
    run_name: str = RUN_NAME,
    backtest_year: int = BACKTEST_YEAR,
) -> Tuple[QuantBook, Dict[str, pd.DataFrame], Dict[str, Dict[str, Any]]]:
    qb = QuantBook()
    qb.object_store.clear()

    loaded: Dict[str, pd.DataFrame] = {}
    metadata: Dict[str, Dict[str, Any]] = {}

    for label, prefix in ARTIFACT_PREFIXES.items():
        key = _base_key(prefix, run_name, backtest_year)
        try:
            df, info = _read_csv_artifact(qb, key)
            loaded[label] = _parse_time_column(df)
            metadata[label] = {"base_key": key, **info, "rows": int(len(df))}
            print(f"[OK] {label}: rows={len(df)} | key={info['key']} | mode={info['mode']}")
        except Exception as err:  # noqa: BLE001
            metadata[label] = {"base_key": key, "error": str(err)}
            print(f"[MISS] {label}: {err}")
    return qb, loaded, metadata


def summarize_detector_handoff(loaded: Dict[str, pd.DataFrame]) -> None:
    print("\n=== Detector/Handoff Summary ===")

    timeline = loaded.get("regime_timeline")
    decisions = loaded.get("regime_decisions")
    signals = loaded.get("signal_lifecycle")

    if timeline is not None and "transition_overlay" in timeline.columns:
        overlays = timeline["transition_overlay"].astype(str).value_counts().to_dict()
        flips = (
            int((timeline["transition_overlay"] != timeline["transition_overlay"].shift(1)).sum())
            - 1
        )
        print(f"Timeline rows: {len(timeline)} | overlay flips: {max(flips, 0)}")
        print(f"Overlay counts: {overlays}")
    else:
        print("Timeline artifact missing or transition_overlay column missing")

    if decisions is not None and {"engine", "engine_decision"}.issubset(decisions.columns):
        top_decisions = (
            decisions.assign(
                engine=decisions["engine"].astype(str).str.upper(),
                engine_decision=decisions["engine_decision"].astype(str).str.upper(),
            )
            .groupby(["engine", "engine_decision"])
            .size()
            .sort_values(ascending=False)
            .head(12)
        )
        print("Top regime decision tuples:")
        print(top_decisions)
    else:
        print("Regime decisions artifact missing or required columns missing")

    if signals is not None and {"engine", "event"}.issubset(signals.columns):
        top_events = (
            signals.assign(
                engine=signals["engine"].astype(str).str.upper(),
                event=signals["event"].astype(str).str.upper(),
            )
            .groupby(["engine", "event"])
            .size()
            .sort_values(ascending=False)
            .head(20)
        )
        print("Top signal lifecycle tuples:")
        print(top_events)
    else:
        print("Signal lifecycle artifact missing or required columns missing")


def summarize_vass_overlay_and_exit_plumbing(loaded: Dict[str, pd.DataFrame]) -> None:
    print("\n=== VASS Overlay + Exit Plumbing ===")
    sig_df = loaded.get("signal_lifecycle")
    tl_df = loaded.get("regime_timeline")
    order_df = loaded.get("order_lifecycle")
    router_df = loaded.get("router_rejections")

    if sig_df is None or tl_df is None:
        print("Skipped: missing signal_lifecycle or regime_timeline")
        return
    if "time" not in sig_df.columns or "time" not in tl_df.columns:
        print("Skipped: missing time column")
        return

    engine_col = _pick_col(sig_df, ["engine"])
    event_col = _pick_col(sig_df, ["event"])
    if not engine_col or not event_col:
        print("Skipped: missing engine/event columns in signal_lifecycle")
        return

    vass = sig_df[sig_df[engine_col].astype(str).str.upper().str.contains("VASS", na=False)].copy()
    if vass.empty:
        print("No VASS rows found in signal_lifecycle")
        return
    vass["event_u"] = vass[event_col].astype(str).str.upper()

    tl_cols = ["time"]
    for col in ("transition_overlay", "base_regime"):
        if col in tl_df.columns:
            tl_cols.append(col)
    tl_join = tl_df[tl_cols].sort_values("time").reset_index(drop=True)
    joined = pd.merge_asof(
        vass.sort_values("time").reset_index(drop=True),
        tl_join,
        on="time",
        direction="backward",
        tolerance=timedelta(hours=JOIN_TOLERANCE_HOURS),
    )
    overlay_col = "transition_overlay" if "transition_overlay" in joined.columns else None
    if overlay_col:
        ctab = pd.crosstab(joined[overlay_col].fillna("NO_MATCH"), joined["event_u"])
        print("VASS events by overlay:")
        print(ctab)

    if order_df is None or router_df is None:
        print("Skipped exit plumbing summary: missing order_lifecycle or router_rejections")
        return

    cat_pattern = r"VASS_TAIL_RISK_CAP|SPREAD_HARD_STOP_DURING_HOLD|TAIL_RISK_CAP|HARD_STOP"
    cat_rows = order_df[_row_text(order_df).str.contains(cat_pattern, na=False)].copy()
    cat_rows = cat_rows.sort_values("time") if "time" in cat_rows.columns else cat_rows

    approved = vass[vass["event_u"] == "APPROVED"][["time"]].sort_values("time")
    reentries = 0
    if "time" in cat_rows.columns and not approved.empty:
        for ts in cat_rows["time"]:
            if pd.isna(ts):
                continue
            same_day = approved[(approved["time"].dt.date == ts.date()) & (approved["time"] > ts)]
            if not same_day.empty:
                reentries += 1
    print(f"Catastrophic exit rows={len(cat_rows)} | same-session re-entry rows={reentries}")

    qinv_pattern = r"R_CONTRACT_QUOTE_INVALID|EXIT_NET_VALUE_NEGATIVE"
    qinv = router_df[_row_text(router_df).str.contains(qinv_pattern, na=False)].copy()
    qinv = qinv.sort_values("time") if "time" in qinv.columns else qinv

    nearby = 0
    if "time" in cat_rows.columns and "time" in qinv.columns:
        for ts in cat_rows["time"]:
            if pd.isna(ts):
                continue
            hit = (
                (qinv["time"] >= ts - timedelta(minutes=10))
                & (qinv["time"] <= ts + timedelta(minutes=30))
            ).any()
            nearby += int(hit)
    print(f"Quote-invalid rows={len(qinv)} | catastrophic exits with nearby quote-invalid={nearby}")

    sample_cols = [
        c
        for c in ["time", "stage", "code", "symbol", "source_tag", "trace_id", "detail", "engine"]
        if c in qinv.columns
    ]
    if sample_cols:
        print(qinv[sample_cols].head(20))


def run(
    run_name: str = RUN_NAME,
    backtest_year: int = BACKTEST_YEAR,
) -> Tuple[QuantBook, Dict[str, pd.DataFrame], Dict[str, Dict[str, Any]]]:
    qb, loaded, metadata = load_objectstore_artifacts(
        run_name=run_name, backtest_year=backtest_year
    )
    print("\n=== Load Metadata ===")
    print(json.dumps(metadata, indent=2, default=str))
    summarize_detector_handoff(loaded)
    summarize_vass_overlay_and_exit_plumbing(loaded)
    return qb, loaded, metadata


# Execute by default in notebook usage.
qb, loaded_artifacts, load_meta = run()
