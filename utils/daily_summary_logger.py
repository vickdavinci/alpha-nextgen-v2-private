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
        f"MarginRejects={algo._diag_margin_reject_count}"
    )
