from __future__ import annotations

import config
from models.enums import RegimeLevel


def log_daily_summary(algo) -> None:
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

    algo.Log(summary)
    spread_exit_fill_strict = algo._diag_spread_exit_fill_count
    if algo._order_lifecycle_suppressed_count > 0:
        algo.Log(
            f"ORDER_LIFECYCLE_CAP_HIT: Logged={algo._order_lifecycle_log_count} | "
            f"Suppressed={algo._order_lifecycle_suppressed_count} | "
            f"Cap={int(getattr(config, 'LOG_ORDER_LIFECYCLE_MAX_PER_DAY', 200))}"
        )

    dte_order = ["2", "3", "4", "5", "OTHER"]
    fmt = lambda d: ",".join(f"{k}:{int(d.get(k, 0))}" for k in dte_order)
    top_drop_pairs = sorted(
        algo._diag_micro_drop_reason_by_dte.items(), key=lambda kv: kv[1], reverse=True
    )[:5]
    top_drop = ";".join(f"{k}={v}" for k, v in top_drop_pairs) if top_drop_pairs else "NONE"
    algo.Log(
        "MICRO_DTE_DIAG_SUMMARY: "
        f"Cand[{fmt(algo._diag_micro_dte_candidates)}] | "
        f"Approved[{fmt(algo._diag_micro_dte_approved)}] | "
        f"Dropped[{fmt(algo._diag_micro_dte_dropped)}] | "
        f"Win[{fmt(algo._diag_micro_dte_win)}] | "
        f"Loss[{fmt(algo._diag_micro_dte_loss)}] | "
        f"TopDrop[{top_drop}]"
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
        for engine in ("VASS", "MICRO", "ITM"):
            store = algo._diag_router_reject_reason_counts_by_engine.get(engine, {})
            out.append(f"{engine}[{_top_counts(store)}]")
        return " ".join(out)

    def _fmt_engine_exit_diag() -> str:
        out = []
        for engine in ("VASS", "MICRO", "ITM"):
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
        return (
            f"ITM({int(cand.get('ITM', 0))}/{int(app.get('ITM', 0))}/{int(drp.get('ITM', 0))}/{int(res.get('ITM', 0))}) "
            f"MICRO({int(cand.get('MICRO', 0))}/{int(app.get('MICRO', 0))}/{int(drp.get('MICRO', 0))}/{int(res.get('MICRO', 0))})"
        )

    vass_reject_top = _top_counts(getattr(algo, "_diag_vass_reject_reason_counts", {}), top_n=5)

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

    algo.Log(
        "OPTIONS_DIAG_SUMMARY: "
        f"Candidates={algo._diag_intraday_candidate_count} | "
        f"Approved={algo._diag_intraday_approved_count} | "
        f"Dropped={algo._diag_intraday_dropped_count} | "
        f"RouterRejects={algo._diag_intraday_router_reject_count} | "
        f"Results={algo._diag_intraday_result_count} | "
        f"VASS_Blocks={algo._diag_vass_block_count} | "
        f"OverlayBlocks={algo._diag_overlay_block_count} | "
        f"OverlaySlotBlocks={algo._diag_overlay_slot_block_count} | "
        f"SpreadCloseEscalations={algo._diag_spread_close_escalation_count} | "
        f"SpreadEntrySignal={algo._diag_spread_entry_signal_count} | "
        f"SpreadEntrySubmit={algo._diag_spread_entry_submit_count} | "
        f"SpreadEntryFill={algo._diag_spread_entry_fill_count} | "
        f"SpreadExitSignal={algo._diag_spread_exit_signal_count} | "
        f"SpreadExitSubmit={algo._diag_spread_exit_submit_count} | "
        f"SpreadExitFill={spread_exit_fill_strict} | "
        f"SpreadExitFillStrict={spread_exit_fill_strict} | "
        f"SpreadExitCanceled={algo._diag_spread_exit_canceled_count} | "
        f"SpreadRemoved={algo._diag_spread_position_removed_count} | "
        f"SpreadRemovedFillPath={algo._diag_spread_removed_fill_path_count} | "
        f"SpreadGhostRemoved={algo._diag_spread_ghost_removed_count} | "
        f"SpreadLossBeyondStop={algo._diag_spread_loss_beyond_stop_count} | "
        f"MicroTagRecovery={algo._diag_micro_tag_recovery_count} | "
        f"MicroEodSweepClose={algo._diag_micro_eod_sweep_close_count} | "
        f"MicroPendingCancelIgnored={algo._diag_micro_pending_cancel_ignored_count} | "
        f"MarginRejects={algo._diag_margin_reject_count} | "
        f"IntradayByEngine(C/A/D/R)={_fmt_intraday_funnel_by_engine()} | "
        f"TopRouterRejects={top_router_rejects_str} | "
        f"TopRouterRejectsByEngine={_fmt_engine_top_rejects()} | "
        f"VASSRejectTop={vass_reject_top} | "
        f"ExitPathCounts={exit_counts_str} | "
        f"ExitPathPnL={exit_pnl_str} | "
        f"ExitByEngine={_fmt_engine_exit_diag()} | "
        f"KillActive={kill_active} | GovScale={governor_scale:.0%} | "
        f"ITMDrawdown={itm_drawdown_detail} | ITMDDBlocked={itm_dd_blocked} | "
        f"ITMCounts={itm_diag_counts} | ITMBlocks={itm_diag_blocks} | "
        f"ITMPause={itm_global_pause or 'NONE'} | "
        f"ITMCallPause={itm_call_pause or 'NONE'} | ITMPutPause={itm_put_pause or 'NONE'} | "
        f"KillSuppressMin={suppression_min} | KSSkipActive={ks_skip_active} | "
        f"KSSkipUntil={ks_skip_until or 'NONE'}"
    )
