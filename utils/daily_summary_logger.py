from __future__ import annotations

import config
from models.enums import RegimeLevel


def log_daily_summary(algo) -> None:
    def _log(message: str, priority: int = 2) -> None:
        budget_log = getattr(algo, "_budget_log", None)
        if callable(budget_log):
            budget_log(message, priority=priority)
            return
        algo.Log(message)

    ending_equity = algo.Portfolio.TotalPortfolioValue
    regime_score = float(
        algo._last_regime_score
        if algo._last_regime_score is not None
        else algo.regime_engine.get_previous_score()
    )
    if regime_score >= config.REGIME_RISK_ON:
        regime_state_label = RegimeLevel.RISK_ON.value
    elif regime_score >= config.REGIME_NEUTRAL:
        regime_state_label = RegimeLevel.NEUTRAL.value
    elif regime_score >= config.REGIME_CAUTIOUS:
        regime_state_label = RegimeLevel.CAUTIOUS.value
    elif regime_score >= config.REGIME_DEFENSIVE:
        regime_state_label = RegimeLevel.DEFENSIVE.value
    else:
        regime_state_label = RegimeLevel.RISK_OFF.value

    summary = algo.scheduler.get_day_summary(
        starting_equity=algo.equity_sod,
        ending_equity=ending_equity,
        trades=algo.today_trades,
        safeguards=algo.today_safeguards,
        moo_orders=[],
        regime_score=regime_score,
        regime_state=regime_state_label,
        days_running=algo.cold_start_engine.get_days_running(),
    )

    _log(summary, priority=1)
    spread_exit_fill_strict = algo._diag_spread_exit_fill_count
    if algo._order_lifecycle_suppressed_count > 0:
        _log(
            f"ORDER_LIFECYCLE_CAP_HIT: Logged={algo._order_lifecycle_log_count} | "
            f"Suppressed={algo._order_lifecycle_suppressed_count} | "
            f"Cap={int(getattr(config, 'LOG_ORDER_LIFECYCLE_MAX_PER_DAY', 200))}",
            priority=2,
        )
    sampled_suppressed = getattr(algo, "_high_freq_log_suppressed_counts", {}) or {}
    budget_bytes = int(getattr(algo, "_log_budget_bytes_used", 0) or 0)
    budget_supp_total = int(getattr(algo, "_log_budget_suppressed_total", 0) or 0)
    budget_supp_by_priority = getattr(algo, "_log_budget_suppressed_by_priority", {}) or {}
    if sampled_suppressed or budget_supp_total > 0:
        top_sampled = sorted(sampled_suppressed.items(), key=lambda kv: kv[1], reverse=True)[:5]
        top_sampled_str = (
            ";".join(f"{k}:{int(v)}" for k, v in top_sampled) if top_sampled else "NONE"
        )
        budget_supp_str = (
            f"P1:{int(budget_supp_by_priority.get('P1', 0))};"
            f"P2:{int(budget_supp_by_priority.get('P2', 0))};"
            f"P3:{int(budget_supp_by_priority.get('P3', 0))}"
        )
        _log(
            "LOG_BUDGET_SUMMARY: "
            f"SampleSuppressed={int(sum(sampled_suppressed.values()))} | "
            f"Top={top_sampled_str} | "
            f"BytesUsed={budget_bytes} | "
            f"BudgetSuppressed={budget_supp_total}({budget_supp_str}) | "
            f"SampleFirstN={int(getattr(config, 'LOG_HIGHFREQ_SAMPLE_FIRST_N_PER_KEY', 1))} | "
            f"SampleEveryN={int(getattr(config, 'LOG_HIGHFREQ_SAMPLE_EVERY_N', 0))}",
            priority=1,
        )

    def _fmt_drop_rca_daily() -> str:
        agg = getattr(algo, "_daily_drop_reason_agg", {}) or {}
        if not agg:
            return "NONE"
        max_reasons = max(1, int(getattr(config, "LOG_DROP_AGG_MAX_REASONS_PER_CATEGORY", 8)))
        preferred_order = ["INTRADAY_BLOCKED", "INTRADAY_DROPPED", "VASS_FALLBACK"]
        ordered_categories = [c for c in preferred_order if c in agg]
        ordered_categories.extend(c for c in sorted(agg.keys()) if c not in preferred_order)
        category_tokens = []
        for category in ordered_categories:
            bucket = agg.get(category, {}) or {}
            if not bucket:
                continue
            ranked = sorted(
                bucket.items(),
                key=lambda kv: int((kv[1] or {}).get("count", 0)),
                reverse=True,
            )[:max_reasons]
            reason_tokens = []
            for reason, payload in ranked:
                payload = payload or {}
                count = int(payload.get("count", 0))
                first = str(payload.get("first", "") or "")
                last = str(payload.get("last", "") or "")
                if first and last:
                    window = first if first == last else f"{first}-{last}"
                    reason_tokens.append(f"{reason}:{count}@{window}")
                else:
                    reason_tokens.append(f"{reason}:{count}")
            if reason_tokens:
                category_tokens.append(f"{category}[{';'.join(reason_tokens)}]")
        return " ".join(category_tokens) if category_tokens else "NONE"

    drop_rca_daily = _fmt_drop_rca_daily()
    if drop_rca_daily != "NONE":
        _log(f"DROP_RCA_DAILY: {drop_rca_daily}", priority=1)

    dte_order = ["2", "3", "4", "5", "OTHER"]
    fmt = lambda d: ",".join(f"{k}:{int(d.get(k, 0))}" for k in dte_order)
    top_drop_pairs = sorted(
        algo._diag_micro_drop_reason_by_dte.items(), key=lambda kv: kv[1], reverse=True
    )[:5]
    top_drop = ";".join(f"{k}={v}" for k, v in top_drop_pairs) if top_drop_pairs else "NONE"
    _log(
        "MICRO_DTE_DIAG_SUMMARY: "
        f"Cand[{fmt(algo._diag_micro_dte_candidates)}] | "
        f"Approved[{fmt(algo._diag_micro_dte_approved)}] | "
        f"Dropped[{fmt(algo._diag_micro_dte_dropped)}] | "
        f"Win[{fmt(algo._diag_micro_dte_win)}] | "
        f"Loss[{fmt(algo._diag_micro_dte_loss)}] | "
        f"TopDrop[{top_drop}]",
        priority=1,
    )

    top_router_rejects = sorted(
        algo._diag_router_reject_reason_counts.items(), key=lambda kv: kv[1], reverse=True
    )[:5]
    top_router_rejects_str = (
        ";".join(f"{code}:{count}" for code, count in top_router_rejects)
        if top_router_rejects
        else "NONE"
    )

    def _top_counts(counts: dict, top_n: int = 3) -> str:
        pairs = sorted((counts or {}).items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        return ";".join(f"{k}:{int(v)}" for k, v in pairs) if pairs else "NONE"

    def _fmt_engine_top_rejects() -> str:
        out = []
        engines = ["VASS", "MICRO", "ITM"]
        if algo._diag_router_reject_reason_counts_by_engine.get("OTHER", {}):
            engines.append("OTHER")
        for engine in engines:
            store = algo._diag_router_reject_reason_counts_by_engine.get(engine, {})
            out.append(f"{engine}[{_top_counts(store)}]")
        return " ".join(out)

    def _fmt_engine_exit_diag() -> str:
        out = []
        engines = ["VASS", "MICRO", "ITM"]
        if algo._diag_exit_path_counts_by_engine.get(
            "OTHER", {}
        ) or algo._diag_exit_path_pnl_by_engine.get("OTHER", {}):
            engines.append("OTHER")
        for engine in engines:
            cnt_store = algo._diag_exit_path_counts_by_engine.get(engine, {})
            pnl_store = algo._diag_exit_path_pnl_by_engine.get(engine, {})
            cnt_top = _top_counts(cnt_store)
            pnl_pairs = sorted(
                (pnl_store or {}).items(), key=lambda kv: abs(float(kv[1])), reverse=True
            )[:3]
            pnl_top = (
                ";".join(f"{k}:{float(v):+.0f}" for k, v in pnl_pairs) if pnl_pairs else "NONE"
            )
            out.append(f"{engine}[C={cnt_top}|P={pnl_top}]")
        return " ".join(out)

    def _fmt_intraday_funnel_by_engine() -> str:
        cand = algo._diag_intraday_candidates_by_engine
        app = algo._diag_intraday_approved_by_engine
        drp = algo._diag_intraday_dropped_by_engine
        res = algo._diag_intraday_results_by_engine
        engines = ["ITM", "MICRO"]
        if any(
            int(store.get("OTHER", 0)) > 0
            for store in (cand or {}, app or {}, drp or {}, res or {})
        ):
            engines.append("OTHER")
        parts = []
        for engine in engines:
            parts.append(
                f"{engine}({int(cand.get(engine, 0))}/{int(app.get(engine, 0))}/{int(drp.get(engine, 0))}/{int(res.get(engine, 0))})"
            )
        return " ".join(parts)

    def _fmt_intraday_drop_reasons_by_engine() -> str:
        out = []
        stores = getattr(algo, "_diag_intraday_drop_reason_counts_by_engine", {})
        for engine in ("MICRO", "ITM", "OTHER"):
            out.append(f"{engine}[{_top_counts(stores.get(engine, {}), top_n=3)}]")
        return " ".join(out)

    def _fmt_transition_derisk_totals() -> str:
        store = getattr(algo, "_diag_transition_derisk_counts", {}) or {}
        det = int(store.get("de_risk_on_deterioration", 0))
        rec = int(store.get("de_risk_on_recovery", 0))
        return f"DET:{det};REC:{rec}"

    def _fmt_transition_derisk_by_engine() -> str:
        store = getattr(algo, "_diag_transition_derisk_counts_by_engine", {}) or {}
        out = []
        for engine in ("VASS", "ITM", "MICRO"):
            row = store.get(engine, {}) or {}
            det = int(row.get("de_risk_on_deterioration", 0))
            rec = int(row.get("de_risk_on_recovery", 0))
            out.append(f"{engine}[DET:{det};REC:{rec}]")
        return " ".join(out)

    vass_reject_top = _top_counts(getattr(algo, "_diag_vass_reject_reason_counts", {}), top_n=5)
    intraday_drop_top = _top_counts(
        getattr(algo, "_diag_intraday_drop_reason_counts", {}),
        top_n=8,
    )

    exit_path_counts = sorted(algo._diag_exit_path_counts.items(), key=lambda kv: kv[0])
    exit_path_pnl = sorted(algo._diag_exit_path_pnl.items(), key=lambda kv: kv[0])
    exit_counts_str = (
        ";".join(f"{k}:{int(v)}" for k, v in exit_path_counts) if exit_path_counts else "NONE"
    )
    exit_pnl_str = (
        ";".join(f"{k}:{float(v):+.0f}" for k, v in exit_path_pnl) if exit_path_pnl else "NONE"
    )

    kill_active = bool(algo.risk_engine.is_kill_switch_active())
    governor_scale = float(getattr(algo, "_governor_scale", 1.0) or 0.0)
    itm_state = {}
    try:
        itm_state = algo.options_engine.get_itm_horizon_state() or {}
    except Exception:
        itm_state = {}
    itm_drawdown_detail = str(itm_state.get("last_drawdown_detail", "NA") or "NA")
    itm_global_pause = str(itm_state.get("pause_until", "") or "")
    itm_call_pause = str(itm_state.get("call_pause_until", "") or "")
    itm_put_pause = str(itm_state.get("put_pause_until", "") or "")
    itm_dd_blocked = bool(itm_state.get("dd_blocked", False))
    itm_diag_counts = _top_counts(itm_state.get("diag_counts", {}), top_n=5)
    itm_diag_blocks = _top_counts(itm_state.get("diag_block_codes", {}), top_n=5)
    suppression_min = int(getattr(algo, "_kill_switch_suppression_minutes", 0) or 0)
    ks_skip_until = str(getattr(algo.risk_engine, "_ks_skip_until_date", "") or "")
    ks_skip_active = bool(ks_skip_until) and str(algo.Time.date()) <= ks_skip_until
    vass_mfe_peak = float(getattr(algo, "_diag_vass_mfe_peak_max_profit_pct", 0.0) or 0.0)
    vass_mfe_t1 = int(getattr(algo, "_diag_vass_mfe_t1_hits", 0) or 0)
    vass_mfe_t2 = int(getattr(algo, "_diag_vass_mfe_t2_hits", 0) or 0)
    vass_mfe_lock_exits = int(getattr(algo, "_diag_vass_mfe_lock_exits", 0) or 0)
    vass_tail_cap_exits = int(getattr(algo, "_diag_vass_tail_cap_exits", 0) or 0)

    compact_parts = [
        f"C={algo._diag_intraday_candidate_count}",
        f"A={algo._diag_intraday_approved_count}",
        f"D={algo._diag_intraday_dropped_count}",
        f"RR={algo._diag_intraday_router_reject_count}",
        f"R={algo._diag_intraday_result_count}",
        f"VB={algo._diag_vass_block_count}",
        f"SE={algo._diag_spread_entry_signal_count}/{algo._diag_spread_entry_submit_count}/{algo._diag_spread_entry_fill_count}",
        f"SX={algo._diag_spread_exit_signal_count}/{algo._diag_spread_exit_submit_count}/{spread_exit_fill_strict}",
        f"IE={_fmt_intraday_funnel_by_engine()}",
    ]
    sparse_counts = [
        ("OB", algo._diag_overlay_block_count),
        ("OSB", algo._diag_overlay_slot_block_count),
        ("SCE", algo._diag_spread_close_escalation_count),
        ("SXC", algo._diag_spread_exit_canceled_count),
        ("SR", algo._diag_spread_position_removed_count),
        ("SRF", algo._diag_spread_removed_fill_path_count),
        ("SGR", algo._diag_spread_ghost_removed_count),
        ("SLS", algo._diag_spread_loss_beyond_stop_count),
        ("MTR", algo._diag_micro_tag_recovery_count),
        ("MES", algo._diag_micro_eod_sweep_close_count),
        ("MPCI", algo._diag_micro_pending_cancel_ignored_count),
        ("MRG", algo._diag_margin_reject_count),
    ]
    compact_parts.extend(f"{k}={int(v)}" for k, v in sparse_counts if int(v) != 0)
    if intraday_drop_top != "NONE":
        compact_parts.append(f"Drop={intraday_drop_top}")
    intraday_drop_by_engine = _fmt_intraday_drop_reasons_by_engine()
    if intraday_drop_by_engine != "MICRO[NONE] ITM[NONE] OTHER[NONE]":
        compact_parts.append(f"DropEng={intraday_drop_by_engine}")
    if top_router_rejects_str != "NONE":
        compact_parts.append(f"Rj={top_router_rejects_str}")
    router_by_engine = _fmt_engine_top_rejects()
    if router_by_engine != "VASS[NONE] MICRO[NONE] ITM[NONE]":
        compact_parts.append(f"RjEng={router_by_engine}")
    if vass_reject_top != "NONE":
        compact_parts.append(f"Vj={vass_reject_top}")
    if (
        vass_mfe_peak > 0
        or vass_mfe_t1 > 0
        or vass_mfe_t2 > 0
        or vass_mfe_lock_exits > 0
        or vass_tail_cap_exits > 0
    ):
        compact_parts.append(
            f"VMFE={vass_mfe_peak:.1%}/{vass_mfe_t1}/{vass_mfe_t2}/{vass_mfe_lock_exits}/{vass_tail_cap_exits}"
        )
    transition_total = _fmt_transition_derisk_totals()
    if transition_total != "DET:0;REC:0":
        compact_parts.append(f"TD={transition_total}")
    transition_by_engine = _fmt_transition_derisk_by_engine()
    if transition_by_engine != "VASS[DET:0;REC:0] ITM[DET:0;REC:0] MICRO[DET:0;REC:0]":
        compact_parts.append(f"TDE={transition_by_engine}")
    if exit_counts_str != "NONE":
        compact_parts.append(f"ExitC={exit_counts_str}")
    if exit_pnl_str != "NONE":
        compact_parts.append(f"ExitP={exit_pnl_str}")
    exit_by_engine = _fmt_engine_exit_diag()
    if exit_by_engine != "VASS[C=NONE|P=NONE] MICRO[C=NONE|P=NONE] ITM[C=NONE|P=NONE]":
        compact_parts.append(f"ExitE={exit_by_engine}")
    compact_parts.append(f"Kill={int(kill_active)}")
    compact_parts.append(f"Gov={governor_scale:.0%}")
    if itm_drawdown_detail != "NA":
        compact_parts.append(f"ITMDD={itm_drawdown_detail}")
    if itm_dd_blocked:
        compact_parts.append("ITMDB=1")
    if itm_diag_counts != "NONE":
        compact_parts.append(f"ITMC={itm_diag_counts}")
    if itm_diag_blocks != "NONE":
        compact_parts.append(f"ITMB={itm_diag_blocks}")
    if itm_global_pause:
        compact_parts.append(f"ITMP={itm_global_pause}")
    if itm_call_pause:
        compact_parts.append(f"ITMCP={itm_call_pause}")
    if itm_put_pause:
        compact_parts.append(f"ITMPP={itm_put_pause}")
    if suppression_min > 0:
        compact_parts.append(f"KSM={suppression_min}")
    if ks_skip_active:
        compact_parts.append("KSSA=1")
    if ks_skip_until:
        compact_parts.append(f"KSU={ks_skip_until}")
    _log("OPTIONS_DIAG_SUMMARY: " + " | ".join(compact_parts), priority=1)
