"""VASS spread exit evaluator extracted from options_engine."""

from __future__ import annotations

from datetime import datetime

import config
from engines.satellite.options_primitives import SpreadStrategy
from models.enums import OptionDirection, Urgency
from models.target_weight import TargetWeight


def check_spread_exit_signals_impl(
    self,
    long_leg_price: float,
    short_leg_price: float,
    regime_score: float,
    current_dte: int,
    vix_current: Optional[float] = None,
    spread_override: Optional[SpreadPosition] = None,
    underlying_price: Optional[float] = None,
) -> Optional[List[TargetWeight]]:
    """
    V2.3: Check for spread exit signals.

    Exit conditions:
    1. Take profit at 50% of max profit
    2. Stop loss at 50% of entry debit
    3. Close by 5 DTE (avoid gamma acceleration)
    4. V2.22 Neutrality exit (regime in dead zone 45-60 with flat P&L)
    5. Regime reversal (Bull exit if < 45, Bear exit if > 60)

    Args:
        long_leg_price: Current price of long leg.
        short_leg_price: Current price of short leg.
        regime_score: Current regime score.
        current_dte: Current days to expiration.

    Returns:
        List of TargetWeights for both legs exit, or None.
    """
    spread = spread_override or self.get_spread_position()
    if spread is None:
        return None

    # V2.12 Fix #1: Must have contracts to exit
    if spread.num_spreads <= 0:
        self.log(
            f"SPREAD_EXIT_SKIP: No contracts to exit | num_spreads={spread.num_spreads}",
            trades_only=True,
        )
        return None

    # V2.12 Fix #2: Don't fire duplicate exit signals while closing
    if spread.is_closing:
        return None

    spread_type_upper = str(getattr(spread, "spread_type", "") or "").upper()
    is_credit_spread = spread_type_upper in {
        "BULL_PUT_CREDIT",
        "BEAR_CALL_CREDIT",
        str(SpreadStrategy.BULL_PUT_CREDIT.value),
        str(SpreadStrategy.BEAR_CALL_CREDIT.value),
    }
    is_bullish_spread = spread_type_upper in {
        "BULL_CALL",
        "BULL_CALL_DEBIT",
        "BULL_PUT_CREDIT",
    }
    is_bullish_debit_spread = spread_type_upper in {"BULL_CALL", "BULL_CALL_DEBIT"}
    is_bearish_debit_spread = spread_type_upper in {"BEAR_PUT", "BEAR_PUT_DEBIT"}
    is_bearish_spread = spread_type_upper in {
        "BEAR_PUT",
        "BEAR_PUT_DEBIT",
        "BEAR_CALL_CREDIT",
    }

    def _resolve_debit_spread_mark_value(context_label: str) -> float:
        """
        Resolve debit spread mark value using:
        1) quoted leg mid difference
        2) intrinsic-value floor (when underlying price is available)
        3) hard floor at 0 as final safety clamp
        """
        raw_value = float(long_leg_price) - float(short_leg_price)
        mark_value = raw_value

        ul = float(underlying_price or 0.0)
        if ul > 0:
            long_strike = float(getattr(spread.long_leg, "strike", 0.0) or 0.0)
            short_strike = float(getattr(spread.short_leg, "strike", 0.0) or 0.0)
            intrinsic_value = None
            if is_bullish_debit_spread:
                intrinsic_value = max(0.0, ul - long_strike) - max(0.0, ul - short_strike)
            elif is_bearish_debit_spread:
                intrinsic_value = max(0.0, long_strike - ul) - max(0.0, short_strike - ul)

            if intrinsic_value is not None:
                width_cap = float(getattr(spread, "width", 0.0) or 0.0)
                intrinsic_value = max(0.0, intrinsic_value)
                if width_cap > 0:
                    intrinsic_value = min(intrinsic_value, width_cap)
                if intrinsic_value > mark_value:
                    self.log(
                        f"SPREAD_INTRINSIC_OVERRIDE: {context_label} | Key={self._build_spread_key(spread)} | "
                        f"RawValue=${mark_value:.2f} -> Intrinsic=${intrinsic_value:.2f} | "
                        f"UL=${ul:.2f} LongK={long_strike:.2f} ShortK={short_strike:.2f}",
                        trades_only=True,
                    )
                    mark_value = intrinsic_value

        width_cap = float(getattr(spread, "width", 0.0) or 0.0)
        if width_cap > 0 and mark_value > width_cap:
            self.log(
                f"SPREAD_MARK_CAP_APPLIED: {context_label} | Key={self._build_spread_key(spread)} | "
                f"RawValue=${mark_value:.2f} -> WidthCap=${width_cap:.2f}",
                trades_only=True,
            )
            mark_value = width_cap

        if mark_value < 0:
            self.log(
                f"SPREAD_PNL_CLAMP_APPLIED: {context_label} | Key={self._build_spread_key(spread)} | "
                f"RawValue=${mark_value:.2f} -> $0.00",
                trades_only=True,
            )
            mark_value = 0.0

        return mark_value

    vass_exit_profile = self._get_vass_exit_profile(spread=spread, vix_current=vix_current)
    vass_tier = str(vass_exit_profile.get("tier", "MED"))
    vass_ref_vix = vass_exit_profile.get("ref_vix")
    vass_profile_tag = (
        f"Tier={vass_tier}"
        if vass_ref_vix is None
        else f"Tier={vass_tier} RefVIX={float(vass_ref_vix):.1f}"
    )
    vass_exit_policy_mode = str(getattr(config, "VASS_EXIT_POLICY_MODE", "LEGACY")).upper()
    stored_policy_mode = str(getattr(spread, "entry_policy_mode", "") or "").upper()
    if stored_policy_mode in {"LEGACY"}:
        thesis_first_mode = False
    elif stored_policy_mode in {"THESIS_FIRST", "CREDIT_THETA_FIRST_ACTIVE"}:
        thesis_first_mode = True
    else:
        thesis_first_mode = vass_exit_policy_mode == "THESIS_FIRST"
    regime_confirmed_no_stop_mode = thesis_first_mode and bool(
        getattr(config, "VASS_REGIME_CONFIRMED_NO_STOP", False)
    )
    regime_confirmed = False
    if regime_confirmed_no_stop_mode:
        bull_confirm_min = float(getattr(config, "VASS_REGIME_CONFIRMED_BULL_MIN", 57.0))
        bear_confirm_max = float(getattr(config, "VASS_REGIME_CONFIRMED_BEAR_MAX", 43.0))
        regime_confirmed = (is_bullish_spread and regime_score >= bull_confirm_min) or (
            is_bearish_spread and regime_score <= bear_confirm_max
        )

    profit_target_enabled = bool(getattr(config, "VASS_ENABLE_PROFIT_TARGET_EXITS", True))
    profit_target_open_delay_minutes = int(
        getattr(config, "VASS_PROFIT_TARGET_OPEN_DELAY_MINUTES", 0) or 0
    )
    trail_exit_enabled = bool(getattr(config, "VASS_ENABLE_TRAIL_PROFIT_EXITS", True))
    mark_stop_enabled = bool(getattr(config, "VASS_ENABLE_MARK_STOP_EXITS", True))
    tail_cap_enabled = bool(getattr(config, "VASS_ENABLE_TAIL_CAP_EXITS", True))
    mfe_lock_enabled = bool(getattr(config, "VASS_ENABLE_MFE_LOCK_EXITS", True))
    mfe_lock_in_regime_confirmed = bool(getattr(config, "VASS_MFE_LOCK_IN_REGIME_CONFIRMED", True))
    neutrality_exit_enabled = bool(getattr(config, "VASS_ENABLE_NEUTRALITY_EXITS", True))
    day4_eod_exit_enabled = bool(getattr(config, "VASS_ENABLE_DAY4_EOD_EXITS", True))
    mfe_t1_in_confirmed_disabled = False
    thesis_soft_stop_enabled = bool(
        getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_ENABLED", False)
    )
    thesis_soft_stop_thesis_only = bool(
        getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_THESIS_ONLY", True)
    )
    thesis_soft_stop_mode = (
        thesis_soft_stop_enabled
        and is_bullish_debit_spread
        and (thesis_first_mode or not thesis_soft_stop_thesis_only)
    )
    disable_tactical_exits_for_thesis = thesis_soft_stop_mode and bool(
        getattr(config, "VASS_BULL_DEBIT_THESIS_ONLY_DISABLE_TACTICAL_EXITS", True)
    )
    disable_vix_spike_for_thesis = thesis_soft_stop_mode and bool(
        getattr(config, "VASS_BULL_DEBIT_THESIS_ONLY_DISABLE_VIX_SPIKE_EXITS", True)
    )
    if disable_tactical_exits_for_thesis:
        # V12.27: thesis mode should avoid tactical exits that clip long-DTE winners.
        profit_target_enabled = False
        trail_exit_enabled = False
        mfe_lock_enabled = False
        neutrality_exit_enabled = False
        day4_eod_exit_enabled = False

    if stored_policy_mode in {"LEGACY", "THESIS_FIRST"}:
        credit_theta_first_mode = False
    elif stored_policy_mode == "CREDIT_THETA_FIRST_ACTIVE":
        credit_theta_first_mode = bool(is_credit_spread)
    else:
        credit_theta_first_mode = bool(
            is_credit_spread and getattr(config, "VASS_CREDIT_THETA_FIRST_ENABLED", False)
        )
        if credit_theta_first_mode and bool(
            getattr(config, "VASS_CREDIT_THETA_FIRST_REQUIRE_REGIME_CONFIRMED", True)
        ):
            credit_theta_first_mode = bool(regime_confirmed)

    credit_mark_stop_suppressed = credit_theta_first_mode and bool(
        getattr(config, "VASS_CREDIT_THETA_FIRST_SUPPRESS_MARK_STOP", True)
    )
    credit_conviction_floor_suppressed = credit_theta_first_mode and bool(
        getattr(config, "VASS_CREDIT_THETA_FIRST_SUPPRESS_CONVICTION_FLOOR", True)
    )
    credit_mfe_t1_suppressed = credit_theta_first_mode and bool(
        getattr(config, "VASS_CREDIT_THETA_FIRST_SUPPRESS_MFE_T1", True)
    )

    credit_mark_stop_disabled_by_theta = False
    if credit_mark_stop_suppressed:
        mark_stop_enabled = False
        credit_mark_stop_disabled_by_theta = True

    if (
        credit_theta_first_mode
        and self.algorithm is not None
        and hasattr(self.algorithm, "_diag_vass_credit_theta_first_active_checks")
    ):
        self.algorithm._diag_vass_credit_theta_first_active_checks = (
            int(getattr(self.algorithm, "_diag_vass_credit_theta_first_active_checks", 0) or 0) + 1
        )

    if regime_confirmed:
        if is_credit_spread:
            if bool(getattr(config, "VASS_REGIME_CONFIRMED_DISABLE_CREDIT_TRAIL", False)):
                trail_exit_enabled = False
            if bool(getattr(config, "VASS_REGIME_CONFIRMED_DISABLE_CREDIT_MARK_STOP", False)):
                mark_stop_enabled = False
        else:
            if bool(getattr(config, "VASS_REGIME_CONFIRMED_DISABLE_DEBIT_TRAIL", True)):
                trail_exit_enabled = False
            if bool(getattr(config, "VASS_REGIME_CONFIRMED_DISABLE_DEBIT_MARK_STOP", True)):
                mark_stop_enabled = False
            # V12.23.2: T1 breakeven floor contradicts confirmed thesis — disable T1,
            # keep T2 (meaningful profit lock) active.
            if bool(getattr(config, "VASS_REGIME_CONFIRMED_DISABLE_DEBIT_MFE_T1", True)):
                mfe_t1_in_confirmed_disabled = True
        # V12.9: tail_cap stays ALWAYS active — conviction floor prevents unbounded losses

    regime_break_enabled = thesis_first_mode and bool(
        getattr(config, "VASS_REGIME_BREAK_EXIT_ENABLED", False)
    )
    regime_break_bull_floor = float(getattr(config, "VASS_REGIME_BREAK_BULL_FLOOR", 50.0))
    regime_break_bear_ceiling = float(getattr(config, "VASS_REGIME_BREAK_BEAR_CEILING", 50.0))

    regime_break_reason = None
    if regime_break_enabled:
        if is_bullish_spread and regime_score < regime_break_bull_floor:
            regime_break_reason = (
                f"VASS_REGIME_BREAK_BULL: Regime {regime_score:.0f} < {regime_break_bull_floor:.0f}"
            )
        elif is_bearish_spread and regime_score > regime_break_bear_ceiling:
            regime_break_reason = f"VASS_REGIME_BREAK_BEAR: Regime {regime_score:.0f} > {regime_break_bear_ceiling:.0f}"

    def _profit_target_open_delay_active() -> bool:
        """Gate profit-target exits in the opening minutes to avoid wide opening-book fills."""
        if profit_target_open_delay_minutes <= 0 or self.algorithm is None:
            return False
        try:
            now = self.algorithm.Time
            minute_of_day = int(now.hour * 60 + now.minute)
            market_open_minute = 9 * 60 + 30
            return (
                market_open_minute
                <= minute_of_day
                < (market_open_minute + profit_target_open_delay_minutes)
            )
        except Exception:
            return False

    def _record_profit_target_open_delay_skip() -> None:
        if self.algorithm is not None and hasattr(
            self.algorithm, "_diag_vass_profit_target_open_delay_skip_count"
        ):
            self.algorithm._diag_vass_profit_target_open_delay_skip_count = (
                int(
                    getattr(self.algorithm, "_diag_vass_profit_target_open_delay_skip_count", 0)
                    or 0
                )
                + 1
            )

        # Throttle to one log per spread per day.
        log_day_key = ""
        if self.algorithm is not None:
            log_day_key = str(self.algorithm.Time.date())
        if getattr(spread, "_profit_target_open_delay_logged_day", "") != log_day_key:
            spread._profit_target_open_delay_logged_day = log_day_key
            self.log(
                "PROFIT_TARGET_OPEN_DELAY_SKIP: "
                f"Key={self._build_spread_key(spread)} | "
                f"Delay={profit_target_open_delay_minutes}m | "
                f"Time={self.algorithm.Time.strftime('%H:%M') if self.algorithm is not None else 'NA'}",
                trades_only=True,
            )

    credit_dte_exit_default = int(
        getattr(config, "CREDIT_SPREAD_DTE_EXIT", getattr(config, "SPREAD_DTE_EXIT", 5))
    )
    if regime_confirmed:
        if is_credit_spread:
            dte_exit_threshold = int(
                getattr(
                    config,
                    "VASS_REGIME_CONFIRMED_DTE_EXIT_CREDIT",
                    credit_dte_exit_default,
                )
            )
        else:
            dte_exit_threshold = int(
                getattr(
                    config,
                    "VASS_REGIME_CONFIRMED_DTE_EXIT_DEBIT",
                    getattr(config, "VASS_REGIME_CONFIRMED_DTE_EXIT", config.SPREAD_DTE_EXIT),
                )
            )
    else:
        if is_credit_spread:
            dte_exit_threshold = credit_dte_exit_default
        else:
            dte_exit_threshold = int(getattr(config, "SPREAD_DTE_EXIT", 5))

    def _resolve_vass_tail_cap_pct(resolved_dte: int) -> float:
        base_pct = float(getattr(config, "VASS_TAIL_RISK_CAP_PCT_EQUITY", 0.015))
        if not bool(getattr(config, "VASS_TAIL_RISK_CAP_USE_DTE_OVERLAY", False)):
            return max(0.0, base_pct)

        short_max = int(getattr(config, "VASS_TAIL_RISK_CAP_DTE_SHORT_MAX", 9))
        med_max = int(getattr(config, "VASS_TAIL_RISK_CAP_DTE_MED_MAX", 21))
        short_mult = float(getattr(config, "VASS_TAIL_RISK_CAP_DTE_SHORT_MULT", 0.75))
        med_mult = float(getattr(config, "VASS_TAIL_RISK_CAP_DTE_MED_MULT", 1.00))
        long_mult = float(getattr(config, "VASS_TAIL_RISK_CAP_DTE_LONG_MULT", 1.25))

        dte_mult = long_mult
        if resolved_dte <= short_max:
            dte_mult = short_mult
        elif resolved_dte <= med_max:
            dte_mult = med_mult

        overlay_mult = 1.0
        try:
            transition_ctx = self._get_regime_transition_context(regime_score=regime_score) or {}
            overlay = str(transition_ctx.get("transition_overlay", "STABLE") or "STABLE").upper()
            overlay_mults = getattr(config, "VASS_TAIL_RISK_CAP_OVERLAY_MULTIPLIERS", {})
            if isinstance(overlay_mults, dict):
                overlay_mult = float(
                    overlay_mults.get(
                        overlay,
                        overlay_mults.get("STABLE", 1.0),
                    )
                )
        except Exception:
            overlay_mult = 1.0

        resolved = base_pct * dte_mult * overlay_mult
        min_pct = float(getattr(config, "VASS_TAIL_RISK_CAP_MIN_PCT_EQUITY", 0.0))
        max_pct = float(getattr(config, "VASS_TAIL_RISK_CAP_MAX_PCT_EQUITY", 1.0))
        if max_pct < min_pct:
            min_pct, max_pct = max_pct, min_pct
        return max(min_pct, min(max_pct, resolved))

    def _apply_catastrophic_reentry_lock(exit_code_text: str, exit_reason_text: str) -> int:
        if not bool(getattr(config, "VASS_CATASTROPHIC_EXIT_NO_REENTRY_REST_OF_SESSION", False)):
            return 0
        if self.algorithm is None:
            return 0
        code_u = str(exit_code_text or "").upper()
        reason_u = str(exit_reason_text or "").upper()
        catastrophic_codes = {
            "VASS_TAIL_RISK_CAP",
            "SPREAD_HARD_STOP_DURING_HOLD",
            "SPREAD_HARD_STOP_TRIGGERED_PCT",
            "SPREAD_HARD_STOP_TRIGGERED_WIDTH",
        }
        is_catastrophic = (
            code_u in catastrophic_codes
            or "VASS_TAIL_RISK_CAP" in reason_u
            or "SPREAD_HARD_STOP_DURING_HOLD" in reason_u
        )
        if not is_catastrophic:
            return 0

        spread_dir_label = self._spread_direction_label(spread.spread_type)
        spread_dir = None
        if spread_dir_label == "BULLISH":
            spread_dir = OptionDirection.CALL
        elif spread_dir_label == "BEARISH":
            spread_dir = OptionDirection.PUT
        if spread_dir is None:
            return 0

        self._record_vass_direction_day_entry(spread_dir, self.algorithm.Time)
        cooldown_days = int(getattr(config, "VASS_CATASTROPHIC_DIRECTION_COOLDOWN_DAYS", 0) or 0)
        if cooldown_days > 0 and getattr(self, "_vass_entry_engine_enabled", False):
            try:
                self._vass_entry_engine.set_direction_loss_cooldown(
                    direction=spread_dir,
                    start_dt=self.algorithm.Time,
                    cooldown_days=cooldown_days,
                )
            except Exception:
                pass
        configured_lock = int(getattr(config, "VASS_CATASTROPHIC_EXIT_LOCK_MINUTES", 0))
        if configured_lock > 0:
            lock_minutes = configured_lock
        else:
            minute_of_day = int(self.algorithm.Time.hour * 60 + self.algorithm.Time.minute)
            close_minutes = int(getattr(config, "VASS_SESSION_CLOSE_MINUTES", 16 * 60))
            lock_minutes = close_minutes - minute_of_day
        if lock_minutes <= 0:
            lock_minutes = int(getattr(config, "VASS_CATASTROPHIC_EXIT_FALLBACK_LOCK_MINUTES", 120))
        if lock_minutes <= 0:
            return 0

        self._set_directional_spread_cooldown(
            cooldown_key=spread_dir.value,
            minutes=lock_minutes,
            reason=f"CATASTROPHIC_EXIT:{code_u or 'UNKNOWN'}",
        )
        if spread_dir == OptionDirection.CALL:
            self._set_directional_spread_cooldown(
                cooldown_key=SpreadStrategy.BULL_PUT_CREDIT.value,
                minutes=lock_minutes,
                reason=f"CATASTROPHIC_EXIT:{code_u or 'UNKNOWN'}",
            )
        else:
            self._set_directional_spread_cooldown(
                cooldown_key=SpreadStrategy.BEAR_CALL_CREDIT.value,
                minutes=lock_minutes,
                reason=f"CATASTROPHIC_EXIT:{code_u or 'UNKNOWN'}",
            )
        return int(lock_minutes)

    if bool(getattr(config, "VASS_ATR_ADAPTIVE_EXITS_ENABLED", True)):
        atr_pct = self._resolve_qqq_atr_pct(underlying_price=underlying_price)
        if atr_pct is not None and atr_pct > 0:
            atr_ref = max(1e-6, float(getattr(config, "VASS_ATR_PCT_REF", 0.015)))
            raw_mult = float(atr_pct / atr_ref)
            mult_min = float(getattr(config, "VASS_ATR_EXIT_MULT_MIN", 0.85))
            mult_max = float(getattr(config, "VASS_ATR_EXIT_MULT_MAX", 1.25))
            if mult_max < mult_min:
                mult_min, mult_max = mult_max, mult_min
            atr_mult = max(mult_min, min(raw_mult, mult_max))
            for profile_key in (
                "target_pct",
                "stop_pct",
                "trail_activate_pct",
                "trail_offset_pct",
            ):
                if profile_key in vass_exit_profile:
                    vass_exit_profile[profile_key] = (
                        float(vass_exit_profile[profile_key]) * atr_mult
                    )
            if bool(getattr(config, "VASS_ATR_ADAPT_HARD_AND_EOD", True)):
                if "hard_stop_pct" in vass_exit_profile:
                    vass_exit_profile["hard_stop_pct"] = (
                        float(vass_exit_profile["hard_stop_pct"]) * atr_mult
                    )
                if "eod_gate_pct" in vass_exit_profile:
                    # eod_gate_pct is negative; scaling by ATRx widens/tightens consistently by regime vol.
                    vass_exit_profile["eod_gate_pct"] = (
                        float(vass_exit_profile["eod_gate_pct"]) * atr_mult
                    )
            vass_profile_tag = f"{vass_profile_tag} ATRx={atr_mult:.2f} ATR%={atr_pct:.2%}"

    # V9.4 P0: Exit signal cooldown — if a previous exit signal was sent but the
    # close order failed (margin, liquidity, etc.), don't re-fire every minute.
    # Wait SPREAD_EXIT_RETRY_MINUTES before retrying.
    retry_minutes = int(getattr(config, "SPREAD_EXIT_RETRY_MINUTES", 15))
    if retry_minutes > 0 and self.algorithm is not None:
        spread_key = self._build_spread_key(spread)
        last_exit_time = self._spread_exit_signal_cooldown.get(spread_key)
        if last_exit_time is not None:
            elapsed = (self.algorithm.Time - last_exit_time).total_seconds() / 60.0
            if elapsed < retry_minutes:
                return None

    # Phase A: anti-churn hold window for non-emergency spread exits.
    # Emergency exits (assignment/0DTE mandatory) are handled in check_assignment_risk_exit().
    min_hold_minutes = int(getattr(config, "SPREAD_MIN_HOLD_MINUTES", 0))
    if min_hold_minutes > 0 and self.algorithm is not None:
        try:
            entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
            live_minutes = (self.algorithm.Time - entry_dt).total_seconds() / 60.0
            mandatory_dte = int(getattr(config, "SPREAD_FORCE_CLOSE_DTE", 1))
            if 0 <= live_minutes < min_hold_minutes and current_dte > mandatory_dte:
                hold_guard_bypass = spread.spread_type in (
                    "BULL_PUT_CREDIT",
                    "BEAR_CALL_CREDIT",
                    SpreadStrategy.BULL_PUT_CREDIT.value,
                    SpreadStrategy.BEAR_CALL_CREDIT.value,
                )
                # Credit spreads should not be forced through the debit hold window.
                # Let credit stop/target logic run immediately under current market conditions.
                if spread.spread_type in (
                    "BULL_CALL",
                    "BEAR_PUT",
                    SpreadStrategy.BULL_CALL_DEBIT.value,
                    SpreadStrategy.BEAR_PUT_DEBIT.value,
                ):
                    entry_debit = float(getattr(spread, "net_debit", 0.0) or 0.0)
                    if entry_debit > 0:
                        current_spread_value = _resolve_debit_spread_mark_value("HoldGuard")
                        pnl = current_spread_value - entry_debit
                        pnl_pct = pnl / entry_debit

                        hard_stop_pct = float(vass_exit_profile.get("hard_stop_pct", 0.0))
                        if mark_stop_enabled and hard_stop_pct > 0 and pnl_pct <= -hard_stop_pct:
                            self.log(
                                f"SPREAD_HARD_STOP_DURING_HOLD: {pnl_pct:.1%} <= -{hard_stop_pct:.0%} | "
                                f"Key={self._build_spread_key(spread)} | Held={live_minutes:.0f}m",
                                trades_only=True,
                            )
                            spread.is_closing = True
                            catastrophic_lock_minutes = _apply_catastrophic_reentry_lock(
                                "SPREAD_HARD_STOP_DURING_HOLD",
                                f"SPREAD_HARD_STOP_DURING_HOLD {pnl_pct:.1%}",
                            )
                            if self.algorithm is not None:
                                self._spread_exit_signal_cooldown[
                                    self._build_spread_key(spread)
                                ] = self.algorithm.Time
                            return [
                                TargetWeight(
                                    symbol=self._symbol_str(spread.long_leg.symbol),
                                    target_weight=0.0,
                                    source="OPT",
                                    urgency=Urgency.IMMEDIATE,
                                    reason=(
                                        f"SPREAD_EXIT: SPREAD_HARD_STOP_DURING_HOLD {pnl_pct:.1%} "
                                        f"(lost > {hard_stop_pct:.0%} hard cap)"
                                    ),
                                    requested_quantity=spread.num_spreads,
                                    metadata={
                                        "spread_close_short": True,
                                        "spread_type": spread.spread_type,
                                        "spread_short_leg_symbol": self._symbol_str(
                                            spread.short_leg.symbol
                                        ),
                                        "spread_short_leg_quantity": spread.num_spreads,
                                        "spread_key": self._build_spread_key(spread),
                                        "spread_width": spread.width,
                                        "spread_entry_debit": entry_debit,
                                        "spread_exit_estimated_net_value": current_spread_value,
                                        "spread_exit_code": "SPREAD_HARD_STOP_DURING_HOLD",
                                        "spread_exit_reason": (
                                            f"SPREAD_HARD_STOP_DURING_HOLD {pnl_pct:.1%} "
                                            f"(lost > {hard_stop_pct:.0%} hard cap)"
                                        ),
                                        "spread_exit_emergency": True,
                                        "vass_no_reentry_lock_minutes": catastrophic_lock_minutes,
                                        "is_credit_spread": False,
                                        "spread_credit_received": 0.0,
                                    },
                                )
                            ]

                        # EOD risk gate during hold to reduce overnight tail losses.
                        eod_gate_enabled = (
                            bool(getattr(config, "SPREAD_EOD_HOLD_RISK_GATE_ENABLED", False))
                            and not disable_tactical_exits_for_thesis
                        )
                        eod_gate_pct = float(vass_exit_profile.get("eod_gate_pct", -0.25))
                        eod_gate_min_hold_minutes = int(
                            getattr(config, "SPREAD_EOD_GATE_MIN_HOLD_MINUTES", 0)
                        )
                        if (
                            eod_gate_enabled
                            and pnl_pct <= eod_gate_pct
                            and self.algorithm is not None
                        ):
                            eod_hour, eod_min = 15, 45
                            is_eod = self.algorithm.Time.hour > eod_hour or (
                                self.algorithm.Time.hour == eod_hour
                                and self.algorithm.Time.minute >= eod_min
                            )
                            if is_eod and live_minutes >= max(0, eod_gate_min_hold_minutes):
                                self.log(
                                    f"SPREAD_EOD_HOLD_RISK_GATE: {pnl_pct:.1%} <= {eod_gate_pct:.0%} | "
                                    f"Key={self._build_spread_key(spread)} | Held={live_minutes/1440:.0f}d",
                                    trades_only=True,
                                )
                                if bool(
                                    getattr(
                                        config,
                                        "VASS_EOD_GATE_BLOCK_SAME_DAY_REENTRY",
                                        True,
                                    )
                                ):
                                    spread_dir_label = self._spread_direction_label(
                                        spread.spread_type
                                    )
                                    spread_dir = None
                                    if spread_dir_label == "BULLISH":
                                        spread_dir = OptionDirection.CALL
                                    elif spread_dir_label == "BEARISH":
                                        spread_dir = OptionDirection.PUT
                                    if spread_dir is not None:
                                        self._record_vass_direction_day_entry(
                                            spread_dir,
                                            self.algorithm.Time,
                                        )
                                        cooldown_minutes = int(
                                            getattr(
                                                config,
                                                "VASS_EOD_GATE_DIRECTION_COOLDOWN_MINUTES",
                                                0,
                                            )
                                        )
                                        if cooldown_minutes > 0:
                                            self._set_directional_spread_cooldown(
                                                cooldown_key=spread_dir.value,
                                                minutes=cooldown_minutes,
                                                reason="EOD_HOLD_RISK_GATE",
                                            )
                                            if spread_dir == OptionDirection.CALL:
                                                self._set_directional_spread_cooldown(
                                                    cooldown_key=SpreadStrategy.BULL_PUT_CREDIT.value,
                                                    minutes=cooldown_minutes,
                                                    reason="EOD_HOLD_RISK_GATE",
                                                )
                                            else:
                                                self._set_directional_spread_cooldown(
                                                    cooldown_key=SpreadStrategy.BEAR_CALL_CREDIT.value,
                                                    minutes=cooldown_minutes,
                                                    reason="EOD_HOLD_RISK_GATE",
                                                )
                                spread.is_closing = True
                                self._spread_exit_signal_cooldown[
                                    self._build_spread_key(spread)
                                ] = self.algorithm.Time
                                return [
                                    TargetWeight(
                                        symbol=self._symbol_str(spread.long_leg.symbol),
                                        target_weight=0.0,
                                        source="OPT",
                                        urgency=Urgency.IMMEDIATE,
                                        reason=(
                                            f"SPREAD_EXIT: EOD_HOLD_RISK_GATE {pnl_pct:.1%} "
                                            f"(<= {eod_gate_pct:.0%} at EOD during hold)"
                                        ),
                                        requested_quantity=spread.num_spreads,
                                        metadata={
                                            "spread_close_short": True,
                                            "spread_type": spread.spread_type,
                                            "spread_short_leg_symbol": self._symbol_str(
                                                spread.short_leg.symbol
                                            ),
                                            "spread_short_leg_quantity": spread.num_spreads,
                                            "spread_key": self._build_spread_key(spread),
                                            "spread_width": spread.width,
                                            "spread_entry_debit": entry_debit,
                                            "spread_exit_estimated_net_value": current_spread_value,
                                            "spread_exit_code": "EOD_HOLD_RISK_GATE",
                                            "spread_exit_reason": (
                                                f"EOD_HOLD_RISK_GATE {pnl_pct:.1%} "
                                                f"(<= {eod_gate_pct:.0%} at EOD during hold)"
                                            ),
                                            "is_credit_spread": False,
                                            "spread_credit_received": 0.0,
                                        },
                                    )
                                ]

                        # Profitable debit spreads can bypass hold guard and use normal exit cascade.
                        if pnl_pct > 0:
                            hold_guard_bypass = True
                        if not hold_guard_bypass and bool(
                            getattr(config, "SPREAD_HOLD_GUARD_SOFT_ENABLED", True)
                        ):
                            if bool(
                                getattr(
                                    config,
                                    "SPREAD_HOLD_GUARD_ALLOW_TRANSITION_BYPASS",
                                    True,
                                )
                            ):
                                transition_ctx = self._get_regime_transition_context(
                                    regime_score=regime_score
                                )
                                transition_overlay = str(
                                    transition_ctx.get("transition_overlay", "") or ""
                                ).upper()
                                if (
                                    transition_overlay in {"DETERIORATION", "RECOVERY"}
                                    or bool(transition_ctx.get("strong_deterioration", False))
                                    or bool(transition_ctx.get("strong_recovery", False))
                                ):
                                    hold_guard_bypass = True
                                    self.log(
                                        f"SPREAD_EXIT_GUARD_BYPASS: Transition={transition_overlay or 'NA'} | "
                                        f"Key={self._build_spread_key(spread)} | PnL={pnl_pct:.1%}",
                                        trades_only=True,
                                    )

                            if (
                                not hold_guard_bypass
                                and regime_break_reason is not None
                                and regime_break_enabled
                            ):
                                hold_guard_bypass = True
                                self.log(
                                    f"SPREAD_EXIT_GUARD_BYPASS: RegimeBreak {regime_break_reason} | "
                                    f"Key={self._build_spread_key(spread)}",
                                    trades_only=True,
                                )

                            if not hold_guard_bypass and pnl_pct < 0:
                                base_stop_pct = float(
                                    vass_exit_profile.get(
                                        "stop_pct",
                                        getattr(config, "SPREAD_STOP_LOSS_PCT", 0.35),
                                    )
                                )
                                stop_multipliers = getattr(
                                    config,
                                    "SPREAD_STOP_REGIME_MULTIPLIERS",
                                    {75: 1.0, 50: 1.0, 40: 1.0, 0: 1.0},
                                )
                                stop_multiplier = 1.0
                                for threshold in sorted(stop_multipliers.keys(), reverse=True):
                                    if regime_score >= threshold:
                                        stop_multiplier = float(stop_multipliers[threshold])
                                        break
                                adaptive_stop_pct = base_stop_pct * stop_multiplier
                                hard_cap_pct = float(vass_exit_profile.get("hard_stop_pct", 0.0))
                                if hard_cap_pct > 0:
                                    adaptive_stop_pct = min(adaptive_stop_pct, hard_cap_pct)

                                loss_bypass_fraction = float(
                                    getattr(
                                        config,
                                        "SPREAD_HOLD_GUARD_LOSS_BYPASS_STOP_FRACTION",
                                        0.75,
                                    )
                                )
                                if (
                                    not hold_guard_bypass
                                    and 0 < loss_bypass_fraction < 1
                                    and adaptive_stop_pct > 0
                                ):
                                    bypass_stop_pct = adaptive_stop_pct * loss_bypass_fraction
                                    if pnl_pct <= -bypass_stop_pct:
                                        hold_guard_bypass = True
                                        self.log(
                                            f"SPREAD_EXIT_GUARD_BYPASS: LossBypass {pnl_pct:.1%} <= -{bypass_stop_pct:.0%} "
                                            f"(Frac={loss_bypass_fraction:.2f}) | "
                                            f"Key={self._build_spread_key(spread)}",
                                            trades_only=True,
                                        )

                                severe_mult = max(
                                    1.0,
                                    float(
                                        getattr(
                                            config,
                                            "SPREAD_HOLD_GUARD_SEVERE_STOP_MULTIPLIER",
                                            1.10,
                                        )
                                    ),
                                )
                                severe_stop_pct = adaptive_stop_pct * severe_mult
                                if hard_cap_pct > 0:
                                    severe_stop_pct = min(severe_stop_pct, hard_cap_pct)
                                if severe_stop_pct > 0 and pnl_pct <= -severe_stop_pct:
                                    hold_guard_bypass = True
                                    self.log(
                                        f"SPREAD_EXIT_GUARD_BYPASS: SevereLoss {pnl_pct:.1%} <= -{severe_stop_pct:.0%} | "
                                        f"Key={self._build_spread_key(spread)}",
                                        trades_only=True,
                                    )

                if not hold_guard_bypass:
                    spread_key = self._build_spread_key(spread)
                    if spread_key not in self._spread_hold_guard_logged:
                        self._spread_hold_guard_logged.add(spread_key)
                        hold_days = min_hold_minutes / 1440.0
                        self.log(
                            f"SPREAD_EXIT_GUARD_HOLD: Key={spread_key} | Sig={spread.spread_type} | "
                            f"Hold={hold_days:.0f}d ({min_hold_minutes}m) | DTE={current_dte}",
                            trades_only=True,
                        )
                    return None
        except Exception:
            pass

    exit_reason = None
    credit_theta_hold_guard_active = False
    credit_theta_hold_minutes = 0.0
    if (
        credit_theta_first_mode
        and is_credit_spread
        and self.algorithm is not None
        and current_dte > int(getattr(config, "SPREAD_FORCE_CLOSE_DTE", 1))
    ):
        try:
            credit_hold_min = max(
                0,
                int(getattr(config, "VASS_CREDIT_THETA_FIRST_MIN_HOLD_MINUTES", 0) or 0),
            )
            if credit_hold_min > 0:
                entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                credit_theta_hold_minutes = max(
                    0.0,
                    (self.algorithm.Time - entry_dt).total_seconds() / 60.0,
                )
                credit_theta_hold_guard_active = 0 <= credit_theta_hold_minutes < credit_hold_min
                if credit_theta_hold_guard_active:
                    spread_key = self._build_spread_key(spread)
                    if spread_key not in self._spread_hold_guard_logged:
                        self._spread_hold_guard_logged.add(spread_key)
                        self.log(
                            f"CREDIT_THETA_FIRST_ACTIVE: HOLD_GUARD | Key={spread_key} | Sig={spread.spread_type} | "
                            f"Held={credit_theta_hold_minutes:.0f}m < {credit_hold_min}m | DTE={current_dte}",
                            trades_only=True,
                        )
        except Exception:
            credit_theta_hold_guard_active = False
            credit_theta_hold_minutes = 0.0

    # V12.29: In THETA_FIRST mode, suppress credit mark-stop only during the initial hold window.
    # After hold guard expires, re-enable CREDIT_STOP_2X to cap left-tail losses.
    if (
        is_credit_spread
        and credit_theta_first_mode
        and credit_mark_stop_disabled_by_theta
        and not credit_theta_hold_guard_active
        and not (
            regime_confirmed
            and bool(getattr(config, "VASS_REGIME_CONFIRMED_DISABLE_CREDIT_MARK_STOP", False))
        )
    ):
        mark_stop_enabled = True

    # ---------------------------------------------------------------------
    # P0: VIX Spike Auto-Exit (bullish spreads only)
    # Close CALL spreads if VIX spikes to panic levels or 5d change surges
    # ---------------------------------------------------------------------
    vix_spike_enabled = bool(getattr(config, "SWING_VIX_SPIKE_EXIT_ENABLED", True)) and (
        not disable_vix_spike_for_thesis
    )
    vix_spike_level = getattr(config, "SWING_VIX_SPIKE_EXIT_LEVEL", 25.0)
    vix_spike_5d = getattr(config, "SWING_VIX_SPIKE_EXIT_5D_PCT", 0.20)
    vix_5d_change = self._iv_sensor.get_vix_5d_change() if self._iv_sensor.is_ready() else None

    # V6.22: Transition exit priority - force close wrong-way bullish spreads in STRESS.
    if (
        exit_reason is None
        and bool(getattr(config, "SPREAD_OVERLAY_STRESS_EXIT_ENABLED", False))
        and not disable_tactical_exits_for_thesis
        and is_bullish_debit_spread
        and vix_current is not None
    ):
        overlay_state = self.get_regime_overlay_state(
            vix_current=vix_current, regime_score=regime_score
        )
        if overlay_state == "STRESS":
            exit_reason = (
                f"OVERLAY_STRESS_EXIT: Overlay={overlay_state} | "
                f"Regime={regime_score:.0f} | VIX={vix_current:.1f}"
            )

    if (
        vix_spike_enabled
        and exit_reason is None
        and is_bullish_debit_spread
        and vix_current is not None
    ):
        if vix_current >= vix_spike_level:
            exit_reason = f"VIX_SPIKE_EXIT: VIX {vix_current:.1f} >= {vix_spike_level}"
        elif vix_5d_change is not None and vix_5d_change >= vix_spike_5d:
            exit_reason = f"VIX_SPIKE_EXIT: 5D change {vix_5d_change:+.0%} >= {vix_spike_5d:.0%}"

    # V12.4: Pre-close transition de-risk for bullish debit VASS positions.
    # Avoid carrying wrong-way swing debit exposure overnight when macro overlay is weakening.
    if (
        exit_reason is None
        and is_bullish_debit_spread
        and bool(getattr(config, "VASS_OVERNIGHT_DERISK_ENABLED", False))
        and not disable_tactical_exits_for_thesis
        and not regime_confirmed
        and self.algorithm is not None
        and current_dte > 0
    ):
        try:
            cutoff = str(getattr(config, "VASS_OVERNIGHT_DERISK_TIME", "15:40"))
            cutoff_hour, cutoff_minute = [int(x) for x in cutoff.split(":", 1)]
            is_preclose_window = self.algorithm.Time.hour > cutoff_hour or (
                self.algorithm.Time.hour == cutoff_hour
                and self.algorithm.Time.minute >= cutoff_minute
            )
            if is_preclose_window:
                transition_ctx = self._get_regime_transition_context() or {}
                transition_overlay = str(transition_ctx.get("transition_overlay", "STABLE")).upper()
                derisk_deterioration = bool(
                    getattr(config, "VASS_OVERNIGHT_DERISK_ON_DETERIORATION", True)
                )
                derisk_ambiguous = bool(getattr(config, "VASS_OVERNIGHT_DERISK_ON_AMBIGUOUS", True))
                should_derisk = (
                    transition_overlay == "DETERIORATION" and derisk_deterioration
                ) or (transition_overlay == "AMBIGUOUS" and derisk_ambiguous)
                if should_derisk:
                    exit_reason = (
                        f"OVERNIGHT_DERISK_{transition_overlay}: Pre-close de-risk | "
                        f"DTE={current_dte} | {vass_profile_tag}"
                    )
        except Exception:
            pass

    if exit_reason is None and regime_break_reason is not None:
        exit_reason = regime_break_reason

    # V10.5: Regime deterioration exits are evaluated after P&L is known.
    if is_credit_spread:
        # CREDIT SPREAD P&L: Profit when spread value DECREASES
        # Entry: Received credit (stored as negative net_debit)
        # Current: Cost to buy back spread (short - long)
        current_spread_value = short_leg_price - long_leg_price  # Cost to close
        entry_credit = abs(spread.net_debit)  # Credit received (stored as negative)

        # Profit = credit_received - current_spread_cost
        pnl = entry_credit - current_spread_value
        pnl_pct = pnl / spread.max_profit if spread.max_profit > 0 else 0

        # V10.15: Track MFE relative to max profit for harvesting locks.
        mfe_ratio = pnl / spread.max_profit if spread.max_profit > 0 else 0.0
        if mfe_ratio > spread.highest_pnl_max_profit_pct:
            spread.highest_pnl_max_profit_pct = mfe_ratio

        if (
            mfe_lock_enabled
            and bool(getattr(config, "VASS_MFE_LOCK_ENABLED", True))
            and (mfe_lock_in_regime_confirmed or not regime_confirmed)
            and spread.max_profit > 0
            and not credit_theta_hold_guard_active
        ):
            prev_tier = int(getattr(spread, "mfe_lock_tier", 0) or 0)
            t1 = float(getattr(config, "VASS_MFE_T1_TRIGGER", 0.25))
            t2 = float(getattr(config, "VASS_MFE_T2_TRIGGER", 0.45))
            floor_t2_pct = float(vass_exit_profile.get("mfe_t2_floor_pct", 0.15))
            commission_cost = spread.num_spreads * config.SPREAD_COMMISSION_PER_CONTRACT
            commission_per_share = (
                commission_cost / (spread.num_spreads * 100) if spread.num_spreads > 0 else 0.0
            )
            if spread.highest_pnl_max_profit_pct >= t2:
                spread.mfe_lock_tier = max(spread.mfe_lock_tier, 2)
            elif spread.highest_pnl_max_profit_pct >= t1:
                spread.mfe_lock_tier = max(spread.mfe_lock_tier, 1)
            self._record_vass_mfe_diag(spread, prev_tier)

            floor_pnl = None
            if spread.mfe_lock_tier >= 2:
                floor_pnl = spread.max_profit * floor_t2_pct + commission_per_share
            elif spread.mfe_lock_tier >= 1 and not credit_mfe_t1_suppressed:
                floor_pnl = commission_per_share

            if floor_pnl is not None and pnl <= floor_pnl:
                if self.algorithm is not None and hasattr(
                    self.algorithm, "_diag_vass_mfe_lock_exits"
                ):
                    self.algorithm._diag_vass_mfe_lock_exits = (
                        int(getattr(self.algorithm, "_diag_vass_mfe_lock_exits", 0) or 0) + 1
                    )
                exit_reason = (
                    f"MFE_LOCK_T{spread.mfe_lock_tier} {pnl:.1%} (MFE={spread.highest_pnl_max_profit_pct:.1%}, "
                    f"Floor=${floor_pnl:.2f}, {vass_profile_tag})"
                )

        if (
            exit_reason is None
            and bool(getattr(config, "VASS_TAIL_RISK_CAP_ENABLED", True))
            and tail_cap_enabled
            and pnl < 0
            and self.algorithm is not None
        ):
            equity = float(getattr(self.algorithm.Portfolio, "TotalPortfolioValue", 0.0) or 0.0)
            cap_pct = _resolve_vass_tail_cap_pct(current_dte)
            loss_dollars = abs(float(pnl)) * 100.0 * max(1, int(spread.num_spreads))
            cap_dollars = max(0.0, equity * cap_pct)
            # V12.9: Conviction floor — per-trade % loss cap for credit spreads.
            # For credits: loss_pct = (current_spread_value - entry_credit) / max_loss
            floor_pct = float(getattr(config, "VASS_TAIL_RISK_CAP_FLOOR_PCT", 0.35))
            min_loss_pct = float(
                getattr(config, "VASS_TAIL_RISK_CAP_CREDIT_MIN_LOSS_PCT", floor_pct)
            )
            max_loss = spread.width * 100.0 * max(1, int(spread.num_spreads)) - (
                entry_credit * 100.0 * max(1, int(spread.num_spreads))
            )
            credit_loss_pct = (
                (current_spread_value - entry_credit) / (spread.width - entry_credit)
                if (spread.width - entry_credit) > 0
                else 0.0
            )
            credit_floor_hit = credit_loss_pct >= floor_pct and pnl < 0
            if credit_conviction_floor_suppressed:
                credit_floor_hit = False
            loss_gate_hit = credit_loss_pct >= max(0.0, min_loss_pct)
            equity_cap_hit = cap_dollars > 0 and loss_dollars >= cap_dollars and loss_gate_hit
            credit_tail_cap_allowed = True
            if (
                is_credit_spread
                and credit_theta_first_mode
                and bool(getattr(config, "VASS_CREDIT_THETA_FIRST_TAIL_CAP_ONLY_EMERGENCY", True))
            ):
                emergency_dte_max = int(
                    getattr(config, "VASS_CREDIT_THETA_FIRST_TAIL_CAP_EMERGENCY_DTE_MAX", 14)
                )
                emergency_loss_pct = float(
                    getattr(config, "VASS_CREDIT_THETA_FIRST_TAIL_CAP_EMERGENCY_LOSS_PCT", 0.70)
                )
                emergency_by_hold = bool(credit_theta_hold_guard_active)
                emergency_by_dte = current_dte > 0 and current_dte <= emergency_dte_max
                emergency_by_loss = credit_loss_pct >= max(0.0, emergency_loss_pct)
                credit_tail_cap_allowed = emergency_by_hold or emergency_by_dte or emergency_by_loss
                if not credit_tail_cap_allowed:
                    if self.algorithm is not None:
                        self.algorithm._diag_vass_credit_tail_cap_suppressed = (
                            int(
                                getattr(self.algorithm, "_diag_vass_credit_tail_cap_suppressed", 0)
                                or 0
                            )
                            + 1
                        )
                    if not bool(getattr(spread, "_credit_tail_cap_suppressed_logged", False)):
                        spread._credit_tail_cap_suppressed_logged = True
                        self.log(
                            "TAIL_CAP_SUPPRESSED_CREDIT_THETA: "
                            f"Key={self._build_spread_key(spread)} | "
                            f"LossPct={credit_loss_pct:.1%} | DTE={current_dte} | "
                            f"HoldGuard={bool(credit_theta_hold_guard_active)}",
                            trades_only=True,
                        )
            if credit_tail_cap_allowed and (equity_cap_hit or credit_floor_hit):
                if hasattr(self.algorithm, "_diag_vass_tail_cap_exits"):
                    self.algorithm._diag_vass_tail_cap_exits = (
                        int(getattr(self.algorithm, "_diag_vass_tail_cap_exits", 0) or 0) + 1
                    )
                if credit_floor_hit and not equity_cap_hit:
                    exit_reason = (
                        f"VASS_CONVICTION_FLOOR: CreditLoss={credit_loss_pct:.1%} >= "
                        f"Floor={floor_pct:.0%} ({vass_profile_tag})"
                    )
                else:
                    exit_reason = f"VASS_TAIL_RISK_CAP: Loss=${loss_dollars:.0f} >= Cap=${cap_dollars:.0f} ({cap_pct:.1%} eq)"

        # Exit 1: Credit profit target.
        credit_profit_target_pct = float(getattr(config, "CREDIT_SPREAD_PROFIT_TARGET", 0.50))
        if regime_confirmed:
            credit_profit_target_pct = float(
                getattr(
                    config,
                    "VASS_REGIME_CONFIRMED_PROFIT_TARGET_PCT_CREDIT",
                    getattr(
                        config, "VASS_REGIME_CONFIRMED_PROFIT_TARGET_PCT", credit_profit_target_pct
                    ),
                )
            )
        profit_target = spread.max_profit * credit_profit_target_pct
        if profit_target_enabled and pnl >= profit_target and not credit_theta_hold_guard_active:
            if _profit_target_open_delay_active():
                _record_profit_target_open_delay_skip()
            else:
                exit_reason = (
                    f"CREDIT_PROFIT_TARGET +{pnl_pct:.1%} "
                    f"(P&L ${pnl:.2f} >= ${profit_target:.2f})"
                )

        # Exit 2: Credit Stop Loss (actual loss exceeds % of max loss)
        # Max loss = width - credit received
        # V9.1 FIX: Old formula compared raw spread value against max_loss * multiplier,
        # which sat BELOW entry credit → stop fired on every trade at 20-min hold expiry.
        # Correct formula: stop fires when spread value exceeds entry_credit + (max_loss * multiplier),
        # meaning the trade must actually LOSE multiplier% of max_loss before stopping.
        max_loss = spread.width - entry_credit
        stop_mode = str(getattr(config, "CREDIT_SPREAD_STOP_MODE", "LEGACY") or "LEGACY").upper()
        stop_exit_code = "CREDIT_STOP_LOSS"
        stop_threshold = 0.0
        stop_details = ""
        if stop_mode == "TWO_X_CREDIT":
            stop_mult_2x = float(getattr(config, "CREDIT_SPREAD_STOP_2X_MULTIPLIER", 2.0))
            stop_threshold = max(entry_credit, entry_credit * stop_mult_2x)
            stop_exit_code = "CREDIT_STOP_2X"
            stop_details = f"Mode=TWO_X_CREDIT Mult={stop_mult_2x:.2f}"
        else:
            credit_stop_mult = float(getattr(config, "CREDIT_SPREAD_STOP_MULTIPLIER", 0.35))
            if bool(getattr(config, "CREDIT_SPREAD_TIERED_STOP_ENABLED", True)):
                if vass_tier == "LOW":
                    credit_stop_mult = float(
                        getattr(config, "CREDIT_SPREAD_STOP_MULT_LOW_VIX", credit_stop_mult)
                    )
                elif vass_tier == "HIGH":
                    credit_stop_mult = float(
                        getattr(config, "CREDIT_SPREAD_STOP_MULT_HIGH_VIX", credit_stop_mult)
                    )
                else:
                    credit_stop_mult = float(
                        getattr(config, "CREDIT_SPREAD_STOP_MULT_MED_VIX", credit_stop_mult)
                    )
            stop_threshold = entry_credit + max_loss * credit_stop_mult
            stop_details = f"Mode=LEGACY Mult={credit_stop_mult:.2f}"
        if (
            exit_reason is None
            and mark_stop_enabled
            and pnl < 0
            and current_spread_value >= stop_threshold
            and not credit_theta_hold_guard_active
        ):
            loss_pct = (current_spread_value - entry_credit) / max_loss if max_loss > 0 else 0
            exit_reason = (
                f"{stop_exit_code} {loss_pct:.1%} "
                f"(spread value ${current_spread_value:.2f} >= ${stop_threshold:.2f}, "
                f"{stop_details}, {vass_profile_tag})"
            )

        # Exit 2A: Short-leg delta breach (credit spreads only).
        # Gamma/assignment risk rises quickly once short strike starts getting tested.
        if (
            exit_reason is None
            and bool(getattr(config, "CREDIT_SPREAD_DELTA_EXIT_ENABLED", True))
            and not credit_theta_hold_guard_active
        ):
            delta_dte_max = int(getattr(config, "CREDIT_SPREAD_DELTA_EXIT_DTE_MAX", 14))
            if current_dte > 0 and current_dte <= delta_dte_max:
                short_delta = abs(float(getattr(spread.short_leg, "delta", 0.0) or 0.0))
                delta_threshold = float(getattr(config, "CREDIT_SPREAD_DELTA_EXIT_THRESHOLD", 0.30))
                if short_delta >= delta_threshold:
                    exit_reason = (
                        f"CREDIT_DELTA_BREACH |Δ|={short_delta:.2f} >= {delta_threshold:.2f} "
                        f"(DTE={current_dte} <= {delta_dte_max}, {vass_profile_tag})"
                    )

        # Exit 2B: Regime deterioration de-risk (only once spread is already losing).
        if (
            exit_reason is None
            and getattr(config, "SPREAD_REGIME_DETERIORATION_EXIT_ENABLED", True)
            and not regime_confirmed
            and not credit_theta_hold_guard_active
        ):
            min_loss_pct = float(getattr(config, "SPREAD_REGIME_DETERIORATION_MIN_LOSS_PCT", -0.15))
            if pnl_pct <= min_loss_pct:
                delta = getattr(config, "SPREAD_REGIME_DETERIORATION_DELTA", 10)
                bull_exit = getattr(config, "SPREAD_REGIME_DETERIORATION_BULL_EXIT", 60)
                bear_exit = getattr(config, "SPREAD_REGIME_DETERIORATION_BEAR_EXIT", 55)
                if is_bullish_spread:
                    required_drop = spread.regime_at_entry - delta
                    if regime_score <= bull_exit and regime_score <= required_drop:
                        exit_reason = (
                            f"REGIME_DETERIORATION: {spread.regime_at_entry:.0f} → {regime_score:.0f} "
                            f"(<= {bull_exit}, drop {delta}+, loss {pnl_pct:.1%})"
                        )
                elif is_bearish_spread:
                    required_rise = spread.regime_at_entry + delta
                    if regime_score >= bear_exit and regime_score >= required_rise:
                        exit_reason = (
                            f"REGIME_IMPROVEMENT: {spread.regime_at_entry:.0f} → {regime_score:.0f} "
                            f"(>= {bear_exit}, rise {delta}+, loss {pnl_pct:.1%})"
                        )

        # Exit 3: DTE exit (close by 5 DTE)
        if exit_reason is None and current_dte <= dte_exit_threshold:
            exit_reason = f"DTE_EXIT ({current_dte} DTE <= {dte_exit_threshold})"

        # Exit 4: Phase C staged neutrality de-risk.
        if (
            exit_reason is None
            and neutrality_exit_enabled
            and not regime_confirmed
            and not credit_theta_hold_guard_active
        ):
            neutrality_reason = self._check_neutrality_staged_exit(
                spread=spread,
                regime_score=regime_score,
                pnl_pct=pnl_pct,
            )
            if neutrality_reason:
                exit_reason = neutrality_reason

        # V6.1: Removed Credit Regime reversal exit - legacy logic conflicted with conviction-based entry
        # Credit spreads now exit via: STOP_LOSS, PROFIT_TARGET, DTE_EXIT, NEUTRALITY_EXIT

    else:
        # DEBIT SPREAD P&L: Original logic
        current_spread_value = _resolve_debit_spread_mark_value("ExitCheck")
        raw_tradeable_spread_value = float(long_leg_price) - float(short_leg_price)
        if raw_tradeable_spread_value < 0:
            raw_tradeable_spread_value = 0.0
        width_cap = float(getattr(spread, "width", 0.0) or 0.0)
        if width_cap > 0 and raw_tradeable_spread_value > width_cap:
            raw_tradeable_spread_value = width_cap
        entry_debit = spread.net_debit
        pnl = current_spread_value - entry_debit
        pnl_pct = pnl / entry_debit if entry_debit > 0 else 0
        tradeable_pnl = raw_tradeable_spread_value - entry_debit
        tradeable_pnl_pct = tradeable_pnl / entry_debit if entry_debit > 0 else 0
        trail_eval_pnl_pct = tradeable_pnl_pct if is_bearish_debit_spread else pnl_pct
        mfe_eval_pnl = tradeable_pnl if is_bearish_debit_spread else pnl
        mfe_eval_pnl_pct = tradeable_pnl_pct if is_bearish_debit_spread else pnl_pct

        # V12.25: Thesis-first invalidation for BULL_CALL_DEBIT based on underlying QQQ.
        # Primary invalidation is underlying breach, not option mark noise.
        if (
            exit_reason is None
            and is_bullish_debit_spread
            and bool(getattr(config, "VASS_BULL_DEBIT_QQQ_INVALIDATION_ENABLED", False))
        ):
            entry_underlying = float(getattr(spread, "entry_underlying_price", 0.0) or 0.0)
            current_underlying = float(underlying_price or 0.0)
            if entry_underlying > 0 and current_underlying > 0:
                intraday_pct = float(
                    getattr(config, "VASS_BULL_DEBIT_QQQ_INVALIDATION_INTRADAY_PCT", 0.040)
                )
                close_pct = float(
                    getattr(config, "VASS_BULL_DEBIT_QQQ_INVALIDATION_CLOSE_PCT", 0.035)
                )
                intraday_floor = entry_underlying * (1.0 - max(0.0, intraday_pct))
                close_floor = entry_underlying * (1.0 - max(0.0, close_pct))
                if current_underlying <= intraday_floor:
                    exit_reason = (
                        f"QQQ_INVALIDATION_INTRADAY: QQQ {current_underlying:.2f} <= "
                        f"{intraday_floor:.2f} ({intraday_pct:.1%} below entry {entry_underlying:.2f})"
                    )
                elif self.algorithm is not None:
                    close_time = str(
                        getattr(config, "VASS_BULL_DEBIT_QQQ_INVALIDATION_CLOSE_TIME", "15:45")
                    )
                    close_hour = 15
                    close_minute = 45
                    try:
                        close_hour, close_minute = [int(x) for x in close_time.split(":", 1)]
                    except Exception:
                        pass
                    now = self.algorithm.Time
                    in_close_window = (now.hour > close_hour) or (
                        now.hour == close_hour and now.minute >= close_minute
                    )
                    if in_close_window and current_underlying <= close_floor:
                        exit_reason = (
                            f"QQQ_INVALIDATION_CLOSE: QQQ {current_underlying:.2f} <= "
                            f"{close_floor:.2f} ({close_pct:.1%} below entry {entry_underlying:.2f})"
                        )

        if (
            exit_reason is None
            and thesis_soft_stop_mode
            and self.algorithm is not None
            and entry_debit > 0
        ):
            # V12.27: Thesis-only degradation stop for BULL_CALL_DEBIT.
            # Trigger only after enough thesis time is consumed and probability of
            # breakeven degrades meaningfully versus remaining expected move.
            algo = self.algorithm
            if hasattr(algo, "_diag_vass_thesis_soft_stop_checks"):
                algo._diag_vass_thesis_soft_stop_checks = (
                    int(getattr(algo, "_diag_vass_thesis_soft_stop_checks", 0) or 0) + 1
                )

            now = algo.Time
            held_days = 0.0
            try:
                entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                held_days = max(
                    0.0,
                    (now - entry_dt).total_seconds() / 86400.0,
                )
            except Exception:
                held_days = 0.0

            entry_dte = int(getattr(spread.long_leg, "days_to_expiry", 0) or 0)
            if entry_dte <= 0:
                entry_dte = max(int(current_dte or 0), 1)
            time_used = held_days / max(1.0, float(entry_dte))

            current_underlying = float(underlying_price or 0.0)
            entry_underlying = float(getattr(spread, "entry_underlying_price", 0.0) or 0.0)
            atr_pct = self._resolve_qqq_atr_pct(underlying_price=current_underlying)
            atr_dollars = (
                float(current_underlying) * float(atr_pct)
                if atr_pct is not None and current_underlying > 0
                else 0.0
            )
            atr_move_from_entry = 0.0
            if atr_dollars > 0 and entry_underlying > 0:
                atr_move_from_entry = (current_underlying - entry_underlying) / atr_dollars

            breakeven = float(getattr(spread.long_leg, "strike", 0.0) or 0.0) + float(entry_debit)
            distance_to_breakeven = (
                max(0.0, breakeven - current_underlying) if current_underlying > 0 else float("inf")
            )
            remaining_dte = max(1, int(current_dte))
            min_expected_move_atr = float(
                getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_MIN_EXPECTED_MOVE_ATR", 0.75)
            )
            expected_move = (
                atr_dollars * max(min_expected_move_atr, float(remaining_dte) ** 0.5)
                if atr_dollars > 0
                else 0.0
            )
            if distance_to_breakeven <= 0:
                p_be = 1.0
            elif expected_move <= 0:
                p_be = 0.0
            else:
                p_be = pow(2.718281828, -(distance_to_breakeven / expected_move))

            pbe_base = float(getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_PBE_BASE", 0.12))
            pbe_slope = float(getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_PBE_SLOPE", 0.30))
            pbe_min = float(getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_PBE_MIN", 0.12))
            pbe_max = float(getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_PBE_MAX", 0.45))
            pbe_threshold = pbe_base + pbe_slope * max(0.0, time_used)
            if pbe_max < pbe_min:
                pbe_min, pbe_max = pbe_max, pbe_min
            pbe_threshold = max(pbe_min, min(pbe_max, pbe_threshold))

            min_time_used = float(
                getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_MIN_TIME_USED", 0.35)
            )
            max_pnl_pct = float(
                getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_MAX_PNL_PCT", -0.22)
            )
            max_atr_move = float(
                getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_MAX_ATR_MOVE", -0.75)
            )
            breach = (
                time_used >= min_time_used
                and pnl_pct <= max_pnl_pct
                and atr_move_from_entry <= max_atr_move
                and p_be <= pbe_threshold
            )
            if breach:
                if hasattr(algo, "_diag_vass_thesis_soft_stop_armed"):
                    algo._diag_vass_thesis_soft_stop_armed = (
                        int(getattr(algo, "_diag_vass_thesis_soft_stop_armed", 0) or 0) + 1
                    )
                spread.thesis_soft_stop_streak = (
                    int(getattr(spread, "thesis_soft_stop_streak", 0) or 0) + 1
                )
            else:
                spread.thesis_soft_stop_streak = 0

            required_bars = max(
                1,
                int(getattr(config, "VASS_BULL_DEBIT_THESIS_SOFT_STOP_CONSECUTIVE_BARS", 2) or 1),
            )
            if breach and int(spread.thesis_soft_stop_streak) >= required_bars:
                if hasattr(algo, "_diag_vass_thesis_soft_stop_exits"):
                    algo._diag_vass_thesis_soft_stop_exits = (
                        int(getattr(algo, "_diag_vass_thesis_soft_stop_exits", 0) or 0) + 1
                    )
                exit_reason = (
                    f"THESIS_SOFT_STOP tUsed={time_used:.2f} "
                    f"P&L={pnl_pct:.1%} ATRmv={atr_move_from_entry:.2f} "
                    f"Pbe={p_be:.2f}<={pbe_threshold:.2f} "
                    f"Streak={int(spread.thesis_soft_stop_streak)}/{required_bars}"
                )

        # V10.15: Track MFE relative to max profit for harvesting locks.
        mfe_ratio = mfe_eval_pnl / spread.max_profit if spread.max_profit > 0 else 0.0
        if mfe_ratio > spread.highest_pnl_max_profit_pct:
            spread.highest_pnl_max_profit_pct = mfe_ratio

        if (
            mfe_lock_enabled
            and bool(getattr(config, "VASS_MFE_LOCK_ENABLED", True))
            and (mfe_lock_in_regime_confirmed or not regime_confirmed)
            and spread.max_profit > 0
        ):
            prev_tier = int(getattr(spread, "mfe_lock_tier", 0) or 0)
            t1 = float(getattr(config, "VASS_MFE_T1_TRIGGER", 0.25))
            t2 = float(getattr(config, "VASS_MFE_T2_TRIGGER", 0.45))
            floor_t2_pct = float(vass_exit_profile.get("mfe_t2_floor_pct", 0.15))
            commission_cost = spread.num_spreads * config.SPREAD_COMMISSION_PER_CONTRACT
            commission_per_share = (
                commission_cost / (spread.num_spreads * 100) if spread.num_spreads > 0 else 0.0
            )
            if spread.highest_pnl_max_profit_pct >= t2:
                spread.mfe_lock_tier = max(spread.mfe_lock_tier, 2)
            elif spread.highest_pnl_max_profit_pct >= t1:
                spread.mfe_lock_tier = max(spread.mfe_lock_tier, 1)
            self._record_vass_mfe_diag(spread, prev_tier)

            floor_pnl = None
            if spread.mfe_lock_tier >= 2:
                floor_pnl = spread.max_profit * floor_t2_pct + commission_per_share
            elif spread.mfe_lock_tier >= 1 and not mfe_t1_in_confirmed_disabled:
                floor_pnl = commission_per_share

            if floor_pnl is not None and mfe_eval_pnl <= floor_pnl:
                if self.algorithm is not None and hasattr(
                    self.algorithm, "_diag_vass_mfe_lock_exits"
                ):
                    self.algorithm._diag_vass_mfe_lock_exits = (
                        int(getattr(self.algorithm, "_diag_vass_mfe_lock_exits", 0) or 0) + 1
                    )
                exit_reason = (
                    f"MFE_LOCK_T{spread.mfe_lock_tier} {mfe_eval_pnl_pct:.1%} "
                    f"(MFE={spread.highest_pnl_max_profit_pct:.1%}, Floor=${floor_pnl:.2f}, {vass_profile_tag})"
                )

        if (
            exit_reason is None
            and bool(getattr(config, "VASS_TAIL_RISK_CAP_ENABLED", True))
            and tail_cap_enabled
            and not thesis_first_mode
            and pnl < 0
            and self.algorithm is not None
        ):
            equity = float(getattr(self.algorithm.Portfolio, "TotalPortfolioValue", 0.0) or 0.0)
            cap_pct = _resolve_vass_tail_cap_pct(current_dte)
            loss_dollars = abs(float(pnl)) * 100.0 * max(1, int(spread.num_spreads))
            cap_dollars = max(0.0, equity * cap_pct)
            # V12.9: Conviction floor — per-trade % loss cap for debit spreads.
            # Debit floor: pnl_pct <= -FLOOR_PCT (pnl_pct is already pnl/entry_debit)
            floor_pct = float(getattr(config, "VASS_TAIL_RISK_CAP_FLOOR_PCT", 0.35))
            min_loss_pct = float(
                getattr(config, "VASS_TAIL_RISK_CAP_DEBIT_MIN_LOSS_PCT", floor_pct)
            )
            debit_floor_hit = pnl_pct <= -floor_pct
            loss_gate_hit = pnl_pct <= -max(0.0, min_loss_pct)
            equity_cap_hit = cap_dollars > 0 and loss_dollars >= cap_dollars and loss_gate_hit
            if equity_cap_hit or debit_floor_hit:
                if hasattr(self.algorithm, "_diag_vass_tail_cap_exits"):
                    self.algorithm._diag_vass_tail_cap_exits = (
                        int(getattr(self.algorithm, "_diag_vass_tail_cap_exits", 0) or 0) + 1
                    )
                if debit_floor_hit and not equity_cap_hit:
                    exit_reason = (
                        f"VASS_CONVICTION_FLOOR: DebitLoss={pnl_pct:.1%} <= "
                        f"-{floor_pct:.0%} ({vass_profile_tag})"
                    )
                else:
                    exit_reason = f"VASS_TAIL_RISK_CAP: Loss=${loss_dollars:.0f} >= Cap=${cap_dollars:.0f} ({cap_pct:.1%} eq)"

        # V10.7: Day-4 EOD decision for debit spreads.
        # Rule: at/after day-4 EOD, close spreads when P&L is above the threshold,
        # keep only deeper losers for additional recovery time (hard stop still active).
        if (
            exit_reason is None
            and day4_eod_exit_enabled
            and not regime_confirmed
            and bool(getattr(config, "VASS_DAY4_EOD_DECISION_ENABLED", False))
            and self.algorithm is not None
        ):
            try:
                entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                held_days = (self.algorithm.Time.date() - entry_dt.date()).days
                decision_days = int(getattr(config, "VASS_DAY4_EOD_MIN_HOLD_DAYS", 4))
                decision_time = str(getattr(config, "VASS_DAY4_EOD_DECISION_TIME", "15:45"))
                decision_hour, decision_minute = [int(x) for x in decision_time.split(":", 1)]
                is_eod_window = self.algorithm.Time.hour > decision_hour or (
                    self.algorithm.Time.hour == decision_hour
                    and self.algorithm.Time.minute >= decision_minute
                )
                if held_days >= decision_days and is_eod_window:
                    close_threshold = float(getattr(config, "VASS_DAY4_EOD_KEEP_IF_PNL_GT", 0.0))
                    if pnl_pct <= close_threshold:
                        exit_reason = (
                            f"DAY4_EOD_CLOSE {pnl_pct:.1%} (<= {close_threshold:.0%}) | "
                            f"Held={held_days}d"
                        )
                    else:
                        spread_key = self._build_spread_key(spread)
                        if spread_key not in self._spread_hold_guard_logged:
                            self._spread_hold_guard_logged.add(spread_key)
                            self.log(
                                f"DAY4_EOD_KEEP: Key={spread_key} | P&L={pnl_pct:.1%} > {close_threshold:.0%} | "
                                f"Held={held_days}d",
                                trades_only=True,
                            )
                        return None
            except Exception:
                pass

        # Exit 1: Profit target.
        if regime_confirmed:
            adaptive_profit_pct = float(
                getattr(
                    config,
                    "VASS_REGIME_CONFIRMED_PROFIT_TARGET_PCT_DEBIT",
                    getattr(
                        config,
                        "VASS_REGIME_CONFIRMED_PROFIT_TARGET_PCT",
                        vass_exit_profile.get("target_pct", config.SPREAD_PROFIT_TARGET_PCT),
                    ),
                )
            )
        else:
            base_profit_pct = float(
                vass_exit_profile.get("target_pct", config.SPREAD_PROFIT_TARGET_PCT)
            )
            profit_multipliers = getattr(
                config, "SPREAD_PROFIT_REGIME_MULTIPLIERS", {75: 1.0, 50: 1.0, 40: 1.0, 0: 1.0}
            )

            # Find applicable multiplier based on regime score
            profit_multiplier = 1.0
            for threshold in sorted(profit_multipliers.keys(), reverse=True):
                if regime_score >= threshold:
                    profit_multiplier = profit_multipliers[threshold]
                    break

            adaptive_profit_pct = base_profit_pct * profit_multiplier

        # V2.16-BT: Commission-aware profit target
        # Require NET profit (after commission) to meet the target, not just gross
        commission_cost = spread.num_spreads * config.SPREAD_COMMISSION_PER_CONTRACT
        raw_profit_target = spread.max_profit * adaptive_profit_pct
        # V6.4 Fix: Convert commission to per-share basis to match pnl units
        # pnl and raw_profit_target are per-share, commission_cost is total dollars
        # Each spread = 100 shares, so divide by (num_spreads * 100)
        commission_per_share = (
            commission_cost / (spread.num_spreads * 100) if spread.num_spreads > 0 else 0
        )
        profit_target = raw_profit_target + commission_per_share
        net_pnl = pnl - commission_per_share
        profit_target_pnl = pnl
        profit_target_net_pnl = net_pnl
        profit_target_pnl_pct = pnl_pct
        if is_bearish_debit_spread:
            profit_target_pnl = raw_tradeable_spread_value - entry_debit
            profit_target_net_pnl = profit_target_pnl - commission_per_share
            profit_target_pnl_pct = profit_target_pnl / entry_debit if entry_debit > 0 else 0
        if profit_target_enabled and profit_target_pnl >= profit_target:
            if _profit_target_open_delay_active():
                _record_profit_target_open_delay_skip()
            else:
                exit_reason = (
                    f"PROFIT_TARGET +{profit_target_pnl_pct:.1%} (Net ${profit_target_net_pnl:.2f} >= ${raw_profit_target:.2f}) | "
                    f"Target {adaptive_profit_pct:.0%} (regime {regime_score:.0f}) | "
                    f"Gross ${profit_target_pnl:.2f} - Commission ${commission_cost:.2f} | {vass_profile_tag}"
                )

        # Exit 1B: TRAILING STOP — lock in gains after reaching activation threshold
        # V9.4: Once spread reaches +X% unrealized, trail stop from high-water mark
        trail_activate_pct = float(vass_exit_profile.get("trail_activate_pct", 0.20))
        trail_offset_pct = float(vass_exit_profile.get("trail_offset_pct", 0.15))
        if exit_reason is None and trail_exit_enabled and trail_eval_pnl_pct > 0:
            # Update high-water mark
            if trail_eval_pnl_pct > spread.highest_pnl_pct:
                spread.highest_pnl_pct = trail_eval_pnl_pct
            # Trail stop activates after reaching activation threshold
            if spread.highest_pnl_pct >= trail_activate_pct:
                trail_stop_level = spread.highest_pnl_pct - trail_offset_pct
                if trail_eval_pnl_pct <= trail_stop_level:
                    exit_reason = (
                        f"TRAIL_STOP {trail_eval_pnl_pct:.1%} "
                        f"(High={spread.highest_pnl_pct:.1%}, Trail={trail_stop_level:.1%}, {vass_profile_tag})"
                    )

        # Exit 2: STOP LOSS
        # Add a hard cap across regimes, then apply adaptive stop logic.
        if exit_reason is None and mark_stop_enabled and pnl_pct < 0:
            width_stop_pct = float(getattr(config, "SPREAD_HARD_STOP_WIDTH_PCT", 0.0))
            if width_stop_pct > 0 and float(getattr(spread, "width", 0.0)) > 0:
                width_loss_cap = float(spread.width) * width_stop_pct
                if pnl <= -width_loss_cap:
                    exit_reason = (
                        f"SPREAD_HARD_STOP_TRIGGERED_WIDTH {pnl:.2f} (loss <= -${width_loss_cap:.2f}, "
                        f"{width_stop_pct:.0%} of width ${float(spread.width):.2f})"
                    )
        if exit_reason is None and mark_stop_enabled and pnl_pct < 0:
            hard_stop_pct = float(
                vass_exit_profile.get(
                    "hard_stop_pct",
                    getattr(config, "SPREAD_HARD_STOP_LOSS_PCT", 0.0),
                )
            )
            if hard_stop_pct > 0 and pnl_pct <= -hard_stop_pct:
                exit_reason = (
                    f"SPREAD_HARD_STOP_TRIGGERED_PCT {pnl_pct:.1%} "
                    f"(lost > {hard_stop_pct:.0%} hard cap, {vass_profile_tag})"
                )
        if (
            exit_reason is None
            and mark_stop_enabled
            and pnl_pct < 0
            and not disable_tactical_exits_for_thesis
        ):
            base_stop_pct = float(vass_exit_profile.get("stop_pct", config.SPREAD_STOP_LOSS_PCT))
            stop_multipliers = getattr(
                config, "SPREAD_STOP_REGIME_MULTIPLIERS", {75: 1.0, 50: 1.0, 40: 1.0, 0: 1.0}
            )
            stop_multiplier = 1.0
            for threshold in sorted(stop_multipliers.keys(), reverse=True):
                if regime_score >= threshold:
                    stop_multiplier = stop_multipliers[threshold]
                    break
            adaptive_stop_pct = base_stop_pct * stop_multiplier
            hard_cap_pct = float(
                vass_exit_profile.get(
                    "hard_stop_pct",
                    getattr(config, "SPREAD_HARD_STOP_LOSS_PCT", 0.0),
                )
            )
            if hard_cap_pct > 0:
                adaptive_stop_pct = min(adaptive_stop_pct, hard_cap_pct)
            if pnl_pct < -adaptive_stop_pct:
                exit_reason = f"STOP_LOSS {pnl_pct:.1%} (lost > {adaptive_stop_pct:.0%} of entry, {vass_profile_tag})"

        # Exit 3: Time stop for debit spreads (hold window cap).
        if (
            exit_reason is None
            and self.algorithm is not None
            and not disable_tactical_exits_for_thesis
        ):
            max_hold_days = int(getattr(config, "VASS_DEBIT_MAX_HOLD_DAYS", 0))
            low_vix_days = int(getattr(config, "VASS_DEBIT_MAX_HOLD_DAYS_LOW_VIX", max_hold_days))
            low_vix_threshold = float(getattr(config, "VASS_DEBIT_LOW_VIX_THRESHOLD", 16.0))
            if (
                vix_current is not None
                and low_vix_days > 0
                and float(vix_current) < low_vix_threshold
            ):
                max_hold_days = (
                    min(max_hold_days, low_vix_days) if max_hold_days > 0 else low_vix_days
                )
            if max_hold_days > 0:
                try:
                    entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
                    held_days = (self.algorithm.Time.date() - entry_dt.date()).days
                    if held_days >= max_hold_days:
                        exit_reason = (
                            f"SPREAD_TIME_STOP ({held_days}d >= {max_hold_days}d max hold)"
                        )
                except Exception:
                    pass

        # Exit 3B: Regime deterioration de-risk (only once spread is already losing).
        if (
            exit_reason is None
            and getattr(config, "SPREAD_REGIME_DETERIORATION_EXIT_ENABLED", True)
            and not disable_tactical_exits_for_thesis
            and not regime_confirmed
        ):
            min_loss_pct = float(getattr(config, "SPREAD_REGIME_DETERIORATION_MIN_LOSS_PCT", -0.15))
            if pnl_pct <= min_loss_pct:
                delta = getattr(config, "SPREAD_REGIME_DETERIORATION_DELTA", 10)
                bull_exit = getattr(config, "SPREAD_REGIME_DETERIORATION_BULL_EXIT", 60)
                bear_exit = getattr(config, "SPREAD_REGIME_DETERIORATION_BEAR_EXIT", 55)
                if is_bullish_spread:
                    required_drop = spread.regime_at_entry - delta
                    if regime_score <= bull_exit and regime_score <= required_drop:
                        exit_reason = (
                            f"REGIME_DETERIORATION: {spread.regime_at_entry:.0f} → {regime_score:.0f} "
                            f"(<= {bull_exit}, drop {delta}+, loss {pnl_pct:.1%})"
                        )
                elif is_bearish_spread:
                    required_rise = spread.regime_at_entry + delta
                    if regime_score >= bear_exit and regime_score >= required_rise:
                        exit_reason = (
                            f"REGIME_IMPROVEMENT: {spread.regime_at_entry:.0f} → {regime_score:.0f} "
                            f"(>= {bear_exit}, rise {delta}+, loss {pnl_pct:.1%})"
                        )

        # Exit 4: DTE exit (close by 5 DTE)
        if exit_reason is None and current_dte <= dte_exit_threshold:
            exit_reason = f"DTE_EXIT ({current_dte} DTE <= {dte_exit_threshold})"

        # Exit 5: Phase C staged neutrality de-risk.
        if exit_reason is None and neutrality_exit_enabled and not regime_confirmed:
            neutrality_reason = self._check_neutrality_staged_exit(
                spread=spread,
                regime_score=regime_score,
                pnl_pct=pnl_pct,
            )
            if neutrality_reason:
                exit_reason = neutrality_reason

        # V6.1: Removed Debit Regime reversal exit - legacy logic conflicted with conviction-based entry
        # Debit spreads now exit via: STOP_LOSS, PROFIT_TARGET, DTE_EXIT, NEUTRALITY_EXIT

    if exit_reason is None:
        return None

    # Any non-neutrality terminal exit clears staged-neutrality memory for this spread.
    if not str(exit_reason).startswith("NEUTRALITY_"):
        self._spread_neutrality_warn_by_key.pop(self._build_spread_key(spread), None)

    spread_key = self._build_spread_key(spread)
    self.log(
        f"SPREAD: EXIT_SIGNAL | Key={spread_key} | {exit_reason} | "
        f"Long=${long_leg_price:.2f} Short=${short_leg_price:.2f} | "
        f"P&L={pnl_pct:.1%}",
        trades_only=True,
    )

    exit_code = "SPREAD_EXIT_UNSPECIFIED"
    try:
        exit_code = str(exit_reason).split(" ", 1)[0].split(":", 1)[0]
    except Exception:
        pass
    catastrophic_lock_minutes = _apply_catastrophic_reentry_lock(exit_code, str(exit_reason))

    # V2.12 Fix #2: Lock the position to prevent duplicate exit signals
    spread.is_closing = True

    # V9.4 P0: Record exit signal time for retry cooldown
    if self.algorithm is not None:
        cooldown_key = self._build_spread_key(spread)
        self._spread_exit_signal_cooldown[cooldown_key] = self.algorithm.Time

    # V2.5 FIX: Return SINGLE exit signal with combo metadata
    # (Same structure as entry, so router creates atomic ComboMarketOrder)
    # Previously returned TWO signals which executed as separate orders!
    return [
        TargetWeight(
            symbol=self._symbol_str(spread.long_leg.symbol),
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=f"SPREAD_EXIT: {exit_reason}",
            requested_quantity=spread.num_spreads,
            metadata={
                "spread_close_short": True,  # Tells router this is an exit
                "spread_type": spread.spread_type,
                "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                "spread_short_leg_quantity": spread.num_spreads,
                "spread_key": self._build_spread_key(spread),
                "spread_width": spread.width,
                "spread_entry_debit": (
                    max(float(spread.net_debit), 0.0) if not is_credit_spread else 0.0
                ),
                "spread_exit_estimated_net_value": float(current_spread_value),
                "spread_exit_code": exit_code,
                "spread_exit_reason": str(exit_reason),
                "spread_exit_profile": vass_profile_tag,
                "spread_exit_emergency": catastrophic_lock_minutes > 0,
                "vass_no_reentry_lock_minutes": catastrophic_lock_minutes,
                "is_credit_spread": is_credit_spread,
                "spread_credit_received": abs(spread.net_debit) if is_credit_spread else 0.0,
                "credit_theta_first_active": bool(credit_theta_first_mode),
                "spread_exit_policy": (
                    "CREDIT_THETA_FIRST_ACTIVE" if credit_theta_first_mode else "LEGACY"
                ),
            },
        ),
    ]


# =========================================================================
# V2.4.1 FRIDAY FIREWALL
# =========================================================================
