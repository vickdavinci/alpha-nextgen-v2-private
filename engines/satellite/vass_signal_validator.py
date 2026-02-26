"""VASS signal validation helpers extracted from options_engine."""

from __future__ import annotations

import config
from engines.satellite.options_primitives import SpreadStrategy
from models.enums import OptionDirection, OptionsMode, Urgency
from models.target_weight import TargetWeight


def check_spread_entry_signal_impl(
    self,
    regime_score: float,
    vix_current: float,
    adx_value: float,
    current_price: float,
    ma200_value: float,
    iv_rank: float,
    current_hour: int,
    current_minute: int,
    current_date: str,
    portfolio_value: float,
    long_leg_contract: Optional[OptionContract] = None,
    short_leg_contract: Optional[OptionContract] = None,
    gap_filter_triggered: bool = False,
    vol_shock_active: bool = False,
    size_multiplier: float = 1.0,
    margin_remaining: Optional[float] = None,
    dte_min: int = None,
    dte_max: int = None,
    is_eod_scan: bool = False,
    direction: Optional[OptionDirection] = None,
    ma50_value: float = 0.0,
    candidate_contracts: Optional[List[OptionContract]] = None,
) -> Optional[TargetWeight]:
    """
    V2.3: Check for debit spread entry signal.

    Debit Spreads have defined risk (max loss = net debit).
    V6.0: Direction is now passed in from conviction resolution (VASS/MICRO).
    Caller (main.py) resolves VASS conviction vs macro before calling.

    Args:
        regime_score: Market regime score (0-100).
        vix_current: Current VIX level.
        adx_value: Current ADX(14) value.
        current_price: Current QQQ price.
        ma200_value: 200-day moving average value.
        iv_rank: IV percentile (0-100).
        current_hour: Current hour (0-23) Eastern.
        current_minute: Current minute (0-59).
        current_date: Current date string.
        portfolio_value: Total portfolio value.
        long_leg_contract: ATM contract for long leg.
        short_leg_contract: OTM contract for short leg.
        gap_filter_triggered: True if gap filter is active.
        vol_shock_active: True if vol shock pause is active.
        size_multiplier: Position size multiplier (default 1.0). V2.3.20: Set to 0.5
            during cold start to reduce risk.
        margin_remaining: Available margin from portfolio router. V2.21: Used for
            pre-submission margin estimation to prevent broker rejections.
        direction: V6.0: Direction from conviction resolution (CALL or PUT).
        ma50_value: Optional 50-day moving average used for bullish debit
            trend blocking during bear transitions.
        candidate_contracts: Optional same-cycle candidate pool for leg-reselection
            recovery when assignment gate rejects initial short leg.

    Returns:
        TargetWeight for spread entry (with short leg in metadata), or None.
    """

    def fail(reason: str) -> Optional[TargetWeight]:
        self.set_last_entry_validation_failure(reason)
        return None

    def fail_quality(detail: str) -> Optional[TargetWeight]:
        self.set_last_entry_validation_failure(f"R_CONTRACT_QUALITY:{detail}")
        return None

    # Reset previous validation reason for this attempt
    self.set_last_entry_validation_failure(None)

    # V2.8: Update IV sensor with current VIX (for smoothing)
    self._iv_sensor.update(vix_current)

    overlay_state = self.get_regime_overlay_state(
        vix_current=vix_current, regime_score=regime_score
    )
    can_swing, swing_reason = self.can_enter_swing(direction=direction, overlay_state=overlay_state)
    if not can_swing:
        return fail(swing_reason)

    day_gap_reason = self._check_vass_direction_day_gap(direction, current_date)
    if day_gap_reason is not None:
        return fail(day_gap_reason)

    if self.has_pending_spread_entry():
        return fail("R_PENDING_SPREAD_ENTRY")

    if self._vass_entry_engine_enabled and self._vass_entry_engine.should_block_for_loss_breaker(
        str(current_date)
    ):
        return fail("R_VASS_LOSS_BREAKER_PAUSE")

    # Scoped daily attempt budget (per spread key), replaces global one-attempt lock.
    attempt_key = f"DEBIT_{direction.value if direction is not None else 'NONE'}"
    if not self._can_attempt_spread_entry(attempt_key):
        return fail("R_ATTEMPT_BUDGET_EXHAUSTED")
    attempt_recorded = False
    if bool(getattr(config, "SPREAD_ATTEMPT_COUNT_ON_VALIDATION_FAILURE", False)):
        self._record_spread_entry_attempt(attempt_key)
        attempt_recorded = True

    # V2.27/O-20: Win Rate Gate - block/scale or monitor-only mode.
    win_rate_scale = self.get_win_rate_scale()
    gate_mode = str(getattr(config, "WIN_RATE_GATE_VASS_EXECUTION_MODE", "enforce")).lower()
    if gate_mode == "monitor_only":
        # Log monitor-only state once per day unless state changed.
        monitor_day = str(self.algorithm.Time.date()) if self.algorithm is not None else "NONE"
        monitor_key = (
            f"{monitor_day}|{self._win_rate_shutoff}|"
            f"{len(self._spread_result_history)}|"
            f"{sum(1 for x in self._spread_result_history if x)}"
        )
        if monitor_key != self._last_win_rate_monitor_log_key:
            self.log(
                f"WIN_RATE_GATE: MONITOR_ONLY | RawScale={win_rate_scale:.0%} | "
                f"Shutoff={self._win_rate_shutoff} | History={self._spread_result_history}",
                trades_only=True,
            )
            self._last_win_rate_monitor_log_key = monitor_key
        win_rate_scale = 1.0
    elif win_rate_scale == 0.0:
        if getattr(config, "VASS_WIN_RATE_HARD_BLOCK", True):
            self.log(
                f"WIN_RATE_GATE: BLOCKED | Shutoff active | "
                f"History={self._spread_result_history}",
                trades_only=True,
            )
            return fail("WIN_RATE_GATE_BLOCK")
        win_rate_scale = float(
            getattr(config, "VASS_WIN_RATE_SHUTOFF_SCALE", config.WIN_RATE_SIZING_MINIMUM)
        )
        self.log(
            f"WIN_RATE_GATE: SHUTOFF_OVERRIDE | Applying minimum scale {win_rate_scale:.0%} | "
            f"History={self._spread_result_history}",
            trades_only=True,
        )

    # V6.10 P4: Margin Pre-Check BEFORE Signal Approval
    # Check if we have sufficient margin for at least 1 spread before proceeding
    margin_check_enabled = getattr(config, "MARGIN_CHECK_BEFORE_SIGNAL", False)
    if margin_check_enabled and margin_remaining is not None:
        spread_width = self._get_dynamic_spread_widths(current_price)["width_target"]
        min_spreads = getattr(config, "MARGIN_PRE_CHECK_MIN_SPREADS", 1)
        buffer_pct = getattr(config, "MARGIN_PRE_CHECK_BUFFER", 0.15)

        # Margin required = width × 100 × num_spreads × (1 + buffer)
        per_contract_margin = self.estimate_spread_margin_per_contract(
            spread_width=spread_width,
            spread_type="DEBIT",
        )
        min_margin_required = per_contract_margin * min_spreads * (1 + buffer_pct)

        if margin_remaining < min_margin_required:
            self.log(
                f"MARGIN_PRE_CHECK: BLOCKED | Available=${margin_remaining:,.0f} | "
                f"Required=${min_margin_required:,.0f} (width=${spread_width} × {min_spreads} × {1+buffer_pct:.0%})",
                trades_only=True,
            )
            return fail("R_MARGIN_PRECHECK")

    # V2.6 Bug #16: Post-trade margin cooldown
    # After closing a spread, broker takes time to settle - wait before new entry
    if self._last_spread_exit_time is not None:
        try:
            from datetime import datetime

            exit_time = datetime.strptime(self._last_spread_exit_time[:19], "%Y-%m-%d %H:%M:%S")
            current_time_dt = datetime.strptime(current_date + " 12:00:00", "%Y-%m-%d %H:%M:%S")
            # Use current_hour/minute if available
            if current_hour is not None and current_minute is not None:
                current_time_dt = current_time_dt.replace(hour=current_hour, minute=current_minute)

            elapsed_minutes = (current_time_dt - exit_time).total_seconds() / 60
            if elapsed_minutes < config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES:
                self.log(
                    f"SPREAD: Entry blocked - margin cooldown | "
                    f"Elapsed={elapsed_minutes:.1f}m < {config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES}m"
                )
                return fail("R_POST_EXIT_MARGIN_COOLDOWN")
            else:
                # Cooldown expired, clear the tracking
                self._last_spread_exit_time = None
        except (ValueError, TypeError):
            # If parsing fails, clear the tracking and proceed
            self._last_spread_exit_time = None

    # V2.9: Check trade limits (Bug #4 fix) - Uses comprehensive counter
    if not self._can_trade_options(OptionsMode.SWING):
        return fail("TRADE_LIMIT_BLOCK")

    # Determine spread direction based on regime
    if regime_score < config.SPREAD_REGIME_CRISIS:
        # Regime < 30: Crisis mode - no spreads, protective puts only
        self.log(
            f"SPREAD: No entry - regime {regime_score:.1f} < {config.SPREAD_REGIME_CRISIS} (crisis mode)"
        )
        return fail("REGIME_CRISIS_BLOCK")

    # V6.0: Direction now passed in from conviction resolution
    # Caller (main.py) has already resolved VASS conviction vs macro direction
    if direction is None:
        self.log("SPREAD: No entry - direction not provided (conviction resolution required)")
        return fail("DIRECTION_MISSING")

    transition_ctx = self._get_regime_transition_context(regime_score=regime_score)
    transition_regime = float(
        max(
            float(regime_score),
            float(transition_ctx.get("transition_score", regime_score) or regime_score),
        )
    )
    base_regime = str(transition_ctx.get("base_regime", "") or "").upper()
    transition_overlay = str(transition_ctx.get("transition_overlay", "") or "").upper()

    # Derive spread type and VIX max from direction
    if direction == OptionDirection.CALL:
        spread_type = "BULL_CALL"
        vix_max = config.SPREAD_VIX_MAX_BULL
    else:
        spread_type = "BEAR_PUT"
        vix_max = config.SPREAD_VIX_MAX_BEAR
    recovery_relax_active = bool(getattr(config, "VASS_RECOVERY_RELAX_ENABLED", True)) and (
        spread_type == "BULL_CALL"
        and (base_regime == "BULLISH" or transition_overlay == "RECOVERY")
    )
    policy_gate, policy_reason = self.evaluate_transition_policy_block(
        engine="VASS",
        direction=direction,
        transition_ctx=transition_ctx,
    )
    if policy_gate:
        gate_to_reason = {
            "VASS_TRANSITION_BLOCK_BULL_ON_DETERIORATION": "R_VASS_BULL_TRANSITION_BLOCK",
            "VASS_TRANSITION_BLOCK_BEAR_ON_RECOVERY": "R_VASS_BEAR_TRANSITION_BLOCK",
            "VASS_TRANSITION_BLOCK_AMBIGUOUS": "R_VASS_TRANSITION_AMBIGUOUS",
            "TRANSITION_HANDOFF_PUT_THROTTLE": "R_VASS_HANDOFF_PUT_THROTTLE",
            "TRANSITION_HANDOFF_CALL_THROTTLE": "R_VASS_HANDOFF_CALL_THROTTLE",
        }
        mapped_reason = gate_to_reason.get(policy_gate, policy_gate)
        self.log(
            f"SPREAD: {spread_type} blocked by transition policy | "
            f"Gate={policy_gate} | {policy_reason} | "
            f"Regime={regime_score:.1f} | Delta={float(transition_ctx.get('delta', 0.0)):+.1f}"
        )
        return fail(mapped_reason)

    # V9.7: Block BEAR_PUT_DEBIT in RISK_ON — 12.5% WR in 2017 (regime was 88% RISK_ON)
    bear_put_risk_on_max = float(getattr(config, "VASS_BEAR_PUT_REGIME_MAX", 0))
    if (
        bear_put_risk_on_max > 0
        and spread_type == "BEAR_PUT"
        and regime_score >= bear_put_risk_on_max
    ):
        self.log(
            f"SPREAD: BEAR_PUT blocked in RISK_ON | "
            f"Regime={regime_score:.1f} >= {bear_put_risk_on_max:.0f}"
        )
        return fail("R_BEAR_PUT_RISK_ON_BLOCK")

    # V9.4 F4: Require minimum regime for BULL spread entries
    bull_regime_min = float(getattr(config, "VASS_BULL_SPREAD_REGIME_MIN", 0))
    if bull_regime_min > 0 and spread_type == "BULL_CALL" and regime_score < bull_regime_min:
        transition_override = (
            bool(getattr(config, "REGIME_TRANSITION_GUARD_ENABLED", True))
            and bool(transition_ctx.get("strong_recovery", False))
            and transition_regime
            >= float(getattr(config, "VASS_BULL_TRANSITION_MIN_REGIME", bull_regime_min))
        )
        if transition_override:
            self.log(
                f"SPREAD: BULL_CALL transition override | "
                f"Regime={transition_regime:.1f} < Floor={bull_regime_min:.0f} | "
                f"Delta={float(transition_ctx.get('delta', 0.0)):+.1f}"
            )
        else:
            self.log(
                f"SPREAD: BULL_CALL blocked by regime floor | "
                f"Regime={regime_score:.1f} < {bull_regime_min:.0f}"
            )
            return fail("R_BULL_REGIME_FLOOR")

    # V9.4 F5: Block BULL spreads when QQQ is below 20MA (trend confirmation)
    if (
        getattr(config, "VASS_BULL_MA20_GATE_ENABLED", False)
        and spread_type == "BULL_CALL"
        and self.algorithm is not None
    ):
        qqq_sma20 = getattr(self.algorithm, "qqq_sma20", None)
        if qqq_sma20 is not None and getattr(qqq_sma20, "IsReady", False):
            sma20_value = float(qqq_sma20.Current.Value)
            ma20_floor = sma20_value
            if recovery_relax_active:
                ma20_tol = float(getattr(config, "VASS_RECOVERY_RELAX_MA20_TOLERANCE_PCT", 0.003))
                ma20_floor = sma20_value * (1.0 - max(0.0, ma20_tol))
            if current_price < ma20_floor:
                self.log(
                    f"SPREAD: BULL_CALL blocked by MA20 gate | "
                    f"QQQ={current_price:.2f} < MA20 floor={ma20_floor:.2f} "
                    f"(MA20={sma20_value:.2f})"
                )
                return fail("R_BULL_MA20_GATE")

    # Bear hardening: block bullish debit spreads when short-term trend is down.
    if (
        spread_type == "BULL_CALL"
        and bool(getattr(config, "VASS_BULL_CALL_MA50_BLOCK_ENABLED", True))
        and ma50_value > 0
        and current_price < ma50_value
        and regime_score < float(getattr(config, "VASS_BULL_CALL_MA50_BLOCK_REGIME_MAX", 60.0))
    ):
        self.log(
            f"SPREAD: BULL_CALL blocked by MA50 trend gate | "
            f"QQQ={current_price:.2f} < MA50={ma50_value:.2f} | "
            f"Regime={regime_score:.1f}"
        )
        return fail("E_BULL_CALL_MA50_REGIME_BLOCK")

    # V6.19: Stress override for BULL_CALL_DEBIT to mitigate regime lag during corrections.
    # Rule:
    # - Hard block when VIX is already elevated, or when VIX is elevated + accelerating.
    # - In early-stress zone, keep participation but reduce size to preserve optionality.
    if spread_type == "BULL_CALL":
        if overlay_state == "STRESS":
            self.log(
                f"SPREAD: BULL_CALL blocked by overlay | "
                f"Overlay={overlay_state} | VIX={vix_current:.1f} | Regime={regime_score:.1f}"
            )
            return fail("E_OVERLAY_STRESS_BULL_BLOCK")
        vix_5d_change = (
            self._iv_sensor.get_vix_5d_change() if self._iv_sensor.is_conviction_ready() else None
        )
        hard_vix = float(getattr(config, "BULL_CALL_STRESS_BLOCK_VIX", 22.0))
        accel_vix = float(getattr(config, "BULL_CALL_STRESS_ACCEL_VIX", 18.0))
        accel_5d = float(getattr(config, "BULL_CALL_STRESS_ACCEL_5D", 0.20))
        early_low = float(getattr(config, "BULL_CALL_EARLY_STRESS_VIX_LOW", 16.0))
        early_high = float(getattr(config, "BULL_CALL_EARLY_STRESS_VIX_HIGH", 18.0))
        early_size = float(getattr(config, "BULL_CALL_EARLY_STRESS_SIZE", 0.50))

        hard_block = vix_current >= hard_vix
        accel_block = (
            vix_current >= accel_vix and vix_5d_change is not None and vix_5d_change >= accel_5d
        )
        if hard_block or accel_block:
            reason = (
                f"VIX={vix_current:.1f} >= {hard_vix:.1f}"
                if hard_block
                else f"VIX={vix_current:.1f} >= {accel_vix:.1f} and VIX5d={vix_5d_change:+.1%} >= {accel_5d:.1%}"
            )
            self.log(f"SPREAD: BULL_CALL stress blocked | {reason}")
            return fail("BULL_CALL_STRESS_BLOCK")

        if early_low <= vix_current < early_high:
            adjusted = min(size_multiplier, early_size)
            if adjusted < size_multiplier:
                self.log(
                    f"SPREAD: BULL_CALL early-stress size reduction | "
                    f"VIX={vix_current:.1f} in [{early_low:.1f},{early_high:.1f}) | "
                    f"Size {size_multiplier:.0%}->{adjusted:.0%}"
                )
                size_multiplier = adjusted

    # V6.4: Pre-entry assignment risk gate for BEAR_PUT spreads
    # Block entry if short PUT strike is too close to ATM or ITM
    if (
        spread_type == "BEAR_PUT"
        and config.BEAR_PUT_ENTRY_GATE_ENABLED
        and short_leg_contract is not None
        and current_price > 0
    ):
        (
            enforce_assignment_gate,
            min_otm_pct,
            gate_profile,
        ) = self._resolve_put_assignment_gate_profile(
            overlay_state=overlay_state,
            vix_current=vix_current,
            regime_score=regime_score,
        )
        if enforce_assignment_gate:
            short_strike = short_leg_contract.strike
            # For PUTs: OTM when strike < price, ITM when strike > price
            # Calculate how far OTM the short strike is (negative = ITM)
            otm_pct = (current_price - short_strike) / current_price
            if otm_pct < min_otm_pct:
                replacement_short = None
                if bool(getattr(config, "BEAR_PUT_ASSIGNMENT_RESELECT_ENABLED", True)):
                    replacement_short = self._find_safer_bear_put_short_leg(
                        contracts=candidate_contracts,
                        long_leg_contract=long_leg_contract,
                        current_price=current_price,
                        min_otm_pct=min_otm_pct,
                    )
                if replacement_short is not None and str(replacement_short.symbol) != str(
                    short_leg_contract.symbol
                ):
                    old_short = short_leg_contract
                    short_leg_contract = replacement_short
                    new_otm_pct = (current_price - short_leg_contract.strike) / current_price
                    self.log(
                        f"SPREAD: BEAR_PUT assignment reselect | "
                        f"Old={old_short.strike:.0f} ({otm_pct:.1%}) -> "
                        f"New={short_leg_contract.strike:.0f} ({new_otm_pct:.1%}) | "
                        f"Min={min_otm_pct:.1%} | Profile={gate_profile}",
                        trades_only=True,
                    )
                else:
                    self.log(
                        f"SPREAD: Entry blocked - BEAR_PUT assignment risk | "
                        f"Short strike {short_strike:.0f} is {otm_pct:.1%} OTM "
                        f"(min {min_otm_pct:.1%}) | "
                        f"QQQ={current_price:.2f}"
                    )
                    return fail(f"BEAR_PUT_ASSIGNMENT_GATE_{gate_profile}")

            # Re-validate OTM requirement after optional reselect.
            short_strike = short_leg_contract.strike
            otm_pct = (current_price - short_strike) / current_price
            if otm_pct < min_otm_pct:
                self.log(
                    f"SPREAD: Entry blocked - BEAR_PUT assignment risk | "
                    f"Short strike {short_strike:.0f} is {otm_pct:.1%} OTM "
                    f"(min {min_otm_pct:.1%}) | "
                    f"QQQ={current_price:.2f}"
                )
                return fail(f"BEAR_PUT_ASSIGNMENT_GATE_{gate_profile}")

    # VIX filter
    if vix_current > vix_max:
        self.log(f"SPREAD: No entry - VIX {vix_current:.1f} > max {vix_max} for {spread_type}")
        return fail("VIX_MAX_BLOCK")

    # V6.14 OPT: Avoid long PUT debit spreads at panic highs and reduce size in elevated fear.
    if spread_type == "BEAR_PUT":
        put_entry_vix_max = getattr(config, "PUT_ENTRY_VIX_MAX", 36.0)
        if vix_current > put_entry_vix_max:
            self.log(
                f"SPREAD: BEAR_PUT blocked - VIX {vix_current:.1f} > max {put_entry_vix_max:.1f}"
            )
            return fail("PUT_ENTRY_VIX_MAX_BLOCK")
        put_reduce_start = getattr(config, "PUT_SIZE_REDUCTION_VIX_START", 30.0)
        put_reduce_factor = getattr(config, "PUT_SIZE_REDUCTION_FACTOR", 0.50)
        if vix_current >= put_reduce_start:
            size_multiplier *= put_reduce_factor
            self.log(
                f"SPREAD: BEAR_PUT size reduced in high VIX | "
                f"VIX={vix_current:.1f} >= {put_reduce_start:.1f} | "
                f"Multiplier={size_multiplier:.2f}",
                trades_only=True,
            )

    # Check safeguards
    if gap_filter_triggered:
        self.log("SPREAD: Entry blocked - gap filter active")
        return fail("GAP_FILTER_BLOCK")

    if vol_shock_active:
        self.log("SPREAD: Entry blocked - vol shock active")
        return fail("VOL_SHOCK_BLOCK")

    # Check time window (10:00 AM - 2:30 PM ET)
    # V3.0: EOD scan at 15:45 bypasses time window — chain is valid at EOD
    time_minutes = current_hour * 60 + current_minute
    if not is_eod_scan and not (10 * 60 <= time_minutes <= 14 * 60 + 30):
        if not self._swing_time_warning_logged:
            self.log("SPREAD: Entry blocked - outside time window (10:00-14:30)")
            self._swing_time_warning_logged = True
        return fail("TIME_WINDOW_BLOCK")

    # Validate contracts
    if long_leg_contract is None or short_leg_contract is None:
        self.log("SPREAD: Entry blocked - missing contract legs")
        return fail_quality("MISSING_SPREAD_LEGS")

    now_dt = self._parse_dt(current_date, current_hour, current_minute)
    signature = self._build_vass_signature(
        spread_type=spread_type,
        direction=direction,
        long_leg_contract=long_leg_contract,
    )
    expiry_bucket = str(getattr(long_leg_contract, "expiry", "") or "").strip()
    if not expiry_bucket:
        expiry_bucket = f"DTE:{int(getattr(long_leg_contract, 'days_to_expiry', -1))}"
    expiry_block_reason = self._check_expiry_concentration_cap(
        expiry_bucket=expiry_bucket,
        direction=direction,
        regime_score=regime_score,
        vix_current=vix_current,
    )
    if expiry_block_reason:
        return fail(expiry_block_reason)
    similar_block_reason = self._check_vass_similar_entry_guard(signature, now_dt)
    if similar_block_reason:
        return fail(similar_block_reason)

    # Validate contract directions match spread type
    if long_leg_contract.direction != direction:
        self.log(
            f"SPREAD: Entry blocked - long leg direction {long_leg_contract.direction.value} "
            f"doesn't match spread type {spread_type}"
        )
        return fail_quality("LONG_LEG_DIRECTION_MISMATCH")

    if short_leg_contract.direction != direction:
        self.log(
            f"SPREAD: Entry blocked - short leg direction {short_leg_contract.direction.value} "
            f"doesn't match spread type {spread_type}"
        )
        return fail_quality("SHORT_LEG_DIRECTION_MISMATCH")

    if (
        spread_type == "BULL_CALL"
        and bool(getattr(config, "VASS_BULL_SHORT_CALL_DISTANCE_GUARD_ENABLED", True))
        and current_price > 0
    ):
        short_otm_pct = (float(short_leg_contract.strike) - float(current_price)) / float(
            current_price
        )
        base_min_otm_pct = max(
            0.0, float(getattr(config, "VASS_BULL_SHORT_CALL_MIN_OTM_PCT", 0.008))
        )
        atr_floor_pct = 0.0
        atr_pct = self._resolve_qqq_atr_pct(underlying_price=current_price)
        if atr_pct is not None and atr_pct > 0:
            atr_mult = max(0.0, float(getattr(config, "VASS_BULL_SHORT_CALL_MIN_ATR_MULT", 0.60)))
            atr_floor_pct = float(atr_pct) * atr_mult
        min_short_otm_pct = max(base_min_otm_pct, atr_floor_pct)
        if short_otm_pct < min_short_otm_pct:
            self.log(
                f"SPREAD: Entry blocked - short CALL too close to spot | "
                f"ShortOTM={short_otm_pct:.2%} < Min={min_short_otm_pct:.2%} | "
                f"Short={float(short_leg_contract.strike):.2f} Spot={float(current_price):.2f} "
                f"(ATR%={atr_pct:.2%})"
                if atr_pct is not None
                else f"SPREAD: Entry blocked - short CALL too close to spot | "
                f"ShortOTM={short_otm_pct:.2%} < Min={min_short_otm_pct:.2%} | "
                f"Short={float(short_leg_contract.strike):.2f} Spot={float(current_price):.2f}"
            )
            return fail_quality("SHORT_CALL_DISTANCE_TOO_TIGHT")

    # Validate DTE range — use VASS-aware bounds if provided
    effective_dte_min = dte_min if dte_min is not None else config.SPREAD_DTE_MIN
    effective_dte_max = dte_max if dte_max is not None else config.SPREAD_DTE_MAX
    if long_leg_contract.days_to_expiry < effective_dte_min:
        self.log(
            f"SPREAD: Entry blocked - DTE {long_leg_contract.days_to_expiry} < "
            f"min {effective_dte_min}"
        )
        return fail_quality("DTE_BELOW_MIN")

    if long_leg_contract.days_to_expiry > effective_dte_max:
        self.log(
            f"SPREAD: Entry blocked - DTE {long_leg_contract.days_to_expiry} > "
            f"max {effective_dte_max}"
        )
        return fail_quality("DTE_ABOVE_MAX")

    # V2.6 Bug #4: Validate short leg DTE matches long leg (within 1 day tolerance)
    dte_diff = abs(long_leg_contract.days_to_expiry - short_leg_contract.days_to_expiry)
    if dte_diff > 1:
        self.log(
            f"SPREAD: Entry blocked - DTE mismatch | "
            f"Long={long_leg_contract.days_to_expiry} Short={short_leg_contract.days_to_expiry} | "
            f"Diff={dte_diff} > 1 day"
        )
        return fail_quality("DTE_LONG_SHORT_MISMATCH")

    # V2.6 Bug #9: Re-validate delta bounds before entry (delta can drift after selection)
    # This is a defensive check - legs were already filtered during selection
    long_delta_abs = abs(long_leg_contract.delta) if long_leg_contract.delta else 0
    short_delta_abs = abs(short_leg_contract.delta) if short_leg_contract.delta else 0

    # Direction-specific delta + liquidity thresholds (PUTs are looser)
    if spread_type == "BEAR_PUT":
        long_delta_min = config.SPREAD_LONG_LEG_DELTA_MIN_PUT
        long_delta_max = config.SPREAD_LONG_LEG_DELTA_MAX_PUT
        short_delta_min = config.SPREAD_SHORT_LEG_DELTA_MIN_PUT
        short_delta_max = config.SPREAD_SHORT_LEG_DELTA_MAX_PUT
        min_oi = config.OPTIONS_MIN_OPEN_INTEREST_PUT
        spread_max = config.OPTIONS_SPREAD_MAX_PCT_PUT
        spread_warn = config.OPTIONS_SPREAD_WARNING_PCT_PUT
    else:
        long_delta_min = config.SPREAD_LONG_LEG_DELTA_MIN
        long_delta_max = config.SPREAD_LONG_LEG_DELTA_MAX
        short_delta_min = config.SPREAD_SHORT_LEG_DELTA_MIN
        short_delta_max = config.SPREAD_SHORT_LEG_DELTA_MAX
        min_oi = config.OPTIONS_MIN_OPEN_INTEREST
        spread_max = config.OPTIONS_SPREAD_MAX_PCT
        spread_warn = config.OPTIONS_SPREAD_WARNING_PCT

    if long_delta_abs < long_delta_min:
        self.log(
            f"SPREAD: Entry blocked - long leg delta drift | "
            f"Delta={long_delta_abs:.2f} < min {long_delta_min}"
        )
        return fail_quality("LONG_DELTA_BELOW_MIN")

    if long_delta_abs > long_delta_max:
        self.log(
            f"SPREAD: Entry blocked - long leg delta drift | "
            f"Delta={long_delta_abs:.2f} > max {long_delta_max}"
        )
        return fail_quality("LONG_DELTA_ABOVE_MAX")

    # Short leg delta validation (only if not using width-based selection)
    if not config.SPREAD_SHORT_LEG_BY_WIDTH:
        if short_delta_abs < short_delta_min:
            self.log(
                f"SPREAD: Entry blocked - short leg delta drift | "
                f"Delta={short_delta_abs:.2f} < min {short_delta_min}"
            )
            return fail_quality("SHORT_DELTA_BELOW_MIN")

        if short_delta_abs > short_delta_max:
            self.log(
                f"SPREAD: Entry blocked - short leg delta drift | "
                f"Delta={short_delta_abs:.2f} > max {short_delta_max}"
            )
            return fail_quality("SHORT_DELTA_ABOVE_MAX")

    # P0: Bull debit structure must carry minimum net directional delta.
    if spread_type == "BULL_CALL":
        net_delta = max(0.0, long_delta_abs - short_delta_abs)
        min_net_delta = float(getattr(config, "VASS_BULL_DEBIT_NET_DELTA_MIN", 0.0))
        if min_net_delta > 0 and net_delta < min_net_delta:
            self.log(
                f"SPREAD: Entry blocked - net delta too low | "
                f"NetDelta={net_delta:.2f} < Min={min_net_delta:.2f} | "
                f"LongDelta={long_delta_abs:.2f} ShortDelta={short_delta_abs:.2f}",
                trades_only=True,
            )
            return fail_quality("BULL_NET_DELTA_TOO_LOW")

    # V2.3.8: Calculate spread width and enforce VIX-adaptive minimum width.
    # V12.12: dynamic width scaling from percentage-of-underlying.
    width = abs(short_leg_contract.strike - long_leg_contract.strike)
    dyn_widths = self._get_dynamic_spread_widths(current_price)
    effective_width_min = self._get_effective_spread_width_min(
        vix_current=vix_current, current_price=current_price
    )
    dyn_width_max = dyn_widths["width_max"]
    if width < effective_width_min or width > dyn_width_max:
        self.log(
            f"SPREAD: Entry blocked - width ${width:.0f} outside "
            f"${effective_width_min:.0f}-${dyn_width_max:.0f}",
            trades_only=True,
        )
        return fail_quality("WIDTH_OUT_OF_RANGE")

    # Calculate entry score
    entry_score = self.calculate_entry_score(
        adx_value=adx_value,
        current_price=current_price,
        ma200_value=ma200_value,
        iv_rank=iv_rank,
        bid_ask_spread_pct=long_leg_contract.spread_pct,
        open_interest=long_leg_contract.open_interest,
        min_open_interest=min_oi,
        spread_max_pct=spread_max,
        spread_warn_pct=spread_warn,
        iv_profile="DEBIT",
    )

    if not entry_score.is_valid:
        self.log(
            f"SPREAD: Entry blocked - score {entry_score.total:.2f} < "
            f"{config.OPTIONS_ENTRY_SCORE_MIN}"
        )
        return fail_quality("ENTRY_SCORE_BELOW_MIN")

    # Calculate net debit and max profit
    # V2.14 Fix #22: Use conservative pricing (ASK/BID) to prevent tier cap violations
    # Evidence: Trade #20 sized at mid $2.75 but filled at $3.96 (44% slippage)
    # For debit spreads: BUY long (pay ASK), SELL short (receive BID)
    conservative_long = (
        long_leg_contract.ask if long_leg_contract.ask > 0 else long_leg_contract.mid_price
    )
    conservative_short = (
        short_leg_contract.bid if short_leg_contract.bid > 0 else short_leg_contract.mid_price
    )
    conservative_net_debit = conservative_long - conservative_short

    # Apply slippage buffer for worst-case sizing
    slippage_buffer = getattr(config, "SPREAD_SIZING_SLIPPAGE_BUFFER", 0.10)
    net_debit_for_sizing = conservative_net_debit * (1 + slippage_buffer)

    # Log conservative sizing calculation
    self.log(
        f"SPREAD_SIZE: Conservative=${net_debit_for_sizing:.2f} | "
        f"LongASK=${conservative_long:.2f} ShortBID=${conservative_short:.2f} | "
        f"Buffer={slippage_buffer:.0%}"
    )

    # Use mid price for max profit calculation (actual fill determines P&L)
    net_debit = long_leg_contract.mid_price - short_leg_contract.mid_price
    if net_debit <= 0:
        self.log(f"SPREAD: Entry blocked - net debit ${net_debit:.2f} <= 0")
        return fail_quality("NET_DEBIT_NON_POSITIVE")

    max_profit = width - net_debit
    if max_profit <= 0:
        self.log(f"SPREAD: Entry blocked - max profit ${max_profit:.2f} <= 0")
        return fail_quality("MAX_PROFIT_NON_POSITIVE")

    if bool(getattr(config, "VASS_GREEKS_ENTRY_GATE_ENABLED", False)):
        long_theta = float(getattr(long_leg_contract, "theta", 0.0) or 0.0)
        short_theta = float(getattr(short_leg_contract, "theta", 0.0) or 0.0)
        long_vega = float(getattr(long_leg_contract, "vega", 0.0) or 0.0)
        short_vega = float(getattr(short_leg_contract, "vega", 0.0) or 0.0)
        net_theta = long_theta - short_theta
        net_vega = long_vega - short_vega
        theta_burn_ratio = abs(min(0.0, net_theta)) / max(net_debit, 1e-6)
        vega_ratio = abs(net_vega) / max(net_debit, 1e-6)
        max_theta_ratio = float(getattr(config, "VASS_DEBIT_MAX_THETA_TO_DEBIT", 1.0))
        max_vega_ratio = float(getattr(config, "VASS_DEBIT_MAX_VEGA_TO_DEBIT", 99.0))
        if theta_burn_ratio > max_theta_ratio:
            self.log(
                f"SPREAD: Entry blocked - THETA_BURN_TO_DEBIT {theta_burn_ratio:.1%} > "
                f"{max_theta_ratio:.0%} | NetTheta={net_theta:.4f} Debit={net_debit:.2f}",
                trades_only=True,
            )
            return fail_quality("THETA_BURN_TOO_HIGH")
        if vega_ratio > max_vega_ratio:
            self.log(
                f"SPREAD: Entry blocked - VEGA_TO_DEBIT {vega_ratio:.1%} > "
                f"{max_vega_ratio:.0%} | NetVega={net_vega:.4f} Debit={net_debit:.2f}",
                trades_only=True,
            )
            return fail_quality("VEGA_EXPOSURE_TOO_HIGH")

    # V10.16: Adaptive debit-to-width quality cap by current VIX regime.
    min_debit_pct = float(getattr(config, "SPREAD_MIN_DEBIT_TO_WIDTH_PCT", 0.28))
    max_debit_pct = self._get_spread_debit_width_cap(vix_current)
    if recovery_relax_active:
        relaxed_cap_max = float(getattr(config, "VASS_RECOVERY_RELAX_MAX_DW_CAP", 0.55))
        relaxed_cap_bump = float(getattr(config, "VASS_RECOVERY_RELAX_DW_CAP_BUMP", 0.09))
        max_debit_pct = min(relaxed_cap_max, max_debit_pct + max(0.0, relaxed_cap_bump))

    debit_to_width = net_debit / width if width > 0 else 1.0
    if bool(getattr(config, "VASS_POP_GATE_ENABLED", False)):
        # PoP proxy at breakeven: interpolate between long- and short-leg ITM probabilities.
        pop_proxy = long_delta_abs - debit_to_width * max(0.0, long_delta_abs - short_delta_abs)
        pop_proxy = max(0.0, min(1.0, pop_proxy))
        min_pop = float(getattr(config, "VASS_POP_MIN_DEBIT", 0.0))
        if pop_proxy < min_pop:
            self.log(
                f"SPREAD: Entry blocked - PoP proxy {pop_proxy:.1%} < {min_pop:.0%} | "
                f"LongDelta={long_delta_abs:.2f} ShortDelta={short_delta_abs:.2f} "
                f"Debit/Width={debit_to_width:.1%}",
                trades_only=True,
            )
            return fail_quality("POP_BELOW_MIN")

    abs_cap_vix = float(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_VIX", 15.0))
    abs_cap_scaled = self._get_spread_absolute_debit_cap(vix_current, width)
    dynamic_abs_cap = bool(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_DYNAMIC_ENABLED", False))
    apply_all_vix = bool(getattr(config, "SPREAD_DW_ABSOLUTE_CAP_APPLY_ALL_VIX", False))
    should_apply_abs_cap = (dynamic_abs_cap and apply_all_vix) or (
        vix_current is not None and float(vix_current) < abs_cap_vix
    )
    if should_apply_abs_cap and net_debit > abs_cap_scaled:
        vix_label = f"{float(vix_current):.1f}" if vix_current is not None else "NA"
        self.log(
            f"SPREAD: Entry blocked - ABS_DEBIT_CAP ${net_debit:.2f} > ${abs_cap_scaled:.2f} | "
            f"VIX={vix_label} | Width=${width:.0f}",
            trades_only=True,
        )
        return fail_quality("DEBIT_ABSOLUTE_CAP_EXCEEDED")

    if debit_to_width > max_debit_pct:
        self.log(
            f"SPREAD: Entry blocked - DEBIT_TO_WIDTH {debit_to_width:.1%} > {max_debit_pct:.0%} | "
            f"Debit=${net_debit:.2f} Width=${width:.0f}",
            trades_only=True,
        )
        return fail_quality("DEBIT_TO_WIDTH_TOO_HIGH")
    if debit_to_width < min_debit_pct:
        self.log(
            f"SPREAD: Entry blocked - DEBIT_TO_WIDTH {debit_to_width:.1%} < {min_debit_pct:.0%} | "
            f"Debit=${net_debit:.2f} Width=${width:.0f}",
            trades_only=True,
        )
        return fail_quality("DEBIT_TO_WIDTH_TOO_LOW")

    # Production friction gate: entry friction should not consume too much of
    # expected target profit.
    if bool(getattr(config, "SPREAD_ENTRY_FRICTION_GATE_ENABLED", True)):
        entry_friction = max(0.0, conservative_net_debit - net_debit)
        base_profit_pct = float(getattr(config, "SPREAD_PROFIT_TARGET_PCT", 0.50))
        profit_multipliers = getattr(
            config, "SPREAD_PROFIT_REGIME_MULTIPLIERS", {75: 1.0, 50: 1.0, 40: 1.0, 0: 1.0}
        )
        profit_multiplier = 1.0
        for threshold in sorted(profit_multipliers.keys(), reverse=True):
            if regime_score >= threshold:
                profit_multiplier = float(profit_multipliers[threshold])
                break
        adaptive_profit_pct = base_profit_pct * profit_multiplier
        expected_target_profit = max_profit * adaptive_profit_pct
        if expected_target_profit > 0:
            friction_to_target = entry_friction / expected_target_profit
            friction_cap = float(getattr(config, "SPREAD_ENTRY_FRICTION_TO_TARGET_MAX", 0.35))
            if friction_to_target > friction_cap:
                self.log(
                    f"SPREAD: Entry blocked - FRICTION_TO_TARGET {friction_to_target:.1%} > "
                    f"{friction_cap:.0%} | Friction=${entry_friction:.2f} "
                    f"TargetProfit=${expected_target_profit:.2f}",
                    trades_only=True,
                )
                return fail_quality("FRICTION_TO_TARGET_TOO_HIGH")

    if bool(getattr(config, "SPREAD_ENTRY_COMMISSION_GATE_ENABLED", False)):
        max_profit_dollars = max_profit * 100.0
        commission_per_spread = float(getattr(config, "SPREAD_COMMISSION_PER_CONTRACT", 2.60))
        ratio_limit = float(getattr(config, "SPREAD_MAX_COMMISSION_TO_MAX_PROFIT_RATIO", 0.15))
        fee_to_profit_ratio = (
            commission_per_spread / max_profit_dollars if max_profit_dollars > 0 else 1.0
        )
        if fee_to_profit_ratio > ratio_limit:
            self.log(
                f"SPREAD: Entry blocked - COMMISSION_TO_MAX_PROFIT {fee_to_profit_ratio:.1%} "
                f"> {ratio_limit:.0%} | MaxProfit=${max_profit_dollars:.2f} "
                f"Commission=${commission_per_spread:.2f}",
                trades_only=True,
            )
            return fail_quality("COMMISSION_TO_MAX_PROFIT_TOO_HIGH")

    # V12.14: Budget-proportional VASS sizing (universal for debit + credit)
    portfolio_value = (
        float(portfolio_value)
        if portfolio_value and portfolio_value > 0
        else (self.algorithm.Portfolio.TotalPortfolioValue if self.algorithm else 50000)
    )
    vass_budget = portfolio_value * float(getattr(config, "OPTIONS_SWING_ALLOCATION", 0.35))
    deploy_pct = float(getattr(config, "VASS_DEPLOY_PCT_OF_BUDGET", 0.40))
    swing_max_dollars = vass_budget * deploy_pct

    # Bucket constraint — respect total VASS capital already deployed
    remaining_vass = self._get_bucket_remaining_dollars("VASS", float(portfolio_value))
    swing_max_dollars = min(swing_max_dollars, remaining_vass)
    if swing_max_dollars <= 0:
        self.log("SPREAD: Entry blocked - VASS bucket exhausted", trades_only=True)
        return fail("R_BUCKET_VASS_EXHAUSTED")
    # V2.14: Use conservative net debit for sizing (prevents tier cap violations)
    cost_per_spread = net_debit_for_sizing * 100  # 100 shares per contract
    num_spreads = int(swing_max_dollars / cost_per_spread)
    self.log(
        f"SIZING: SWING | Cap=${swing_max_dollars:,.0f} (budget=${vass_budget:,.0f} x{deploy_pct:.0%}) | "
        f"Cost/spread=${cost_per_spread:.2f} | Qty={num_spreads}"
    )

    # V12.14: Unified R:R modulation — better quality = more contracts (debit: lower D/W = better)
    if num_spreads > 0 and bool(getattr(config, "VASS_RR_SCALING_ENABLED", False)):
        floor_scale = float(getattr(config, "VASS_RR_FLOOR_SCALE", 0.60))
        ref = float(getattr(config, "VASS_RR_DEBIT_REFERENCE_DW", 0.35))
        worst = float(getattr(config, "VASS_RR_DEBIT_WORST_DW", 0.48))
        if debit_to_width <= ref:
            rr_scale = 1.0
        elif debit_to_width >= worst:
            rr_scale = floor_scale
        else:
            rr_scale = 1.0 - (1.0 - floor_scale) * (debit_to_width - ref) / (worst - ref)
        rr_adjusted = max(1, int(num_spreads * rr_scale))
        if rr_adjusted != num_spreads:
            self.log(
                f"SIZING: RR_SCALE | D/W={debit_to_width:.1%} | Scale={rr_scale:.2f} | "
                f"{num_spreads} -> {rr_adjusted} spreads",
                trades_only=True,
            )
            num_spreads = rr_adjusted

    # V2.27: Apply win rate gate scaling to contract count
    if win_rate_scale < 1.0:
        scaled = max(1, int(num_spreads * win_rate_scale))
        self.log(
            f"WIN_RATE_GATE: REDUCED | Scale={win_rate_scale:.0%} | "
            f"{num_spreads} -> {scaled} spreads",
            trades_only=True,
        )
        num_spreads = scaled

    # V6.0: Apply cold start multiplier (macro gate removed - conviction handles direction)
    if size_multiplier < 1.0:
        min_size = getattr(config, "OPTIONS_MIN_COMBINED_SIZE_PCT", 0.10)
        if size_multiplier < min_size:
            self.log(
                f"SPREAD: Entry blocked - cold start size {size_multiplier:.0%} < min {min_size:.0%}"
            )
            return fail("COLD_START_BELOW_MIN")

        scaled = max(1, int(num_spreads * size_multiplier))
        self.log(
            f"SPREAD: Sizing reduced | {num_spreads} -> {scaled} spreads | "
            f"SizeMult={size_multiplier:.0%}",
            trades_only=True,
        )
        num_spreads = scaled

    # V6.10 P5: Choppy market size reduction
    choppy_scale = self.get_choppy_market_scale()
    if choppy_scale < 1.0 and num_spreads > 1:
        choppy_adjusted = max(1, int(num_spreads * choppy_scale))
        self.log(
            f"SPREAD: Choppy market reduction | {num_spreads} -> {choppy_adjusted} spreads | "
            f"ChoppyScale={choppy_scale:.0%}",
            trades_only=True,
        )
        num_spreads = choppy_adjusted

    # V2.21 Layer 1: Pre-submission margin estimation
    # Scale num_spreads down to fit within available margin
    if margin_remaining is not None and margin_remaining > 0 and width > 0:
        safety_factor = getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80)
        usable_margin = self.get_usable_margin(margin_remaining)
        if self._rejection_margin_cap is not None:
            self.log(
                f"SIZING: Rejection cap active | Cap=${self._rejection_margin_cap:,.0f} | "
                f"Usable=${usable_margin:,.0f}",
                trades_only=True,
            )

        estimated_margin_per_spread = self.estimate_spread_margin_per_contract(
            spread_width=width,
            spread_type=spread_type,
        )
        if estimated_margin_per_spread > 0:
            max_by_margin = int(usable_margin / estimated_margin_per_spread)
            if max_by_margin < num_spreads:
                self.log(
                    f"SIZING: MARGIN_SCALE | {num_spreads} -> {max_by_margin} spreads | "
                    f"Margin=${margin_remaining:,.0f} x{safety_factor:.0%}=${usable_margin:,.0f} | "
                    f"Per-spread=${estimated_margin_per_spread:,.0f}",
                    trades_only=True,
                )
                num_spreads = max_by_margin

    # V2.21: Floor at MIN_SPREAD_CONTRACTS — skip without consuming daily attempt
    min_contracts = getattr(config, "MIN_SPREAD_CONTRACTS", 2)
    if 0 < num_spreads < min_contracts:
        self.log(
            f"SPREAD: Entry skipped — {num_spreads} < min {min_contracts} | "
            f"MARGIN_SCALE_BELOW_MIN_CONTRACTS",
            trades_only=True,
        )
        return fail("MARGIN_SCALE_BELOW_MIN_CONTRACTS")  # Preserve explicit reason

    if num_spreads <= 0:
        self.log(
            f"SPREAD: Entry blocked - cap ${swing_max_dollars} too small "
            f"for debit ${net_debit:.2f}"
        )
        return fail("NUM_SPREADS_NON_POSITIVE")

    # V2.12 Fix #3: Enforce SPREAD_MAX_CONTRACTS hard cap
    # Evidence from V2.11: Position accumulated to 80 contracts (5× intended)
    # This cap prevents runaway position accumulation from exit signal bugs
    hard_cap = int(getattr(config, "SPREAD_MAX_CONTRACTS_HARD_CAP", config.SPREAD_MAX_CONTRACTS))
    if num_spreads > hard_cap:
        self.log(f"SPREAD_LIMIT: Capped contracts | Requested={num_spreads} > Max={hard_cap}")
        num_spreads = hard_cap

    # V6.0 Fix: Assignment-aware sizing using spread width
    # Max loss on spread = width * 100 * contracts (NOT underlying * 100 * contracts)
    if self.algorithm:
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
    else:
        portfolio_value = 50000  # Default for testing

    assignment_multiplier = self.get_assignment_aware_size_multiplier(
        spread_width=width,  # V6.0: Use spread width, not underlying price
        portfolio_value=portfolio_value,
        requested_contracts=num_spreads,
    )
    if assignment_multiplier < 1.0:
        adjusted_contracts = max(1, int(num_spreads * assignment_multiplier))
        if adjusted_contracts < num_spreads:
            num_spreads = adjusted_contracts

    # Store pending spread entry details
    self._pending_spread_long_leg = long_leg_contract
    self._pending_spread_short_leg = short_leg_contract
    self._pending_spread_type = spread_type
    self._pending_net_debit = net_debit
    self._pending_max_profit = max_profit
    self._pending_spread_width = width
    self._pending_spread_entry_vix = float(vix_current) if vix_current is not None else None
    self._pending_spread_entry_since = self.algorithm.Time if self.algorithm is not None else None
    self._pending_num_contracts = num_spreads
    self._pending_entry_score = entry_score.total

    # Backward compatibility: only count success when pre-validation counting is disabled.
    if not attempt_recorded:
        self._record_spread_entry_attempt(attempt_key)

    reason = (
        f"{spread_type}: Regime={regime_score:.0f} | VIX={vix_current:.1f} | "
        f"Long={long_leg_contract.strike} Short={short_leg_contract.strike} | "
        f"Debit=${net_debit:.2f} MaxProfit=${max_profit:.2f} | x{num_spreads}"
    )

    self.log(
        f"SPREAD: ENTRY_SIGNAL | {reason} | "
        f"DTE={long_leg_contract.days_to_expiry} Score={entry_score.total:.2f}",
        trades_only=True,
    )

    # Return TargetWeight for long leg, with short leg info in metadata
    # V2.4.1 FIX: Use actual allocation value, not 1.0
    return TargetWeight(
        symbol=self._symbol_str(long_leg_contract.symbol),
        target_weight=config.OPTIONS_SWING_ALLOCATION,  # V2.4.1: Actual allocation
        source="OPT",
        urgency=Urgency.IMMEDIATE,
        reason=reason,
        requested_quantity=num_spreads,
        metadata={
            "spread_type": spread_type,
            "spread_short_leg_symbol": self._symbol_str(short_leg_contract.symbol),
            "spread_short_leg_quantity": num_spreads,
            "vass_signature_key": signature,
            "spread_net_debit": net_debit,
            "spread_cost_or_credit": net_debit,
            "spread_max_profit": max_profit,
            "spread_width": width,
            # V2.8: VASS metadata
            "vass_iv_environment": self._iv_sensor.classify()
            if self._iv_sensor.is_ready()
            else "MEDIUM",
            "vass_smoothed_vix": self._iv_sensor.get_smoothed_vix(),
            "vass_strategy": "BULL_CALL_DEBIT" if spread_type == "BULL_CALL" else "BEAR_PUT_DEBIT",
            # V2.19: Store prices for router lookup (_get_current_prices fix)
            "contract_price": long_leg_contract.mid_price,
            "short_leg_price": short_leg_contract.mid_price,
        },
    )


def check_credit_spread_entry_signal_impl(
    self,
    regime_score: float,
    vix_current: float,
    adx_value: float,
    current_price: float,
    ma200_value: float,
    iv_rank: float,
    current_hour: int,
    current_minute: int,
    current_date: str,
    portfolio_value: float,
    short_leg_contract: Optional[OptionContract] = None,
    long_leg_contract: Optional[OptionContract] = None,
    strategy: Optional[SpreadStrategy] = None,
    gap_filter_triggered: bool = False,
    vol_shock_active: bool = False,
    size_multiplier: float = 1.0,
    margin_remaining: Optional[float] = None,
    is_eod_scan: bool = False,
    direction: Optional[OptionDirection] = None,
) -> Optional[TargetWeight]:
    """
    V2.23: Check for credit spread entry signal.

    Credit spreads collect premium upfront and profit from time decay.
    Selected by VASS when IV environment is HIGH (VIX > 25).

    Strategy Matrix:
    - HIGH IV + BULLISH → Bull Put Credit (sell OTM put, buy further OTM put)
    - HIGH IV + BEARISH → Bear Call Credit (sell OTM call, buy further OTM call)

    Sizing is based on MAX LOSS (width - credit), not premium received.
    This method mirrors check_spread_entry_signal() validation gates but
    uses _calculate_credit_spread_size() for margin-based sizing.

    Args:
        regime_score: Market regime score (0-100).
        vix_current: Current VIX level.
        adx_value: Current ADX(14) value.
        current_price: Current QQQ price.
        ma200_value: 200-day moving average value.
        iv_rank: IV percentile (0-100).
        current_hour: Current hour (0-23) Eastern.
        current_minute: Current minute (0-59).
        current_date: Current date string.
        portfolio_value: Total portfolio value.
        short_leg_contract: Contract we SELL (collect premium).
        long_leg_contract: Contract we BUY (protection).
        strategy: SpreadStrategy (BULL_PUT_CREDIT or BEAR_CALL_CREDIT).
        gap_filter_triggered: True if gap filter is active.
        vol_shock_active: True if vol shock pause is active.
        size_multiplier: Position size multiplier (default 1.0).
        margin_remaining: Available margin from portfolio router.
        direction: V6.0: Direction from conviction resolution (CALL or PUT).

    Returns:
        TargetWeight for credit spread entry, or None.
    """

    def fail(reason: str) -> Optional[TargetWeight]:
        self.set_last_entry_validation_failure(reason)
        return None

    def fail_quality(detail: str) -> Optional[TargetWeight]:
        self.set_last_entry_validation_failure(f"R_CONTRACT_QUALITY:{detail}")
        return None

    # Reset previous validation reason for this attempt
    self.set_last_entry_validation_failure(None)

    # V2.8: Update IV sensor with current VIX (for smoothing)
    self._iv_sensor.update(vix_current)

    overlay_state = self.get_regime_overlay_state(
        vix_current=vix_current, regime_score=regime_score
    )
    can_swing, swing_reason = self.can_enter_swing(direction=direction, overlay_state=overlay_state)
    if not can_swing:
        return fail(swing_reason)

    day_gap_reason = self._check_vass_direction_day_gap(direction, current_date)
    if day_gap_reason is not None:
        return fail(day_gap_reason)

    if self.has_pending_spread_entry():
        return fail("R_PENDING_SPREAD_ENTRY")

    if self._vass_entry_engine_enabled and self._vass_entry_engine.should_block_for_loss_breaker(
        str(current_date)
    ):
        return fail("R_VASS_LOSS_BREAKER_PAUSE")

    # Scoped daily attempt budget (strategy-specific), replaces global one-attempt lock.
    attempt_key = f"CREDIT_{strategy.value if strategy is not None else 'NONE'}"
    if not self._can_attempt_spread_entry(attempt_key):
        return fail("R_ATTEMPT_BUDGET_EXHAUSTED")
    attempt_recorded = False
    if bool(getattr(config, "SPREAD_ATTEMPT_COUNT_ON_VALIDATION_FAILURE", False)):
        self._record_spread_entry_attempt(attempt_key)
        attempt_recorded = True

    # V2.27/O-20: Win Rate Gate - block/scale or monitor-only mode.
    win_rate_scale = self.get_win_rate_scale()
    gate_mode = str(getattr(config, "WIN_RATE_GATE_VASS_EXECUTION_MODE", "enforce")).lower()
    if gate_mode == "monitor_only":
        self.log(
            f"WIN_RATE_GATE: CREDIT MONITOR_ONLY | RawScale={win_rate_scale:.0%} | "
            f"Shutoff={self._win_rate_shutoff} | History={self._spread_result_history}",
            trades_only=True,
        )
        win_rate_scale = 1.0
    elif win_rate_scale == 0.0:
        if getattr(config, "VASS_WIN_RATE_HARD_BLOCK", True):
            self.log(
                f"WIN_RATE_GATE: CREDIT BLOCKED | Shutoff active | "
                f"History={self._spread_result_history}",
                trades_only=True,
            )
            return fail("WIN_RATE_GATE_BLOCK")
        win_rate_scale = float(
            getattr(config, "VASS_WIN_RATE_SHUTOFF_SCALE", config.WIN_RATE_SIZING_MINIMUM)
        )
        self.log(
            f"WIN_RATE_GATE: CREDIT SHUTOFF_OVERRIDE | Applying minimum scale {win_rate_scale:.0%} | "
            f"History={self._spread_result_history}",
            trades_only=True,
        )

    # V6.10 P4: Margin Pre-Check BEFORE Signal Approval
    # Check if we have sufficient margin for at least 1 spread before proceeding
    margin_check_enabled = getattr(config, "MARGIN_CHECK_BEFORE_SIGNAL", False)
    if margin_check_enabled and margin_remaining is not None:
        # Credit spreads use dynamic credit width target
        spread_width = self._get_dynamic_spread_widths(current_price)["credit_width_target"]
        min_spreads = getattr(config, "MARGIN_PRE_CHECK_MIN_SPREADS", 1)
        buffer_pct = getattr(config, "MARGIN_PRE_CHECK_BUFFER", 0.15)

        # Margin required = width × 100 × num_spreads × (1 + buffer)
        per_contract_margin = self.estimate_spread_margin_per_contract(
            spread_width=spread_width,
            spread_type="CREDIT",
            credit_received=None,
        )
        min_margin_required = per_contract_margin * min_spreads * (1 + buffer_pct)

        if margin_remaining < min_margin_required:
            self.log(
                f"MARGIN_PRE_CHECK: CREDIT BLOCKED | Available=${margin_remaining:,.0f} | "
                f"Required=${min_margin_required:,.0f} (width=${spread_width} × {min_spreads} × {1+buffer_pct:.0%})",
                trades_only=True,
            )
            return fail("R_MARGIN_PRECHECK")

    # Post-trade margin cooldown
    if self._last_spread_exit_time is not None:
        try:
            from datetime import datetime

            exit_time = datetime.strptime(self._last_spread_exit_time[:19], "%Y-%m-%d %H:%M:%S")
            current_time_dt = datetime.strptime(current_date + " 12:00:00", "%Y-%m-%d %H:%M:%S")
            if current_hour is not None and current_minute is not None:
                current_time_dt = current_time_dt.replace(hour=current_hour, minute=current_minute)

            elapsed_minutes = (current_time_dt - exit_time).total_seconds() / 60
            if elapsed_minutes < config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES:
                self.log(
                    f"CREDIT_SPREAD: Entry blocked - margin cooldown | "
                    f"Elapsed={elapsed_minutes:.1f}m < {config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES}m"
                )
                return fail("R_POST_EXIT_MARGIN_COOLDOWN")
            else:
                self._last_spread_exit_time = None
        except (ValueError, TypeError):
            self._last_spread_exit_time = None

    # Check trade limits
    if not self._can_trade_options(OptionsMode.SWING):
        return fail("TRADE_LIMIT_BLOCK")

    # Regime crisis check
    if regime_score < config.SPREAD_REGIME_CRISIS:
        self.log(
            f"CREDIT_SPREAD: No entry - regime {regime_score:.1f} < "
            f"{config.SPREAD_REGIME_CRISIS} (crisis mode)"
        )
        return fail("REGIME_CRISIS_BLOCK")

    # V6.0: Direction now passed in from conviction resolution
    # Caller (main.py) has already resolved VASS conviction vs macro direction
    if direction is None:
        self.log(
            "CREDIT_SPREAD: No entry - direction not provided (conviction resolution required)"
        )
        return fail("DIRECTION_MISSING")

    transition_ctx = self._get_regime_transition_context(regime_score=regime_score)
    transition_regime = float(
        max(
            float(regime_score),
            float(transition_ctx.get("transition_score", regime_score) or regime_score),
        )
    )
    if (
        bool(getattr(config, "REGIME_TRANSITION_GUARD_ENABLED", True))
        and bool(getattr(config, "VASS_TRANSITION_BLOCK_AMBIGUOUS", True))
        and bool(transition_ctx.get("ambiguous", False))
    ):
        self.log(
            f"CREDIT_SPREAD: blocked in ambiguous transition zone | "
            f"Regime={regime_score:.1f} | Delta={float(transition_ctx.get('delta', 0.0)):+.1f}"
        )
        return fail("R_VASS_TRANSITION_AMBIGUOUS")

    if overlay_state == "STRESS" and strategy == SpreadStrategy.BULL_PUT_CREDIT:
        self.log(
            f"CREDIT_SPREAD: BULL_PUT_CREDIT blocked by overlay | "
            f"Overlay={overlay_state} | VIX={vix_current:.1f} | Regime={regime_score:.1f}"
        )
        return fail("E_OVERLAY_STRESS_BULL_BLOCK")

    # Check safeguards
    if gap_filter_triggered:
        self.log("CREDIT_SPREAD: Entry blocked - gap filter active")
        return fail("GAP_FILTER_BLOCK")

    if vol_shock_active:
        self.log("CREDIT_SPREAD: Entry blocked - vol shock active")
        return fail("VOL_SHOCK_BLOCK")

    # Check time window (10:00 AM - 2:30 PM ET)
    # V3.0: EOD scan at 15:45 bypasses time window — chain is valid at EOD
    time_minutes = current_hour * 60 + current_minute
    if not is_eod_scan and not (10 * 60 <= time_minutes <= 14 * 60 + 30):
        return fail("TIME_WINDOW_BLOCK")

    # Validate contracts
    if short_leg_contract is None or long_leg_contract is None:
        self.log("CREDIT_SPREAD: Entry blocked - missing contract legs")
        return fail_quality("MISSING_SPREAD_LEGS")

    # Validate strategy
    if strategy is None or not self.is_credit_strategy(strategy):
        self.log(f"CREDIT_SPREAD: Entry blocked - invalid strategy {strategy}")
        return fail_quality("INVALID_CREDIT_STRATEGY")

    # Determine spread type from strategy
    spread_type = strategy.value  # "BULL_PUT_CREDIT" or "BEAR_CALL_CREDIT"
    transition_bias = (
        OptionDirection.CALL if strategy == SpreadStrategy.BULL_PUT_CREDIT else OptionDirection.PUT
    )
    policy_gate, policy_reason = self.evaluate_transition_policy_block(
        engine="VASS",
        direction=transition_bias,
        transition_ctx=transition_ctx,
    )
    if policy_gate:
        gate_to_reason = {
            "VASS_TRANSITION_BLOCK_BULL_ON_DETERIORATION": "R_VASS_BULL_TRANSITION_BLOCK",
            "VASS_TRANSITION_BLOCK_BEAR_ON_RECOVERY": "R_VASS_BEAR_TRANSITION_BLOCK",
            "VASS_TRANSITION_BLOCK_AMBIGUOUS": "R_VASS_TRANSITION_AMBIGUOUS",
            "TRANSITION_HANDOFF_PUT_THROTTLE": "R_VASS_HANDOFF_PUT_THROTTLE",
            "TRANSITION_HANDOFF_CALL_THROTTLE": "R_VASS_HANDOFF_CALL_THROTTLE",
        }
        mapped_reason = gate_to_reason.get(policy_gate, policy_gate)
        self.log(
            f"CREDIT_SPREAD: {spread_type} blocked by transition policy | "
            f"Gate={policy_gate} | {policy_reason} | "
            f"Regime={regime_score:.1f} | Delta={float(transition_ctx.get('delta', 0.0)):+.1f}"
        )
        return fail(mapped_reason)

    bull_regime_min = float(getattr(config, "VASS_BULL_SPREAD_REGIME_MIN", 0))
    if (
        strategy == SpreadStrategy.BULL_PUT_CREDIT
        and bull_regime_min > 0
        and regime_score < bull_regime_min
    ):
        transition_override = (
            bool(getattr(config, "REGIME_TRANSITION_GUARD_ENABLED", True))
            and bool(transition_ctx.get("strong_recovery", False))
            and transition_regime
            >= float(getattr(config, "VASS_BULL_TRANSITION_MIN_REGIME", bull_regime_min))
        )
        if transition_override:
            self.log(
                f"CREDIT_SPREAD: BULL_PUT transition override | "
                f"Regime={transition_regime:.1f} < Floor={bull_regime_min:.0f} | "
                f"Delta={float(transition_ctx.get('delta', 0.0)):+.1f}"
            )
        else:
            self.log(
                f"CREDIT_SPREAD: BULL_PUT blocked by regime floor | "
                f"Regime={regime_score:.1f} < {bull_regime_min:.0f}"
            )
            return fail("R_BULL_REGIME_FLOOR")

    now_dt = self._parse_dt(current_date, current_hour, current_minute)
    signature = self._build_vass_signature(
        spread_type=spread_type,
        direction=direction,
        long_leg_contract=long_leg_contract,
    )
    expiry_bucket = str(getattr(long_leg_contract, "expiry", "") or "").strip()
    if not expiry_bucket:
        expiry_bucket = f"DTE:{int(getattr(long_leg_contract, 'days_to_expiry', -1))}"
    expiry_block_reason = self._check_expiry_concentration_cap(
        expiry_bucket=expiry_bucket,
        direction=direction,
        regime_score=regime_score,
        vix_current=vix_current,
    )
    if expiry_block_reason:
        return fail(expiry_block_reason)
    similar_block_reason = self._check_vass_similar_entry_guard(signature, now_dt)
    if similar_block_reason:
        return fail(similar_block_reason)

    # V6.4: Pre-entry assignment risk gate for credit spreads with short PUTs
    # BULL_PUT_CREDIT has a short PUT (higher strike) - check assignment risk
    if (
        strategy == SpreadStrategy.BULL_PUT_CREDIT
        and config.BEAR_PUT_ENTRY_GATE_ENABLED
        and short_leg_contract is not None
        and current_price > 0
    ):
        min_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT", 0.03))
        stress_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT_STRESS", min_otm_pct))
        low_vix_threshold = float(getattr(config, "BEAR_PUT_ENTRY_LOW_VIX_THRESHOLD", 18.0))
        relaxed_otm_pct = float(getattr(config, "BEAR_PUT_ENTRY_MIN_OTM_PCT_RELAXED", 0.015))
        relaxed_regime_min = float(getattr(config, "BEAR_PUT_ENTRY_RELAXED_REGIME_MIN", 60.0))
        gate_profile = "BASE"
        if overlay_state in {"STRESS", "EARLY_STRESS"}:
            min_otm_pct = min(min_otm_pct, stress_otm_pct)
            gate_profile = "STRESS"
        if (
            vix_current <= low_vix_threshold
            and regime_score >= relaxed_regime_min
            and gate_profile == "BASE"
        ):
            min_otm_pct = min(min_otm_pct, relaxed_otm_pct)
            gate_profile = "LOW_VIX_RELAXED"
        short_strike = short_leg_contract.strike
        # For short PUTs: OTM when strike < price, ITM when strike > price
        otm_pct = (current_price - short_strike) / current_price
        if otm_pct < min_otm_pct:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - BULL_PUT assignment risk | "
                f"Short strike {short_strike:.0f} is {otm_pct:.1%} OTM "
                f"(min {min_otm_pct:.1%}) | "
                f"QQQ={current_price:.2f}"
            )
            return fail(f"BEAR_PUT_ASSIGNMENT_GATE_{gate_profile}")

    # Calculate width
    width = abs(short_leg_contract.strike - long_leg_contract.strike)
    if width <= 0:
        self.log(f"CREDIT_SPREAD: Entry blocked - invalid width {width}")
        return fail_quality("WIDTH_NON_POSITIVE")

    # Calculate credit received (conservative: bid for sell, ask for buy)
    credit_received = short_leg_contract.bid - long_leg_contract.ask
    min_credit_required = self._get_effective_credit_min(vix_current=vix_current)
    if credit_received < min_credit_required:
        self.log(
            f"CREDIT_SPREAD: Entry blocked - credit ${credit_received:.2f} < "
            f"min ${min_credit_required:.2f}"
        )
        return fail_quality("CREDIT_BELOW_MIN")

    credit_to_width = (credit_received / width) if width > 0 else 0.0
    if bool(getattr(config, "VASS_GREEKS_ENTRY_GATE_ENABLED", False)):
        short_theta = float(getattr(short_leg_contract, "theta", 0.0) or 0.0)
        long_theta = float(getattr(long_leg_contract, "theta", 0.0) or 0.0)
        short_vega = float(getattr(short_leg_contract, "vega", 0.0) or 0.0)
        long_vega = float(getattr(long_leg_contract, "vega", 0.0) or 0.0)
        net_theta = short_theta - long_theta
        net_vega = short_vega - long_vega
        min_net_theta = float(getattr(config, "VASS_CREDIT_MIN_NET_THETA", -999.0))
        max_vega_ratio = float(getattr(config, "VASS_CREDIT_MAX_VEGA_TO_CREDIT", 99.0))
        vega_ratio = abs(net_vega) / max(credit_received, 1e-6)
        if net_theta < min_net_theta:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - NET_THETA {net_theta:.4f} < {min_net_theta:.4f}",
                trades_only=True,
            )
            return fail_quality("NET_THETA_UNFAVORABLE")
        if bool(getattr(config, "VASS_CREDIT_THETA_RATIO_GATE_ENABLED", False)):
            theta_ratio = net_theta / max(credit_received, 1e-6)
            min_theta_ratio = float(getattr(config, "VASS_CREDIT_MIN_NET_THETA_RATIO", -0.03))
            if theta_ratio < min_theta_ratio:
                self.log(
                    f"CREDIT_SPREAD: Entry blocked - THETA_RATIO {theta_ratio:.3f} < "
                    f"{min_theta_ratio:.3f} | NetTheta={net_theta:.4f} Credit={credit_received:.2f}",
                    trades_only=True,
                )
                return fail_quality("THETA_RATIO_UNFAVORABLE")
        if vega_ratio > max_vega_ratio:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - VEGA_TO_CREDIT {vega_ratio:.1%} > "
                f"{max_vega_ratio:.0%} | NetVega={net_vega:.4f} Credit={credit_received:.2f}",
                trades_only=True,
            )
            return fail_quality("VEGA_EXPOSURE_TOO_HIGH")

    if bool(getattr(config, "VASS_POP_GATE_ENABLED", False)):
        short_delta_abs = abs(float(getattr(short_leg_contract, "delta", 0.0) or 0.0))
        # Credit spread PoP proxy: probability short leg stays OTM by expiry.
        pop_proxy = max(0.0, min(1.0, 1.0 - short_delta_abs))
        min_pop = float(getattr(config, "VASS_POP_MIN_CREDIT", 0.0))
        if pop_proxy < min_pop:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - PoP proxy {pop_proxy:.1%} < {min_pop:.0%} | "
                f"ShortDelta={short_delta_abs:.2f}",
                trades_only=True,
            )
            return fail_quality("POP_BELOW_MIN")

    min_credit_to_width = self._get_effective_credit_to_width_min(vix_current=vix_current)
    if credit_to_width < min_credit_to_width:
        self.log(
            f"CREDIT_SPREAD: Entry blocked - CREDIT_TO_WIDTH {credit_to_width:.1%} < {min_credit_to_width:.0%} | "
            f"Credit=${credit_received:.2f} Width=${width:.0f}",
            trades_only=True,
        )
        return fail_quality("CREDIT_TO_WIDTH_TOO_LOW")

    if bool(getattr(config, "SPREAD_ENTRY_COMMISSION_GATE_ENABLED", False)):
        max_profit_dollars = credit_received * 100.0
        commission_per_spread = float(getattr(config, "SPREAD_COMMISSION_PER_CONTRACT", 2.60))
        ratio_limit = float(getattr(config, "SPREAD_MAX_COMMISSION_TO_MAX_PROFIT_RATIO", 0.15))
        fee_to_profit_ratio = (
            commission_per_spread / max_profit_dollars if max_profit_dollars > 0 else 1.0
        )
        if fee_to_profit_ratio > ratio_limit:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - COMMISSION_TO_MAX_PROFIT {fee_to_profit_ratio:.1%} "
                f"> {ratio_limit:.0%} | MaxProfit=${max_profit_dollars:.2f} "
                f"Commission=${commission_per_spread:.2f}",
                trades_only=True,
            )
            return fail_quality("COMMISSION_TO_MAX_PROFIT_TOO_HIGH")

    # Calculate entry score (same scoring as debit)
    entry_score = self.calculate_entry_score(
        adx_value=adx_value,
        current_price=current_price,
        ma200_value=ma200_value,
        iv_rank=iv_rank,
        bid_ask_spread_pct=short_leg_contract.spread_pct,
        open_interest=short_leg_contract.open_interest,
        iv_profile="CREDIT",
    )

    if not entry_score.is_valid:
        self.log(
            f"CREDIT_SPREAD: Entry blocked - score {entry_score.total:.2f} < "
            f"{config.OPTIONS_ENTRY_SCORE_MIN}"
        )
        return fail_quality("ENTRY_SCORE_BELOW_MIN")

    # V12.14: Budget-proportional VASS sizing (universal for debit + credit)
    portfolio_value = (
        float(portfolio_value)
        if portfolio_value and portfolio_value > 0
        else (self.algorithm.Portfolio.TotalPortfolioValue if self.algorithm else 50000)
    )
    vass_budget = portfolio_value * float(getattr(config, "OPTIONS_SWING_ALLOCATION", 0.35))
    deploy_pct = float(getattr(config, "VASS_DEPLOY_PCT_OF_BUDGET", 0.40))
    swing_max_dollars = vass_budget * deploy_pct

    # Bucket constraint — respect total VASS capital already deployed
    remaining_vass = self._get_bucket_remaining_dollars("VASS", float(portfolio_value))
    swing_max_dollars = min(swing_max_dollars, remaining_vass)
    if swing_max_dollars <= 0:
        self.log("CREDIT_SPREAD: Entry blocked - VASS bucket exhausted", trades_only=True)
        return fail("R_BUCKET_VASS_EXHAUSTED")
    num_spreads, _credit_per, _max_loss_per, _total_margin = self._calculate_credit_spread_size(
        short_leg_contract, long_leg_contract, swing_max_dollars
    )

    if num_spreads <= 0:
        return fail("NUM_SPREADS_NON_POSITIVE")

    # V12.14: Unified R:R modulation — better quality = more contracts (credit: higher C/W = better)
    if num_spreads > 0 and bool(getattr(config, "VASS_RR_SCALING_ENABLED", False)):
        floor_scale = float(getattr(config, "VASS_RR_FLOOR_SCALE", 0.60))
        ref = float(getattr(config, "VASS_RR_CREDIT_REFERENCE_CW", 0.40))
        worst = float(getattr(config, "VASS_RR_CREDIT_WORST_CW", 0.30))
        if credit_to_width >= ref:
            rr_scale = 1.0
        elif credit_to_width <= worst:
            rr_scale = floor_scale
        else:
            rr_scale = 1.0 - (1.0 - floor_scale) * (ref - credit_to_width) / (ref - worst)
        rr_adjusted = max(1, int(num_spreads * rr_scale))
        if rr_adjusted != num_spreads:
            self.log(
                f"SIZING: RR_SCALE | C/W={credit_to_width:.1%} | Scale={rr_scale:.2f} | "
                f"{num_spreads} -> {rr_adjusted} spreads",
                trades_only=True,
            )
            num_spreads = rr_adjusted

    # V2.27: Apply win rate gate scaling
    if win_rate_scale < 1.0:
        scaled = max(1, int(num_spreads * win_rate_scale))
        self.log(
            f"WIN_RATE_GATE: CREDIT REDUCED | Scale={win_rate_scale:.0%} | "
            f"{num_spreads} -> {scaled} spreads",
            trades_only=True,
        )
        num_spreads = scaled

    # V6.0: Apply cold start multiplier (macro gate removed - conviction handles direction)
    if size_multiplier < 1.0:
        min_size = getattr(config, "OPTIONS_MIN_COMBINED_SIZE_PCT", 0.10)
        if size_multiplier < min_size:
            self.log(
                f"CREDIT_SPREAD: Entry blocked - cold start size {size_multiplier:.0%} < min {min_size:.0%}"
            )
            return fail("COLD_START_BELOW_MIN")

        scaled = max(1, int(num_spreads * size_multiplier))
        self.log(
            f"CREDIT_SPREAD: Sizing reduced | {num_spreads} -> {scaled} spreads | "
            f"SizeMult={size_multiplier:.0%}",
            trades_only=True,
        )
        num_spreads = scaled

    # V6.10 P5: Choppy market size reduction
    choppy_scale = self.get_choppy_market_scale()
    if choppy_scale < 1.0 and num_spreads > 1:
        choppy_adjusted = max(1, int(num_spreads * choppy_scale))
        self.log(
            f"CREDIT_SPREAD: Choppy market reduction | {num_spreads} -> {choppy_adjusted} spreads | "
            f"ChoppyScale={choppy_scale:.0%}",
            trades_only=True,
        )
        num_spreads = choppy_adjusted

    # V2.21: Pre-submission margin estimation
    if margin_remaining is not None and margin_remaining > 0 and width > 0:
        safety_factor = getattr(config, "SPREAD_MARGIN_SAFETY_FACTOR", 0.80)
        usable_margin = self.get_usable_margin(margin_remaining)

        if self._rejection_margin_cap is not None:
            self.log(
                f"CREDIT_SIZING: Rejection cap active | Cap=${self._rejection_margin_cap:,.0f} | "
                f"Usable=${usable_margin:,.0f}",
                trades_only=True,
            )

        estimated_margin_per_spread = self.estimate_spread_margin_per_contract(
            spread_width=width,
            spread_type=spread_type,
            credit_received=credit_received,
        )
        if estimated_margin_per_spread > 0:
            max_by_margin = int(usable_margin / estimated_margin_per_spread)
            if max_by_margin < num_spreads:
                self.log(
                    f"CREDIT_SIZING: MARGIN_SCALE | {num_spreads} -> {max_by_margin} spreads | "
                    f"Margin=${margin_remaining:,.0f} x{safety_factor:.0%}=${usable_margin:,.0f} | "
                    f"Per-spread=${estimated_margin_per_spread:,.0f}",
                    trades_only=True,
                )
                num_spreads = max_by_margin

    # V10.17: Cap credit spread sizing by theoretical max loss budget.
    max_loss_cap_pct = float(getattr(config, "CREDIT_SPREAD_MAX_LOSS_PCT_EQUITY", 0.0))
    if max_loss_cap_pct > 0 and width > 0:
        max_loss_per_spread = max(0.0, (width - credit_received) * 100.0)
        if max_loss_per_spread > 0:
            max_loss_budget = float(portfolio_value) * max_loss_cap_pct
            max_by_theoretical_loss = int(max_loss_budget / max_loss_per_spread)
            if max_by_theoretical_loss < num_spreads:
                self.log(
                    f"CREDIT_SIZING: MAX_LOSS_CAP | {num_spreads} -> {max_by_theoretical_loss} spreads | "
                    f"Budget=${max_loss_budget:,.0f} ({max_loss_cap_pct:.2%} eq) | "
                    f"PerSpreadMaxLoss=${max_loss_per_spread:,.0f}",
                    trades_only=True,
                )
                num_spreads = max_by_theoretical_loss
            if num_spreads <= 0:
                self.log(
                    "CREDIT_SPREAD: Entry blocked - max-loss budget below one spread",
                    trades_only=True,
                )
                return fail("R_CREDIT_MAX_LOSS_CAP")

    # V2.21: Floor at MIN_SPREAD_CONTRACTS
    min_contracts = getattr(config, "MIN_SPREAD_CONTRACTS", 2)
    if 0 < num_spreads < min_contracts:
        self.log(
            f"CREDIT_SPREAD: Entry skipped - {num_spreads} < min {min_contracts} | "
            f"MARGIN_SCALE_BELOW_MIN_CONTRACTS",
            trades_only=True,
        )
        return fail("MARGIN_SCALE_BELOW_MIN_CONTRACTS")  # Preserve explicit reason

    if num_spreads <= 0:
        self.log(
            f"CREDIT_SPREAD: Entry blocked - cannot size position | "
            f"Width=${width:.2f} Credit=${credit_received:.2f}"
        )
        return fail("NUM_SPREADS_NON_POSITIVE_AFTER_MARGIN")

    # Enforce hard cap
    hard_cap = int(getattr(config, "SPREAD_MAX_CONTRACTS_HARD_CAP", config.SPREAD_MAX_CONTRACTS))
    if num_spreads > hard_cap:
        self.log(f"CREDIT_SPREAD_LIMIT: Capped | Requested={num_spreads} > " f"Max={hard_cap}")
        num_spreads = hard_cap

    # Calculate max profit and max loss for metadata
    max_profit = credit_received * 100 * num_spreads  # Credit × 100 × contracts
    max_loss = (width - credit_received) * 100 * num_spreads

    # Store pending spread entry details
    # NOTE: For credit spreads, the "long leg" in our naming convention is the
    # protection leg (cheaper), and "short leg" is the one we sell (more expensive).
    # We store them matching the debit convention for register_spread_entry compatibility.
    self._pending_spread_long_leg = long_leg_contract
    self._pending_spread_short_leg = short_leg_contract
    self._pending_spread_type = spread_type
    self._pending_net_debit = -credit_received  # Negative = credit received
    self._pending_max_profit = credit_received  # Max profit per spread = credit
    self._pending_spread_width = width
    self._pending_spread_entry_vix = float(vix_current) if vix_current is not None else None
    self._pending_spread_entry_since = self.algorithm.Time if self.algorithm is not None else None
    self._pending_num_contracts = num_spreads
    self._pending_entry_score = entry_score.total

    # Backward compatibility: only count success when pre-validation counting is disabled.
    if not attempt_recorded:
        self._record_spread_entry_attempt(attempt_key)

    reason = (
        f"{spread_type}: Regime={regime_score:.0f} | VIX={vix_current:.1f} | "
        f"Sell {short_leg_contract.strike} Buy {long_leg_contract.strike} | "
        f"Credit=${credit_received:.2f} Width=${width:.0f} | x{num_spreads}"
    )

    self.log(
        f"CREDIT_SPREAD: ENTRY_SIGNAL | {reason} | "
        f"DTE={short_leg_contract.days_to_expiry} Score={entry_score.total:.2f} | "
        f"MaxProfit=${max_profit:.0f} MaxLoss=${max_loss:.0f}",
        trades_only=True,
    )

    # V2.23.1 APVP Fix: Use LONG leg (protection) as primary symbol to match
    # router combo convention. Router expects: primary=BUY leg, metadata=SELL leg.
    # Broker handles credit/debit mechanics through ComboMarketOrder.
    return TargetWeight(
        symbol=self._symbol_str(long_leg_contract.symbol),
        target_weight=config.OPTIONS_SWING_ALLOCATION,
        source="OPT",
        urgency=Urgency.IMMEDIATE,
        reason=reason,
        requested_quantity=num_spreads,  # Positive (matches debit convention)
        metadata={
            "spread_type": spread_type,
            "spread_short_leg_symbol": self._symbol_str(short_leg_contract.symbol),
            "spread_short_leg_quantity": num_spreads,
            "vass_signature_key": signature,
            "spread_net_debit": -credit_received,  # Negative = credit
            "spread_cost_or_credit": credit_received,
            "spread_credit_received": credit_received,
            "spread_max_profit": credit_received,
            "spread_max_loss_per_spread": width - credit_received,
            "spread_width": width,
            "is_credit_spread": True,
            # VASS metadata
            "vass_iv_environment": self._iv_sensor.classify()
            if self._iv_sensor.is_ready()
            else "HIGH",
            "vass_smoothed_vix": self._iv_sensor.get_smoothed_vix(),
            "vass_strategy": strategy.value,
            # Prices for router lookup (long leg = primary)
            "contract_price": long_leg_contract.mid_price,
            "short_leg_price": short_leg_contract.mid_price,
        },
    )
