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
REQUIRE_EXACT_RUN_KEYS = False

ARTIFACT_PREFIXES = {
    "regime_decisions": "regime_observability",
    "regime_timeline": "regime_timeline_observability",
    "signal_lifecycle": "signal_lifecycle_observability",
    "router_rejections": "router_rejection_observability",
    "order_lifecycle": "order_lifecycle_observability",
}

EXPECTED_ARTIFACT_COLUMNS: Dict[str, List[str]] = {
    "regime_decisions": [
        "time",
        "engine",
        "engine_decision",
        "strategy_attempted",
        "gate_name",
    ],
    "regime_timeline": [
        "time",
        "source",
        "transition_overlay",
        "base_regime",
        "effective_score",
    ],
    "signal_lifecycle": [
        "time",
        "engine",
        "event",
        "signal_id",
        "trace_id",
        "code",
    ],
    "router_rejections": [
        "time",
        "stage",
        "code",
        "symbol",
        "source_tag",
        "trace_id",
        "incident_id",
    ],
    "order_lifecycle": [
        "time",
        "status",
        "order_id",
        "symbol",
        "order_tag",
        "tag_origin",
        "trace_id",
        "incident_id",
    ],
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


def _base_key_candidates(prefix: str, run_name: str, backtest_year: int) -> List[str]:
    """Return candidate ObjectStore keys for modern and legacy run labels."""
    year = _safe_key_component(backtest_year, "year")
    run_suffixes = [
        _safe_key_component(run_name, "run"),
        f"year_{year}",
        "DEFAULT",
        "default",
    ]
    seen: set[str] = set()
    out: List[str] = []
    for suffix in run_suffixes:
        key = f"{prefix}__{suffix}_{year}.csv"
        if key not in seen:
            out.append(key)
            seen.add(key)
    return out


def _fallback_key_candidates(prefix: str, backtest_year: int) -> List[str]:
    """Return non-run-pinned fallback keys for diagnostics only."""
    year = _safe_key_component(backtest_year, "year")
    return [
        f"{prefix}__year_{year}_{year}.csv",
        f"{prefix}__DEFAULT_{year}.csv",
        f"{prefix}__default_{year}.csv",
    ]


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


def _key_exists(qb: QuantBook, key: str) -> bool:
    return any(qb.object_store.contains_key(candidate) for candidate in _key_candidates(key))


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


def _read_csv_artifact(
    qb: QuantBook, base_key: str, expected_columns: Optional[List[str]] = None
) -> Tuple[Optional[pd.DataFrame], Dict[str, Any]]:
    """Return (DataFrame, info) on success, or (None, {"error": msg}) on miss."""
    direct_key, direct_payload = _read_text_from_store(qb, base_key)
    if direct_payload is not None:
        payload_text = str(direct_payload or "")
        if not payload_text.strip():
            empty_df = pd.DataFrame(columns=list(expected_columns or []))
            return empty_df, {"mode": "single", "key": direct_key, "empty_artifact": True}
        try:
            return pd.read_csv(io.StringIO(payload_text)), {"mode": "single", "key": direct_key}
        except pd.errors.EmptyDataError:
            empty_df = pd.DataFrame(columns=list(expected_columns or []))
            return empty_df, {"mode": "single", "key": direct_key, "empty_artifact": True}

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
                return None, {"error": f"Missing shard {idx} for {base_key}"}
            # Legacy/sparse shard mode: keep scanning; some runs can have non-contiguous parts.
            continue
        part_payloads.append(payload)

    if not part_payloads:
        return None, {"error": f"Object Store artifact not found: {base_key}"}

    merged = _merge_csv_parts(part_payloads)
    if not merged.strip():
        empty_df = pd.DataFrame(columns=list(expected_columns or []))
        return empty_df, {
            "mode": "sharded",
            "key": manifest_store_key or base_key,
            "parts": len(part_payloads),
            "empty_artifact": True,
        }

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
    require_exact_run_keys: bool = REQUIRE_EXACT_RUN_KEYS,
) -> Tuple[QuantBook, Dict[str, pd.DataFrame], Dict[str, Dict[str, Any]]]:
    qb = QuantBook()

    loaded: Dict[str, pd.DataFrame] = {}
    metadata: Dict[str, Dict[str, Any]] = {}

    for label, prefix in ARTIFACT_PREFIXES.items():
        attempted_keys: List[str] = []
        last_error: Optional[str] = None
        found = False
        expected_columns = EXPECTED_ARTIFACT_COLUMNS.get(label, [])
        exact_key = _base_key(prefix, run_name, backtest_year)
        fallback_keys = _fallback_key_candidates(prefix, backtest_year)
        fallback_present = [key for key in fallback_keys if _key_exists(qb, key)]
        if require_exact_run_keys:
            candidate_keys = [exact_key]
        else:
            candidate_keys = _base_key_candidates(prefix, run_name, backtest_year)

        for key in candidate_keys:
            attempted_keys.append(key)
            try:
                df, info = _read_csv_artifact(qb, key, expected_columns=expected_columns)
                if df is None:
                    last_error = info.get("error", "unknown")
                    continue
                missing_cols: List[str] = []
                for col in expected_columns:
                    if col not in df.columns:
                        df[col] = ""
                        missing_cols.append(col)
                loaded[label] = _parse_time_column(df)
                metadata[label] = {
                    "require_exact_run_keys": require_exact_run_keys,
                    "exact_key": exact_key,
                    "exact_key_found": key == exact_key,
                    "fallback_keys_present": fallback_present,
                    "base_key": key,
                    "attempted_keys": attempted_keys,
                    **info,
                    "rows": int(len(df)),
                    "schema_missing_columns": missing_cols,
                }
                fallback_hint = (
                    f" | fallbacks_present={fallback_present}" if fallback_present else ""
                )
                missing_hint = f" | schema_backfill={missing_cols}" if missing_cols else ""
                empty_hint = " | empty_artifact=True" if bool(info.get("empty_artifact")) else ""
                print(
                    f"[OK] {label}: rows={len(df)} | key={info['key']} | mode={info['mode']} | base={key}"
                    f"{empty_hint}{missing_hint}{fallback_hint}"
                )
                found = True
                break
            except Exception as err:
                last_error = str(err)
        if not found:
            metadata[label] = {
                "require_exact_run_keys": require_exact_run_keys,
                "exact_key": exact_key,
                "exact_key_found": False,
                "fallback_keys_present": fallback_present,
                "base_key": attempted_keys[0]
                if attempted_keys
                else _base_key(prefix, run_name, backtest_year),
                "attempted_keys": attempted_keys,
                "error": last_error or "unknown error",
            }
            fallback_hint = f" | fallbacks_present={fallback_present}" if fallback_present else ""
            print(f"[MISS] {label}: {last_error} | attempted={attempted_keys}{fallback_hint}")
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


def summarize_engine_funnel_and_blockers(loaded: Dict[str, pd.DataFrame]) -> None:
    print("\n=== Engine Funnel + Blockers (Every Run) ===")
    sig_df = loaded.get("signal_lifecycle")
    if sig_df is None or sig_df.empty:
        print("Skipped: missing signal_lifecycle")
        return

    engine_col = _pick_col(sig_df, ["engine"])
    event_col = _pick_col(sig_df, ["event"])
    if not engine_col or not event_col:
        print("Skipped: missing engine/event columns")
        return

    df = sig_df.copy()
    df["engine_u"] = df[engine_col].astype(str).str.upper()
    df["event_u"] = df[event_col].astype(str).str.upper()

    funnel = df.groupby(["engine_u", "event_u"]).size().unstack(fill_value=0).sort_index()
    for col in ("CANDIDATE", "APPROVED", "DROPPED"):
        if col not in funnel.columns:
            funnel[col] = 0
    funnel = funnel[["CANDIDATE", "APPROVED", "DROPPED"]]
    funnel["APPROVAL_RATE_PCT"] = (
        (funnel["APPROVED"] / funnel["CANDIDATE"].replace(0, pd.NA)) * 100.0
    ).fillna(0.0)
    print("Engine funnel (candidate/approved/dropped):")
    print(funnel.sort_values(["CANDIDATE", "APPROVED"], ascending=False))

    vass_drop = df[(df["engine_u"].str.contains("VASS", na=False)) & (df["event_u"] == "DROPPED")]
    if vass_drop.empty:
        print("No VASS DROPPED rows found")
        return

    code_col = _pick_col(vass_drop, ["code"])
    gate_col = _pick_col(vass_drop, ["gate_name"])
    reason_col = _pick_col(vass_drop, ["reason"])
    dir_col = _pick_col(vass_drop, ["direction"])
    strategy_col = _pick_col(vass_drop, ["strategy"])

    if code_col:
        print("\nTop VASS drop codes:")
        print(vass_drop[code_col].astype(str).value_counts().head(30))
    if gate_col:
        print("\nTop VASS gate_name:")
        print(vass_drop[gate_col].astype(str).value_counts().head(30))
    if reason_col:
        print("\nTop VASS reason:")
        print(vass_drop[reason_col].astype(str).value_counts().head(20))
    if dir_col and strategy_col:
        print("\nVASS dropped by direction+strategy:")
        print(
            vass_drop.assign(
                direction_u=vass_drop[dir_col].astype(str).str.upper(),
                strategy_u=vass_drop[strategy_col].astype(str).str.upper(),
            )
            .groupby(["direction_u", "strategy_u"])
            .size()
            .sort_values(ascending=False)
            .head(20)
        )


def summarize_router_and_trace_health(loaded: Dict[str, pd.DataFrame]) -> None:
    print("\n=== Router + Trace Health (Every Run) ===")
    router_df = loaded.get("router_rejections")
    sig_df = loaded.get("signal_lifecycle")
    order_df = loaded.get("order_lifecycle")

    if router_df is None or router_df.empty:
        print("Router rejections: missing or empty")
    else:
        code_col = _pick_col(router_df, ["code"])
        stage_col = _pick_col(router_df, ["stage"])
        src_col = _pick_col(router_df, ["source_tag"])
        trace_col = _pick_col(router_df, ["trace_id"])
        print(f"Router rejection rows: {len(router_df)}")
        if code_col:
            print("Top router codes:")
            print(router_df[code_col].astype(str).value_counts().head(20))
        if stage_col:
            print("Top router stages:")
            print(router_df[stage_col].astype(str).value_counts().head(10))
        if src_col:
            print("Top router source_tag:")
            print(router_df[src_col].astype(str).value_counts().head(10))
        if trace_col:
            trace_non_empty = (router_df[trace_col].astype(str).str.strip() != "").sum()
            print(
                f"Router trace coverage: {trace_non_empty}/{len(router_df)} "
                f"({(100.0 * trace_non_empty / max(1, len(router_df))):.1f}%)"
            )

    if sig_df is None or sig_df.empty:
        print("Signal lifecycle trace coverage: missing signal_lifecycle")
    else:
        event_col = _pick_col(sig_df, ["event"])
        trace_col = _pick_col(sig_df, ["trace_id"])
        if event_col and trace_col:
            approved = sig_df[sig_df[event_col].astype(str).str.upper() == "APPROVED"]
            if approved.empty:
                print("Approved signal trace coverage: no APPROVED rows")
            else:
                non_empty = (approved[trace_col].astype(str).str.strip() != "").sum()
                print(
                    f"Approved signal trace coverage: {non_empty}/{len(approved)} "
                    f"({(100.0 * non_empty / max(1, len(approved))):.1f}%)"
                )

    if order_df is None or order_df.empty:
        print("Order lifecycle: missing or empty")
    else:
        code_col = _pick_col(order_df, ["code"])
        stage_col = _pick_col(order_df, ["stage"])
        trace_col = _pick_col(order_df, ["trace_id"])
        print(f"Order lifecycle rows: {len(order_df)}")
        if code_col:
            print("Top order lifecycle codes:")
            print(order_df[code_col].astype(str).value_counts().head(20))
        if stage_col:
            print("Top order lifecycle stages:")
            print(order_df[stage_col].astype(str).value_counts().head(10))
        if trace_col:
            non_empty = (order_df[trace_col].astype(str).str.strip() != "").sum()
            print(
                f"Order trace coverage: {non_empty}/{len(order_df)} "
                f"({(100.0 * non_empty / max(1, len(order_df))):.1f}%)"
            )


def summarize_every_run_checklist(
    loaded: Dict[str, pd.DataFrame], metadata: Dict[str, Dict[str, Any]]
) -> None:
    print("\n=== ObjectStore Checklist (Source of Truth for Every Run) ===")
    required = [
        "regime_decisions",
        "regime_timeline",
        "signal_lifecycle",
        "router_rejections",
        "order_lifecycle",
    ]
    for key in required:
        info = metadata.get(key, {})
        if "error" in info:
            print(f"[MISSING] {key}: {info.get('error')}")
        else:
            print(
                f"[READY] {key}: rows={info.get('rows', 0)} | "
                f"mode={info.get('mode', 'unknown')} | key={info.get('key', info.get('base_key', ''))}"
            )
        exact_key = info.get("exact_key")
        if exact_key:
            print(
                f"        exact_key={exact_key} | exact_found={info.get('exact_key_found', False)}"
            )
            if info.get("fallback_keys_present"):
                print(f"        fallback_keys_present={info.get('fallback_keys_present')}")

    print("\nRequired analysis blocks per run:")
    print("1) Detector/Handoff health: overlay flips + STABLE/DETERIORATION/RECOVERY mix.")
    print("2) Engine funnel: CANDIDATE/APPROVED/DROPPED with approval-rate by engine.")
    print("3) VASS blocker drilldown: top code/gate_name/reason + direction/strategy splits.")
    print("4) Router health: top reject code/stage/source_tag + trace coverage.")
    print(
        "5) Exit plumbing health: catastrophic exits, quote-invalid coupling, same-session re-entry."
    )
    print("6) Trace integrity: APPROVED signal trace coverage + order trace coverage.")


def run(
    run_name: str = RUN_NAME,
    backtest_year: int = BACKTEST_YEAR,
    require_exact_run_keys: bool = REQUIRE_EXACT_RUN_KEYS,
) -> Tuple[QuantBook, Dict[str, pd.DataFrame], Dict[str, Dict[str, Any]]]:
    print(
        f"Starting ObjectStore load | run_name={run_name} | backtest_year={backtest_year} "
        f"| require_exact_run_keys={require_exact_run_keys}"
    )
    qb, loaded, metadata = load_objectstore_artifacts(
        run_name=run_name,
        backtest_year=backtest_year,
        require_exact_run_keys=require_exact_run_keys,
    )
    print("\n=== Load Metadata ===")
    print(json.dumps(metadata, indent=2, default=str))
    summarize_every_run_checklist(loaded, metadata)
    summarize_detector_handoff(loaded)
    summarize_engine_funnel_and_blockers(loaded)
    summarize_vass_overlay_and_exit_plumbing(loaded)
    summarize_router_and_trace_health(loaded)
    return qb, loaded, metadata


# Execute by default in notebook usage.
qb, loaded_artifacts, load_meta = run()
