from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from AlgorithmImports import Slice

import config
from models.enums import IntradayStrategy, OptionDirection, Urgency
from models.target_weight import TargetWeight


class MainRegimeMixin:
    def _evaluate_base_regime_candidate(self, effective_score: float) -> str:
        """State-machine candidate for macro base regime with hysteresis thresholds."""
        bull_enter = float(getattr(config, "REGIME_BASE_BULL_ENTER", 57.0))
        bull_exit = float(getattr(config, "REGIME_BASE_BULL_EXIT", 53.0))
        bear_enter = float(getattr(config, "REGIME_BASE_BEAR_ENTER", 43.0))
        bear_exit = float(getattr(config, "REGIME_BASE_BEAR_EXIT", 47.0))
        current = str(getattr(self, "_regime_base_state", "NEUTRAL")).upper()

        if current == "BULLISH":
            if effective_score < bull_exit:
                return "BEARISH" if effective_score <= bear_enter else "NEUTRAL"
            return "BULLISH"
        if current == "BEARISH":
            if effective_score > bear_exit:
                return "BULLISH" if effective_score >= bull_enter else "NEUTRAL"
            return "BEARISH"
        if effective_score >= bull_enter:
            return "BULLISH"
        if effective_score <= bear_enter:
            return "BEARISH"
        return "NEUTRAL"

    def _advance_detector_state(
        self,
        current_state: str,
        candidate_state: str,
        candidate_streak: int,
        desired_state: str,
        dwell_required: int,
    ) -> Tuple[str, str, int]:
        """Advance state-machine with dwell bars."""
        desired = str(desired_state or "").upper() or current_state
        current = str(current_state or "").upper() or "NEUTRAL"
        candidate = str(candidate_state or current).upper()
        dwell = max(int(dwell_required), 1)

        if desired == current:
            return current, desired, 0
        if desired == candidate:
            streak = int(candidate_streak) + 1
        else:
            candidate = desired
            streak = 1
        if streak >= dwell:
            return desired, desired, 0
        return current, candidate, streak

    def _update_regime_detector_state(
        self,
        effective: float,
        detector_score: float,
        eod_delta: float,
        momentum_roc: float,
        vix_5d_change: float,
        sample_seq: int,
    ) -> Dict[str, Any]:
        """Compute transition raw signals and advance base/overlay detector state."""
        recovery_delta_min = float(getattr(config, "REGIME_TRANSITION_RECOVERY_DELTA_MIN", 2.0))
        recovery_detector_delta_min = float(
            getattr(config, "REGIME_TRANSITION_RECOVERY_DETECTOR_DELTA_MIN", 0.8)
        )
        recovery_eod_agreement_min = float(
            getattr(config, "REGIME_TRANSITION_RECOVERY_EOD_AGREEMENT_MIN", 0.15)
        )
        recovery_mom_min = float(getattr(config, "REGIME_TRANSITION_RECOVERY_MOMENTUM_MIN", 0.015))
        recovery_vix_5d_max = float(getattr(config, "REGIME_TRANSITION_RECOVERY_VIX_5D_MAX", 0.05))
        deterioration_delta_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_DELTA_MAX", -2.0)
        )
        deterioration_detector_delta_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_DETECTOR_DELTA_MAX", -0.8)
        )
        deterioration_eod_agreement_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_EOD_AGREEMENT_MAX", -0.15)
        )
        deterioration_mom_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_MOMENTUM_MAX", -0.015)
        )
        deterioration_vix_5d_min = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_VIX_5D_MIN", 0.10)
        )
        deterioration_fast_eod_delta_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_FAST_EOD_DELTA_MAX", -1.8)
        )
        deterioration_fast_mom_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_FAST_MOMENTUM_MAX", -0.020)
        )
        deterioration_fast_vix_5d_min = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_FAST_VIX_5D_MIN", 0.12)
        )
        deterioration_fast_detector_delta_max = float(
            getattr(config, "REGIME_TRANSITION_DETERIORATION_FAST_DETECTOR_DELTA_MAX", -0.05)
        )
        ambiguous_low = float(getattr(config, "REGIME_TRANSITION_AMBIGUOUS_LOW", 47.0))
        ambiguous_high = float(getattr(config, "REGIME_TRANSITION_AMBIGUOUS_HIGH", 55.0))
        ambiguous_delta_max = float(getattr(config, "REGIME_TRANSITION_AMBIGUOUS_DELTA_MAX", 1.5))
        ambiguous_detector_delta_max = float(
            getattr(config, "REGIME_TRANSITION_AMBIGUOUS_DETECTOR_DELTA_MAX", 0.8)
        )
        ambiguous_max_bars = int(getattr(config, "REGIME_TRANSITION_AMBIGUOUS_MAX_BARS", 6))

        now_key = f"SEQ:{int(sample_seq)}"
        is_new_sample = self._regime_detector_last_update_key != now_key
        if is_new_sample:
            prev_score = getattr(self, "_regime_detector_prev_score", None)
            if prev_score is None:
                detector_delta = 0.0
            else:
                detector_delta = float(detector_score - float(prev_score))
            self._regime_detector_prev_score = float(detector_score)
        else:
            detector_delta = float(self._regime_detector_last_raw.get("detector_delta", 0.0) or 0.0)

        recovery_by_detector = detector_delta >= recovery_detector_delta_min
        recovery_by_eod = (
            eod_delta >= recovery_delta_min and detector_delta >= recovery_eod_agreement_min
        )
        raw_recovery = (
            (recovery_by_detector or recovery_by_eod)
            and momentum_roc >= recovery_mom_min
            and vix_5d_change <= recovery_vix_5d_max
        )
        deterioration_by_detector = detector_delta <= deterioration_detector_delta_max
        deterioration_by_eod = (
            eod_delta <= deterioration_delta_max
            and detector_delta <= deterioration_eod_agreement_max
        )
        deterioration_by_fast = (
            eod_delta <= deterioration_fast_eod_delta_max
            and momentum_roc <= deterioration_fast_mom_max
            and vix_5d_change >= deterioration_fast_vix_5d_min
            and detector_delta <= deterioration_fast_detector_delta_max
        )
        raw_deterioration = (
            (deterioration_by_detector or deterioration_by_eod)
            and momentum_roc <= deterioration_mom_max
            and vix_5d_change >= deterioration_vix_5d_min
        ) or deterioration_by_fast
        ambiguous_by_detector = abs(detector_delta) <= ambiguous_detector_delta_max
        ambiguous_by_eod = abs(eod_delta) <= ambiguous_delta_max
        raw_ambiguous = (
            ambiguous_low <= detector_score <= ambiguous_high
            and ambiguous_by_detector
            and ambiguous_by_eod
            and not raw_recovery
            and not raw_deterioration
        )
        ambiguous_timed_out = False
        if is_new_sample:
            if raw_recovery:
                if recovery_by_detector and recovery_by_eod:
                    self._inc_transition_path_counter("RECOVERY_BOTH")
                elif recovery_by_detector:
                    self._inc_transition_path_counter("RECOVERY_DETECTOR")
                else:
                    self._inc_transition_path_counter("RECOVERY_EOD")
            elif raw_deterioration:
                if deterioration_by_fast and not (
                    deterioration_by_detector or deterioration_by_eod
                ):
                    self._inc_transition_path_counter("DETERIORATION_FAST")
                elif deterioration_by_detector and deterioration_by_eod:
                    self._inc_transition_path_counter("DETERIORATION_BOTH")
                elif deterioration_by_detector:
                    self._inc_transition_path_counter("DETERIORATION_DETECTOR")
                else:
                    self._inc_transition_path_counter("DETERIORATION_EOD")
            elif raw_ambiguous:
                self._inc_transition_path_counter("AMBIGUOUS_BOTH")
        overlay_candidate = "STABLE"
        if raw_recovery:
            overlay_candidate = "RECOVERY"
        elif raw_deterioration:
            overlay_candidate = "DETERIORATION"
        elif raw_ambiguous:
            overlay_candidate = "AMBIGUOUS"

        if is_new_sample:
            base_candidate = self._evaluate_base_regime_candidate(detector_score)
            prev_base_state = str(getattr(self, "_regime_base_state", "NEUTRAL")).upper()
            prev_overlay_state = str(getattr(self, "_regime_overlay_state", "STABLE")).upper()
            overlay_dwell = int(getattr(config, "REGIME_OVERLAY_STATE_DWELL_BARS", 2))
            if overlay_candidate == "RECOVERY":
                overlay_dwell = int(
                    getattr(config, "REGIME_OVERLAY_DWELL_RECOVERY_BARS", overlay_dwell)
                )
            elif overlay_candidate == "DETERIORATION":
                overlay_dwell = int(
                    getattr(config, "REGIME_OVERLAY_DWELL_DETERIORATION_BARS", overlay_dwell)
                )
            elif overlay_candidate == "AMBIGUOUS":
                overlay_dwell = int(
                    getattr(config, "REGIME_OVERLAY_DWELL_AMBIGUOUS_BARS", overlay_dwell)
                )
            if prev_overlay_state == "DETERIORATION" and overlay_candidate != "DETERIORATION":
                overlay_dwell = max(
                    overlay_dwell,
                    int(getattr(config, "REGIME_OVERLAY_EXIT_DETERIORATION_DWELL_BARS", 3)),
                )
            if bool(getattr(config, "REGIME_BASE_STATE_MACHINE_ENABLED", True)):
                (
                    self._regime_base_state,
                    self._regime_base_candidate_state,
                    self._regime_base_candidate_streak,
                ) = self._advance_detector_state(
                    current_state=self._regime_base_state,
                    candidate_state=self._regime_base_candidate_state,
                    candidate_streak=self._regime_base_candidate_streak,
                    desired_state=base_candidate,
                    dwell_required=int(getattr(config, "REGIME_BASE_STATE_DWELL_BARS", 2)),
                )
                (
                    self._regime_overlay_state,
                    self._regime_overlay_candidate_state,
                    self._regime_overlay_candidate_streak,
                ) = self._advance_detector_state(
                    current_state=self._regime_overlay_state,
                    candidate_state=self._regime_overlay_candidate_state,
                    candidate_streak=self._regime_overlay_candidate_streak,
                    desired_state=overlay_candidate,
                    dwell_required=overlay_dwell,
                )
            else:
                self._regime_base_state = base_candidate
                self._regime_overlay_state = overlay_candidate
                self._regime_base_candidate_state = base_candidate
                self._regime_overlay_candidate_state = overlay_candidate
                self._regime_base_candidate_streak = 0
                self._regime_overlay_candidate_streak = 0
            current_seq = int(sample_seq)
            if str(self._regime_base_state).upper() != prev_base_state:
                self._regime_base_state_enter_seq = current_seq
            elif int(getattr(self, "_regime_base_state_enter_seq", 0)) <= 0:
                self._regime_base_state_enter_seq = current_seq
            if str(self._regime_overlay_state).upper() != prev_overlay_state:
                self._regime_overlay_state_enter_seq = current_seq
            elif int(getattr(self, "_regime_overlay_state_enter_seq", 0)) <= 0:
                self._regime_overlay_state_enter_seq = current_seq
            if self._regime_overlay_state == "AMBIGUOUS":
                self._regime_overlay_ambiguous_bars = (
                    int(getattr(self, "_regime_overlay_ambiguous_bars", 0)) + 1
                )
            else:
                self._regime_overlay_ambiguous_bars = 0
            if (
                ambiguous_max_bars > 0
                and self._regime_overlay_state == "AMBIGUOUS"
                and self._regime_overlay_ambiguous_bars >= ambiguous_max_bars
            ):
                self._regime_overlay_state = "STABLE"
                self._regime_overlay_candidate_state = "STABLE"
                self._regime_overlay_candidate_streak = 0
                self._regime_overlay_ambiguous_bars = 0
                ambiguous_timed_out = True
                self._inc_transition_path_counter("AMBIGUOUS_TIMEOUT")
            self._regime_detector_last_update_key = now_key

        self._regime_detector_last_raw = {
            "raw_recovery": bool(raw_recovery),
            "raw_deterioration": bool(raw_deterioration),
            "raw_ambiguous": bool(raw_ambiguous),
            "ambiguous_timed_out": bool(ambiguous_timed_out),
            "overlay_candidate": overlay_candidate,
            "base_candidate": self._evaluate_base_regime_candidate(detector_score),
            "detector_score": float(detector_score),
            "detector_delta": float(detector_delta),
            "eod_delta": float(eod_delta),
            "recovery_by_detector": bool(recovery_by_detector),
            "recovery_by_eod": bool(recovery_by_eod),
            "deterioration_by_detector": bool(deterioration_by_detector),
            "deterioration_by_eod": bool(deterioration_by_eod),
            "deterioration_by_fast": bool(deterioration_by_fast),
            "recovery_eod_agreement_min": float(recovery_eod_agreement_min),
            "deterioration_eod_agreement_max": float(deterioration_eod_agreement_max),
            "ambiguous_by_detector": bool(ambiguous_by_detector),
            "ambiguous_by_eod": bool(ambiguous_by_eod),
            # Backward-compatible alias expected by existing guards/log readers.
            "delta": float(detector_delta),
            "sample_seq": int(sample_seq),
        }
        return dict(self._regime_detector_last_raw)

    def _get_regime_transition_context(self) -> Dict[str, Any]:
        """Build macro detector context with base regime + transition overlay state-machine."""
        effective = float(self._get_effective_regime_score_for_options())
        eod_score = float(getattr(self, "_last_regime_score", effective) or effective)
        intraday_score_raw = getattr(self, "_intraday_regime_score", None)
        intraday_score = (
            float(intraday_score_raw) if intraday_score_raw is not None else float(eod_score)
        )
        eod_delta = float(intraday_score - eod_score)
        detector_score = float(intraday_score if intraday_score_raw is not None else effective)
        momentum_roc = getattr(self, "_intraday_regime_momentum_roc", None)
        if momentum_roc is None:
            momentum_roc = getattr(self, "_last_regime_momentum_roc", 0.0)
        vix_5d_change = getattr(self, "_intraday_regime_vix_5d_change", None)
        if vix_5d_change is None:
            vix_5d_change = getattr(self, "_last_regime_vix_5d_change", 0.0)
        momentum_roc = float(momentum_roc or 0.0)
        vix_5d_change = float(vix_5d_change or 0.0)

        raw = self._update_regime_detector_state(
            effective=effective,
            detector_score=detector_score,
            eod_delta=eod_delta,
            momentum_roc=momentum_roc,
            vix_5d_change=vix_5d_change,
            sample_seq=int(getattr(self, "_regime_detector_sample_seq", 0)),
        )
        detector_delta = float(raw.get("detector_delta", raw.get("delta", 0.0)) or 0.0)
        transition_overlay = str(getattr(self, "_regime_overlay_state", "STABLE")).upper()
        strong_recovery = transition_overlay == "RECOVERY"
        strong_deterioration = transition_overlay == "DETERIORATION"
        ambiguous = transition_overlay == "AMBIGUOUS"
        sample_seq = int(raw.get("sample_seq", getattr(self, "_regime_detector_sample_seq", 0)))
        overlay_enter_seq = int(
            getattr(self, "_regime_overlay_state_enter_seq", sample_seq) or sample_seq
        )
        overlay_bars_since_flip = max(0, sample_seq - overlay_enter_seq)

        transition_score = effective
        if strong_recovery and intraday_score > effective:
            lift_max = float(getattr(config, "REGIME_TRANSITION_RECOVERY_SCORE_LIFT_MAX", 8.0))
            transition_score = min(intraday_score, effective + lift_max)

        return {
            "effective_score": float(effective),
            "detector_score": float(detector_score),
            "eod_score": float(eod_score),
            "intraday_score": float(intraday_score),
            "delta": float(detector_delta),
            "eod_delta": float(eod_delta),
            "momentum_roc": float(momentum_roc),
            "vix_5d_change": float(vix_5d_change),
            "base_regime": str(getattr(self, "_regime_base_state", "NEUTRAL")).upper(),
            "transition_overlay": transition_overlay,
            "strong_recovery": bool(strong_recovery),
            "strong_deterioration": bool(strong_deterioration),
            "ambiguous": bool(ambiguous),
            "overlay_bars_since_flip": int(overlay_bars_since_flip),
            "transition_score": float(transition_score),
            "raw_recovery": bool(raw.get("raw_recovery", False)),
            "raw_deterioration": bool(raw.get("raw_deterioration", False)),
            "raw_ambiguous": bool(raw.get("raw_ambiguous", False)),
            "ambiguous_timed_out": bool(raw.get("ambiguous_timed_out", False)),
            "recovery_by_detector": bool(raw.get("recovery_by_detector", False)),
            "recovery_by_eod": bool(raw.get("recovery_by_eod", False)),
            "deterioration_by_detector": bool(raw.get("deterioration_by_detector", False)),
            "deterioration_by_eod": bool(raw.get("deterioration_by_eod", False)),
            "deterioration_by_fast": bool(raw.get("deterioration_by_fast", False)),
            "overlay_candidate": str(raw.get("overlay_candidate", "STABLE")).upper(),
            "base_candidate": str(raw.get("base_candidate", "NEUTRAL")).upper(),
            "sample_seq": sample_seq,
        }

    def _record_transition_derisk_action(self, action: str, engine: str) -> None:
        """Track transition-time open-position de-risk actions for RCA summaries."""
        action_key = str(action or "").strip().lower()
        if action_key not in {"de_risk_on_deterioration", "de_risk_on_recovery"}:
            return
        self._diag_transition_derisk_counts[action_key] = (
            int(self._diag_transition_derisk_counts.get(action_key, 0)) + 1
        )
        engine_bucket = str(engine or "OTHER").upper()
        if engine_bucket not in self._diag_transition_derisk_counts_by_engine:
            engine_bucket = "OTHER"
        store = self._diag_transition_derisk_counts_by_engine.setdefault(
            engine_bucket,
            {"de_risk_on_deterioration": 0, "de_risk_on_recovery": 0},
        )
        store[action_key] = int(store.get(action_key, 0)) + 1

    def _get_transition_execution_context(self) -> Dict[str, Any]:
        """Return one transition snapshot per minute/sample for all options execution paths."""
        minute_key = self.Time.strftime("%Y-%m-%d %H:%M")
        sample_seq = int(getattr(self, "_regime_detector_sample_seq", 0))
        cached = (
            dict(self._transition_execution_context)
            if isinstance(self._transition_execution_context, dict)
            else None
        )
        if (
            cached is not None
            and self._transition_execution_context_minute_key == minute_key
            and self._transition_execution_context_sample_seq == sample_seq
        ):
            if hasattr(self, "options_engine") and self.options_engine is not None:
                try:
                    self.options_engine.set_transition_context_snapshot(cached)
                except Exception:
                    pass
            return cached

        ctx = self._get_regime_transition_context()
        if not isinstance(ctx, dict):
            ctx = {}
        self._transition_execution_context = dict(ctx)
        self._transition_execution_context_minute_key = minute_key
        self._transition_execution_context_sample_seq = int(
            ctx.get("sample_seq", sample_seq) or sample_seq
        )
        if hasattr(self, "options_engine") and self.options_engine is not None:
            try:
                self.options_engine.set_transition_context_snapshot(ctx)
            except Exception:
                pass
        return dict(ctx)

    def _apply_transition_handoff_open_position_derisk(self, data: Slice) -> bool:
        """
        Transition handoff: de-risk existing wrong-way positions early after overlay flips.

        Returns:
            True when de-risk exits were queued this cycle.
        """
        if not bool(getattr(config, "TRANSITION_HANDOFF_OPEN_DERISK_ENABLED", True)):
            return False
        if not self._is_primary_market_open():
            return False
        if not hasattr(self, "options_engine") or self.options_engine is None:
            return False

        ctx = self._get_transition_execution_context()
        overlay = str(ctx.get("transition_overlay", "") or "").upper()
        if overlay not in {"DETERIORATION", "RECOVERY"}:
            return False

        bars_since_flip = int(ctx.get("overlay_bars_since_flip", 999) or 999)
        intraday_derisk_bars = max(
            1, int(getattr(config, "TRANSITION_HANDOFF_OPEN_DERISK_BARS", 4))
        )
        vass_derisk_bars = max(
            1,
            int(getattr(config, "VASS_TRANSITION_OPEN_DERISK_BARS", intraday_derisk_bars)),
        )
        if bars_since_flip >= max(intraday_derisk_bars, vass_derisk_bars):
            return False

        action_key = (
            "de_risk_on_deterioration" if overlay == "DETERIORATION" else "de_risk_on_recovery"
        )
        queued_any = False

        # De-risk open VASS spreads first.
        for spread in list(self.options_engine.get_spread_positions() or []):
            spread_type = str(getattr(spread, "spread_type", "") or "").upper()
            is_bullish_spread = spread_type in {"BULL_CALL", "BULL_CALL_DEBIT", "BULL_PUT_CREDIT"}
            is_bearish_spread = spread_type in {"BEAR_PUT", "BEAR_PUT_DEBIT", "BEAR_CALL_CREDIT"}
            wrong_way = (overlay == "DETERIORATION" and is_bullish_spread) or (
                overlay == "RECOVERY" and is_bearish_spread
            )
            if not wrong_way:
                continue
            if bars_since_flip >= vass_derisk_bars:
                continue

            long_symbol = self._normalize_symbol_str(spread.long_leg.symbol)
            short_symbol = self._normalize_symbol_str(spread.short_leg.symbol)
            if self._has_open_order_for_symbol(long_symbol) or self._has_open_order_for_symbol(
                short_symbol
            ):
                continue

            reason = f"SPREAD_EXIT: TRANSITION_DERISK_{overlay}"
            signal = TargetWeight(
                symbol=long_symbol,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=spread.num_spreads,
                metadata={
                    "spread_close_short": True,
                    "spread_short_leg_symbol": short_symbol,
                    "spread_short_leg_quantity": spread.num_spreads,
                    "spread_key": self._build_spread_runtime_key(spread),
                    "exit_type": f"TRANSITION_DERISK_{overlay}",
                },
            )
            self._normalize_spread_close_quantities(signal)
            self.portfolio_router.receive_signal(signal)
            self._record_spread_exit_reason(self._build_spread_runtime_key(spread), reason)
            self._diag_spread_exit_signal_count += 1
            self._diag_spread_exit_submit_count += 1
            self._record_transition_derisk_action(action_key, "VASS")
            self.Log(
                f"TRANSITION_OPEN_DERISK: VASS queued | Overlay={overlay} | "
                f"Type={spread_type} | BarsSinceFlip={bars_since_flip}/{vass_derisk_bars}"
            )
            queued_any = True

        # De-risk open ITM/MICRO wrong-way single-leg options.
        for intraday_pos in list(self.options_engine.get_intraday_positions() or []):
            if intraday_pos is None or getattr(intraday_pos, "contract", None) is None:
                continue
            symbol_key = self._normalize_symbol_str(intraday_pos.contract.symbol)
            if not symbol_key:
                continue
            if self._has_open_non_oco_order_for_symbol(symbol_key):
                continue
            if self.options_engine.has_pending_intraday_exit(symbol=symbol_key):
                continue

            strategy_name = str(getattr(intraday_pos, "entry_strategy", "") or "").upper()
            if strategy_name == IntradayStrategy.PROTECTIVE_PUTS.value:
                continue

            direction = getattr(intraday_pos.contract, "direction", None)
            is_call = direction == OptionDirection.CALL or (
                direction is None and "C" in symbol_key and "P" not in symbol_key
            )
            is_put = direction == OptionDirection.PUT or (
                direction is None and "P" in symbol_key and "C" not in symbol_key
            )
            wrong_way = (overlay == "DETERIORATION" and is_call) or (
                overlay == "RECOVERY" and is_put
            )
            if not wrong_way:
                continue
            if bars_since_flip >= intraday_derisk_bars:
                continue
            if not self.options_engine.mark_pending_intraday_exit(symbol_key):
                continue

            live_qty = abs(self._get_option_holding_quantity(symbol_key))
            if live_qty <= 0:
                live_qty = abs(int(getattr(intraday_pos, "num_contracts", 0) or 0))
            if live_qty <= 0:
                continue

            lane = self.options_engine._find_intraday_lane_by_symbol(symbol_key)
            engine_bucket = "ITM" if str(lane or "").upper() == "ITM" else "MICRO"
            self.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=symbol_key,
                    target_weight=0.0,
                    source="OPT_INTRADAY",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"TRANSITION_DERISK_{overlay}",
                    requested_quantity=live_qty,
                    metadata={"intraday_strategy": str(strategy_name or "UNKNOWN")},
                )
            )
            self._record_transition_derisk_action(action_key, engine_bucket)
            self.Log(
                f"TRANSITION_OPEN_DERISK: {engine_bucket} queued | Overlay={overlay} | "
                f"Symbol={symbol_key[-20:]} | BarsSinceFlip={bars_since_flip}/{intraday_derisk_bars}"
            )
            queued_any = True

        if queued_any:
            self._process_immediate_signals()
        return queued_any
