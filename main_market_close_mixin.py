from __future__ import annotations

from datetime import datetime
from typing import Optional


class MainMarketCloseMixin:
    def _get_primary_market_close_time(self) -> Optional[datetime]:
        """Resolve today's primary-session close time for SPY exchange hours."""
        try:
            exchange_hours = self.Securities[self.spy].Exchange.Hours
            next_close = exchange_hours.GetNextMarketClose(self.Time, False)
            if next_close.date() == self.Time.date():
                return next_close
            # Compatibility path: some QC engine builds can return next-session close.
            # Re-anchor to today's midnight and ask for the next close from there.
            today_anchor = datetime(self.Time.year, self.Time.month, self.Time.day)
            anchor_close = exchange_hours.GetNextMarketClose(today_anchor, False)
            if anchor_close.date() == self.Time.date():
                return anchor_close
        except Exception:
            return None
        return None

    def _on_market_close(self) -> None:
        """
        Market close at 16:00 ET.

        Submits MOO orders, persists state and logs daily summary.
        Resets daily tracking variables.
        """
        # Skip during warmup - no orders allowed
        if self.IsWarmingUp:
            return
        if self._market_close_ran_date == self.Time.date():
            return
        self._market_close_ran_date = self.Time.date()

        # V3.0 FIX: Always process EOD signals - internal logic handles Governor scaling
        # V6.11: At Governor 0%, hedges (SH) and bearish PUTs are still allowed
        # The scaling logic in _process_eod_signals zeros out bullish positions
        if hasattr(self, "_eod_capital_state") and self._eod_capital_state is not None:
            if self._governor_scale <= 0.0:
                self.Log("EOD_GOVERNOR_0: Processing defensive signals only (hedges + PUTs)")
            self._process_eod_signals(self._eod_capital_state)
            self._eod_capital_state = None

        self._ensure_daily_proxy_windows_snapshot()
        self._record_regime_timeline_event(source="MARKET_CLOSE")
        self._flush_regime_decision_artifact()
        self._flush_regime_timeline_artifact()
        self._flush_signal_lifecycle_artifact()
        self._flush_router_rejection_artifact()
        self._flush_order_lifecycle_artifact()

        # Save all state
        self._save_state()

        # V6.14: Cache closes for next day's pre-market VIX ladder.
        self._vix_prior_close = self._get_vix_level()
        uvxy_close = self.Securities[self.uvxy].Close if hasattr(self, "uvxy") else 0.0
        if uvxy_close > 0:
            self._uvxy_prior_close = uvxy_close
        self._last_market_close_check = self.Time.date()

        # V6.12: Log EOD P&L summary
        if hasattr(self, "pnl_tracker"):
            self.pnl_tracker.log_eod_summary(str(self.Time.date()))
            self.pnl_tracker.log_optimization_summary(str(self.Time.date()))
            # Reset session counters for next day
            self.pnl_tracker.reset_session()

        # V6.19: Emit daily options diagnostics summary for funnel validation.
        self._log_daily_summary()

        # Reset daily tracking
        self.today_trades.clear()
        self.today_safeguards.clear()
        self.symbols_to_skip.clear()
        self._splits_logged_today.clear()
        self._greeks_breach_logged = False
        self._last_swing_scan_time = None  # V2.19: Allow fresh swing scan next trading day
        # V2.20: Clear all rejection cooldowns at end of day
        self._trend_rejection_cooldown_until = None
        self._options_swing_cooldown_until = None
        self._options_intraday_cooldown_until = None
        self._options_intraday_cooldown_until_by_lane = {"MICRO": None, "ITM": None}
        self._options_spread_cooldown_until = None
        self._mr_rejection_cooldown_until = None
        self._diag_margin_reject_count = 0
        self._diag_intraday_candidate_count = 0
        self._diag_intraday_approved_count = 0
        self._diag_intraday_dropped_count = 0
        self._diag_intraday_router_reject_count = 0
        self._diag_intraday_result_count = 0
        self._diag_vass_block_count = 0
        self._diag_overlay_block_count = 0
        self._diag_overlay_slot_block_count = 0
        self._diag_spread_close_escalation_count = 0
        self._diag_spread_entry_signal_count = 0
        self._diag_spread_entry_submit_count = 0
        self._diag_spread_entry_fill_count = 0
        self._diag_spread_exit_signal_count = 0
        self._diag_spread_exit_submit_count = 0
        self._diag_spread_exit_fill_count = 0
        self._diag_spread_exit_canceled_count = 0
        self._diag_spread_position_removed_count = 0
        self._diag_spread_removed_fill_path_count = 0
        self._diag_spread_ghost_removed_count = 0
        self._diag_spread_loss_beyond_stop_count = 0
        self._diag_micro_tag_recovery_count = 0
        self._diag_micro_eod_sweep_close_count = 0
        self._diag_micro_pending_cancel_ignored_count = 0
        self._order_lifecycle_log_count = 0
        self._order_lifecycle_suppressed_count = 0
        self._recon_orphan_close_submitted.clear()
        self._recon_orphan_seen_streak.clear()
        self._recon_orphan_first_seen_at.clear()
        self._recon_orphan_last_log_at.clear()
        self._spread_last_exit_reason.clear()
        self._last_micro_update_log_signature = None
        self._last_micro_update_log_time = None
        self._last_spread_construct_fail_log_at = None
        self._external_exec_event_logged.clear()
        self._order_tag_hint_cache.clear()
        self._spread_ghost_flat_streak_by_key.clear()
        self._spread_ghost_last_log_by_key.clear()
        self._last_intraday_dte_routing_log_by_key.clear()
        self._recent_router_rejections.clear()
        self._diag_router_reject_reason_counts.clear()
        for _store in self._diag_router_reject_reason_counts_by_engine.values():
            _store.clear()
        if hasattr(self, "portfolio_router") and self.portfolio_router is not None:
            try:
                self.portfolio_router.clear_preclear_diag_counts()
            except Exception:
                pass
        self._diag_vass_reject_reason_counts.clear()
        for _k in list(self._diag_vass_slot_concurrent_reject_by_direction.keys()):
            self._diag_vass_slot_concurrent_reject_by_direction[_k] = 0
        self._diag_vass_mfe_peak_max_profit_pct = 0.0
        self._diag_vass_mfe_t1_hits = 0
        self._diag_vass_mfe_t2_hits = 0
        self._diag_vass_mfe_lock_exits = 0
        self._diag_vass_tail_cap_exits = 0
        self._diag_vass_credit_theta_first_active_checks = 0
        self._diag_vass_premarket_itm_guarded_skip_count = 0
        self._diag_vass_friday_firewall_skipped_dte_count = 0
        self._diag_vass_thesis_soft_stop_checks = 0
        self._diag_vass_thesis_soft_stop_armed = 0
        self._diag_vass_thesis_soft_stop_exits = 0
        self._diag_exit_path_counts.clear()
        self._diag_exit_path_pnl.clear()
        for _store in self._diag_exit_path_counts_by_engine.values():
            _store.clear()
        for _store in self._diag_exit_path_pnl_by_engine.values():
            _store.clear()
        for _k in self._diag_intraday_candidates_by_engine.keys():
            self._diag_intraday_candidates_by_engine[_k] = 0
        for _k in self._diag_intraday_approved_by_engine.keys():
            self._diag_intraday_approved_by_engine[_k] = 0
        for _k in self._diag_intraday_dropped_by_engine.keys():
            self._diag_intraday_dropped_by_engine[_k] = 0
        self._diag_intraday_drop_reason_counts.clear()
        for _store in self._diag_intraday_drop_reason_counts_by_engine.values():
            _store.clear()
        self._diag_transition_path_counts.clear()
        for _k in self._diag_transition_derisk_counts.keys():
            self._diag_transition_derisk_counts[_k] = 0
        for _store in self._diag_transition_derisk_counts_by_engine.values():
            for _k in list(_store.keys()):
                _store[_k] = 0
        for _k in self._diag_intraday_results_by_engine.keys():
            self._diag_intraday_results_by_engine[_k] = 0
        for _store in (
            self._diag_micro_dte_candidates,
            self._diag_micro_dte_approved,
            self._diag_micro_dte_dropped,
            self._diag_micro_dte_win,
            self._diag_micro_dte_loss,
        ):
            for _k in list(_store.keys()):
                _store[_k] = 0
        self._diag_micro_drop_reason_by_dte.clear()
        self._diag_intraday_candidate_ids_logged.clear()
        self._diag_intraday_approved_ids_logged.clear()
        self._diag_intraday_dropped_ids_logged.clear()
        self._single_leg_last_exit_reason.clear()
        self._last_vass_rejection_log_by_key.clear()
        self._last_intraday_diag_log_by_key.clear()
        self._high_freq_log_seen_counts.clear()
        self._high_freq_log_suppressed_counts.clear()
        self._daily_drop_reason_agg.clear()
        self._transition_execution_context = None
        self._transition_execution_context_minute_key = None
        self._transition_execution_context_sample_seq = -1
        if hasattr(self, "options_engine") and self.options_engine is not None:
            try:
                self.options_engine.clear_transition_context_snapshot()
            except Exception:
                pass
        # V3.0 P1-B: Exit retry trackers are intraday plumbing only; clear daily.
        if self._pending_exit_orders:
            cleared = len(self._pending_exit_orders)
            self._pending_exit_orders.clear()
            self._exit_retry_scheduled_at.clear()
            self.Log(f"EOD_CLEANUP: Cleared {cleared} pending exit order trackers")
        if getattr(self, "_spread_fill_tracker", None) is not None:
            self._spread_fill_tracker = None
        self.options_engine.clear_ic_fill_trackers()

        # NOTE: _kill_switch_handled_today is NOT reset here - it resets at 09:25 pre-market
        # Resetting here causes double-trigger since OnData runs at 16:00 after EOD handler
