from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from AlgorithmImports import *

import config
from engines.satellite.options_engine import (
    OptionContract,
    SpreadStrategy,
    is_expiration_firewall_day,
)
from models.enums import IntradayStrategy, OptionDirection, Urgency
from models.target_weight import TargetWeight


class MainOptionsMixin:
    """Large options-scan methods extracted from main.py (move-only)."""

    def _reset_regime_detector_runtime_state(self) -> None:
        """Single owner for detector refresh-state reset fields."""
        self._regime_detector_last_update_key = None
        self._regime_detector_last_raw = {}
        self._regime_overlay_ambiguous_bars = 0

    def _scrub_stale_spread_margin_reservations(self) -> None:
        """
        Clear phantom spread margin reservations when no spread is active/pending.

        Safety net for submit/reject/cancel edge paths where spread margin remains
        reserved even though engine state is flat.
        """
        if not hasattr(self, "portfolio_router") or self.portfolio_router is None:
            return
        reserved = float(self.portfolio_router.get_reserved_spread_margin() or 0.0)
        if reserved <= 0:
            return

        has_active_spread = bool(self.options_engine.get_spread_positions())
        has_pending_spread = bool(self.options_engine.has_pending_spread_entry())
        has_pending_leg_map = bool(getattr(self, "_pending_spread_orders", {})) or bool(
            getattr(self, "_pending_spread_orders_reverse", {})
        )
        has_fill_tracker = getattr(self, "_spread_fill_tracker", None) is not None
        if has_active_spread or has_pending_spread or has_pending_leg_map or has_fill_tracker:
            return

        self.portfolio_router.clear_all_spread_margins()
        self.Log(
            f"SPREAD_MARGIN_SCRUB: Cleared stale reservation ${reserved:,.2f} with no active/pending spread state"
        )

    def _initialize_runtime_state(self) -> None:
        # Daily/account tracking.
        self.equity_prior_close = 0.0
        self.equity_sod = 0.0
        self.spy_prior_close = 0.0
        self.spy_open = 0.0
        self._governor_scale = 1.0
        self._last_risk_result = None
        self.today_trades = []
        self.today_safeguards = []
        self.symbols_to_skip = set()
        self._splits_logged_today = set()
        self._greeks_breach_logged = False
        self._kill_switch_handled_today = False
        self._margin_cb_in_progress = False

        # Log throttles/aggregates.
        self._last_vass_rejection_log_by_key: Dict[str, datetime] = {}
        self._last_intraday_diag_log_by_key: Dict[str, datetime] = {}
        self._high_freq_log_seen_counts: Dict[str, int] = {}
        self._high_freq_log_suppressed_counts: Dict[str, int] = {}
        self._daily_drop_reason_agg: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._diag_intraday_candidate_ids_logged = set()
        self._diag_intraday_approved_ids_logged = set()
        self._diag_intraday_dropped_ids_logged = set()
        self._last_swing_scan_time = None
        self._engine_force_exit_fallback_date = None
        self._mr_force_close_fallback_date = None
        self._intraday_force_close_ran_date = None
        self._mr_force_close_ran_date = None
        self._eod_processing_ran_date = None
        self._market_close_ran_date = None

        # Detector and reconciliation state.
        self._regime_detector_prev_score = None
        self._last_reconcile_positions_run = None

        # Scoped rejection cooldowns.
        self._trend_rejection_cooldown_until = None
        self._options_swing_cooldown_until = None
        self._options_intraday_cooldown_until = None
        self._options_intraday_cooldown_until_by_lane = {"MICRO": None, "ITM": None}
        self._options_spread_cooldown_until = None
        self._mr_rejection_cooldown_until = None
        self._intraday_retry_state_by_lane = {
            "MICRO": {"pending": False, "expires": None, "direction": None, "reason_code": None},
            "ITM": {"pending": False, "expires": None, "direction": None, "reason_code": None},
        }
        self._intraday_entry_snapshot = {}
        self._micro_open_symbols = set()
        self._itm_open_symbols = set()
        self._intraday_close_in_progress_symbols = set()
        self._intraday_force_exit_submitted_symbols = {}
        self._intraday_hold_loss_block_log_date = {}

        # Pre-market VIX ladder.
        self._premarket_vix_ladder_level = 0
        self._premarket_vix_ladder_reason = "L0"
        self._premarket_vix_size_mult = 1.0
        self._premarket_vix_entry_block_until = None
        self._premarket_vix_call_block_until = None
        self._premarket_vix_shock_pct = 0.0
        self._premarket_vix_shock_memory_until = None
        self._vix_prior_close = 15.0
        self._uvxy_prior_close = 0.0

        # Spread lifecycle/state.
        self._pending_spread_orders = {}
        self._pending_spread_orders_reverse = {}
        self._spread_fill_tracker = None
        self._spread_close_trackers = {}
        self._external_exec_event_logged = set()

        # Diagnostics.
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
        self._diag_micro_signal_seq = 0
        self._diag_itm_signal_seq = 0
        self._diag_micro_tag_recovery_count = 0
        self._diag_micro_eod_sweep_close_count = 0
        self._diag_micro_pending_cancel_ignored_count = 0
        self._order_lifecycle_log_count = 0
        self._order_lifecycle_suppressed_count = 0
        self._diag_micro_dte_candidates = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_dte_approved = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_dte_dropped = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_dte_win = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_dte_loss = {"2": 0, "3": 0, "4": 0, "5": 0, "OTHER": 0}
        self._diag_micro_drop_reason_by_dte = {}
        self._diag_router_reject_reason_counts = {}
        self._diag_router_reject_reason_counts_by_engine = {
            "VASS": {},
            "MICRO": {},
            "ITM": {},
            "OTHER": {},
        }
        self._recent_router_rejections: List[Any] = []
        self._diag_vass_reject_reason_counts = {}
        self._diag_vass_slot_concurrent_reject_by_direction = {
            "BULLISH": 0,
            "BEARISH": 0,
            "UNKNOWN": 0,
        }
        self._diag_transition_path_counts: Dict[str, int] = {}
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
        self._diag_exit_path_counts = {}
        self._diag_exit_path_pnl = {}
        self._diag_exit_path_counts_by_engine = {"VASS": {}, "MICRO": {}, "ITM": {}, "OTHER": {}}
        self._diag_exit_path_pnl_by_engine = {"VASS": {}, "MICRO": {}, "ITM": {}, "OTHER": {}}
        self._diag_intraday_candidates_by_engine = {"MICRO": 0, "ITM": 0, "OTHER": 0}
        self._diag_intraday_approved_by_engine = {"MICRO": 0, "ITM": 0, "OTHER": 0}
        self._diag_intraday_dropped_by_engine = {"MICRO": 0, "ITM": 0, "OTHER": 0}
        self._diag_intraday_drop_reason_counts = {}
        self._diag_intraday_drop_reason_counts_by_engine = {"MICRO": {}, "ITM": {}, "OTHER": {}}
        self._diag_intraday_results_by_engine = {"MICRO": 0, "ITM": 0, "OTHER": 0}
        self._diag_transition_derisk_counts = {
            "de_risk_on_deterioration": 0,
            "de_risk_on_recovery": 0,
        }
        self._diag_transition_derisk_counts_by_engine = {
            "VASS": {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
            "ITM": {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
            "MICRO": {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
            "OTHER": {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
        }
        self._transition_execution_context: Optional[Dict[str, Any]] = None
        self._transition_execution_context_minute_key: Optional[str] = None
        self._transition_execution_context_sample_seq: int = -1
        self._last_micro_update_log_signature = None
        self._last_micro_update_log_time = None
        self._last_spread_construct_fail_log_at = None
        self._intraday_regime_score = None
        self._intraday_regime_updated_at = None
        self._intraday_regime_momentum_roc = None
        self._intraday_regime_vix_5d_change = None
        self._regime_base_state = "NEUTRAL"
        self._regime_base_candidate_state = "NEUTRAL"
        self._regime_base_candidate_streak = 0
        self._regime_base_state_enter_seq = 0
        self._regime_overlay_state = "STABLE"
        self._regime_overlay_candidate_state = "STABLE"
        self._regime_overlay_candidate_streak = 0
        self._regime_overlay_state_enter_seq = 0
        self._regime_detector_sample_seq = 0
        self._regime_detector_prev_score: Optional[float] = None
        self._reset_regime_detector_runtime_state()
        self._regime_decision_records: List[Dict[str, Any]] = []
        self._regime_decision_overflow_logged = False
        self._regime_observability_key = self._build_regime_observability_key()
        self._regime_timeline_records: List[Dict[str, Any]] = []
        self._regime_timeline_overflow_logged = False
        self._regime_timeline_observability_key = self._build_regime_timeline_observability_key()
        self._signal_lifecycle_records: List[Dict[str, Any]] = []
        self._signal_lifecycle_overflow_logged = False
        self._signal_lifecycle_observability_key = self._build_signal_lifecycle_observability_key()
        self._router_rejection_records: List[Dict[str, Any]] = []
        self._router_rejection_overflow_logged = False
        self._router_rejection_artifact_bootstrapped = False
        self._router_rejection_observability_key = self._build_router_rejection_observability_key()
        # Bootstrap router rejection artifact header at initialize-time so runs
        # that terminate early still expose the channel in ObjectStore.
        try:
            self._flush_router_rejection_artifact()
        except Exception:
            pass
        self._order_lifecycle_records: List[Dict[str, Any]] = []
        self._order_lifecycle_overflow_logged = False
        self._order_lifecycle_observability_key = self._build_order_lifecycle_observability_key()
        self._observability_log_fallback_signature_by_key: Dict[str, str] = {}
        self._diag_vass_signal_seq = 0
        self._last_regime_effective_log_at = None
        self._last_intraday_dte_routing_log_by_key = {}
        self._order_tag_hint_cache = {}
        self._order_lifecycle_tag_by_order_id = {}
        self._last_option_fill_tag_by_symbol = {}
        self._last_option_fill_time_by_symbol = {}
        self._order_tag_map_logged_ids = set()
        self._order_tag_resolve_logged_ids = set()
        self._last_state_persist_at = None
        self._daily_proxy_window_last_update: Dict[str, Any] = {}

        # Exit retries/forced-close lifecycle.
        self._pending_exit_orders = {}
        self._exit_retry_scheduled_at = {}
        # Preserve restored close-ladder state on startup/restart (live only).
        if bool(getattr(self, "LiveMode", False)):
            self._spread_forced_close_retry = dict(
                getattr(self, "_spread_forced_close_retry", {}) or {}
            )
            self._spread_forced_close_reason = dict(
                getattr(self, "_spread_forced_close_reason", {}) or {}
            )
            self._spread_forced_close_cancel_counts = dict(
                getattr(self, "_spread_forced_close_cancel_counts", {}) or {}
            )
            self._spread_forced_close_retry_cycles = dict(
                getattr(self, "_spread_forced_close_retry_cycles", {}) or {}
            )
            self._spread_last_close_submit_at = dict(
                getattr(self, "_spread_last_close_submit_at", {}) or {}
            )
            self._spread_close_first_cancel_at = dict(
                getattr(self, "_spread_close_first_cancel_at", {}) or {}
            )
            self._spread_close_intent_by_key = dict(
                getattr(self, "_spread_close_intent_by_key", {}) or {}
            )
        else:
            self._spread_forced_close_retry = {}
            self._spread_forced_close_reason = {}
            self._spread_forced_close_cancel_counts = {}
            self._spread_forced_close_retry_cycles = {}
            self._spread_last_close_submit_at = {}
            self._spread_close_first_cancel_at = {}
            self._spread_close_intent_by_key = {}
        self._spread_exit_mark_cache = {}
        self._spread_last_exit_reason = {}
        self._single_leg_last_exit_reason = {}
        self._spread_ghost_flat_streak_by_key = {}
        self._spread_ghost_last_log_by_key = {}
        self._friday_spread_reconcile_date = None
        self._recon_orphan_close_submitted = {}
        self._recon_orphan_seen_streak = {}
        self._recon_orphan_first_seen_at = {}
        self._recon_orphan_last_log_at = {}

        # Margin call circuit breaker.
        self._margin_call_consecutive_count = 0
        self._margin_call_cooldown_until = None

    def _normalize_engine_lane(self, lane: Optional[str]) -> str:
        lane_key = str(lane or "").upper()
        return lane_key if lane_key in ("MICRO", "ITM") else "UNKNOWN"

    def _set_engine_lane_cooldown(self, lane: Optional[str], until: Optional[datetime]) -> None:
        lane_key = self._normalize_engine_lane(lane)
        bucket = getattr(self, "_options_intraday_cooldown_until_by_lane", None)
        if not isinstance(bucket, dict):
            bucket = {"MICRO": None, "ITM": None}
            self._options_intraday_cooldown_until_by_lane = bucket
        if lane_key not in bucket:
            bucket[lane_key] = None
        bucket[lane_key] = until
        # Keep legacy aggregate field updated for existing telemetry/reporting.
        active_until = [dt for dt in bucket.values() if dt is not None]
        self._options_intraday_cooldown_until = max(active_until) if active_until else None

    def _get_engine_lane_cooldown_until(self, lane: Optional[str]) -> Optional[datetime]:
        lane_key = self._normalize_engine_lane(lane)
        bucket = getattr(self, "_options_intraday_cooldown_until_by_lane", None)
        if isinstance(bucket, dict):
            return bucket.get(lane_key)
        return getattr(self, "_options_intraday_cooldown_until", None)

    def _is_engine_lane_cooldown_active(self, lane: Optional[str]) -> bool:
        lane_key = self._normalize_engine_lane(lane)
        until = self._get_engine_lane_cooldown_until(lane_key)
        if until is None:
            return False
        now = getattr(self, "Time", None)
        if now is None:
            return True
        if now >= until:
            self._set_engine_lane_cooldown(lane_key, None)
            return False
        return True

    def _get_engine_retry_state(self, lane: Optional[str]) -> Dict[str, Any]:
        lane_key = self._normalize_engine_lane(lane)
        bucket = getattr(self, "_intraday_retry_state_by_lane", None)
        if not isinstance(bucket, dict):
            bucket = {}
        lane_state = bucket.get(lane_key)
        if not isinstance(lane_state, dict):
            lane_state = {}
        lane_state.setdefault("pending", False)
        lane_state.setdefault("expires", None)
        lane_state.setdefault("direction", None)
        lane_state.setdefault("reason_code", None)
        bucket[lane_key] = lane_state
        self._intraday_retry_state_by_lane = bucket
        return lane_state

    def _clear_engine_retry(self, lane: Optional[str]) -> None:
        state = self._get_engine_retry_state(lane)
        state["pending"] = False
        state["expires"] = None
        state["direction"] = None
        state["reason_code"] = None

    def _queue_engine_retry(
        self,
        lane: Optional[str],
        direction: Optional[OptionDirection],
        reason_code: str,
        expires_at: Optional[datetime],
    ) -> None:
        if direction is None or expires_at is None:
            self._clear_engine_retry(lane)
            return
        state = self._get_engine_retry_state(lane)
        state["pending"] = True
        state["expires"] = expires_at
        state["direction"] = direction
        state["reason_code"] = str(reason_code or "")

    def _consume_engine_retry(self, lane: Optional[str]) -> Optional[Tuple[OptionDirection, str]]:
        state = self._get_engine_retry_state(lane)
        if not bool(state.get("pending", False)):
            return None
        expires = state.get("expires")
        direction = state.get("direction")
        reason_code = str(state.get("reason_code") or "")
        now = getattr(self, "Time", None)
        if now is None or expires is None or now > expires or direction is None:
            self._clear_engine_retry(lane)
            return None
        self._clear_engine_retry(lane)
        return direction, reason_code

    def _micro_dte_bucket(self, dte: Optional[int]) -> str:
        """Normalize intraday DTE to compact telemetry buckets."""
        try:
            d = int(dte) if dte is not None else -1
        except Exception:
            d = -1
        if d in (2, 3, 4, 5):
            return str(d)
        return "OTHER"

    def _inc_micro_dte_counter(self, store: Dict[str, int], dte: Optional[int]) -> str:
        """Increment a MICRO DTE diagnostics counter and return resolved bucket."""
        bucket = self._micro_dte_bucket(dte)
        store[bucket] = int(store.get(bucket, 0)) + 1
        return bucket

    def _record_micro_drop_reason_dte(self, code: str, dte: Optional[int]) -> None:
        """Track drop reason x DTE bucket for funnel RCA."""
        bucket = self._micro_dte_bucket(dte)
        reason = str(code or "E_UNKNOWN")
        key = f"{reason}|{bucket}"
        self._diag_micro_drop_reason_by_dte[key] = (
            int(self._diag_micro_drop_reason_by_dte.get(key, 0)) + 1
        )

    def _engine_bucket_from_strategy(self, strategy: Optional[Any]) -> str:
        """Normalize intraday strategy into daily summary engine buckets."""
        name = str(getattr(strategy, "value", strategy) or "").upper()
        if "ITM_MOMENTUM" in name:
            return "ITM"
        if (
            "MICRO_" in name
            or "PROTECTIVE_PUTS" in name
            or name in {"DEBIT_FADE", "INTRADAY_DEBIT_FADE", "OTM_MOMENTUM"}
        ):
            return "MICRO"
        return "OTHER"

    def _inc_engine_counter(self, store: Dict[str, int], strategy: Optional[Any]) -> str:
        """Increment per-engine intraday diagnostics counter and return bucket."""
        bucket = self._engine_bucket_from_strategy(strategy)
        store[bucket] = int(store.get(bucket, 0)) + 1
        return bucket

    def _record_engine_drop_reason(self, code: str, strategy: Optional[Any]) -> None:
        """Persist drop-reason metrics independent of log throttling."""
        reason = self._canonical_options_reason_code(code or "E_UNKNOWN")
        self._diag_intraday_drop_reason_counts[reason] = (
            int(self._diag_intraday_drop_reason_counts.get(reason, 0)) + 1
        )
        bucket = self._engine_bucket_from_strategy(strategy)
        store = self._diag_intraday_drop_reason_counts_by_engine.setdefault(bucket, {})
        store[reason] = int(store.get(reason, 0)) + 1

    def _record_vass_reject_reason(self, reason_code: str, direction: Optional[str] = None) -> None:
        """Track VASS reject reason counts for daily funnel RCA."""
        code = self._canonical_options_reason_code(str(reason_code or "UNKNOWN"))
        self._diag_vass_reject_reason_counts[code] = (
            int(self._diag_vass_reject_reason_counts.get(code, 0)) + 1
        )
        if code != "R_SLOT_VASS_CONCURRENT_MAX":
            return
        direction_key = str(direction or "").strip().upper()
        if direction_key in {"CALL", "BULLISH"}:
            bucket = "BULLISH"
        elif direction_key in {"PUT", "BEARISH"}:
            bucket = "BEARISH"
        else:
            bucket = "UNKNOWN"
        self._diag_vass_slot_concurrent_reject_by_direction[bucket] = (
            int(self._diag_vass_slot_concurrent_reject_by_direction.get(bucket, 0)) + 1
        )

    def _inc_transition_path_counter(self, key: str) -> None:
        """Track detector/eod transition trigger path usage for daily RCA."""
        label = str(key or "").strip().upper()
        if not label:
            return
        self._diag_transition_path_counts[label] = (
            int(self._diag_transition_path_counts.get(label, 0)) + 1
        )

    def _normalize_spread_close_quantities(self, signal: TargetWeight) -> None:
        """Normalize spread close quantities from live holdings to avoid stale qty closes."""
        try:
            md = signal.metadata or {}
            if not bool(md.get("spread_close_short", False)):
                return
            long_symbol = self._normalize_symbol_str(signal.symbol)
            short_symbol = self._normalize_symbol_str(md.get("spread_short_leg_symbol", ""))
            if not long_symbol or not short_symbol:
                return
            live_long = abs(self._get_option_holding_quantity(long_symbol))
            live_short = abs(self._get_option_holding_quantity(short_symbol))
            if live_long <= 0 or live_short <= 0:
                return
            close_qty = min(live_long, live_short)
            if close_qty <= 0:
                return
            signal.requested_quantity = int(close_qty)
            md["spread_short_leg_quantity"] = int(close_qty)

            # Backfill spread close metadata so router/telemetry paths remain consistent
            # across all close emitters (retry, gamma/assignment, firewall, time exits).
            spread_key = str(md.get("spread_key", "") or "").strip()
            spread_obj = None
            try:
                for candidate in self.options_engine.get_spread_positions():
                    candidate_key = self._build_spread_runtime_key(candidate)
                    if spread_key and candidate_key == spread_key:
                        spread_obj = candidate
                        break
                if spread_obj is None:
                    for candidate in self.options_engine.get_spread_positions():
                        c_long = self._normalize_symbol_str(candidate.long_leg.symbol)
                        c_short = self._normalize_symbol_str(candidate.short_leg.symbol)
                        if long_symbol == c_long and short_symbol == c_short:
                            spread_obj = candidate
                            spread_key = self._build_spread_runtime_key(candidate)
                            break
            except Exception:
                spread_obj = None
            if spread_key and not str(md.get("spread_key", "") or "").strip():
                md["spread_key"] = spread_key
            if spread_obj is not None:
                spread_type = str(getattr(spread_obj, "spread_type", "") or "")
                try:
                    entry_net = float(getattr(spread_obj, "net_debit", 0.0) or 0.0)
                except Exception:
                    entry_net = 0.0
                if spread_type and not str(md.get("spread_type", "") or "").strip():
                    md["spread_type"] = spread_type
                if "is_credit_spread" not in md:
                    md["is_credit_spread"] = bool(
                        entry_net < 0 or "CREDIT" in str(spread_type).upper()
                    )
                if "spread_entry_debit" not in md:
                    md["spread_entry_debit"] = float(max(0.0, entry_net))
                if "spread_entry_credit" not in md:
                    md["spread_entry_credit"] = float(max(0.0, -entry_net))
            else:
                spread_type = str(md.get("spread_type", "") or "")
                if "is_credit_spread" not in md and spread_type:
                    md["is_credit_spread"] = bool("CREDIT" in spread_type.upper())
                if "spread_entry_debit" not in md:
                    try:
                        debit_val = float(md.get("spread_entry_debit", 0.0) or 0.0)
                    except Exception:
                        debit_val = 0.0
                    md["spread_entry_debit"] = float(max(0.0, debit_val))
                if "spread_entry_credit" not in md:
                    try:
                        credit_val = float(md.get("spread_entry_credit", 0.0) or 0.0)
                    except Exception:
                        credit_val = 0.0
                    md["spread_entry_credit"] = float(max(0.0, credit_val))

            if not str(md.get("spread_exit_code", "") or "").strip():
                reason_text = str(md.get("exit_type", "") or signal.reason or "").strip().upper()
                token = reason_text.split(":", 1)[0].split(" ", 1)[0]
                token = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in token)
                token = "_".join(part for part in token.split("_") if part)
                if token:
                    md["spread_exit_code"] = token[:32]
            if not str(md.get("spread_exit_reason", "") or "").strip() and signal.reason:
                md["spread_exit_reason"] = str(signal.reason)
            signal.metadata = md
        except Exception:
            return

    def _clear_micro_symbol_tracking(self, symbol: str) -> None:
        """Clear intraday tracking artifacts for a symbol after flat/orphan handling."""
        sym_norm = self._normalize_symbol_str(symbol)
        if not sym_norm:
            return
        self._micro_open_symbols.discard(sym_norm)
        self._itm_open_symbols.discard(sym_norm)
        self._intraday_entry_snapshot.pop(sym_norm, None)
        self._clear_engine_close_guard(sym_norm)

    def _find_option_contract(self, symbol: str, data: Slice):
        if self._qqq_option_symbol is None or self._qqq_option_symbol not in data.OptionChains:
            return None
        try:
            for contract in data.OptionChains[self._qqq_option_symbol]:
                if str(contract.Symbol) == symbol:
                    return contract
        except Exception:
            return None
        return None

    def _get_option_current_price(self, symbol: str, data: Slice) -> Optional[float]:
        """Get current mid/last price for an option from the chain."""
        contract = self._find_option_contract(symbol, data)
        if contract is None:
            return None
        bid = contract.BidPrice
        ask = contract.AskPrice
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return contract.LastPrice if contract.LastPrice > 0 else None

    def _get_option_current_dte(self, symbol: str, data: Slice) -> Optional[int]:
        """Get current days-to-expiry for an option symbol."""
        contract = self._find_option_contract(symbol, data)
        if contract is None:
            return None
        dte = (contract.Expiry.date() - self.Time.date()).days
        return max(0, dte)

    def _get_option_expiry_date(self, symbol: str, data: Slice) -> Optional[str]:
        """Get expiry date string (YYYY-MM-DD) for an option symbol."""
        contract = self._find_option_contract(symbol, data)
        return str(contract.Expiry.date()) if contract is not None else None

    def _get_contract_prices(self, contract) -> Tuple[float, float]:
        return self.options_engine.get_contract_prices(contract)

    def _select_engine_option_contract(
        self,
        chain,
        direction: OptionDirection,
        strategy: IntradayStrategy = None,
        vix_current: Optional[float] = None,
    ) -> Optional[OptionContract]:
        """Select intraday option contract for MICRO entry."""
        if chain is None:
            return None

        qqq_price = self.Securities[self.qqq].Price

        # Strategy-aware delta selection (ITM vs MICRO_DEBIT_FADE vs MICRO_OTM_MOMENTUM).
        if not hasattr(self, "_intraday_delta_log_by_key"):
            self._intraday_delta_log_by_key = {}

        def _log_engine_delta_once(key: str, message: str) -> None:
            day = self.Time.strftime("%Y-%m-%d") if hasattr(self, "Time") else "NA"
            throttle_key = f"{day}|{key}"
            if self._intraday_delta_log_by_key.get(throttle_key):
                return
            self._intraday_delta_log_by_key[throttle_key] = True
            self.Log(message)

        if strategy == IntradayStrategy.ITM_MOMENTUM:
            if bool(getattr(config, "ITM_ENGINE_ENABLED", False)):
                delta_min_v2 = float(getattr(config, "ITM_DELTA_MIN", 0.65))
                delta_max_v2 = float(getattr(config, "ITM_DELTA_MAX", 0.75))
                target_delta = (delta_min_v2 + delta_max_v2) / 2.0
                _log_engine_delta_once(
                    "ITM_ENGINE",
                    f"INTRADAY_DELTA: ITM_ENGINE using delta_mid={target_delta:.2f} "
                    f"(range {delta_min_v2:.2f}-{delta_max_v2:.2f})",
                )
            else:
                target_delta = config.INTRADAY_ITM_DELTA
                _log_engine_delta_once(
                    "ITM_LEGACY",
                    f"INTRADAY_DELTA: ITM_MOMENTUM using delta={target_delta}",
                )
        elif strategy in (IntradayStrategy.MICRO_DEBIT_FADE, IntradayStrategy.DEBIT_FADE):
            target_delta = float(
                getattr(
                    config, "MICRO_DEBIT_FADE_DELTA_TARGET", config.OPTIONS_INTRADAY_DELTA_TARGET
                )
            )
        elif strategy == IntradayStrategy.MICRO_OTM_MOMENTUM:
            if direction == OptionDirection.CALL:
                delta_min = float(
                    getattr(
                        config,
                        "MICRO_OTM_CALL_DELTA_MIN",
                        getattr(
                            config,
                            "MICRO_OTM_MOMENTUM_DELTA_MIN",
                            getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MIN", 0.20),
                        ),
                    )
                )
                delta_max = float(
                    getattr(
                        config,
                        "MICRO_OTM_CALL_DELTA_MAX",
                        getattr(
                            config,
                            "MICRO_OTM_MOMENTUM_DELTA_MAX",
                            getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MAX", 0.50),
                        ),
                    )
                )
            elif direction == OptionDirection.PUT:
                delta_min = float(
                    getattr(
                        config,
                        "MICRO_OTM_PUT_DELTA_MIN",
                        getattr(
                            config,
                            "MICRO_OTM_MOMENTUM_DELTA_MIN",
                            getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MIN", 0.20),
                        ),
                    )
                )
                delta_max = float(
                    getattr(
                        config,
                        "MICRO_OTM_PUT_DELTA_MAX",
                        getattr(
                            config,
                            "MICRO_OTM_MOMENTUM_DELTA_MAX",
                            getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MAX", 0.50),
                        ),
                    )
                )
            else:
                delta_min = float(
                    getattr(
                        config,
                        "MICRO_OTM_MOMENTUM_DELTA_MIN",
                        getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MIN", 0.20),
                    )
                )
                delta_max = float(
                    getattr(
                        config,
                        "MICRO_OTM_MOMENTUM_DELTA_MAX",
                        getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MAX", 0.50),
                    )
                )
            target_delta = (delta_min + delta_max) / 2.0
        elif strategy == IntradayStrategy.PROTECTIVE_PUTS:
            target_delta = float(getattr(config, "PROTECTIVE_PUTS_DELTA_TARGET", 0.45))
        else:
            target_delta = config.OPTIONS_INTRADAY_DELTA_TARGET

        # Determine which OptionRight to filter for
        required_right = OptionRight.Call if direction == OptionDirection.CALL else OptionRight.Put

        # V9.5: Regime-aware MICRO DTE routing
        effective_dte_min = int(getattr(config, "OPTIONS_INTRADAY_DTE_MIN", 1))
        effective_dte_max = int(getattr(config, "OPTIONS_INTRADAY_DTE_MAX", 5))
        if bool(getattr(config, "MICRO_DTE_ROUTING_ENABLED", False)) and vix_current is not None:
            try:
                vix_val = float(vix_current)
                low_thr = float(getattr(config, "MICRO_DTE_LOW_VIX_THRESHOLD", 16.0))
                high_thr = float(getattr(config, "MICRO_DTE_HIGH_VIX_THRESHOLD", 25.0))
                if vix_val < low_thr:
                    effective_dte_min = int(getattr(config, "MICRO_DTE_LOW_VIX_MIN", 2))
                    effective_dte_max = int(getattr(config, "MICRO_DTE_LOW_VIX_MAX", 3))
                elif vix_val >= high_thr:
                    effective_dte_min = int(getattr(config, "MICRO_DTE_HIGH_VIX_MIN", 2))
                    effective_dte_max = int(getattr(config, "MICRO_DTE_HIGH_VIX_MAX", 4))
                else:
                    effective_dte_min = int(getattr(config, "MICRO_DTE_MEDIUM_VIX_MIN", 2))
                    effective_dte_max = int(getattr(config, "MICRO_DTE_MEDIUM_VIX_MAX", 3))
            except Exception:
                pass

        if strategy in (IntradayStrategy.MICRO_DEBIT_FADE, IntradayStrategy.DEBIT_FADE):
            effective_dte_min = int(getattr(config, "MICRO_DEBIT_FADE_DTE_MIN", 0))
            effective_dte_max = int(getattr(config, "MICRO_DEBIT_FADE_DTE_MAX", 2))
            key = f"MICRO_FADE|{effective_dte_min}|{effective_dte_max}"
            last_log_at = self._last_intraday_dte_routing_log_by_key.get(key)
            should_log = last_log_at is None or (
                self.Time - last_log_at
            ).total_seconds() / 60.0 >= int(getattr(config, "MICRO_DTE_DIAG_LOG_INTERVAL_MIN", 30))
            if should_log:
                self.Log(
                    f"INTRADAY_DTE_ROUTING: MICRO_DEBIT_FADE fixed window DTE=[{effective_dte_min}-{effective_dte_max}]"
                )
                self._last_intraday_dte_routing_log_by_key[key] = self.Time

        if strategy == IntradayStrategy.MICRO_OTM_MOMENTUM:
            effective_dte_min = int(getattr(config, "MICRO_OTM_MOMENTUM_DTE_MIN", 0))
            effective_dte_max = int(getattr(config, "MICRO_OTM_MOMENTUM_DTE_MAX", 1))
            key = f"MICRO_OTM|{effective_dte_min}|{effective_dte_max}"
            last_log_at = self._last_intraday_dte_routing_log_by_key.get(key)
            should_log = last_log_at is None or (
                self.Time - last_log_at
            ).total_seconds() / 60.0 >= int(getattr(config, "MICRO_DTE_DIAG_LOG_INTERVAL_MIN", 30))
            if should_log:
                self.Log(
                    f"INTRADAY_DTE_ROUTING: MICRO_OTM_MOMENTUM fixed window DTE=[{effective_dte_min}-{effective_dte_max}]"
                )
                self._last_intraday_dte_routing_log_by_key[key] = self.Time

        if strategy == IntradayStrategy.PROTECTIVE_PUTS:
            effective_dte_min = int(getattr(config, "PROTECTIVE_PUTS_DTE_MIN", 0))
            effective_dte_max = int(getattr(config, "PROTECTIVE_PUTS_DTE_MAX", 2))
            # Late-day crash hedges should avoid 0DTE decay/expiry risk.
            late_hour = int(getattr(config, "PROTECTIVE_PUTS_LATE_DAY_MIN_DTE_HOUR", 13))
            try:
                if hasattr(self, "Time") and int(self.Time.hour) >= late_hour:
                    effective_dte_min = max(effective_dte_min, 1)
            except Exception:
                pass
            key = f"PROTECTIVE_PUTS|{effective_dte_min}|{effective_dte_max}"
            last_log_at = self._last_intraday_dte_routing_log_by_key.get(key)
            should_log = last_log_at is None or (
                self.Time - last_log_at
            ).total_seconds() / 60.0 >= int(getattr(config, "MICRO_DTE_DIAG_LOG_INTERVAL_MIN", 30))
            if should_log:
                self.Log(
                    f"INTRADAY_DTE_ROUTING: PROTECTIVE_PUTS fixed window DTE=[{effective_dte_min}-{effective_dte_max}]"
                )
                self._last_intraday_dte_routing_log_by_key[key] = self.Time

        # ITM DTE overlay.
        if strategy == IntradayStrategy.ITM_MOMENTUM:
            if bool(getattr(config, "ITM_ENGINE_ENABLED", False)):
                effective_dte_min = int(getattr(config, "ITM_DTE_MIN", 5))
                effective_dte_max = int(getattr(config, "ITM_DTE_MAX", 7))
                key = f"ITM_ENGINE|{effective_dte_min}|{effective_dte_max}"
                last_log_at = self._last_intraday_dte_routing_log_by_key.get(key)
                should_log = last_log_at is None or (
                    self.Time - last_log_at
                ).total_seconds() / 60.0 >= int(
                    getattr(config, "ITM_DTE_DIAG_LOG_INTERVAL_MIN", 30)
                )
                if should_log:
                    self.Log(
                        f"INTRADAY_DTE_ROUTING: ITM_ENGINE fixed window DTE=[{effective_dte_min}-{effective_dte_max}]"
                    )
                    self._last_intraday_dte_routing_log_by_key[key] = self.Time
            elif vix_current is not None:
                # Legacy ITM path (engine disabled): keep ITM-only keys, avoid MICRO coupling.
                try:
                    vix_val = float(vix_current)
                    effective_dte_min = int(
                        getattr(config, "INTRADAY_ITM_DTE_MIN", effective_dte_min)
                    )
                    effective_dte_max = int(
                        getattr(config, "INTRADAY_ITM_DTE_MAX", effective_dte_max)
                    )
                    key = f"ITM_LEGACY|{effective_dte_min}|{effective_dte_max}"
                    last_log_at = self._last_intraday_dte_routing_log_by_key.get(key)
                    interval_min = int(getattr(config, "ITM_DTE_DIAG_LOG_INTERVAL_MIN", 30))
                    should_log = (
                        last_log_at is None
                        or (self.Time - last_log_at).total_seconds() / 60.0 >= interval_min
                    )
                    if should_log:
                        self.Log(
                            f"INTRADAY_DTE_ROUTING: ITM_LEGACY | VIX={vix_val:.1f} | "
                            f"DTE=[{effective_dte_min}-{effective_dte_max}]"
                        )
                        self._last_intraday_dte_routing_log_by_key[key] = self.Time
                except Exception:
                    pass

        # V2.13 Fix #16: Add filter diagnostics to track why contracts are rejected
        filter_counts = {
            "direction": 0,
            "dte": 0,
            "greeks": 0,
            "delta": 0,
            "oi": 0,
            "prices": 0,
            "spread": 0,
        }
        total_contracts = 0

        # Filter for target delta, 1-5 DTE, and MATCHING DIRECTION
        candidates = []
        for contract in chain:
            total_contracts += 1

            # V2.3.4 FIX: Filter by direction FIRST
            if contract.Right != required_right:
                filter_counts["direction"] += 1
                continue

            # V6.0 P0 FIX: Triple validation - contract.Right vs Symbol.ID.OptionRight vs OCC symbol
            # This catches any internal inconsistency in the contract data
            symbol_right = contract.Symbol.ID.OptionRight
            occ_symbol = str(contract.Symbol)
            # OCC format: "QQQ   YYMMDDCSSSSSSSS" where C/P is at position after date
            occ_right_char = None
            for char in occ_symbol:
                if char in ("C", "P"):
                    occ_right_char = char
                    break

            expected_occ_char = "C" if required_right == OptionRight.Call else "P"

            # Validate all three sources agree
            if contract.Right != symbol_right:
                self.Log(
                    f"RIGHT_MISMATCH_BLOCKED: contract.Right={contract.Right} vs "
                    f"Symbol.ID.OptionRight={symbol_right} | {occ_symbol}"
                )
                filter_counts["direction"] += 1
                continue

            if occ_right_char and occ_right_char != expected_occ_char:
                self.Log(
                    f"RIGHT_MISMATCH_BLOCKED: OCC symbol has '{occ_right_char}' but "
                    f"expected '{expected_occ_char}' | {occ_symbol}"
                )
                filter_counts["direction"] += 1
                continue

            # Check DTE using config values (1-5 for intraday, V2.13)
            dte = (contract.Expiry - self.Time).days
            if dte < effective_dte_min or dte > effective_dte_max:
                filter_counts["dte"] += 1
                continue

            # V2.3: Get delta and check if within tolerance of target
            # V2.12 Fix #7: Skip contracts with missing or zero Greeks (backtest data gaps)
            if not hasattr(contract, "Greeks") or contract.Greeks.Delta == 0:
                filter_counts["greeks"] += 1
                continue  # Skip contracts without valid Greeks data
            contract_delta = abs(contract.Greeks.Delta)
            delta_diff = abs(contract_delta - target_delta)
            if strategy == IntradayStrategy.ITM_MOMENTUM and bool(
                getattr(config, "ITM_ENGINE_ENABLED", False)
            ):
                itm_engine_delta_min = float(getattr(config, "ITM_DELTA_MIN", 0.65))
                itm_engine_delta_max = float(getattr(config, "ITM_DELTA_MAX", 0.75))
                if contract_delta < itm_engine_delta_min or contract_delta > itm_engine_delta_max:
                    filter_counts["delta"] += 1
                    continue
            else:
                if strategy == IntradayStrategy.PROTECTIVE_PUTS:
                    delta_tolerance = float(
                        getattr(config, "PROTECTIVE_PUTS_DELTA_TOLERANCE", 0.12)
                    )
                else:
                    delta_tolerance = float(getattr(config, "OPTIONS_DELTA_TOLERANCE", 0.10))
                if delta_diff > delta_tolerance:
                    filter_counts["delta"] += 1
                    continue

            # Check liquidity (relaxed for 0DTE)
            if contract.OpenInterest < config.OPTIONS_MIN_OPEN_INTEREST:
                filter_counts["oi"] += 1
                continue

            # Check spread - use safe price getter
            bid, ask = self._get_contract_prices(contract)
            if bid <= 0 or ask <= 0:
                filter_counts["prices"] += 1
                continue

            mid_price = (bid + ask) / 2
            spread_pct = (ask - bid) / mid_price if mid_price > 0 else 1.0

            if spread_pct > config.OPTIONS_SPREAD_WARNING_PCT:
                filter_counts["spread"] += 1
                continue

            # V6.0 P0 FIX: Derive direction from ACTUAL contract.Right, not requested direction
            # This ensures OptionContract.direction matches what we're actually trading
            actual_direction = (
                OptionDirection.CALL if contract.Right == OptionRight.Call else OptionDirection.PUT
            )

            # Create OptionContract object with direction from actual contract
            opt_contract = OptionContract(
                symbol=str(contract.Symbol),
                underlying="QQQ",
                direction=actual_direction,
                strike=contract.Strike,
                expiry=str(contract.Expiry.date()),
                delta=contract_delta,
                gamma=contract.Greeks.Gamma if hasattr(contract, "Greeks") else 0.0,
                vega=contract.Greeks.Vega if hasattr(contract, "Greeks") else 0.0,
                theta=contract.Greeks.Theta if hasattr(contract, "Greeks") else 0.0,
                bid=bid,
                ask=ask,
                mid_price=mid_price,
                open_interest=contract.OpenInterest,
                days_to_expiry=dte,
            )

            # Contract scoring:
            # - ITM_MOMENTUM: target DTE proximity + liquidity/OI quality (multi-day hold intent)
            # - Others: preserve legacy low-DTE preference
            delta_score = 1.0 - (delta_diff / config.OPTIONS_DELTA_TOLERANCE)
            spread_score = 1.0 - spread_pct
            if strategy == IntradayStrategy.ITM_MOMENTUM:
                try:
                    vix_val = float(vix_current) if vix_current is not None else None
                except Exception:
                    vix_val = None
                if bool(getattr(config, "ITM_ENGINE_ENABLED", False)):
                    target_dte = int(getattr(config, "ITM_TARGET_DTE", 6))
                else:
                    low_thr = float(getattr(config, "MICRO_DTE_LOW_VIX_THRESHOLD", 18.0))
                    high_thr = float(getattr(config, "MICRO_DTE_HIGH_VIX_THRESHOLD", 25.0))
                    if vix_val is not None and vix_val < low_thr:
                        target_dte = int(getattr(config, "INTRADAY_ITM_TARGET_DTE_LOW_VIX", 4))
                    elif vix_val is not None and vix_val >= high_thr:
                        target_dte = int(getattr(config, "INTRADAY_ITM_TARGET_DTE_HIGH_VIX", 3))
                    else:
                        target_dte = int(getattr(config, "INTRADAY_ITM_TARGET_DTE_MED_VIX", 3))
                dte_range = max(1, effective_dte_max - effective_dte_min)
                dte_score = max(0.0, 1.0 - (abs(dte - target_dte) / dte_range))
                oi_soft_cap = max(1, int(getattr(config, "INTRADAY_ITM_OI_SOFT_CAP", 2000)))
                oi_score = min(float(contract.OpenInterest), float(oi_soft_cap)) / float(
                    oi_soft_cap
                )

                w_delta = float(getattr(config, "INTRADAY_ITM_SCORE_DELTA_WEIGHT", 0.45))
                w_dte = float(getattr(config, "INTRADAY_ITM_SCORE_DTE_WEIGHT", 0.30))
                w_spread = float(getattr(config, "INTRADAY_ITM_SCORE_SPREAD_WEIGHT", 0.20))
                w_oi = float(getattr(config, "INTRADAY_ITM_SCORE_OI_WEIGHT", 0.05))
                weight_sum = max(1e-9, w_delta + w_dte + w_spread + w_oi)
                score = (
                    (delta_score * w_delta)
                    + (dte_score * w_dte)
                    + (spread_score * w_spread)
                    + (oi_score * w_oi)
                ) / weight_sum
            else:
                dte_score = 1.0 / (1.0 + dte)  # Legacy: strongly prefer lower DTE
                score = (delta_score * 0.4) + (dte_score * 0.4) + (spread_score * 0.2)
            candidates.append((score, opt_contract))

        if not candidates:
            # Plumbing hardening: run one relaxed pass before declaring no contract.
            relaxed_candidates = []
            relaxed_dte_min = max(0, effective_dte_min - 1)
            relaxed_dte_max = max(relaxed_dte_min, effective_dte_max + 1)
            relaxed_delta_tolerance = max(
                float(getattr(config, "OPTIONS_DELTA_TOLERANCE", 0.10)) * 1.75,
                0.18,
            )
            relaxed_spread_cap = min(
                0.80,
                float(getattr(config, "OPTIONS_SPREAD_WARNING_PCT", 0.35)) * 1.35,
            )
            relaxed_oi_min = max(
                1,
                int(float(getattr(config, "OPTIONS_MIN_OPEN_INTEREST", 50)) * 0.50),
            )
            if strategy == IntradayStrategy.ITM_MOMENTUM and bool(
                getattr(config, "ITM_ENGINE_ENABLED", False)
            ):
                relaxed_itm_min = max(0.05, float(getattr(config, "ITM_DELTA_MIN", 0.65)) - 0.05)
                relaxed_itm_max = min(0.99, float(getattr(config, "ITM_DELTA_MAX", 0.75)) + 0.05)
            else:
                relaxed_itm_min = None
                relaxed_itm_max = None

            for contract in chain:
                if contract.Right != required_right:
                    continue
                dte = (contract.Expiry - self.Time).days
                if dte < relaxed_dte_min or dte > relaxed_dte_max:
                    continue
                if not hasattr(contract, "Greeks") or contract.Greeks.Delta == 0:
                    continue

                contract_delta = abs(contract.Greeks.Delta)
                if relaxed_itm_min is not None and relaxed_itm_max is not None:
                    if contract_delta < relaxed_itm_min or contract_delta > relaxed_itm_max:
                        continue
                else:
                    if abs(contract_delta - target_delta) > relaxed_delta_tolerance:
                        continue

                if int(getattr(contract, "OpenInterest", 0) or 0) < relaxed_oi_min:
                    continue

                bid, ask = self._get_contract_prices(contract)
                if bid <= 0 or ask <= 0:
                    continue
                mid_price = (bid + ask) / 2
                if mid_price <= 0:
                    continue
                spread_pct = (ask - bid) / mid_price
                if spread_pct > relaxed_spread_cap:
                    continue

                actual_direction = (
                    OptionDirection.CALL
                    if contract.Right == OptionRight.Call
                    else OptionDirection.PUT
                )
                opt_contract = OptionContract(
                    symbol=str(contract.Symbol),
                    underlying="QQQ",
                    direction=actual_direction,
                    strike=contract.Strike,
                    expiry=str(contract.Expiry.date()),
                    delta=contract_delta,
                    gamma=contract.Greeks.Gamma if hasattr(contract, "Greeks") else 0.0,
                    vega=contract.Greeks.Vega if hasattr(contract, "Greeks") else 0.0,
                    theta=contract.Greeks.Theta if hasattr(contract, "Greeks") else 0.0,
                    bid=bid,
                    ask=ask,
                    mid_price=mid_price,
                    open_interest=contract.OpenInterest,
                    days_to_expiry=dte,
                )
                delta_diff = abs(contract_delta - target_delta)
                score = (1.0 / (1.0 + delta_diff)) + (1.0 / (1.0 + dte))
                relaxed_candidates.append((score, opt_contract))

            if relaxed_candidates:
                relaxed_candidates.sort(key=lambda x: x[0], reverse=True)
                best_relaxed = relaxed_candidates[0][1]
                self.Log(
                    f"INTRADAY_FALLBACK_SELECTED: {direction.value} | "
                    f"Strategy={strategy.value if strategy else 'NONE'} | "
                    f"DTE={best_relaxed.days_to_expiry} | Delta={best_relaxed.delta:.2f} | "
                    f"SpreadCap={relaxed_spread_cap:.2f} | OI>={relaxed_oi_min} | "
                    f"Symbol={best_relaxed.symbol}"
                )
                return best_relaxed

            # V2.13 Fix #16: Log filter diagnostics to identify Black Hole causes
            # T-17 FIX: Enhanced diagnostics with actionable insights
            # Calculate where contracts are being lost in the funnel
            passed_direction = total_contracts - filter_counts["direction"]
            passed_dte = passed_direction - filter_counts["dte"]
            passed_greeks = passed_dte - filter_counts["greeks"]
            passed_delta = passed_greeks - filter_counts["delta"]

            # Identify the primary blocker
            if filter_counts["direction"] > total_contracts * 0.9:
                primary_blocker = "DIRECTION (wrong CALL/PUT ratio in chain)"
            elif filter_counts["dte"] > passed_direction * 0.9:
                primary_blocker = f"DTE (outside {effective_dte_min}-{effective_dte_max} range)"
            elif filter_counts["greeks"] > passed_dte * 0.9:
                primary_blocker = "GREEKS (missing or zero delta data)"
            elif filter_counts["delta"] > passed_greeks * 0.5:
                primary_blocker = (
                    f"DELTA (outside {target_delta:.2f} +/- {config.OPTIONS_DELTA_TOLERANCE})"
                )
            elif filter_counts["prices"] > 0:
                primary_blocker = "PRICES (bid/ask <= 0)"
            else:
                primary_blocker = "SPREAD or OI"

            self.Log(
                f"INTRADAY_FILTER_FAIL: {direction.value} | Total={total_contracts} | "
                f"Dir={filter_counts['direction']} DTE={filter_counts['dte']} "
                f"Greeks={filter_counts['greeks']} Delta={filter_counts['delta']} "
                f"OI={filter_counts['oi']} Prices={filter_counts['prices']} "
                f"Spread={filter_counts['spread']} | "
                f"Funnel: {total_contracts}→{passed_direction}→{passed_dte}→{passed_greeks}→{passed_delta} | "
                f"Blocker={primary_blocker}"
            )
            return None

        # Return best candidate (closest to target delta with lowest DTE)
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]
        # V6.0 P0 FIX: Log the ACTUAL direction from contract, not requested direction
        self.Log(
            f"INTRADAY: Selected {best.direction.value} | Strike={best.strike} | "
            f"Delta={best.delta:.2f} | DTE={best.days_to_expiry} | Mid=${best.mid_price:.2f} | "
            f"Symbol={best.symbol}"
        )
        return best

    def _select_swing_option_contract(
        self, chain, direction: OptionDirection = None
    ) -> Optional[OptionContract]:
        """
        V2.3: Select QQQ option contract for SWING mode (5-45 DTE).

        Target delta: 0.70 (slightly ITM for higher directional exposure)

        Criteria:
        - Target 0.70 delta (±0.15 tolerance)
        - DTE 5-45 days (swing mode only)
        - Sufficient open interest
        - Tight bid-ask spread

        Args:
            chain: QuantConnect options chain.
            direction: OptionDirection.CALL or OptionDirection.PUT (default: CALL)

        Returns:
            OptionContract or None if no suitable contract found.
        """
        if chain is None:
            return None

        # Default to CALL if direction not specified
        if direction is None:
            direction = OptionDirection.CALL

        qqq_price = self.Securities[self.qqq].Price
        target_delta = config.OPTIONS_SWING_DELTA_TARGET  # 0.70

        # Determine which option right to filter for
        target_right = OptionRight.Call if direction == OptionDirection.CALL else OptionRight.Put

        # Filter for target direction, target delta, SWING DTE (5-45 days)
        candidates = []
        for contract in chain:
            if contract.Right != target_right:
                continue

            # Check SWING DTE range (5-45 days per spec)
            dte = (contract.Expiry - self.Time).days
            if dte < config.OPTIONS_SWING_DTE_MIN or dte > config.OPTIONS_SWING_DTE_MAX:
                continue

            # V2.3: Get delta and check if within tolerance of target
            # V2.12 Fix #7: Skip contracts with missing or zero Greeks (backtest data gaps)
            if not hasattr(contract, "Greeks") or contract.Greeks.Delta == 0:
                continue  # Skip contracts without valid Greeks data
            contract_delta = abs(contract.Greeks.Delta)
            delta_diff = abs(contract_delta - target_delta)
            if delta_diff > config.OPTIONS_DELTA_TOLERANCE:
                continue

            # Check liquidity
            if contract.OpenInterest < config.OPTIONS_MIN_OPEN_INTEREST:
                continue

            # Check spread - use safe price getter
            bid, ask = self._get_contract_prices(contract)
            if bid <= 0 or ask <= 0:
                continue

            mid_price = (bid + ask) / 2
            spread_pct = (ask - bid) / mid_price if mid_price > 0 else 1.0

            if spread_pct > config.OPTIONS_SPREAD_WARNING_PCT:
                continue

            # Create OptionContract object with specified direction (CALL or PUT)
            opt_contract = OptionContract(
                symbol=str(contract.Symbol),
                underlying="QQQ",
                direction=direction,  # V2.3: Use direction parameter
                strike=contract.Strike,
                expiry=str(contract.Expiry.date()),
                delta=contract_delta,
                gamma=contract.Greeks.Gamma if hasattr(contract, "Greeks") else 0.0,
                vega=contract.Greeks.Vega if hasattr(contract, "Greeks") else 0.0,
                theta=contract.Greeks.Theta if hasattr(contract, "Greeks") else 0.0,
                bid=bid,
                ask=ask,
                mid_price=mid_price,
                open_interest=contract.OpenInterest,
                days_to_expiry=dte,
            )

            # V2.3: Score by proximity to target delta (0.70) + liquidity
            delta_score = 1.0 - (delta_diff / config.OPTIONS_DELTA_TOLERANCE)
            liquidity_score = 1.0 - spread_pct
            score = (delta_score * 0.7) + (liquidity_score * 0.3)
            candidates.append((score, opt_contract))

        if not candidates:
            return None

        # Return best candidate (closest to target delta with good liquidity)
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def _apply_spread_margin_guard(
        self,
        signal: Optional[TargetWeight],
        source_tag: str,
    ) -> Optional[TargetWeight]:
        """
        Final spread margin guard before router submission.
        Applies identical logic for EOD and intraday VASS spread flows.
        """
        if signal is None or not signal.metadata:
            return signal
        if not signal.metadata.get("spread_short_leg_quantity"):
            return signal

        spread_width = signal.metadata.get("spread_width", config.SPREAD_WIDTH_TARGET)
        spread_type = signal.metadata.get("spread_type", "DEBIT")
        credit_received = signal.metadata.get("spread_credit_received")
        short_qty_raw = int(signal.metadata.get("spread_short_leg_quantity", 0))
        contracts_requested = abs(short_qty_raw)
        if contracts_requested <= 0:
            return None

        base_margin_per_contract = self.options_engine.estimate_spread_margin_per_contract(
            spread_width=spread_width,
            spread_type=spread_type,
            credit_received=credit_received,
        )
        safety = max(getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80), 0.01)
        required_per_contract = base_margin_per_contract / safety

        free_margin = float(self.Portfolio.MarginRemaining)
        total_equity = float(self.Portfolio.TotalPortfolioValue)
        cushion_pct = getattr(config, "MARGIN_MIN_FREE_EQUITY_PCT", 0.10)
        min_free_margin = total_equity * cushion_pct
        effective_free_margin = max(0.0, free_margin - min_free_margin)

        required_margin = contracts_requested * required_per_contract
        if required_margin <= effective_free_margin:
            # Align with router options budget gate to avoid margin-pass/router-reject churn.
            if self.portfolio_router is not None and bool(
                getattr(config, "OPTIONS_BUDGET_GATE_ENABLED", True)
            ):
                budget_required = required_margin
                try:
                    # Use router's own combo estimator for consistency with execute() gate.
                    per_contract, _ = self.portfolio_router._estimate_combo_margin_per_contract(  # type: ignore[attr-defined]
                        signal.metadata
                    )
                    if per_contract > 0:
                        budget_required = contracts_requested * per_contract
                except Exception:
                    pass

                budget_cap = float(self.portfolio_router.get_options_budget_cap())
                budget_used = float(self.portfolio_router.get_options_budget_used())
                projected = budget_used + budget_required
                if budget_cap > 0 and budget_required > 0 and projected > budget_cap:
                    self._diag_vass_block_count += 1
                    self.Log(
                        f"{source_tag}: BLOCKED - options budget precheck | "
                        f"Used=${budget_used:,.0f} + Req=${budget_required:,.0f} > "
                        f"Cap=${budget_cap:,.0f} ({(projected / budget_cap):.1%})"
                    )
                    return None

            self.Log(
                f"{source_tag}: Margin check passed | Required=${required_margin:,.0f} | "
                f"Effective Free=${effective_free_margin:,.0f} | Equity=${total_equity:,.0f}"
            )
            return signal

        max_contracts = int(effective_free_margin / required_per_contract)
        if max_contracts < 1:
            self.Log(
                f"{source_tag}: BLOCKED - Insufficient margin for 1 spread | "
                f"Required=${required_per_contract:,.0f}/contract | "
                f"Effective Free=${effective_free_margin:,.0f}"
            )
            return None

        short_sign = -1 if short_qty_raw < 0 else 1
        signal.metadata["spread_short_leg_quantity"] = short_sign * max_contracts
        signal.metadata["spread_long_leg_quantity"] = max_contracts
        signal.metadata["contracts"] = max_contracts
        signal.requested_quantity = max_contracts

        self.Log(
            f"{source_tag}: MARGIN-SIZED DOWN | "
            f"Requested={contracts_requested} -> Actual={max_contracts} contracts | "
            f"Per=${required_per_contract:,.0f} | Effective Free=${effective_free_margin:,.0f}"
        )
        return signal

    def _build_option_contract_from_fill(
        self,
        symbol: Any,
        fill_price: float,
        direction_hint: Optional[OptionDirection] = None,
    ) -> Optional[OptionContract]:
        """Best-effort OptionContract reconstruction for fill recovery paths."""
        try:
            sec = self.Securities[symbol] if symbol in self.Securities else None
        except Exception:
            sec = None

        try:
            symbol_obj = symbol
            symbol_str = str(symbol_obj)
            strike = float(getattr(getattr(symbol_obj, "ID", None), "StrikePrice", 0.0) or 0.0)
            expiry_obj = getattr(getattr(symbol_obj, "ID", None), "Date", None)
            expiry = str(expiry_obj.date()) if expiry_obj is not None else ""
            right_obj = getattr(getattr(symbol_obj, "ID", None), "OptionRight", None)
            right_str = str(right_obj).upper() if right_obj is not None else ""
        except Exception:
            return None

        if direction_hint is not None:
            direction = direction_hint
        elif "PUT" in right_str or right_str.endswith("P"):
            direction = OptionDirection.PUT
        else:
            direction = OptionDirection.CALL

        bid = float(getattr(sec, "BidPrice", 0.0) or 0.0) if sec is not None else 0.0
        ask = float(getattr(sec, "AskPrice", 0.0) or 0.0) if sec is not None else 0.0
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else float(fill_price)

        delta = 0.0
        gamma = 0.0
        vega = 0.0
        theta = 0.0
        if sec is not None and hasattr(sec, "Greeks") and sec.Greeks is not None:
            try:
                delta = float(abs(sec.Greeks.Delta))
                gamma = float(sec.Greeks.Gamma)
                vega = float(sec.Greeks.Vega)
                theta = float(sec.Greeks.Theta)
            except Exception:
                pass

        open_interest = int(getattr(sec, "OpenInterest", 0) or 0) if sec is not None else 0
        days_to_expiry = 0
        try:
            if expiry_obj is not None:
                days_to_expiry = int((expiry_obj.date() - self.Time.date()).days)
        except Exception:
            pass

        return OptionContract(
            symbol=symbol_str,
            underlying="QQQ",
            direction=direction,
            strike=strike,
            expiry=expiry,
            delta=delta,
            gamma=gamma,
            vega=vega,
            theta=theta,
            bid=bid,
            ask=ask,
            mid_price=mid,
            open_interest=open_interest,
            days_to_expiry=days_to_expiry,
        )

    def _canonical_options_reason_code(self, code: Optional[str]) -> str:
        """
        Normalize legacy/mixed reason codes to explicit E_*/R_* taxonomy.
        """
        raw = (code or "").strip()
        if not raw:
            return "E_NO_REASON_UNCLASSIFIED"
        if raw.startswith("R_CONTRACT_QUALITY:"):
            detail = raw.split(":", 1)[1].strip().upper() if ":" in raw else "UNKNOWN"
            detail_norm = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in detail)
            return f"R_CONTRACT_QUALITY_{detail_norm or 'UNKNOWN'}"
        if raw.startswith("R_EV_PRE_"):
            return raw.split(":", 1)[0]
        if raw.startswith("R_") and ":" in raw:
            # Keep rejection code stable and move runtime numbers/details to reason text.
            # Example: "R_DIRECTION_MIN_GAP: BULLISH elapsed 30m < 120m" -> "R_DIRECTION_MIN_GAP"
            base = raw.split(":", 1)[0].strip().upper()
            normalized = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in base)
            return normalized or "R_UNKNOWN"
        if raw.startswith(("E_", "R_")):
            return raw

        mapping = {
            "TRADE_LIMIT_BLOCK": "R_TRADE_LIMIT",
            "TIME_WINDOW_BLOCK": "E_TIME_WINDOW",
            "GAP_FILTER_BLOCK": "E_GAP_FILTER",
            "VOL_SHOCK_BLOCK": "E_VOL_SHOCK",
            "VIX_MAX_BLOCK": "E_VIX_MAX",
            "PUT_ENTRY_VIX_MAX_BLOCK": "E_PUT_VIX_MAX",
            "WIN_RATE_GATE_BLOCK": "R_WIN_RATE_GATE",
            "REGIME_CRISIS_BLOCK": "E_REGIME_CRISIS",
            "DIRECTION_MISSING": "E_DIRECTION_MISSING",
            "BULL_CALL_STRESS_BLOCK": "E_CALL_GATE_STRESS",
            "SPREAD_COOLDOWN_ACTIVE": "R_SPREAD_COOLDOWN",
            "CREDIT_ENTRY_VALIDATION_FAILED": "R_SPREAD_SELECTION_FAIL_UNCLASSIFIED",
            "DEBIT_ENTRY_VALIDATION_FAILED": "R_SPREAD_SELECTION_FAIL_UNCLASSIFIED",
            "SWING_SLOT_BLOCK": "R_SLOT_BLOCK",
            "INSUFFICIENT_CANDIDATES": "R_INSUFFICIENT_CANDIDATES",
            "OPPOSITE_ROUTE_INSUFFICIENT_CANDIDATES": "R_OPPOSITE_ROUTE_INSUFFICIENT_CANDIDATES",
            "UNKNOWN": "E_UNKNOWN_UNCLASSIFIED",
        }
        if raw in mapping:
            return mapping[raw]
        if raw.startswith("BEAR_PUT_ASSIGNMENT_GATE_"):
            return f"R_{raw}"
        return f"E_{raw}"

    def _scan_options_signals(self, data: Slice) -> None:
        """
        V2.1.1: Scan for Options entry signals during intraday session.

        Dual-Mode Architecture:
        - Swing Mode (5-45 DTE): 4-factor entry scoring
        - Intraday Mode (0-2 DTE): Micro Regime Engine

        V2.3.20: Now allows options during cold start with 50% sizing.

        Args:
            data: Current data slice.
        """
        # V2.3.20: Calculate size multiplier for cold start
        is_cold_start = self.cold_start_engine.is_cold_start_active()
        size_multiplier = config.OPTIONS_COLD_START_MULTIPLIER if is_cold_start else 1.0
        size_multiplier *= self._premarket_vix_size_mult

        # Skip if indicators not ready
        if not self.qqq_adx.IsReady or not self.qqq_sma200.IsReady:
            return

        # V6.14: L3 freeze window blocks all new options entries intraday.
        if self._is_premarket_ladder_entry_block_active():
            if self.Time.minute % 15 == 0:
                until_h, until_m = self._premarket_vix_entry_block_until
                self.Log(
                    f"PREMARKET_LADDER_BLOCK: Options blocked until {until_h:02d}:{until_m:02d} | "
                    f"{self._premarket_vix_ladder_reason}"
                )
            return

        # V2.5 PART 19 FIX: Stateless reconciliation - detect zombie state
        # If internal tracker thinks we have a position, but portfolio is empty,
        # clear the zombie state to unblock trading
        if self.options_engine.has_position():
            actual_option_count = self._get_actual_option_count()
            if actual_option_count == 0:
                self.Log(
                    "OPT_ZOMBIE_FIX: Internal state shows position but portfolio empty - clearing zombie state"
                )
                self.options_engine.clear_all_positions()
            else:
                # Engine isolation: do not short-circuit scans just because an
                # intraday engine already has a position. Entry arbitration is
                # handled downstream by slot/router/risk checks.
                pass

        # V2.3 FIX: Skip if kill switch triggered (prevents new entries after liquidation)
        if self._kill_switch_handled_today:
            # V2.3 DEBUG: Log once per day when options blocked by kill switch (live only)
            if self.Time.hour == 10 and self.Time.minute == 30 and self.LiveMode:
                self.Log("OPT_SCAN: Blocked - Kill switch handled today")
            return

        # V2.27: Skip-day enforcement after Tier 2+ kill switch
        if self.risk_engine.is_ks_skip_day(str(self.Time.date())):
            if self.Time.hour == 10 and self.Time.minute == 30:
                self.Log("OPT_SCAN: Blocked - KS skip day active")
            return

        # V2.9: Skip if in settlement cooldown (Bug #6 fix)
        if not self._can_trade_options_settlement_aware():
            return

        # Plumbing safety: clear stale spread margin reservations before new entries.
        self._scrub_stale_spread_margin_reservations()

        # V2.11 (Pitfall #6): Margin-aware sizing guard
        # Check available margin BEFORE calculating any allocation
        margin_remaining = self.portfolio_router.get_effective_margin_remaining()
        # V3.0 SCALABILITY FIX: Use percentage-based minimum (was hardcoded $1,000)
        # At $50K: 2% = $1,000, at $200K: 2% = $4,000 (scales with portfolio)
        portfolio_value = self.Portfolio.TotalPortfolioValue
        min_margin_required = portfolio_value * config.OPTIONS_MIN_MARGIN_PCT
        if margin_remaining < min_margin_required:
            if self.Time.minute == 0:  # Log once per hour to avoid spam
                self.Log(
                    f"OPT_MARGIN_GUARD: Margin ${margin_remaining:,.0f} < "
                    f"${min_margin_required:,.0f} ({config.OPTIONS_MIN_MARGIN_PCT:.0%} of portfolio) | Options blocked"
                )
            return

        # Calculate effective portfolio value capped by available margin
        # V2.19 FIX: Use min() to cap at OPTIONS_MAX_MARGIN_CAP (not subtract it)
        # V3.0 SCALABILITY FIX: Use percentage-based cap (OPTIONS_MAX_MARGIN_PCT)
        base_tradeable = self.capital_engine.calculate(
            self.Portfolio.TotalPortfolioValue
        ).tradeable_eq
        # Cap margin used for options at percentage of portfolio (scales with size)
        options_max_margin = portfolio_value * config.OPTIONS_MAX_MARGIN_PCT
        margin_available_for_options = min(margin_remaining, options_max_margin)
        # max_portfolio_from_margin = margin_available / OPTIONS_SWING_ALLOCATION
        # This ensures: effective_portfolio * OPTIONS_SWING_ALLOCATION <= margin_available
        max_portfolio_from_margin = (
            margin_available_for_options / config.OPTIONS_SWING_ALLOCATION
            if config.OPTIONS_SWING_ALLOCATION > 0
            else base_tradeable
        )
        effective_portfolio_value = min(base_tradeable, max_portfolio_from_margin)

        if effective_portfolio_value < base_tradeable:
            self.Log(
                f"OPT_MARGIN_CAP: Sizing capped by margin | "
                f"Base=${base_tradeable:,.0f} | Effective=${effective_portfolio_value:,.0f} | "
                f"Margin_remaining=${margin_remaining:,.0f}"
            )

        # V2.3.6 FIX: Scan during active window (10:00-15:00)
        # Removed 10:30 delay - momentum/credit strategies need 10:00-10:30 window
        # Strategy-specific timing (if needed) should be handled in Options Engine
        current_hour = self.Time.hour
        current_minute = self.Time.minute
        # Before 10:00 or after 15:00 -> skip
        if current_hour < 10 or current_hour >= 15:
            return

        # CRITICAL FIX: Validate options symbol is resolved before use
        if not self._validate_options_symbol():
            return

        # Get options chain
        chain = self._get_valid_options_chain(data.OptionChains, mode_label="INTRADAY")
        if chain is None:
            return

        # Get current values
        qqq_price, adx_value, ma200_value, ma50_value = self._get_options_market_snapshot()

        # V2.4.1: Throttle intraday scanning to every 15 minutes (was every minute)
        # This reduces 95 scans/hour to 4 scans/hour
        # V10.16: lane-scoped rejection cooldowns (MICRO/ITM independent).
        micro_intraday_cooldown_active = self._is_engine_lane_cooldown_active("MICRO")
        itm_intraday_cooldown_active = self._is_engine_lane_cooldown_active("ITM")
        # Both-lanes cooldown gate: only suppress scan when both MICRO and ITM lanes are cooling down.
        all_intraday_lanes_cooldown_active = (
            micro_intraday_cooldown_active and itm_intraday_cooldown_active
        )
        # Defaults for explicit ITM pass (only used when intraday scan context is ready).
        itm_dir = None
        itm_reason = ""
        micro_state = None
        vix_intraday = 0.0
        vix_level_cboe = None
        transition_ctx = self._get_transition_execution_context()
        regime_score = float(
            transition_ctx.get("transition_score", self._get_decision_regime_score_for_options())
            or self._get_decision_regime_score_for_options()
        )
        intraday_scan_context_ready = False
        if (
            self._should_scan_engine_cycle()
            and self._qqq_at_open > 0
            and not all_intraday_lanes_cooldown_active
        ):
            # V5.3: Check position limits before scanning
            can_intraday, intraday_limit_reason = self.options_engine.can_enter_single_leg()
            if not can_intraday:
                # V6.2: Log position limit block (was silent - Bug #3 instrumentation)
                self._log_high_frequency_event(
                    config_flag="LOG_INTRADAY_BLOCKED_BACKTEST_ENABLED",
                    category="INTRADAY_BLOCKED",
                    reason_key=self._canonical_options_reason_code(intraday_limit_reason),
                    message=f"INTRADAY: Blocked - {intraday_limit_reason}",
                )
            else:
                # V2.4.1 FIX: Use UVXY-derived VIX proxy instead of stale daily VIX
                # self._current_vix is daily close and doesn't change intraday
                vix_intraday = self._get_vix_engine_proxy()

                # V6.2: Get CBOE VIX level for consistent Micro Regime classification
                # This ensures all _micro_regime_engine.update() calls use same VIX level
                vix_level_cboe = self._get_vix_level()

                # Get macro regime score for direction conflict check
                regime_score = float(
                    transition_ctx.get("transition_score", regime_score) or regime_score
                )

                # V6.3: Calculate UVXY intraday change for conviction check
                uvxy_pct = 0.0
                if hasattr(self, "_uvxy_at_open") and self._uvxy_at_open > 0:
                    uvxy_current = self.Securities[self.uvxy].Price if hasattr(self, "uvxy") else 0
                    if uvxy_current > 0:
                        uvxy_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open

                intraday_scan_context_ready = True

                if bool(getattr(config, "ITM_ENGINE_ENABLED", False)):
                    itm_dir, itm_reason = self.options_engine.get_itm_direction_proposal(
                        qqq_current=qqq_price,
                        transition_ctx=transition_ctx,
                    )

                if bool(getattr(config, "MICRO_ENTRY_ENGINE_ENABLED", True)):
                    self.options_engine.run_micro_engine_cycle(
                        chain=chain,
                        qqq_price=qqq_price,
                        regime_score=regime_score,
                        size_multiplier=size_multiplier,
                        effective_portfolio_value=effective_portfolio_value,
                        vix_intraday=vix_intraday,
                        vix_level_cboe=vix_level_cboe,
                        transition_ctx=transition_ctx,
                        uvxy_pct=uvxy_pct,
                        micro_intraday_cooldown_active=micro_intraday_cooldown_active,
                    )

        if bool(getattr(config, "ITM_ENGINE_ENABLED", False)):
            self.options_engine.run_itm_engine_explicit_cycle(
                chain=chain,
                qqq_price=qqq_price,
                regime_score=regime_score,
                size_multiplier=size_multiplier,
                effective_portfolio_value=effective_portfolio_value,
                vix_intraday=vix_intraday,
                vix_level_cboe=vix_level_cboe,
                transition_ctx=transition_ctx,
                itm_dir=itm_dir,
                itm_reason=itm_reason,
                intraday_scan_context_ready=intraday_scan_context_ready,
                itm_intraday_cooldown_active=itm_intraday_cooldown_active,
            )

        self.options_engine.run_vass_engine_entry_cycle(
            chain=chain,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            size_multiplier=size_multiplier,
            effective_portfolio_value=effective_portfolio_value,
            margin_remaining=margin_remaining,
            vix_level_cboe=vix_level_cboe,
            transition_ctx=transition_ctx,
        )

    def _check_spread_exit(self, data: Slice) -> None:
        """
        V2.3: Check for spread exit conditions.

        Exit conditions:
        1. Take profit at 50% of max profit
        2. Close by 5 DTE (avoid gamma acceleration)
        3. Regime reversal (Bull exit if < 45, Bear exit if > 60)

        Args:
            data: Current data slice.
        """
        # Defensive local import for minified/backtest packaging variants.
        # Ensures spread-exit signals always have TargetWeight available.
        from models.target_weight import TargetWeight

        # Plumbing guard: never emit spread-exit orders while the primary market is closed.
        # Off-hours time-stop signals were creating submit->reconcile churn and masking true fill flow.
        if not self._is_primary_market_open():
            return

        spreads = self.options_engine.get_spread_positions()
        if not spreads:
            return
        active_spread_keys = set()
        for s in spreads:
            active_spread_keys.add(self._build_spread_runtime_key(s))
        for key in list(self._spread_forced_close_retry.keys()):
            if key not in active_spread_keys:
                self._spread_forced_close_retry.pop(key, None)
                self._spread_forced_close_reason.pop(key, None)
                self._spread_forced_close_cancel_counts.pop(key, None)
                self._spread_forced_close_retry_cycles.pop(key, None)
                self._spread_last_close_submit_at.pop(key, None)
                self._spread_close_first_cancel_at.pop(key, None)
                self._spread_close_intent_by_key.pop(key, None)
        for key in list(self._spread_close_trackers.keys()):
            if key not in active_spread_keys:
                self._spread_close_trackers.pop(key, None)
        for key in list(self._spread_exit_mark_cache.keys()):
            if key not in active_spread_keys:
                self._spread_exit_mark_cache.pop(key, None)

        underlying_price = self.Securities["QQQ"].Price
        current_hour = self.Time.hour
        current_minute = self.Time.minute
        available_margin = self.Portfolio.MarginRemaining
        regime_score = self._get_effective_regime_score_for_options()

        for spread in spreads:
            long_symbol = self._normalize_symbol_str(spread.long_leg.symbol)
            short_symbol = self._normalize_symbol_str(spread.short_leg.symbol)
            spread_key = self._build_spread_runtime_key(spread)
            spread_type = str(getattr(spread, "spread_type", "") or "")
            spread_is_credit = bool(
                getattr(spread, "net_debit", 0.0) < 0 or "CREDIT" in spread_type.upper()
            )
            spread_entry_debit = float(max(0.0, getattr(spread, "net_debit", 0.0)))
            spread_entry_credit = float(max(0.0, -getattr(spread, "net_debit", 0.0)))
            vass_fast_close = bool(getattr(config, "VASS_CLOSE_DISABLE_MULTISESSION_RETRY", False))
            vass_close_timeout_sec = max(
                5, int(getattr(config, "VASS_CLOSE_LIMIT_TIMEOUT_SECONDS", 30))
            )
            vass_limit_attempts = max(
                1, int(getattr(config, "VASS_CLOSE_MAX_COMBO_LIMIT_ATTEMPTS", 1))
            )

            # V12.24: timeout-driven retry trigger for active close intents.
            # Do not wait for cancel callbacks/stale cleanup to schedule retries.
            retry_at = self._spread_forced_close_retry.get(spread_key)
            if retry_at is None and vass_fast_close:
                last_submit_at = self._spread_last_close_submit_at.get(spread_key)
                if last_submit_at is not None and (
                    self._has_open_order_for_symbol(long_symbol)
                    or self._has_open_order_for_symbol(short_symbol)
                ):
                    elapsed_sec = max(0.0, (self.Time - last_submit_at).total_seconds())
                    timeout_at = last_submit_at + timedelta(seconds=vass_close_timeout_sec)
                    if elapsed_sec >= float(vass_close_timeout_sec):
                        self._spread_forced_close_reason[
                            spread_key
                        ] = f"LIMIT_TIMEOUT_{vass_close_timeout_sec}s"
                        self._spread_forced_close_retry[spread_key] = self.Time
                        retry_at = self.Time
                        self.Log(
                            "SPREAD_RETRY_TIMEOUT_TRIGGER: "
                            f"Long={long_symbol} Short={short_symbol} | "
                            f"Elapsed={elapsed_sec:.0f}s >= Timeout={vass_close_timeout_sec}s"
                        )
                    else:
                        self._spread_forced_close_retry[spread_key] = timeout_at
                        continue

            # V6.21: If a spread close was canceled, keep retrying close until flat.
            if retry_at is not None and self.Time >= retry_at:
                # Prevent same-bar re-escalation while broker events settle.
                submit_guard_sec = int(getattr(config, "SPREAD_CLOSE_SUBMIT_GUARD_SECONDS", 60))
                last_submit_at = self._spread_last_close_submit_at.get(spread_key)
                if last_submit_at is not None:
                    elapsed_sec = (self.Time - last_submit_at).total_seconds()
                    if (not vass_fast_close) and elapsed_sec < submit_guard_sec:
                        if self._should_log_backtest_category(
                            "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                        ):
                            self.Log(
                                f"SPREAD_RETRY_DEFER_RECENT_SUBMIT: Long={long_symbol} "
                                f"Short={short_symbol} | Elapsed={elapsed_sec:.0f}s < "
                                f"Guard={submit_guard_sec}s"
                            )
                        continue
                retry_reason = self._spread_forced_close_reason.get(
                    spread_key, "CANCELED_CLOSE_RETRY"
                )
                origin_reason = str(self._spread_last_exit_reason.get(spread_key, "") or "")
                intent_seed = " | ".join([origin_reason, retry_reason]).upper()
                close_intent = self._get_or_init_spread_close_intent(
                    spread_key,
                    seed_text=intent_seed,
                    seed_exit_code=origin_reason or retry_reason,
                )
                intent_urgency = str(close_intent.get("urgency", "SOFT") or "SOFT").upper()
                current_phase = str(close_intent.get("phase", "LIMIT") or "LIMIT").upper()
                is_hard_retry = intent_urgency == "HARD"
                # Open-order lifecycle guard: don't stack close submits while either
                # leg already has a live broker order.
                if (not vass_fast_close) and (
                    self._has_open_order_for_symbol(long_symbol)
                    or self._has_open_order_for_symbol(short_symbol)
                ):
                    retry_minutes = int(getattr(config, "SPREAD_CLOSE_RETRY_INTERVAL_MIN", 5))
                    self._spread_forced_close_retry[spread_key] = self.Time + timedelta(
                        minutes=retry_minutes
                    )
                    if self._should_log_backtest_category(
                        "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                    ):
                        self.Log(
                            f"SPREAD_RETRY_DEFER_OPEN_ORDER: Long={long_symbol} "
                            f"Short={short_symbol} | RetryIn={retry_minutes}m | "
                            f"Reason={retry_reason}"
                        )
                    continue
                if vass_fast_close and (
                    self._has_open_order_for_symbol(long_symbol)
                    or self._has_open_order_for_symbol(short_symbol)
                ):
                    timeout_reached = False
                    if last_submit_at is not None:
                        elapsed_sec = max(0.0, (self.Time - last_submit_at).total_seconds())
                        timeout_reached = elapsed_sec >= float(vass_close_timeout_sec)
                    if timeout_reached:
                        canceled = self._cancel_open_spread_close_orders(
                            long_symbol=long_symbol,
                            short_symbol=short_symbol,
                            spread_key=spread_key,
                            reason=f"VASS_CLOSE_LIMIT_TIMEOUT_{vass_close_timeout_sec}s",
                        )
                        if canceled > 0:
                            self._spread_forced_close_reason[
                                spread_key
                            ] = f"LIMIT_TIMEOUT_{vass_close_timeout_sec}s"
                    if last_submit_at is not None:
                        timeout_at = last_submit_at + timedelta(seconds=vass_close_timeout_sec)
                        self._spread_forced_close_retry[spread_key] = max(
                            self.Time + timedelta(seconds=5),
                            timeout_at if not timeout_reached else self.Time + timedelta(seconds=5),
                        )
                    else:
                        self._spread_forced_close_retry[spread_key] = self.Time + timedelta(
                            seconds=vass_close_timeout_sec
                        )
                    continue
                # D1 fix: cancel any linked OCO orders before each retry/escalation cycle.
                self._cancel_spread_linked_oco(
                    long_symbol, short_symbol, reason="SPREAD_CLOSE_RETRY"
                )
                retry_cycles = self._spread_forced_close_retry_cycles.get(spread_key, 0) + 1
                self._spread_forced_close_retry_cycles[spread_key] = retry_cycles
                close_intent["attempt_count"] = max(
                    int(close_intent.get("attempt_count", 0) or 0), retry_cycles
                )
                if vass_fast_close:
                    if is_hard_retry:
                        max_retry_cycles = vass_limit_attempts
                    else:
                        soft_defer_minutes = max(
                            1,
                            int(getattr(config, "VASS_CLOSE_SOFT_EXIT_MAX_DEFER_MINUTES", 120)),
                        )
                        max_retry_cycles = max(
                            vass_limit_attempts,
                            int(
                                max(
                                    1,
                                    (soft_defer_minutes * 60) / max(1, int(vass_close_timeout_sec)),
                                )
                            ),
                        )
                else:
                    max_retry_cycles = int(getattr(config, "SPREAD_CLOSE_MAX_RETRY_CYCLES", 12))
                if retry_cycles >= max_retry_cycles:
                    self._advance_spread_close_intent_phase(spread_key, "SEQ_MARKET")
                    self._diag_spread_close_escalation_count += 1
                    self._diag_spread_exit_signal_count += 1
                    self._diag_spread_exit_submit_count += 1
                    self._record_order_lifecycle_event(
                        status="SPREAD_EXIT_RETRY_MAX",
                        order_id=0,
                        symbol=str(long_symbol or ""),
                        quantity=int(spread.num_spreads or 0),
                        fill_price=0.0,
                        order_type=str(spread.spread_type or ""),
                        order_tag="SPREAD_CLOSE_RETRY",
                        trace_id="",
                        message=(
                            f"Key={spread_key} | Long={long_symbol} | Short={short_symbol} | "
                            f"Reason={retry_reason} | Cycles={retry_cycles}/{max_retry_cycles}"
                        ),
                        source="SPREAD_RETRY",
                    )
                    self.Log(
                        f"SPREAD_RETRY_MAX: Escalating to emergency sequential close | "
                        f"Long={long_symbol} Short={short_symbol} | "
                        f"Cycles={retry_cycles}/{max_retry_cycles} | Reason={retry_reason}"
                    )
                    try:
                        close_ok = self.portfolio_router.execute_spread_close(
                            spread=spread,
                            reason=f"SPREAD_RETRY_MAX:{retry_reason}",
                            is_emergency=True,
                        )
                    except Exception as e:
                        self._record_order_lifecycle_event(
                            status="SPREAD_EXIT_RETRY_MAX_EXCEPTION",
                            order_id=0,
                            symbol=str(long_symbol or ""),
                            quantity=int(spread.num_spreads or 0),
                            fill_price=0.0,
                            order_type=str(spread.spread_type or ""),
                            order_tag="SPREAD_CLOSE_RETRY",
                            trace_id="",
                            message=(
                                f"Key={spread_key} | Long={long_symbol} | Short={short_symbol} | "
                                f"Reason={retry_reason} | Exception={e}"
                            ),
                            source="SPREAD_RETRY",
                        )
                        self._schedule_spread_safe_lock_retry(
                            spread_key=spread_key,
                            long_symbol=long_symbol,
                            short_symbol=short_symbol,
                            retry_reason=retry_reason,
                            detail=f"exception={e}",
                        )
                        continue
                    if not close_ok:
                        self._record_order_lifecycle_event(
                            status="SPREAD_EXIT_RETRY_MAX_FAILED",
                            order_id=0,
                            symbol=str(long_symbol or ""),
                            quantity=int(spread.num_spreads or 0),
                            fill_price=0.0,
                            order_type=str(spread.spread_type or ""),
                            order_tag="SPREAD_CLOSE_RETRY",
                            trace_id="",
                            message=(
                                f"Key={spread_key} | Long={long_symbol} | Short={short_symbol} | "
                                f"Reason={retry_reason} | execute_spread_close=False"
                            ),
                            source="SPREAD_RETRY",
                        )
                        self._schedule_spread_safe_lock_retry(
                            spread_key=spread_key,
                            long_symbol=long_symbol,
                            short_symbol=short_symbol,
                            retry_reason=retry_reason,
                            detail="execute_spread_close_returned_false",
                        )
                        continue
                    self._record_order_lifecycle_event(
                        status="SPREAD_EXIT_RETRY_MAX_SUBMITTED",
                        order_id=0,
                        symbol=str(long_symbol or ""),
                        quantity=int(spread.num_spreads or 0),
                        fill_price=0.0,
                        order_type=str(spread.spread_type or ""),
                        order_tag="SPREAD_CLOSE_RETRY",
                        trace_id="",
                        message=(
                            f"Key={spread_key} | Long={long_symbol} | Short={short_symbol} | "
                            f"Reason={retry_reason} | EmergencySequential=True"
                        ),
                        source="SPREAD_RETRY",
                    )
                    self._spread_forced_close_retry.pop(spread_key, None)
                    self._spread_forced_close_reason.pop(spread_key, None)
                    self._spread_forced_close_cancel_counts.pop(spread_key, None)
                    self._spread_forced_close_retry_cycles.pop(spread_key, None)
                    self._spread_last_close_submit_at.pop(spread_key, None)
                    self._spread_close_first_cancel_at.pop(spread_key, None)
                    self._spread_close_intent_by_key.pop(spread_key, None)
                    continue
                self.Log(
                    f"SPREAD_RETRY: Re-submitting forced close | Long={long_symbol} "
                    f"Short={short_symbol} | Reason={retry_reason} | "
                    f"Cycle={retry_cycles}/{max_retry_cycles}"
                )
                self._diag_spread_exit_signal_count += 1
                self._diag_spread_exit_submit_count += 1
                if current_phase in {"COMBO_MARKET", "SEQ_MARKET"}:
                    self._advance_spread_close_intent_phase(spread_key, current_phase)
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=long_symbol,
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"SPREAD_CLOSE_RETRY:{retry_reason}",
                        requested_quantity=spread.num_spreads,
                        metadata={
                            "spread_close_short": True,
                            "spread_short_leg_symbol": short_symbol,
                            "spread_short_leg_quantity": spread.num_spreads,
                            "spread_key": self._build_spread_runtime_key(spread),
                            "spread_type": str(getattr(spread, "spread_type", "") or ""),
                            "is_credit_spread": bool(
                                getattr(spread, "net_debit", 0.0) < 0
                                or "CREDIT" in str(getattr(spread, "spread_type", "") or "").upper()
                            ),
                            "spread_entry_debit": float(
                                max(0.0, getattr(spread, "net_debit", 0.0))
                            ),
                            "spread_entry_credit": float(
                                max(0.0, -getattr(spread, "net_debit", 0.0))
                            ),
                            "spread_exit_code": "SPREAD_CLOSE_RETRY",
                            "spread_exit_reason": f"SPREAD_CLOSE_RETRY:{retry_reason}",
                            "spread_exit_urgency": intent_urgency,
                            "spread_close_phase": current_phase,
                            "spread_close_force_combo_market": bool(
                                current_phase in {"COMBO_MARKET", "SEQ_MARKET"}
                            ),
                        },
                    )
                )
                # Preserve original exit reason for fill attribution/urgency semantics.
                if not str(self._spread_last_exit_reason.get(spread_key, "") or "").strip():
                    self._record_spread_exit_reason(
                        spread_key, f"SPREAD_CLOSE_RETRY:{retry_reason}"
                    )
                # Backoff retries to reduce order spam while preserving persistence.
                if vass_fast_close:
                    self._spread_forced_close_retry[spread_key] = self.Time + timedelta(
                        seconds=vass_close_timeout_sec
                    )
                else:
                    retry_minutes = int(getattr(config, "SPREAD_CLOSE_RETRY_INTERVAL_MIN", 5))
                    self._spread_forced_close_retry[spread_key] = self.Time + timedelta(
                        minutes=retry_minutes
                    )
                self._spread_last_close_submit_at[spread_key] = self.Time
                continue

            current_dte = spread.long_leg.days_to_expiry
            try:
                if spread.long_leg.expiry:
                    from datetime import datetime

                    expiry_date = datetime.strptime(spread.long_leg.expiry, "%Y-%m-%d").date()
                    current_dte = (expiry_date - self.Time.date()).days
                    spread.long_leg.days_to_expiry = current_dte
                    spread.short_leg.days_to_expiry = current_dte
            except Exception as e:
                self.Log(f"SPREAD_EXIT_WARNING: Failed to parse spread expiry date: {e}")

            if (
                current_dte <= 0
                and self.Time.hour == config.ZERO_DTE_FORCE_EXIT_HOUR
                and self.Time.minute >= config.ZERO_DTE_FORCE_EXIT_MINUTE
            ):
                self.Log(
                    f"0DTE_FIREWALL: Forcing exit 30 min before close | "
                    f"Spread={spread.spread_type} | Time={self.Time.strftime('%H:%M')}"
                )
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=self._normalize_symbol_str(spread.long_leg.symbol),
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason="0DTE_TIME_DECAY",
                        requested_quantity=spread.num_spreads,
                        metadata={
                            "spread_close_short": True,
                            "spread_short_leg_symbol": self._normalize_symbol_str(
                                spread.short_leg.symbol
                            ),
                            "spread_short_leg_quantity": spread.num_spreads,
                            "spread_key": self._build_spread_runtime_key(spread),
                            "spread_type": spread_type,
                            "is_credit_spread": spread_is_credit,
                            "spread_entry_debit": spread_entry_debit,
                            "spread_entry_credit": spread_entry_credit,
                            "spread_exit_code": "0DTE_TIME_DECAY",
                            "spread_exit_reason": "0DTE_TIME_DECAY",
                        },
                    )
                )
                self._spread_last_close_submit_at[spread_key] = self.Time
                self._record_spread_exit_reason(spread_key, "SPREAD_EXIT: 0DTE_TIME_DECAY")
                self._diag_spread_exit_signal_count += 1
                self._diag_spread_exit_submit_count += 1
                continue

            long_leg_price = None
            short_leg_price = None
            try:
                use_exec_marks = bool(getattr(config, "SPREAD_EXIT_USE_EXECUTABLE_MARKS", True))
                long_sec = self.Securities.get(spread.long_leg.symbol)
                if long_sec:
                    if use_exec_marks and long_sec.BidPrice > 0:
                        long_leg_price = long_sec.BidPrice
                    elif long_sec.BidPrice > 0 and long_sec.AskPrice > 0:
                        long_leg_price = (long_sec.BidPrice + long_sec.AskPrice) / 2
                    elif long_sec.Price > 0:
                        long_leg_price = long_sec.Price
                short_sec = self.Securities.get(spread.short_leg.symbol)
                if short_sec:
                    if use_exec_marks and short_sec.AskPrice > 0:
                        short_leg_price = short_sec.AskPrice
                    elif short_sec.BidPrice > 0 and short_sec.AskPrice > 0:
                        short_leg_price = (short_sec.BidPrice + short_sec.AskPrice) / 2
                    elif short_sec.Price > 0:
                        short_leg_price = short_sec.Price
            except Exception:
                pass

            if long_leg_price is None or short_leg_price is None:
                cached = self._spread_exit_mark_cache.get(spread_key, {})
                if long_leg_price is None:
                    long_leg_price = cached.get("long_leg_price")
                if short_leg_price is None:
                    short_leg_price = cached.get("short_leg_price")
                if long_leg_price is not None and short_leg_price is not None:
                    self.Log(
                        f"SPREAD_EXIT_MARK_FALLBACK: Using cached leg marks | "
                        f"Type={spread.spread_type} | DTE={current_dte}"
                    )
                else:
                    # Time-based exits must still fire even when option quotes are unavailable.
                    # This avoids holding stale spreads simply because leg marks are missing.
                    time_exit_reason = None
                    try:
                        max_hold_days = int(getattr(config, "VASS_DEBIT_MAX_HOLD_DAYS", 0))
                        low_vix_days = int(
                            getattr(config, "VASS_DEBIT_MAX_HOLD_DAYS_LOW_VIX", max_hold_days)
                        )
                        low_vix_threshold = float(
                            getattr(config, "VASS_DEBIT_LOW_VIX_THRESHOLD", 16.0)
                        )
                        if (
                            self._current_vix is not None
                            and low_vix_days > 0
                            and float(self._current_vix) < low_vix_threshold
                        ):
                            max_hold_days = (
                                min(max_hold_days, low_vix_days)
                                if max_hold_days > 0
                                else low_vix_days
                            )
                        if max_hold_days > 0:
                            from datetime import datetime as _dt

                            entry_dt = _dt.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                            held_days = (self.Time.date() - entry_dt.date()).days
                            if held_days >= max_hold_days:
                                time_exit_reason = (
                                    f"SPREAD_TIME_STOP_NO_QUOTE ({held_days}d >= {max_hold_days}d)"
                                )
                    except Exception:
                        pass
                    if time_exit_reason is None and current_dte <= int(config.SPREAD_DTE_EXIT):
                        time_exit_reason = f"DTE_EXIT_NO_QUOTE ({current_dte} DTE <= {int(config.SPREAD_DTE_EXIT)})"

                    if time_exit_reason is not None:
                        self.Log(
                            f"SPREAD_EXIT_NO_QUOTE_TIME_BASED: {time_exit_reason} | "
                            f"Type={spread.spread_type}"
                        )
                        self.portfolio_router.receive_signal(
                            TargetWeight(
                                symbol=long_symbol,
                                target_weight=0.0,
                                source="OPT",
                                urgency=Urgency.IMMEDIATE,
                                reason=f"SPREAD_EXIT: {time_exit_reason}",
                                requested_quantity=spread.num_spreads,
                                metadata={
                                    "spread_close_short": True,
                                    "spread_short_leg_symbol": short_symbol,
                                    "spread_short_leg_quantity": spread.num_spreads,
                                    "spread_key": self._build_spread_runtime_key(spread),
                                    "spread_type": spread_type,
                                    "is_credit_spread": spread_is_credit,
                                    "spread_entry_debit": spread_entry_debit,
                                    "spread_entry_credit": spread_entry_credit,
                                    "exit_type": "TIME_BASED_NO_QUOTE",
                                    "spread_exit_code": "TIME_BASED_NO_QUOTE",
                                    "spread_exit_reason": str(time_exit_reason),
                                },
                            )
                        )
                        self._spread_last_close_submit_at[spread_key] = self.Time
                        self._record_spread_exit_reason(
                            spread_key, f"SPREAD_EXIT: {time_exit_reason}"
                        )
                        self._diag_spread_exit_signal_count += 1
                        self._diag_spread_exit_submit_count += 1
                        continue

                    self.Log(
                        f"SPREAD_EXIT_SKIPPED_NO_QUOTE: Missing leg marks | "
                        f"Type={spread.spread_type} | DTE={current_dte} | "
                        f"LongPx={'NA' if long_leg_price is None else f'{long_leg_price:.2f}'} | "
                        f"ShortPx={'NA' if short_leg_price is None else f'{short_leg_price:.2f}'}"
                    )
                    continue

            self._spread_exit_mark_cache[spread_key] = {
                "long_leg_price": float(long_leg_price),
                "short_leg_price": float(short_leg_price),
                "updated_at": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
            }

            min_hold_minutes = int(getattr(config, "SPREAD_MIN_HOLD_MINUTES", 0))
            hold_block_active = False
            if min_hold_minutes > 0:
                try:
                    from datetime import datetime as _dt

                    entry_dt = _dt.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                    live_minutes = (self.Time - entry_dt).total_seconds() / 60.0
                    mandatory_dte = int(getattr(config, "SPREAD_FORCE_CLOSE_DTE", 1))
                    hold_block_active = (
                        0 <= live_minutes < min_hold_minutes and current_dte > mandatory_dte
                    )
                except Exception:
                    hold_block_active = False

            gamma_pin_signals = self.options_engine.check_gamma_pin_exit(
                current_price=underlying_price,
                current_dte=current_dte,
                spread_override=spread,
            )
            if gamma_pin_signals:
                for signal in gamma_pin_signals:
                    self._normalize_spread_close_quantities(signal)
                    self._record_spread_exit_reason(spread_key, signal.reason)
                    self.portfolio_router.receive_signal(signal)
                    self._spread_last_close_submit_at[spread_key] = self.Time
                self._diag_spread_exit_signal_count += len(gamma_pin_signals)
                self._diag_spread_exit_submit_count += len(gamma_pin_signals)
                continue

            assignment_risk_signals = self.options_engine.check_assignment_risk_exit(
                underlying_price=underlying_price,
                current_dte=current_dte,
                current_hour=current_hour,
                current_minute=current_minute,
                available_margin=available_margin,
                spread_override=spread,
            )
            if assignment_risk_signals:
                for signal in assignment_risk_signals:
                    self._normalize_spread_close_quantities(signal)
                    self._record_spread_exit_reason(spread_key, signal.reason)
                    self.portfolio_router.receive_signal(signal)
                    self._spread_last_close_submit_at[spread_key] = self.Time
                self._diag_spread_exit_signal_count += len(assignment_risk_signals)
                self._diag_spread_exit_submit_count += len(assignment_risk_signals)
                continue

            # V6.22: Transition exit priority - close wrong-way bullish spreads first in STRESS.
            # Apply this only when anti-churn minimum-hold window has elapsed.
            if not hold_block_active and bool(
                getattr(config, "SPREAD_OVERLAY_STRESS_EXIT_ENABLED", False)
            ):
                overlay_state = self.options_engine.get_regime_overlay_state(
                    vix_current=self._current_vix, regime_score=regime_score
                )
                spread_type_upper = str(spread.spread_type).upper()
                is_bullish_spread = spread_type_upper in {
                    "BULL_CALL",
                    "BULL_CALL_DEBIT",
                    "BULL_PUT_CREDIT",
                }
                if overlay_state == "STRESS" and is_bullish_spread:
                    self.Log(
                        f"SPREAD_OVERLAY_EXIT: Forcing close in STRESS | "
                        f"Type={spread.spread_type} | VIX={self._current_vix:.1f} | Regime={regime_score:.0f}"
                    )
                    self.portfolio_router.receive_signal(
                        TargetWeight(
                            symbol=long_symbol,
                            target_weight=0.0,
                            source="OPT",
                            urgency=Urgency.IMMEDIATE,
                            reason="SPREAD_EXIT: OVERLAY_STRESS_EXIT",
                            requested_quantity=spread.num_spreads,
                            metadata={
                                "spread_close_short": True,
                                "spread_short_leg_symbol": short_symbol,
                                "spread_short_leg_quantity": spread.num_spreads,
                                "spread_key": self._build_spread_runtime_key(spread),
                                "spread_type": spread_type,
                                "is_credit_spread": spread_is_credit,
                                "spread_entry_debit": spread_entry_debit,
                                "spread_entry_credit": spread_entry_credit,
                                "exit_type": "OVERLAY_STRESS_EXIT",
                                "spread_exit_code": "OVERLAY_STRESS_EXIT",
                                "spread_exit_reason": "OVERLAY_STRESS_EXIT",
                            },
                        )
                    )
                    self._spread_last_close_submit_at[spread_key] = self.Time
                    self._record_spread_exit_reason(spread_key, "SPREAD_EXIT: OVERLAY_STRESS_EXIT")
                    self._diag_spread_exit_signal_count += 1
                    self._diag_spread_exit_submit_count += 1
                    continue

            exit_signals = self.options_engine.check_spread_exit_signals(
                long_leg_price=long_leg_price,
                short_leg_price=short_leg_price,
                regime_score=regime_score,
                current_dte=current_dte,
                vix_current=self._current_vix,
                spread_override=spread,
                underlying_price=underlying_price,
            )
            if exit_signals:
                for signal in exit_signals:
                    self._normalize_spread_close_quantities(signal)
                    self._record_spread_exit_reason(spread_key, signal.reason)
                    self.portfolio_router.receive_signal(signal)
                    self._spread_last_close_submit_at[spread_key] = self.Time
                self._diag_spread_exit_signal_count += len(exit_signals)
                self._diag_spread_exit_submit_count += len(exit_signals)

    def _on_vix_spike_check(self) -> None:
        """
        V2.1.1: Layer 1 VIX spike detection (every 5 minutes).

        V2.3.4: Uses UVXY as intraday proxy since CBOE VIX only supports Daily.
        Checks for sudden VIX spikes (>3% in 5 minutes via UVXY).
        Sets spike alert cooldown if triggered.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # V2.3.4: Use UVXY for intraday spike detection
        uvxy_current = self.Securities[self.uvxy].Price
        if self._uvxy_at_open > 0:
            uvxy_change_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open * 100
            # Derive VIX proxy from UVXY change
            vix_intraday_proxy = self._vix_at_open * (1 + uvxy_change_pct / 150)
        else:
            vix_intraday_proxy = self._current_vix

        # Check for spike using intraday proxy
        spike_alert = self.options_engine.check_micro_spike_alert(
            vix_current=vix_intraday_proxy,
            vix_5min_ago=self._vix_5min_ago,
            current_time=str(self.Time),
        )

        if spike_alert:
            # Throttle VIX spike logs: 1 per LOG_THROTTLE_MINUTES OR if move > threshold
            vix_move = abs(vix_intraday_proxy - self._vix_5min_ago)
            should_log = (
                not hasattr(self, "_last_vix_spike_log")
                or self._last_vix_spike_log is None
                or (self.Time - self._last_vix_spike_log).total_seconds() / 60
                > config.LOG_THROTTLE_MINUTES
                or vix_move >= config.LOG_VIX_SPIKE_MIN_MOVE
            )
            if should_log:
                self.Log(
                    f"VIX_SPIKE: {self._vix_5min_ago:.1f} -> {vix_intraday_proxy:.1f} (via UVXY)"
                )
                self._last_vix_spike_log = self.Time

        # Update 5-min ago value for next check (using proxy)
        self._vix_5min_ago = vix_intraday_proxy

    def _on_micro_regime_update(self) -> None:
        """
        V2.1.1: Layer 2 & 4 - Direction + Regime update (every 15 minutes).

        Updates the Micro Regime Engine with current market data.
        V2.3.4: Uses UVXY as intraday VIX proxy since CBOE VIX only supports Daily.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # V2.3.4: Use UVXY % change as intraday VIX direction proxy
        # UVXY tracks ~1.5x daily VIX moves, so direction is reliable
        uvxy_current = self.Securities[self.uvxy].Price
        if self._uvxy_at_open > 0:
            uvxy_change_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open * 100
            # Derive synthetic "intraday VIX" from UVXY change applied to VIX open
            # If UVXY is up 3%, VIX is approximately up 2% (UVXY is ~1.5x)
            vix_intraday_proxy = self._vix_at_open * (1 + uvxy_change_pct / 150)
        else:
            uvxy_change_pct = 0.0
            vix_intraday_proxy = self._current_vix

        # Get current QQQ price
        qqq_current = self.Securities[self.qqq].Price

        # V2.11 (Pitfall #7): Separate VIX Level from VIX Direction
        # - Level: Use CBOE VIX (daily) - prevents false level spikes from UVXY contango
        # - Direction: Use UVXY proxy (vix_intraday_proxy) - UVXY tracks direction reliably
        vix_level_cboe = self._get_vix_level()  # CBOE VIX for level classification

        # Update micro regime engine with intraday VIX proxy
        # V2.5: Pass macro_regime_score for Grind-Up Override
        vix_open_for_micro = self._vix_at_open
        shock_memory_pct = self._get_premarket_shock_memory_pct()
        if shock_memory_pct > 0:
            anchor = min(max(getattr(config, "MICRO_SHOCK_MEMORY_ANCHOR", 0.60), 0.0), 1.0)
            memory_scale = 1.0 + shock_memory_pct * anchor
            if memory_scale > 1.0:
                vix_open_for_micro = self._vix_at_open / memory_scale

        state = self.options_engine.update_micro_regime_state(
            vix_current=vix_intraday_proxy,  # Use UVXY-derived for direction
            vix_open=vix_open_for_micro,
            qqq_current=qqq_current,
            qqq_open=self._qqq_at_open,
            current_time=str(self.Time),
            macro_regime_score=self._last_regime_score,
            vix_level_override=vix_level_cboe,  # V2.11: Use CBOE VIX for level
        )

        # V8: Backtest log-budget guard for high-frequency MICRO_UPDATE diagnostics.
        micro_dir = state.recommended_direction.value if state.recommended_direction else "NONE"
        # V10: Add VIX tier for telemetry
        vix_tier = "LOW" if vix_level_cboe < 18 else "MED" if vix_level_cboe < 25 else "HIGH"
        micro_msg = (
            f"MICRO_UPDATE: VIX_level={vix_level_cboe:.1f}(CBOE) VIX_tier={vix_tier} VIX_dir_proxy={vix_intraday_proxy:.2f} (UVXY {uvxy_change_pct:+.1f}%) | "
            f"Regime={state.micro_regime.value} | Dir={micro_dir} | "
            f"ShockMem={shock_memory_pct:+.1%}"
        )
        is_live = bool(hasattr(self, "LiveMode") and self.LiveMode)
        if is_live:
            self.Log(micro_msg)
        elif bool(getattr(config, "MICRO_UPDATE_LOG_BACKTEST_ENABLED", True)):
            signature = (
                state.micro_regime.value,
                micro_dir,
                round(float(vix_level_cboe), 1),
                round(float(vix_intraday_proxy), 2),
                round(float(shock_memory_pct), 3),
            )
            on_change_only = bool(getattr(config, "MICRO_UPDATE_LOG_ON_CHANGE_ONLY", True))
            min_minutes = int(getattr(config, "MICRO_UPDATE_LOG_MINUTES", 60))
            should_log = True
            if on_change_only:
                changed = signature != self._last_micro_update_log_signature
                due = (
                    self._last_micro_update_log_time is None
                    or (self.Time - self._last_micro_update_log_time).total_seconds() / 60.0
                    >= min_minutes
                )
                should_log = changed or due
            if should_log:
                self.Log(micro_msg)
                self._last_micro_update_log_signature = signature
                self._last_micro_update_log_time = self.Time

    def _on_friday_firewall(self) -> None:
        """
        V2.4.1: Friday Firewall - close swing options before weekend.

        V2.9: Holiday-aware (Bug #3 fix). Runs on:
        - Friday (normal weeks)
        - Thursday (when Friday is a holiday like Good Friday)

        Rules:
        1. VIX > 25: Close ALL swing options (high volatility weekend risk)
        2. Fresh trade (opened today) AND VIX >= 15: Close it (gambling protection)
        3. Fresh trade AND VIX < 15: Keep it (calm market exception)
        4. Older trades: Keep them (already survived initial risk)
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        # V2.9: Holiday-aware check - only run on expiration firewall days
        if not is_expiration_firewall_day(self):
            return

        # Get current VIX
        vix_current = self._current_vix

        # Check if we have any swing positions
        swing_signals = []
        for spread in self.options_engine.get_spread_positions():
            signals = self.options_engine.check_friday_firewall_exit(
                current_vix=vix_current,
                current_date=str(self.Time.date()),
                vix_close_all_threshold=config.FRIDAY_FIREWALL_VIX_CLOSE_ALL,
                vix_keep_fresh_threshold=config.FRIDAY_FIREWALL_VIX_KEEP_FRESH,
                spread_override=spread,
            )
            if signals:
                swing_signals.extend(signals)

        itm_signals = self._collect_itm_weekend_firewall_signals(current_vix=vix_current)
        firewall_signals = swing_signals + itm_signals

        if firewall_signals:
            for signal in firewall_signals:
                self.Log(f"FRIDAY_FIREWALL: {signal.reason} | VIX={vix_current:.1f}")
                self._normalize_spread_close_quantities(signal)
                self.portfolio_router.receive_signal(signal)
                try:
                    md = signal.metadata or {}
                    spread_key = str(md.get("spread_key", "") or "").strip()
                    if spread_key:
                        self._spread_last_close_submit_at[spread_key] = self.Time
                except Exception:
                    pass

            # Process immediately
            self._process_immediate_signals()
        else:
            self.Log(f"FRIDAY_FIREWALL: No action needed | VIX={vix_current:.1f}")

        # V9.2: Guarded Friday sweep (single pass/day).
        self._reconcile_spread_state()

    def _get_vix_engine_proxy(self) -> float:
        """
        V2.4.1: Get UVXY-derived VIX proxy for intraday direction.

        Calculates synthetic intraday VIX from UVXY % change applied to VIX open.
        This is needed because CBOE VIX only supports Daily resolution in QC.
        UVXY tracks ~1.5x daily VIX moves, so we derive VIX from UVXY change.

        Returns:
            Estimated intraday VIX value derived from UVXY price change.
        """
        uvxy_current = self.Securities[self.uvxy].Price
        if self._uvxy_at_open > 0:
            uvxy_change_pct = (uvxy_current - self._uvxy_at_open) / self._uvxy_at_open * 100
            # Derive synthetic "intraday VIX" from UVXY change applied to VIX open
            # If UVXY is up 3%, VIX is approximately up 2% (UVXY is ~1.5x)
            return self._vix_at_open * (1 + uvxy_change_pct / 150)
        return self._current_vix

    def _get_vix_level(self) -> float:
        """
        V2.11 (Pitfall #7): Get VIX LEVEL from CBOE VIX (daily).

        Uses the actual CBOE VIX for level classification (Low/Medium/High).
        Do NOT derive synthetic VIX level from UVXY - UVXY can gap up while
        VIX is stable due to contango, causing false level spikes.

        V10.8: when CBOE VIX feed appears stale for multiple sessions,
        blend in intraday proxy to avoid frozen-level behavior.

        Returns:
            VIX level used for regime classification.
        """
        stale_threshold = int(getattr(config, "VIX_STALE_MAX_SESSIONS", 3))
        stale_fallback_enabled = bool(getattr(config, "VIX_STALE_LEVEL_FALLBACK_ENABLED", True))
        last_update = getattr(self, "_last_vix_update_date", None)

        if (
            stale_fallback_enabled
            and stale_threshold > 0
            and last_update is not None
            and self.Time.date() is not None
        ):
            days_stale = (self.Time.date() - last_update).days
            if days_stale >= stale_threshold:
                blend = float(getattr(config, "VIX_STALE_LEVEL_FALLBACK_BLEND", 0.35))
                blend = max(0.0, min(1.0, blend))
                proxy = float(self._get_vix_engine_proxy())
                fallback_level = float(self._current_vix) * (1.0 - blend) + proxy * blend

                if getattr(self, "_last_vix_stale_log_date", None) != self.Time.date():
                    self.Log(
                        "VIX_STALE_LEVEL_FALLBACK: "
                        f"Current={self._current_vix:.2f} | Proxy={proxy:.2f} | "
                        f"Blend={blend:.0%} | DaysStale={days_stale}"
                    )
                    self._last_vix_stale_log_date = self.Time.date()
                return fallback_level

        return self._current_vix  # Use daily CBOE VIX, NOT UVXY-derived proxy

    def _should_scan_engine_cycle(self) -> bool:
        """
        V2.4.1: Check if enough time passed since last intraday scan.

        Implements 15-minute throttle to reduce intraday scanning from
        95 scans/hour (every minute) to 4 scans/hour (every 15 minutes).

        Returns:
            True if throttle allows scanning, False otherwise.
        """
        if self._last_intraday_scan is None:
            self._last_intraday_scan = self.Time
            return True

        elapsed_seconds = (self.Time - self._last_intraday_scan).total_seconds()
        if elapsed_seconds >= 900:  # 15 minutes = 900 seconds
            self._last_intraday_scan = self.Time
            return True

        return False

    def _can_trade_options_settlement_aware(self) -> bool:
        """
        V2.9: Check if options trading is allowed based on settlement status.

        Returns:
            False during the first hour after any post-gap market open
            if there is unsettled cash. True otherwise.
        """
        if not config.SETTLEMENT_AWARE_TRADING:
            return True

        # Check if we're in settlement cooldown
        if self._settlement_cooldown_until is not None:
            if self.Time < self._settlement_cooldown_until:
                unsettled = self._get_unsettled_cash()
                if unsettled > 0:
                    # Only log once per minute to avoid spam
                    if self.Time.minute != getattr(self, "_last_settlement_log_minute", -1):
                        self.Log(f"SETTLEMENT: Cooldown active | UnsettledCash=${unsettled:,.0f}")
                        self._last_settlement_log_minute = self.Time.minute
                    return False
            else:
                # V2.13 Fix #9: Log AAP keyword when settlement gate opens
                self.Log("SETTLEMENT_GATE_OPEN: Trading resumed after settlement cooldown")
                self._settlement_cooldown_until = None

        return True
