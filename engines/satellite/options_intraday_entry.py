"""Intraday entry signal evaluator extracted from options_engine."""

from __future__ import annotations

import config
from models.enums import IntradayStrategy, MicroRegime, OptionDirection, OptionsMode, Urgency
from models.target_weight import TargetWeight


def check_intraday_entry_signal_impl(
    self,
    vix_current: float,
    vix_open: float,
    qqq_current: float,
    qqq_open: float,
    current_hour: int,
    current_minute: int,
    current_time: str,
    portfolio_value: float,
    raw_portfolio_value: Optional[float] = None,
    best_contract: Optional[OptionContract] = None,
    size_multiplier: float = 1.0,
    macro_regime_score: float = 50.0,
    governor_scale: float = 1.0,
    direction: Optional[OptionDirection] = None,
    forced_entry_strategy: Optional[IntradayStrategy] = None,
    vix_level_override: Optional[float] = None,  # V6.2: CBOE VIX for level consistency
    underlying_atr: float = 0.0,  # V6.5: QQQ ATR for delta-scaled stops
    micro_state: Optional["MicroRegimeState"] = None,  # Reuse approved state; avoid re-eval drift
    transition_ctx: Optional[Dict[str, Any]] = None,
) -> Optional[TargetWeight]:
    """
    Check for intraday mode entry signal using Micro Regime Engine.

    V2.1.1: Uses VIX Level × VIX Direction = 21 trading regimes.
    V2.3.20: Added size_multiplier for cold start reduced sizing.
    V2.5: Added macro_regime_score for Grind-Up Override logic.
    V3.2: Added governor_scale for intraday Governor gate.
    V6.0: Added direction parameter - use pre-resolved direction from conviction.
    V6.2: Added vix_level_override for consistent VIX level classification.
    V6.5: Added underlying_atr for delta-scaled ATR stop calculation.

    Args:
        vix_current: Current VIX value.
        vix_open: VIX at market open.
        qqq_current: Current QQQ price.
        qqq_open: QQQ at market open.
        current_hour: Current hour (0-23) Eastern.
        current_minute: Current minute (0-59).
        current_time: Timestamp string.
        portfolio_value: Total portfolio value.
        best_contract: Best available contract for the signal.
        size_multiplier: Position size multiplier (default 1.0). V2.3.20: Set to 0.5
            during cold start to reduce risk.
        macro_regime_score: Macro regime score (0-100) for Grind-Up Override. V2.5.
        governor_scale: Governor scaling (0-1). V3.2: At 0%, CALL blocked, PUT allowed.
        direction: V6.0: Pre-resolved direction from conviction. If provided, skip
            recalculation to avoid mismatch with contract selection.
        vix_level_override: V6.2 - CBOE VIX for level classification (ensures consistency
            with scheduled _update_micro_regime calls).
        underlying_atr: V6.5 - QQQ ATR for delta-scaled stop calculation. If 0, falls back
            to fixed percentage stop.

    Returns:
        TargetWeight for intraday entry, or None.
    """

    validation_lane = self._intraday_engine_lane_from_strategy(
        self._canonical_intraday_strategy_name(
            (
                getattr(forced_entry_strategy, "value", forced_entry_strategy)
                if forced_entry_strategy is not None
                else None
            )
        )
    )

    def fail(reason: str, detail: Optional[str] = None) -> Optional[TargetWeight]:
        self.set_last_intraday_validation_failure(validation_lane, reason, detail)
        return None

    # Reset previous validation reason for this attempt
    self.set_last_intraday_validation_failure(validation_lane, None, None)

    if self._pending_intraday_entry or self._pending_intraday_entries:
        self._clear_stale_pending_intraday_entry_if_orphaned()

    # V2.9: Check trade limits (Bug #4 fix) - Uses comprehensive counter
    # Replaces V2.3.14 intraday-only check to also enforce global limit
    if not self._can_trade_options(OptionsMode.INTRADAY, direction=direction):
        # Preserve granular slot-limit cause for funnel diagnostics.
        tl_reason, tl_detail = self.pop_last_trade_limit_failure()
        return fail(tl_reason or "E_INTRADAY_TRADE_LIMIT", tl_detail)

    # Reuse state from generate_micro_intraday_signal when provided.
    # This prevents approved->dropped drift caused by a second update() call.
    state = micro_state
    itm_forced_path = (
        forced_entry_strategy is not None
        and self._canonical_intraday_strategy(forced_entry_strategy)
        == IntradayStrategy.ITM_MOMENTUM
    )
    if state is None:
        if itm_forced_path:
            # ITM sovereign path: read current MICRO state without mutating it.
            state = self._micro_regime_engine.get_state()
        else:
            qqq_atr_pct = None
            if qqq_current > 0 and self.algorithm is not None:
                try:
                    qqq_atr_indicator = getattr(self.algorithm, "qqq_atr", None)
                    if qqq_atr_indicator is not None and bool(
                        getattr(qqq_atr_indicator, "IsReady", False)
                    ):
                        qqq_atr_value = float(qqq_atr_indicator.Current.Value)
                        if qqq_atr_value > 0:
                            qqq_atr_pct = (qqq_atr_value / float(qqq_current)) * 100.0
                except Exception:
                    qqq_atr_pct = None
            state = self._micro_regime_engine.update(
                vix_current=vix_current,
                vix_open=vix_open,
                qqq_current=qqq_current,
                qqq_open=qqq_open,
                current_time=current_time,
                macro_regime_score=macro_regime_score,
                vix_level_override=vix_level_override,  # V6.2: Pass through
                qqq_atr_pct=qqq_atr_pct,
            )

    # V10.10: allow explicit strategy overrides for retry/ITM sovereign paths.
    if forced_entry_strategy is not None:
        entry_strategy = self._canonical_intraday_strategy(forced_entry_strategy)
    else:
        # V6.8: NO_TRADE is now blocked earlier in generate_micro_intraday_signal()
        # Safety net remains for non-ITM override paths.
        if state.recommended_strategy == IntradayStrategy.NO_TRADE:
            itm_sovereign_bypass = False
            if itm_sovereign_bypass:
                entry_strategy = IntradayStrategy.ITM_MOMENTUM
                self.log(
                    f"INTRADAY: ITM_ENGINE strategy override from NO_TRADE | "
                    f"Dir={direction.value} | Regime={state.micro_regime.value}",
                    trades_only=True,
                )
            else:
                self.log(
                    f"INTRADAY: Blocked - NO_TRADE strategy | "
                    f"Regime={state.micro_regime.value} | Score={state.micro_score:.0f}"
                )
                return fail("E_INTRADAY_NO_TRADE_STRATEGY", state.micro_regime.value)
        else:
            entry_strategy = self._canonical_intraday_strategy(state.recommended_strategy)
    if entry_strategy is None:
        return fail("E_INTRADAY_NO_STRATEGY")
    validation_lane = self._intraday_engine_lane_from_strategy(entry_strategy.value)
    self.set_last_intraday_validation_failure(validation_lane, None, None)

    lane_ok, lane_code, lane_detail, pending_lane = self._micro_entry_engine.validate_lane_caps(
        entry_strategy=entry_strategy,
        intraday_positions=self._intraday_positions,
        has_pending_intraday_entry=self.has_pending_intraday_entry,
        intraday_itm_trades_today=self._intraday_itm_trades_today,
        intraday_micro_trades_today=self._intraday_micro_trades_today,
        lane_resolver=self._intraday_engine_lane_from_strategy,
        state=state,
        direction=direction,
        vix_current=vix_level_override if vix_level_override is not None else vix_current,
        transition_ctx=transition_ctx,
    )
    if not lane_ok:
        return fail(lane_code or "E_INTRADAY_LANE_CAP", lane_detail)
    # Engine isolation: do not hard-block this entry because another intraday
    # engine currently owns a position. Concurrency/arbitration is governed by
    # slot caps, per-engine limits, and router margin checks.

    itm_engine_mode = False

    # V3.2: Check if strategy is PROTECTIVE_PUTS (crisis hedge)
    if entry_strategy == IntradayStrategy.PROTECTIVE_PUTS:
        # V3.2: Actually implement protective puts (was previously just returning None)
        if not getattr(config, "PROTECTIVE_PUTS_ENABLED", True):
            self.log(f"INTRADAY: Protective mode (disabled) - regime={state.micro_regime.value}")
            return fail("E_PROTECTIVE_PUTS_DISABLED")

        # Force direction to PUT for protection
        direction = OptionDirection.PUT

        # Protective puts bypass macro gate (defensive by definition)
        # But still respect Governor scaling
        protective_size_pct = getattr(config, "PROTECTIVE_PUTS_SIZE_PCT", 0.02)
        effective_size_pct = protective_size_pct * governor_scale

        if effective_size_pct < 0.005:  # Less than 0.5% = not worth it
            self.log(
                f"INTRADAY: Protective PUT size too small ({effective_size_pct:.1%}) "
                f"| Governor={governor_scale:.0%}"
            )
            return fail("E_INTRADAY_PROTECTIVE_TOO_SMALL", f"{effective_size_pct:.3f}")

        self.log(
            f"PROTECTIVE_PUT: Crisis detected | Micro={state.micro_regime.value} | "
            f"Score={state.micro_score:.0f} | Size={effective_size_pct:.1%}",
            trades_only=True,
        )

        # Continue to contract selection with protective sizing
        # Will be handled below with special sizing path
        is_protective_put = True
    else:
        is_protective_put = False
        # V6.0: Use passed direction from conviction resolution (avoids mismatch with contract)
        # Previously recalculated from state.recommended_direction which could differ
        if direction is None:
            # Fallback to state direction if not passed (backwards compatibility)
            direction = state.recommended_direction
        if direction is None:
            strategy_value = entry_strategy.value if entry_strategy is not None else "UNKNOWN"
            self.log(f"INTRADAY: No direction recommended for {strategy_value}")
            return fail("E_INTRADAY_NO_DIRECTION", strategy_value)

        itm_engine_mode = bool(
            self._itm_horizon_engine.enabled() and entry_strategy == IntradayStrategy.ITM_MOMENTUM
        )
        if itm_engine_mode:
            qqq_sma20 = getattr(self.algorithm, "qqq_sma20", None) if self.algorithm else None
            sma20_value = (
                float(qqq_sma20.Current.Value)
                if qqq_sma20 is not None and getattr(qqq_sma20, "IsReady", False)
                else None
            )
            qqq_adx = getattr(self.algorithm, "qqq_adx", None) if self.algorithm else None
            adx_value = (
                float(qqq_adx.Current.Value)
                if qqq_adx is not None and getattr(qqq_adx, "IsReady", False)
                else None
            )
            vix20_change = (
                self._iv_sensor.get_vix_20d_change() if self._iv_sensor is not None else None
            )
            effective_vix = float(
                vix_level_override if vix_level_override is not None else vix_current
            )
            try:
                if self.algorithm is not None and hasattr(self.algorithm, "_get_vix_level"):
                    effective_vix = float(self.algorithm._get_vix_level())
            except Exception:
                pass

            active_itm_positions = len(self._intraday_positions.get("ITM") or [])

            trace_id = (
                f"ITM|{(current_time or 'NA')[:19]}|{direction.value}|"
                f"{entry_strategy.value if entry_strategy else 'NA'}"
            )
            itm_ok, itm_code, itm_detail = self._itm_horizon_engine.evaluate_entry(
                direction=direction,
                current_time=current_time,
                current_hour=current_hour,
                current_minute=current_minute,
                trace_id=trace_id,
                qqq_current=qqq_current,
                sma20_value=sma20_value,
                adx_value=adx_value,
                vix_current=effective_vix,
                vix20_change=vix20_change,
                portfolio_value=float(
                    raw_portfolio_value if raw_portfolio_value is not None else portfolio_value
                ),
                current_itm_positions=active_itm_positions,
                algorithm=self.algorithm,
            )
            self.log(
                f"ITM_ENGINE_DECISION|Trace={trace_id}|Dir={direction.value}|QQQ={qqq_current:.2f}|"
                f"SMA20={sma20_value if sma20_value is not None else 'NA'}|"
                f"ADX={adx_value if adx_value is not None else 'NA'}|"
                f"VIX={effective_vix:.1f}|"
                f"VIX20d={vix20_change if vix20_change is not None else 'NA'}|"
                f"OpenITM={active_itm_positions}|"
                f"{'PASS' if itm_ok else 'BLOCK'}|{itm_code}|{itm_detail}",
                trades_only=True,
            )
            if not itm_ok and not self._itm_horizon_engine.shadow_mode():
                return fail(itm_code, itm_detail)

        use_micro_entry_engine = entry_strategy in (
            IntradayStrategy.MICRO_DEBIT_FADE,
            IntradayStrategy.MICRO_OTM_MOMENTUM,
            IntradayStrategy.DEBIT_FADE,
            IntradayStrategy.CREDIT_SPREAD,
        )
        if use_micro_entry_engine and bool(getattr(config, "MICRO_ENTRY_ENGINE_ENABLED", True)):
            try:
                state.put_cooldown_until_date = self._put_cooldown_until_date
                state.put_consecutive_losses = self._put_consecutive_losses
            except Exception:
                pass
            (
                size_multiplier,
                micro_fail_code,
                micro_fail_detail,
            ) = self._micro_entry_engine.apply_pre_contract_gates(
                state=state,
                entry_strategy=entry_strategy,
                direction=direction,
                itm_engine_mode=itm_engine_mode,
                current_time=current_time,
                size_multiplier=size_multiplier,
                macro_regime_score=macro_regime_score,
                qqq_current=qqq_current,
                vix_current=vix_current,
                vix_level_override=vix_level_override,
                algorithm=self.algorithm,
                iv_sensor=self._iv_sensor,
                call_cooldown_until_date=self._call_cooldown_until_date,
                call_consecutive_losses=self._call_consecutive_losses,
                transition_ctx=transition_ctx,
            )
            if micro_fail_code is not None:
                return fail(micro_fail_code, micro_fail_detail)
        elif use_micro_entry_engine:
            self.log(
                "INTRADAY: MICRO_ENTRY_ENGINE disabled - legacy fallback removed; blocking entry",
                trades_only=True,
            )
            return fail("E_MICRO_ENGINE_DISABLED")

    transition_engine = None
    if is_protective_put:
        transition_engine = "HEDGE"
    elif itm_engine_mode:
        transition_engine = "ITM"
    if transition_engine is not None and direction in (
        OptionDirection.CALL,
        OptionDirection.PUT,
    ):
        transition_ctx = (
            dict(transition_ctx)
            if isinstance(transition_ctx, dict)
            else self._get_regime_transition_context(macro_regime_score)
        )
        block_gate, block_reason = self.evaluate_transition_policy_block(
            engine=transition_engine,
            direction=direction,
            transition_ctx=transition_ctx,
        )
        if block_gate:
            return fail(block_gate, block_reason)

    if entry_strategy == IntradayStrategy.MICRO_OTM_MOMENTUM:
        transition_ctx = (
            dict(transition_ctx)
            if isinstance(transition_ctx, dict)
            else self._get_regime_transition_context(macro_regime_score)
        )
        if bool(getattr(config, "MICRO_OTM_TRANSITION_BLOCK_ENABLED", True)):
            overlay = str(transition_ctx.get("transition_overlay", "STABLE") or "STABLE").upper()
            bars_since_flip = int(transition_ctx.get("overlay_bars_since_flip", 999) or 999)
            blocked_overlays_cfg = getattr(
                config,
                "MICRO_OTM_TRANSITION_BLOCK_OVERLAYS",
                ("DETERIORATION", "RECOVERY"),
            )
            blocked_overlays = {
                str(item).upper()
                for item in (
                    blocked_overlays_cfg
                    if isinstance(blocked_overlays_cfg, (list, tuple, set))
                    else []
                )
            }
            block_bars = max(1, int(getattr(config, "MICRO_OTM_TRANSITION_BLOCK_BARS", 4)))
            if overlay in blocked_overlays and bars_since_flip < block_bars:
                return fail(
                    "E_MICRO_OTM_TRANSITION_BLOCK",
                    f"Overlay={overlay} Bars={bars_since_flip}/{block_bars}",
                )

        max_otm_entries = int(getattr(config, "MICRO_OTM_MAX_ENTRIES_PER_SESSION", 0))
        if (
            max_otm_entries > 0
            and int(getattr(self, "_intraday_micro_trades_today", 0)) >= max_otm_entries
        ):
            return fail(
                "E_MICRO_OTM_SESSION_CAP",
                f"MICRO={int(getattr(self, '_intraday_micro_trades_today', 0))}/{max_otm_entries}",
            )

    # V3.2: Governor Gate for intraday (closes gap)
    if getattr(config, "INTRADAY_GOVERNOR_GATE_ENABLED", True) and not is_protective_put:
        if governor_scale <= 0:
            if direction == OptionDirection.CALL:
                self.log("INTRADAY: CALL blocked at Governor 0%")
                return fail("E_INTRADAY_GOVERNOR_CALL_BLOCK")
            # PUT allowed at Governor 0% (reduces risk)
            self.log("INTRADAY: PUT allowed at Governor 0% (defensive)", trades_only=True)

    # V6.0: Macro Regime Gate removed - conviction resolution handles direction
    # Direction comes from Micro Regime Engine's recommend_strategy_and_direction()
    # Conviction resolution (resolve_trade_signal) called in main.py before this function

    strategy_names = {
        IntradayStrategy.MICRO_DEBIT_FADE: "MICRO_FADE",
        IntradayStrategy.MICRO_OTM_MOMENTUM: "MICRO_OTM",
        IntradayStrategy.DEBIT_FADE: "MICRO_FADE",
        IntradayStrategy.ITM_MOMENTUM: "ITM_MOM",
        IntradayStrategy.CREDIT_SPREAD: "CREDIT",
        IntradayStrategy.PROTECTIVE_PUTS: "PROTECTIVE_PUTS",
    }
    strategy_name = strategy_names.get(entry_strategy, "UNKNOWN")

    # MICRO anti-churn cooldown: avoid immediate re-entry of same strategy.
    cooldown_min = int(getattr(config, "MICRO_SAME_STRATEGY_COOLDOWN_MINUTES", 0))
    if (
        cooldown_min > 0
        and self._last_intraday_close_time is not None
        and self._last_intraday_close_strategy
    ):
        current_dt = None
        if current_time:
            try:
                current_dt = datetime.strptime(current_time[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                current_dt = None
        if current_dt is None and self.algorithm is not None:
            current_dt = self.algorithm.Time
        if current_dt is not None:
            elapsed = (current_dt - self._last_intraday_close_time).total_seconds() / 60.0
            if 0 <= elapsed < cooldown_min and strategy_name == self._last_intraday_close_strategy:
                self.log(
                    f"INTRADAY: Same-strategy cooldown block | Strategy={strategy_name} | "
                    f"Elapsed={elapsed:.1f}m < {cooldown_min}m",
                    trades_only=True,
                )
                return fail("E_INTRADAY_SAME_STRATEGY_COOLDOWN")

    use_micro_entry_engine = entry_strategy in (
        IntradayStrategy.MICRO_DEBIT_FADE,
        IntradayStrategy.MICRO_OTM_MOMENTUM,
        IntradayStrategy.DEBIT_FADE,
        IntradayStrategy.CREDIT_SPREAD,
    )
    if use_micro_entry_engine and bool(getattr(config, "MICRO_ENTRY_ENGINE_ENABLED", True)):
        tw_ok, tw_code = self._micro_entry_engine.validate_time_window(
            entry_strategy=entry_strategy,
            itm_engine_mode=itm_engine_mode,
            state=state,
            current_hour=current_hour,
            current_minute=current_minute,
        )
        if not tw_ok:
            return fail(tw_code or "E_INTRADAY_TIME_WINDOW")
    elif use_micro_entry_engine:
        self.log(
            "INTRADAY: MICRO_ENTRY_ENGINE disabled - legacy fallback removed; blocking entry",
            trades_only=True,
        )
        return fail("E_MICRO_ENGINE_DISABLED")

    (
        contract_ok,
        contract_code,
        contract_detail,
        strategy_name,
    ) = self._micro_entry_engine.validate_contract_selection(
        entry_strategy=entry_strategy,
        best_contract=best_contract,
        direction=direction,
    )
    if not contract_ok:
        if contract_code == "E_INTRADAY_NO_CONTRACT":
            self.log(f"INTRADAY: {strategy_name} signal but no contract available")
        elif (
            contract_code == "E_INTRADAY_DIRECTION_MISMATCH"
            and best_contract is not None
            and direction is not None
        ):
            self.log(
                f"INTRADAY: Direction mismatch - signal wants {direction.value} "
                f"but contract is {best_contract.direction.value}, skipping"
            )
        return fail(contract_code or "E_INTRADAY_CONTRACT", contract_detail)

    if (
        entry_strategy == IntradayStrategy.MICRO_OTM_MOMENTUM
        and best_contract is not None
        and bool(getattr(config, "MICRO_OTM_0DTE_LATE_ENTRY_BLOCK_ENABLED", True))
    ):
        dte_now = int(getattr(best_contract, "days_to_expiry", -1))
        if dte_now <= 0:
            late_block_start = str(
                getattr(config, "MICRO_OTM_0DTE_LATE_ENTRY_BLOCK_START", "13:45")
            )
            try:
                hh, mm = late_block_start.split(":")
                late_block_minutes = (int(hh) * 60) + int(mm)
            except Exception:
                late_block_minutes = (13 * 60) + 45
            now_minutes = (int(current_hour) * 60) + int(current_minute)
            if now_minutes >= late_block_minutes:
                return fail(
                    "E_MICRO_OTM_0DTE_LATE_BLOCK",
                    f"{current_hour:02d}:{current_minute:02d}>={late_block_start} | DTE={dte_now}",
                )

    strategy_for_friction = self._canonical_intraday_strategy_name(
        entry_strategy.value if entry_strategy is not None else ""
    )
    contract_spread_pct = float(getattr(best_contract, "spread_pct", 1.0) or 1.0)
    (
        friction_ok,
        friction_code,
        friction_detail,
    ) = self._micro_entry_engine.validate_contract_friction(
        strategy_value=strategy_for_friction,
        contract_spread_pct=contract_spread_pct,
        entry_strategy=entry_strategy,
        direction=direction,
        vix_current=vix_level_override if vix_level_override is not None else vix_current,
        current_time=current_time,
        days_to_expiry=getattr(best_contract, "days_to_expiry", None)
        if best_contract is not None
        else None,
        transition_ctx=transition_ctx,
    )
    if not friction_ok:
        self.log(
            f"INTRADAY: Entry blocked - friction {contract_spread_pct:.1%} | "
            f"Strategy={strategy_for_friction}",
            trades_only=True,
        )
        return fail(friction_code or "E_INTRADAY_FRICTION_CAP", friction_detail)

    # V2.18: Use sizing cap (Fix for MarginBuyingPower sizing bug)
    # V3.0 SCALABILITY FIX: Use percentage-based cap instead of hardcoded dollars
    # At $50K: 8% = $4,000, at $200K: 8% = $16,000 (scales with portfolio)
    portfolio_value_for_sizing = (
        float(portfolio_value)
        if portfolio_value and portfolio_value > 0
        else (self.algorithm.Portfolio.TotalPortfolioValue if self.algorithm else 50000)
    )

    # V10.10: Strategy-specific intraday budget slices.
    # ITM uses dedicated 15%/$15k budget, OTM (DEBIT_FADE/CREDIT) uses 10%/$10k.
    intraday_max_pct = getattr(config, "INTRADAY_SPREAD_MAX_PCT", 0.08)
    intraday_abs_cap = 0.0
    if entry_strategy == IntradayStrategy.ITM_MOMENTUM:
        intraday_max_pct = float(getattr(config, "INTRADAY_ITM_MAX_PCT", intraday_max_pct))
        intraday_abs_cap = float(getattr(config, "INTRADAY_ITM_MAX_DOLLARS", 0.0) or 0.0)
    elif entry_strategy in (
        IntradayStrategy.MICRO_DEBIT_FADE,
        IntradayStrategy.MICRO_OTM_MOMENTUM,
        IntradayStrategy.DEBIT_FADE,
        IntradayStrategy.CREDIT_SPREAD,
    ):
        intraday_max_pct = float(getattr(config, "INTRADAY_OTM_MAX_PCT", intraday_max_pct))
        intraday_abs_cap = float(getattr(config, "INTRADAY_OTM_MAX_DOLLARS", 0.0) or 0.0)

    if is_protective_put:
        # Protective puts: fixed percentage, already scaled by Governor
        protective_size_pct = getattr(config, "PROTECTIVE_PUTS_SIZE_PCT", 0.02)
        effective_size_pct = protective_size_pct * governor_scale
        adjusted_cap = portfolio_value_for_sizing * effective_size_pct
        size_mult = 1.0  # Already factored in above
        intraday_max_pct = protective_size_pct  # For logging
    else:
        intraday_max_dollars = portfolio_value_for_sizing * intraday_max_pct
        if intraday_abs_cap > 0:
            intraday_max_dollars = min(intraday_max_dollars, intraday_abs_cap)
        bucket_name = "ITM" if entry_strategy == IntradayStrategy.ITM_MOMENTUM else "OTM"
        remaining_bucket = self._get_bucket_remaining_dollars(
            bucket_name, float(portfolio_value_for_sizing)
        )
        intraday_max_dollars = min(intraday_max_dollars, remaining_bucket)
        if intraday_max_dollars <= 0:
            self.log(
                f"INTRADAY: Entry blocked - {bucket_name} bucket exhausted",
                trades_only=True,
            )
            return fail(f"E_INTRADAY_{bucket_name}_BUCKET_EXHAUSTED")

        # ITM_ENGINE is a sovereign engine: do not couple size to MICRO score ladders.
        if itm_engine_mode and entry_strategy == IntradayStrategy.ITM_MOMENTUM:
            base_mult = float(getattr(config, "ITM_SIZE_MULT", 1.0) or 1.0)
            med_vix_thr = float(getattr(config, "ITM_MED_VIX_THRESHOLD", 18.0))
            high_vix_thr = float(getattr(config, "ITM_HIGH_VIX_THRESHOLD", 25.0))
            vix_val = float(vix_current) if vix_current is not None else med_vix_thr
            if vix_val >= high_vix_thr:
                tier_mult = float(getattr(config, "ITM_SIZE_MULT_HIGH_VIX", 0.50))
            elif vix_val >= med_vix_thr:
                tier_mult = float(getattr(config, "ITM_SIZE_MULT_MED_VIX", 0.75))
            else:
                tier_mult = float(getattr(config, "ITM_SIZE_MULT_LOW_VIX", 1.00))
            strategy_mult = max(0.0, base_mult * tier_mult)
        else:
            # OTM micro paths still use MICRO score ladder.
            if state.micro_score >= config.MICRO_SCORE_PRIME_MR:
                strategy_mult = 1.0  # Full size
            elif state.micro_score >= config.MICRO_SCORE_GOOD_MR:
                strategy_mult = float(getattr(config, "MICRO_SIZE_MULT_MID_CONVICTION", 0.75))
            elif state.micro_score >= config.MICRO_SCORE_MODERATE:
                strategy_mult = 0.5  # Half size
            else:
                strategy_mult = 0.5  # Half size

            # V6.14 OPT: Reduce size in fragile transition states even when tradable.
            if state.micro_regime in (MicroRegime.ELEVATED, MicroRegime.WORSENING):
                strategy_mult = min(strategy_mult, 0.5)

            if entry_strategy == IntradayStrategy.MICRO_OTM_MOMENTUM and bool(
                getattr(config, "MICRO_OTM_TIERED_RISK_ENABLED", False)
            ):
                low_max = float(getattr(config, "MICRO_OTM_VIX_LOW_MAX", 16.0))
                med_max = float(getattr(config, "MICRO_OTM_VIX_MED_MAX", 22.0))
                vix_val = float(vix_current) if vix_current is not None else med_max
                if vix_val < low_max:
                    otm_size = float(getattr(config, "MICRO_OTM_SIZE_MULT_LOW_VIX", 1.0))
                elif vix_val < med_max:
                    otm_size = float(getattr(config, "MICRO_OTM_SIZE_MULT_MED_VIX", 1.0))
                else:
                    otm_size = float(getattr(config, "MICRO_OTM_SIZE_MULT_HIGH_VIX", 0.60))
                strategy_mult *= max(0.0, otm_size)

        # V6.0: Apply combined multipliers (cold_start × governor × strategy)
        combined_mult = size_multiplier * governor_scale * strategy_mult
        min_combined = getattr(config, "OPTIONS_MIN_COMBINED_SIZE_PCT", 0.10)
        if combined_mult < min_combined:
            self.log(
                f"INTRADAY: Entry blocked - combined size {combined_mult:.0%} < min {min_combined:.0%}"
            )
            return fail("E_INTRADAY_COMBINED_SIZE_MIN")

        adjusted_cap = intraday_max_dollars * combined_mult

        # V12.13: ITM budget-proportional sizing — deploy fraction of budget, not all.
        if itm_engine_mode and entry_strategy == IntradayStrategy.ITM_MOMENTUM:
            deploy_pct = float(getattr(config, "ITM_DEPLOY_PCT_OF_BUDGET", 0.60))
            budget_deploy_cap = intraday_max_dollars * deploy_pct
            if adjusted_cap > budget_deploy_cap:
                adjusted_cap = budget_deploy_cap

        size_mult = strategy_mult  # For logging compatibility
    premium = best_contract.mid_price
    if premium <= 0:
        self.log("INTRADAY: Entry blocked - invalid premium price")
        return fail("E_INTRADAY_INVALID_PREMIUM")

    # V2.18: Calculate contracts using cap / (premium * 100)
    num_contracts = int(adjusted_cap / (premium * 100))

    # V9.8: Hard cap all MICRO intraday entries to prevent quantity explosions on cheap options.
    intraday_max_contracts = int(getattr(config, "INTRADAY_MAX_CONTRACTS", 40))
    if bool(getattr(config, "INTRADAY_CONTRACT_CAP_SCALE_WITH_EQUITY", True)):
        base_equity = float(getattr(config, "INTRADAY_MAX_CONTRACTS_BASE_EQUITY", 100000.0))
        min_contract_cap = int(getattr(config, "INTRADAY_MAX_CONTRACTS_MIN", 5))
        if base_equity > 0 and portfolio_value > 0 and intraday_max_contracts > 0:
            equity_scale = min(1.0, float(portfolio_value) / base_equity)
            scaled_cap = max(min_contract_cap, int(intraday_max_contracts * equity_scale))
            if scaled_cap < intraday_max_contracts:
                self.log(
                    f"INTRADAY_CAP_SCALE: BaseCap={intraday_max_contracts} -> {scaled_cap} | "
                    f"Equity=${portfolio_value:,.0f} | Base=${base_equity:,.0f}",
                    trades_only=True,
                )
                intraday_max_contracts = scaled_cap

    if itm_engine_mode:
        itm_engine_cap = int(getattr(config, "ITM_MAX_CONTRACTS_HARD_CAP", 8))
        if itm_engine_cap > 0:
            intraday_max_contracts = (
                itm_engine_cap
                if intraday_max_contracts <= 0
                else min(intraday_max_contracts, itm_engine_cap)
            )
    elif entry_strategy == IntradayStrategy.MICRO_OTM_MOMENTUM and best_contract is not None:
        dte = int(getattr(best_contract, "days_to_expiry", -1))
        if dte <= 0:
            otm_dte_cap = int(getattr(config, "MICRO_OTM_DTE0_MAX_CONTRACTS", 0))
        elif dte == 1:
            otm_dte_cap = int(getattr(config, "MICRO_OTM_DTE1_MAX_CONTRACTS", 0))
        elif dte == 2:
            otm_dte_cap = int(getattr(config, "MICRO_OTM_DTE2_MAX_CONTRACTS", 0))
        else:
            otm_dte_cap = int(getattr(config, "INTRADAY_MAX_CONTRACTS", 0))
        if otm_dte_cap > 0:
            intraday_max_contracts = (
                otm_dte_cap
                if intraday_max_contracts <= 0
                else min(intraday_max_contracts, otm_dte_cap)
            )
            self.log(
                f"INTRADAY_OTM_DTE_CAP: DTE={dte} | Cap={intraday_max_contracts}",
                trades_only=True,
            )

    if intraday_max_contracts > 0 and num_contracts > intraday_max_contracts:
        self.log(
            f"INTRADAY_CAP: {num_contracts} → {intraday_max_contracts} contracts (max)",
            trades_only=True,
        )
        num_contracts = intraday_max_contracts

    # V9.2 RCA: Cap protective puts to prevent outsized 10+ contract bets
    # At 3% sizing on cheap OTM puts, uncapped quantity can reach 10-15 contracts,
    # amplifying losses 5× compared to ITM_MOMENTUM's ~2 contracts.
    if is_protective_put:
        max_protective = int(getattr(config, "PROTECTIVE_PUTS_MAX_CONTRACTS", 5))
        if max_protective > 0 and num_contracts > max_protective:
            self.log(f"PROTECTIVE_CAP: {num_contracts} → {max_protective} contracts (max)")
            num_contracts = max_protective

    self.log(
        f"SIZING: INTRADAY | Cap=${adjusted_cap:,.0f} ({intraday_max_pct:.0%} of ${portfolio_value:,.0f}) | "
        f"Premium=${premium:.2f} | Qty={num_contracts}"
    )
    if num_contracts <= 0:
        allow_one_lot = bool(getattr(config, "INTRADAY_ALLOW_ONE_LOT_WHEN_CAP_TIGHT", False))
        one_lot_max_premium = float(getattr(config, "INTRADAY_ONE_LOT_MAX_PREMIUM", 6.0))
        if (
            allow_one_lot
            and entry_strategy == IntradayStrategy.ITM_MOMENTUM
            and direction == OptionDirection.CALL
            and premium <= one_lot_max_premium
            and adjusted_cap >= premium * 100 * 0.50
        ):
            num_contracts = 1
            self.log(
                f"INTRADAY: One-lot fallback applied | Cap=${adjusted_cap:.0f} | Premium=${premium:.2f}"
            )
        else:
            self.log(
                f"INTRADAY: Entry blocked - cap ${adjusted_cap:.0f} "
                f"too small for premium ${premium:.2f}"
            )
            return fail("E_INTRADAY_CAP_TOO_SMALL")

    # V2.3.4: Use QQQ direction from state
    qqq_dir_str = state.qqq_direction.value if state.qqq_direction else "UNKNOWN"
    reason = (
        f"INTRADAY_{strategy_name}: Regime={state.micro_regime.value} | "
        f"Score={state.micro_score:.0f} | VIX={vix_current:.1f} "
        f"({state.vix_direction.value}) | QQQ={qqq_dir_str} "
        f"({state.qqq_move_pct:+.2f}%) | {direction.value} x{num_contracts}"
    )

    # V2.3.2 FIX #4: Mark this as intraday entry for correct position tracking
    self._pending_intraday_entry = True
    self._pending_intraday_entry_since = self.algorithm.Time if self.algorithm else None
    self._pending_intraday_entry_engine = self._intraday_engine_lane_from_strategy(
        entry_strategy.value
    )

    # V2.3.10 FIX: Set pending contract for register_entry
    # Without this, register_entry fails with "no pending contract"
    self._pending_contract = best_contract
    self._pending_num_contracts = num_contracts
    self._pending_entry_strategy = entry_strategy.value

    # V6.5: Delta-scaled ATR stop calculation
    # Formula: stop_distance = ATR_MULTIPLIER × ATR × abs(delta)
    # This gives more room in high-VIX environments while accounting for option sensitivity
    if getattr(config, "OPTIONS_USE_ATR_STOPS", True) and underlying_atr > 0 and premium > 0:
        atr_multiplier = getattr(config, "OPTIONS_ATR_STOP_MULTIPLIER", 1.5)
        option_delta = abs(best_contract.delta)

        # Calculate stop distance in option price terms
        stop_distance = atr_multiplier * underlying_atr * option_delta
        atr_stop_pct = stop_distance / premium if premium > 0 else 0.20

        # Apply floor and cap
        min_stop = getattr(config, "OPTIONS_ATR_STOP_MIN_PCT", 0.20)
        max_stop = getattr(config, "OPTIONS_ATR_STOP_MAX_PCT", 0.50)

        # V9.2 RCA: Widen stop cap for ITM_MOMENTUM in high-VIX regimes.
        # The standard 28% cap is noise in VIX>25 environments, causing premature
        # stops on trades that would have recovered. Only affects bear-market regimes.
        high_vix_momentum_regimes = {
            MicroRegime.WORSENING_HIGH,
            MicroRegime.DETERIORATING,
            MicroRegime.ELEVATED,
            MicroRegime.WORSENING,
        }
        if (
            entry_strategy == IntradayStrategy.ITM_MOMENTUM
            and state.micro_regime in high_vix_momentum_regimes
        ):
            max_stop = getattr(config, "INTRADAY_HIGH_VIX_STOP_MAX_PCT", 0.40)

        final_stop_pct = max(min_stop, min(atr_stop_pct, max_stop))

        self.log(
            f"STOP_CALC: ATR=${underlying_atr:.2f} × Δ={option_delta:.2f} × {atr_multiplier}× = "
            f"${stop_distance:.2f} | Raw={atr_stop_pct:.0%} → Final={final_stop_pct:.0%} "
            f"(floor={min_stop:.0%}, cap={max_stop:.0%})"
        )
        self._pending_stop_pct = final_stop_pct
    else:
        # Fallback path when ATR is unavailable.
        # Keep fixed 0DTE override optional; otherwise use strategy stop baseline.
        use_static_0dte = bool(
            getattr(config, "OPTIONS_0DTE_STATIC_STOP_OVERRIDE_ENABLED", False)
        ) and bool(best_contract is not None and best_contract.days_to_expiry <= 1)
        if use_static_0dte:
            self._pending_stop_pct = float(getattr(config, "OPTIONS_0DTE_STOP_PCT", 0.25))
        elif entry_strategy == IntradayStrategy.ITM_MOMENTUM:
            self._pending_stop_pct = float(getattr(config, "INTRADAY_ITM_STOP", 0.40))
        elif entry_strategy == IntradayStrategy.MICRO_OTM_MOMENTUM:
            self._pending_stop_pct = float(getattr(config, "MICRO_OTM_MOMENTUM_STOP", 0.30))
        elif entry_strategy == IntradayStrategy.MICRO_DEBIT_FADE:
            self._pending_stop_pct = float(getattr(config, "MICRO_DEBIT_FADE_STOP", 0.25))
        else:
            self._pending_stop_pct = float(getattr(config, "OPTIONS_ATR_STOP_MIN_PCT", 0.12))
        if underlying_atr <= 0:
            self.log(f"STOP_CALC: ATR not ready, using fixed {self._pending_stop_pct:.0%} stop")

    # V6.22 FIX: Protective puts use dedicated stop from config (was dead config).
    # Protective puts are insurance — they need a wider stop (35%) than generic ATR (12-28%)
    # to avoid being stopped out on normal intraday noise before the hedge pays off.
    if is_protective_put:
        self._pending_stop_pct = getattr(config, "PROTECTIVE_PUTS_STOP_PCT", 0.35)
        self.log(
            f"STOP_CALC: Protective PUT override → {self._pending_stop_pct:.0%} "
            f"(config.PROTECTIVE_PUTS_STOP_PCT)"
        )

    # Optional fixed stop override for MICRO_OTM.
    # Keep disabled by default so ATR-stop elasticity remains effective.
    if (
        not is_protective_put
        and entry_strategy == IntradayStrategy.MICRO_OTM_MOMENTUM
        and self._pending_stop_pct is not None
        and bool(getattr(config, "MICRO_OTM_FIXED_STOP_OVERRIDE_ENABLED", False))
    ):
        if bool(getattr(config, "MICRO_OTM_TIERED_RISK_ENABLED", False)):
            low_max = float(getattr(config, "MICRO_OTM_VIX_LOW_MAX", 16.0))
            med_max = float(getattr(config, "MICRO_OTM_VIX_MED_MAX", 22.0))
            vix_val = float(vix_current) if vix_current is not None else med_max
            if vix_val < low_max:
                otm_fixed_stop = float(getattr(config, "MICRO_OTM_STOP_LOW_VIX", 0.30))
            elif vix_val < med_max:
                otm_fixed_stop = float(getattr(config, "MICRO_OTM_STOP_MED_VIX", 0.35))
            else:
                otm_fixed_stop = float(getattr(config, "MICRO_OTM_STOP_HIGH_VIX", 0.40))
        else:
            otm_fixed_stop = float(getattr(config, "MICRO_OTM_MOMENTUM_STOP", 0.35))
        if abs(float(self._pending_stop_pct) - otm_fixed_stop) > 1e-6:
            self.log(
                f"STOP_CALC: MICRO_OTM fixed stop override {self._pending_stop_pct:.0%} -> {otm_fixed_stop:.0%}",
                trades_only=True,
            )
        self._pending_stop_pct = otm_fixed_stop

    # V10.5: widen ITM stops in MED/HIGH VIX only; keep LOW VIX behavior unchanged.
    if (
        not is_protective_put
        and (not itm_engine_mode)
        and entry_strategy == IntradayStrategy.ITM_MOMENTUM
        and self._pending_stop_pct is not None
    ):
        if vix_current >= 25:
            itm_stop_floor = float(getattr(config, "INTRADAY_ITM_STOP_FLOOR_HIGH_VIX", 0.35))
            itm_tier = "HIGH"
        elif vix_current >= 18:
            itm_stop_floor = float(getattr(config, "INTRADAY_ITM_STOP_FLOOR_MED_VIX", 0.30))
            itm_tier = "MED"
        else:
            itm_stop_floor = float(getattr(config, "INTRADAY_ITM_STOP", 0.25))
            itm_tier = "LOW"
        if self._pending_stop_pct < itm_stop_floor:
            self.log(
                f"STOP_CALC: ITM {itm_tier} VIX floor {itm_stop_floor:.0%} > "
                f"ATR {self._pending_stop_pct:.0%} → using floor"
            )
            self._pending_stop_pct = itm_stop_floor

    if (
        not is_protective_put
        and itm_engine_mode
        and entry_strategy == IntradayStrategy.ITM_MOMENTUM
        and self._pending_stop_pct is not None
    ):
        _, itm_engine_stop, _, _, _ = self._itm_horizon_engine.get_exit_profile(vix_current)
        if itm_engine_stop is not None and itm_engine_stop > 0:
            atr_stop = float(self._pending_stop_pct)
            final_itm_stop = float(itm_engine_stop)
            if bool(getattr(config, "ITM_ATR_GUARDRAIL_ENABLED", True)):
                med_vix_thr = float(getattr(config, "ITM_MED_VIX_THRESHOLD", 18.0))
                high_vix_thr = float(getattr(config, "ITM_HIGH_VIX_THRESHOLD", 25.0))
                vix_val = float(vix_current) if vix_current is not None else med_vix_thr
                if vix_val >= high_vix_thr:
                    max_itm_stop = float(
                        getattr(config, "ITM_ATR_GUARDRAIL_MAX_STOP_HIGH_VIX", 0.40)
                    )
                elif vix_val >= med_vix_thr:
                    max_itm_stop = float(
                        getattr(config, "ITM_ATR_GUARDRAIL_MAX_STOP_MED_VIX", 0.35)
                    )
                else:
                    max_itm_stop = float(
                        getattr(config, "ITM_ATR_GUARDRAIL_MAX_STOP_LOW_VIX", 0.30)
                    )
                final_itm_stop = min(max_itm_stop, max(float(itm_engine_stop), atr_stop))
            if abs(float(self._pending_stop_pct) - float(final_itm_stop)) > 1e-6:
                self.log(
                    f"STOP_CALC: ITM_ENGINE stop floor {itm_engine_stop:.0%}, ATR {atr_stop:.0%} -> {final_itm_stop:.0%}",
                    trades_only=True,
                )
            self._pending_stop_pct = float(final_itm_stop)

    self.log(
        f"INTRADAY_SIGNAL: {reason} | Δ={best_contract.delta:.2f} K={best_contract.strike} DTE={best_contract.days_to_expiry} | "
        f"Stop={self._pending_stop_pct:.0%} | TradeCount={self._intraday_trades_today}/{config.INTRADAY_MAX_TRADES_PER_DAY}",
        trades_only=True,
    )
    pending_symbol_norm = self._symbol_str(best_contract.symbol)
    pending_lane = self._intraday_engine_lane_from_strategy(entry_strategy.value)
    existing_key = self._find_pending_intraday_entry_key(symbol=pending_symbol_norm)
    if existing_key is not None:
        existing_payload = self._pending_intraday_entries.get(existing_key) or {}
        existing_lane = str(existing_payload.get("lane", "")).upper()
        if existing_lane and existing_lane != pending_lane:
            return fail(
                "E_INTRADAY_PENDING_SYMBOL_CONFLICT",
                f"{pending_symbol_norm} already pending in lane={existing_lane}",
            )
    pending_key = self._pending_intraday_entry_key(
        symbol=pending_symbol_norm,
        lane=pending_lane,
    )
    self._pending_intraday_entries[pending_key] = {
        "symbol": pending_symbol_norm,
        "lane": pending_lane,
        "contract": best_contract,
        "entry_score": float(
            self._pending_entry_score if self._pending_entry_score is not None else 0.0
        ),
        "num_contracts": int(num_contracts),
        "entry_strategy": entry_strategy.value,
        "stop_pct": float(self._pending_stop_pct or 0.0),
        "created_at": (
            self.algorithm.Time.strftime("%Y-%m-%d %H:%M:%S")
            if self.algorithm is not None and hasattr(self.algorithm, "Time")
            else None
        ),
    }

    # Keep source weights strategy-specific so telemetry reflects ITM/OTM separation.
    if entry_strategy == IntradayStrategy.ITM_MOMENTUM:
        base_weight = float(
            getattr(config, "INTRADAY_ITM_MAX_PCT", config.OPTIONS_INTRADAY_ALLOCATION)
        )
    elif entry_strategy in (
        IntradayStrategy.MICRO_DEBIT_FADE,
        IntradayStrategy.MICRO_OTM_MOMENTUM,
        IntradayStrategy.DEBIT_FADE,
        IntradayStrategy.CREDIT_SPREAD,
    ):
        base_weight = float(
            getattr(config, "INTRADAY_OTM_MAX_PCT", config.OPTIONS_INTRADAY_ALLOCATION)
        )
    else:
        base_weight = float(config.OPTIONS_INTRADAY_ALLOCATION)
    actual_target_weight = base_weight * size_mult

    return TargetWeight(
        symbol=self._symbol_str(best_contract.symbol),
        target_weight=actual_target_weight,  # V2.4.1: Actual allocation, not 1.0
        source="OPT_INTRADAY",
        urgency=Urgency.IMMEDIATE,
        reason=reason,
        requested_quantity=num_contracts,  # V2.3.2: Pass calculated contracts
        metadata={
            "intraday_strategy": entry_strategy.value,
            "contract_price": best_contract.mid_price,
        },
    )
