"""VASS spread exit evaluator extracted from options_engine."""

from __future__ import annotations

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

    vass_exit_profile = self._get_vass_exit_profile(spread=spread, vix_current=vix_current)
    vass_tier = str(vass_exit_profile.get("tier", "MED"))
    vass_ref_vix = vass_exit_profile.get("ref_vix")
    vass_profile_tag = (
        f"Tier={vass_tier}"
        if vass_ref_vix is None
        else f"Tier={vass_tier} RefVIX={float(vass_ref_vix):.1f}"
    )
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
                        current_spread_value = float(long_leg_price) - float(short_leg_price)
                        if current_spread_value < 0:
                            self.log(
                                f"SPREAD_PNL_CLAMP_APPLIED: HoldGuard | Key={self._build_spread_key(spread)} | "
                                f"RawValue=${current_spread_value:.2f} -> $0.00",
                                trades_only=True,
                            )
                            current_spread_value = 0.0
                        pnl = current_spread_value - entry_debit
                        pnl_pct = pnl / entry_debit

                        hard_stop_pct = float(vass_exit_profile.get("hard_stop_pct", 0.0))
                        if hard_stop_pct > 0 and pnl_pct <= -hard_stop_pct:
                            self.log(
                                f"SPREAD_HARD_STOP_DURING_HOLD: {pnl_pct:.1%} <= -{hard_stop_pct:.0%} | "
                                f"Key={self._build_spread_key(spread)} | Held={live_minutes:.0f}m",
                                trades_only=True,
                            )
                            spread.is_closing = True
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
                                        "is_credit_spread": False,
                                        "spread_credit_received": 0.0,
                                    },
                                )
                            ]

                        # EOD risk gate during hold to reduce overnight tail losses.
                        eod_gate_enabled = bool(
                            getattr(config, "SPREAD_EOD_HOLD_RISK_GATE_ENABLED", False)
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

    # V2.8: Determine if credit or debit spread
    is_credit_spread = spread.spread_type in (
        "BULL_PUT_CREDIT",
        "BEAR_CALL_CREDIT",
        SpreadStrategy.BULL_PUT_CREDIT.value,
        SpreadStrategy.BEAR_CALL_CREDIT.value,
    )

    exit_reason = None

    # ---------------------------------------------------------------------
    # P0: VIX Spike Auto-Exit (bullish spreads only)
    # Close CALL spreads if VIX spikes to panic levels or 5d change surges
    # ---------------------------------------------------------------------
    vix_spike_enabled = getattr(config, "SWING_VIX_SPIKE_EXIT_ENABLED", True)
    vix_spike_level = getattr(config, "SWING_VIX_SPIKE_EXIT_LEVEL", 25.0)
    vix_spike_5d = getattr(config, "SWING_VIX_SPIKE_EXIT_5D_PCT", 0.20)
    vix_5d_change = self._iv_sensor.get_vix_5d_change() if self._iv_sensor.is_ready() else None

    is_bullish_spread = spread.spread_type in (
        "BULL_CALL",
        "BULL_PUT_CREDIT",
        SpreadStrategy.BULL_CALL_DEBIT.value,
        SpreadStrategy.BULL_PUT_CREDIT.value,
    )
    is_bullish_debit_spread = spread.spread_type in (
        "BULL_CALL",
        SpreadStrategy.BULL_CALL_DEBIT.value,
    )
    is_bearish_spread = spread.spread_type in (
        "BEAR_PUT",
        "BEAR_CALL_CREDIT",
        SpreadStrategy.BEAR_PUT_DEBIT.value,
        SpreadStrategy.BEAR_CALL_CREDIT.value,
    )

    # V6.22: Transition exit priority - force close wrong-way bullish spreads in STRESS.
    if (
        exit_reason is None
        and bool(getattr(config, "SPREAD_OVERLAY_STRESS_EXIT_ENABLED", False))
        and is_bullish_spread
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

    if vix_spike_enabled and exit_reason is None and is_bullish_spread and vix_current is not None:
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

        if bool(getattr(config, "VASS_MFE_LOCK_ENABLED", True)) and spread.max_profit > 0:
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
            elif spread.mfe_lock_tier >= 1:
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
            and pnl < 0
            and self.algorithm is not None
        ):
            equity = float(getattr(self.algorithm.Portfolio, "TotalPortfolioValue", 0.0) or 0.0)
            cap_pct = float(getattr(config, "VASS_TAIL_RISK_CAP_PCT_EQUITY", 0.015))
            loss_dollars = abs(float(pnl)) * 100.0 * max(1, int(spread.num_spreads))
            cap_dollars = max(0.0, equity * cap_pct)
            if cap_dollars > 0 and loss_dollars >= cap_dollars:
                if hasattr(self.algorithm, "_diag_vass_tail_cap_exits"):
                    self.algorithm._diag_vass_tail_cap_exits = (
                        int(getattr(self.algorithm, "_diag_vass_tail_cap_exits", 0) or 0) + 1
                    )
                exit_reason = f"VASS_TAIL_RISK_CAP: Loss=${loss_dollars:.0f} >= Cap=${cap_dollars:.0f} ({cap_pct:.1%} eq)"

        # Exit 1: Credit Profit Target (50% of max profit)
        profit_target = spread.max_profit * config.CREDIT_SPREAD_PROFIT_TARGET
        if pnl >= profit_target:
            exit_reason = (
                f"CREDIT_PROFIT_TARGET +{pnl_pct:.1%} " f"(P&L ${pnl:.2f} >= ${profit_target:.2f})"
            )

        # Exit 2: Credit Stop Loss (actual loss exceeds % of max loss)
        # Max loss = width - credit received
        # V9.1 FIX: Old formula compared raw spread value against max_loss * multiplier,
        # which sat BELOW entry credit → stop fired on every trade at 20-min hold expiry.
        # Correct formula: stop fires when spread value exceeds entry_credit + (max_loss * multiplier),
        # meaning the trade must actually LOSE multiplier% of max_loss before stopping.
        max_loss = spread.width - entry_credit
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
        if exit_reason is None and pnl < 0 and current_spread_value >= stop_threshold:
            loss_pct = (current_spread_value - entry_credit) / max_loss if max_loss > 0 else 0
            exit_reason = (
                f"CREDIT_STOP_LOSS {loss_pct:.1%} "
                f"(spread value ${current_spread_value:.2f} >= ${stop_threshold:.2f}, "
                f"Mult={credit_stop_mult:.2f}, {vass_profile_tag})"
            )

        # Exit 2B: Regime deterioration de-risk (only once spread is already losing).
        if exit_reason is None and getattr(
            config, "SPREAD_REGIME_DETERIORATION_EXIT_ENABLED", True
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
        if exit_reason is None and current_dte <= config.SPREAD_DTE_EXIT:
            exit_reason = f"DTE_EXIT ({current_dte} DTE <= {config.SPREAD_DTE_EXIT})"

        # Exit 4: Phase C staged neutrality de-risk.
        if exit_reason is None:
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
        current_spread_value = long_leg_price - short_leg_price
        entry_debit = spread.net_debit
        if entry_debit > 0 and current_spread_value < 0:
            self.log(
                f"SPREAD_PNL_CLAMP_APPLIED: ExitCheck | Key={self._build_spread_key(spread)} | "
                f"RawValue=${current_spread_value:.2f} -> $0.00",
                trades_only=True,
            )
            current_spread_value = 0.0
        pnl = current_spread_value - entry_debit
        pnl_pct = pnl / entry_debit if entry_debit > 0 else 0

        # V10.15: Track MFE relative to max profit for harvesting locks.
        mfe_ratio = pnl / spread.max_profit if spread.max_profit > 0 else 0.0
        if mfe_ratio > spread.highest_pnl_max_profit_pct:
            spread.highest_pnl_max_profit_pct = mfe_ratio

        if bool(getattr(config, "VASS_MFE_LOCK_ENABLED", True)) and spread.max_profit > 0:
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
            elif spread.mfe_lock_tier >= 1:
                floor_pnl = commission_per_share

            if floor_pnl is not None and pnl <= floor_pnl:
                if self.algorithm is not None and hasattr(
                    self.algorithm, "_diag_vass_mfe_lock_exits"
                ):
                    self.algorithm._diag_vass_mfe_lock_exits = (
                        int(getattr(self.algorithm, "_diag_vass_mfe_lock_exits", 0) or 0) + 1
                    )
                exit_reason = (
                    f"MFE_LOCK_T{spread.mfe_lock_tier} {pnl_pct:.1%} "
                    f"(MFE={spread.highest_pnl_max_profit_pct:.1%}, Floor=${floor_pnl:.2f}, {vass_profile_tag})"
                )

        if (
            exit_reason is None
            and bool(getattr(config, "VASS_TAIL_RISK_CAP_ENABLED", True))
            and pnl < 0
            and self.algorithm is not None
        ):
            equity = float(getattr(self.algorithm.Portfolio, "TotalPortfolioValue", 0.0) or 0.0)
            cap_pct = float(getattr(config, "VASS_TAIL_RISK_CAP_PCT_EQUITY", 0.015))
            loss_dollars = abs(float(pnl)) * 100.0 * max(1, int(spread.num_spreads))
            cap_dollars = max(0.0, equity * cap_pct)
            if cap_dollars > 0 and loss_dollars >= cap_dollars:
                if hasattr(self.algorithm, "_diag_vass_tail_cap_exits"):
                    self.algorithm._diag_vass_tail_cap_exits = (
                        int(getattr(self.algorithm, "_diag_vass_tail_cap_exits", 0) or 0) + 1
                    )
                exit_reason = f"VASS_TAIL_RISK_CAP: Loss=${loss_dollars:.0f} >= Cap=${cap_dollars:.0f} ({cap_pct:.1%} eq)"

        # V10.7: Day-4 EOD decision for debit spreads.
        # Rule: at/after day-4 EOD, close spreads when P&L is above the threshold,
        # keep only deeper losers for additional recovery time (hard stop still active).
        if (
            exit_reason is None
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

        # Exit 1: Profit target (base 50% of max profit)
        # V3.0: Regime-adaptive profit targets - greedy in bull, defensive in bear
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
        if pnl >= profit_target:
            exit_reason = (
                f"PROFIT_TARGET +{pnl_pct:.1%} (Net ${net_pnl:.2f} >= ${raw_profit_target:.2f}) | "
                f"Target {adaptive_profit_pct:.0%} (regime {regime_score:.0f}) | "
                f"Gross ${pnl:.2f} - Commission ${commission_cost:.2f} | {vass_profile_tag}"
            )

        # Exit 1B: TRAILING STOP — lock in gains after reaching activation threshold
        # V9.4: Once spread reaches +X% unrealized, trail stop from high-water mark
        trail_activate_pct = float(vass_exit_profile.get("trail_activate_pct", 0.20))
        trail_offset_pct = float(vass_exit_profile.get("trail_offset_pct", 0.15))
        if exit_reason is None and pnl_pct > 0:
            # Update high-water mark
            if pnl_pct > spread.highest_pnl_pct:
                spread.highest_pnl_pct = pnl_pct
            # Trail stop activates after reaching activation threshold
            if spread.highest_pnl_pct >= trail_activate_pct:
                trail_stop_level = spread.highest_pnl_pct - trail_offset_pct
                if pnl_pct <= trail_stop_level:
                    exit_reason = (
                        f"TRAIL_STOP {pnl_pct:.1%} "
                        f"(High={spread.highest_pnl_pct:.1%}, Trail={trail_stop_level:.1%}, {vass_profile_tag})"
                    )

        # Exit 2: STOP LOSS
        # Add a hard cap across regimes, then apply adaptive stop logic.
        if exit_reason is None and pnl_pct < 0:
            width_stop_pct = float(getattr(config, "SPREAD_HARD_STOP_WIDTH_PCT", 0.0))
            if width_stop_pct > 0 and float(getattr(spread, "width", 0.0)) > 0:
                width_loss_cap = float(spread.width) * width_stop_pct
                if pnl <= -width_loss_cap:
                    exit_reason = (
                        f"SPREAD_HARD_STOP_TRIGGERED_WIDTH {pnl:.2f} (loss <= -${width_loss_cap:.2f}, "
                        f"{width_stop_pct:.0%} of width ${float(spread.width):.2f})"
                    )
        if exit_reason is None and pnl_pct < 0:
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
        if exit_reason is None and pnl_pct < 0:
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
        if exit_reason is None and self.algorithm is not None:
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
        if exit_reason is None and getattr(
            config, "SPREAD_REGIME_DETERIORATION_EXIT_ENABLED", True
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
        if exit_reason is None and current_dte <= config.SPREAD_DTE_EXIT:
            exit_reason = f"DTE_EXIT ({current_dte} DTE <= {config.SPREAD_DTE_EXIT})"

        # Exit 5: Phase C staged neutrality de-risk.
        if exit_reason is None:
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
                "is_credit_spread": is_credit_spread,
                "spread_credit_received": abs(spread.net_debit) if is_credit_spread else 0.0,
            },
        ),
    ]


# =========================================================================
# V2.4.1 FRIDAY FIREWALL
# =========================================================================
