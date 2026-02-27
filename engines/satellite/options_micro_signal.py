"""MICRO intraday signal generation for OptionsEngine."""

from __future__ import annotations

from typing import Optional, Tuple

import config
from models.enums import IntradayStrategy, MicroRegime, OptionDirection


def generate_micro_engine_signal_impl(
    self,
    vix_current: float,
    vix_open: float,
    qqq_current: float,
    qqq_open: float,
    uvxy_pct: float,
    macro_regime_score: float,
    current_time: str,
    vix_level_override: Optional[float] = None,
    premarket_shock_pct: float = 0.0,
) -> Tuple[bool, Optional[OptionDirection], Optional["MicroRegimeState"], str]:
    """
    Unified entry point for Micro intraday signal generation.
    """
    # Step 1: Single update (eliminates dual-update bug)
    # V6.16: Carry overnight panic context into early session.
    # Adjust effective vix_open baseline so large overnight VIX jumps are not "forgotten" at 10:00.
    vix_open_for_micro = vix_open
    if premarket_shock_pct > 0:
        shock_anchor = min(max(getattr(config, "MICRO_SHOCK_MEMORY_ANCHOR", 0.60), 0.0), 1.0)
        memory_scale = 1.0 + premarket_shock_pct * shock_anchor
        if memory_scale > 1.0:
            vix_open_for_micro = vix_open / memory_scale

    # MICRO owns intraday direction, but macro score is still used by policy gates.
    try:
        macro_for_micro = float(macro_regime_score)
    except Exception:
        macro_for_micro = 50.0
    qqq_atr_pct = None
    if self.algorithm is not None and qqq_current > 0:
        try:
            qqq_atr_indicator = getattr(self.algorithm, "qqq_atr", None)
            if qqq_atr_indicator is not None and bool(getattr(qqq_atr_indicator, "IsReady", False)):
                qqq_atr_value = float(qqq_atr_indicator.Current.Value)
                if qqq_atr_value > 0:
                    qqq_atr_pct = (qqq_atr_value / float(qqq_current)) * 100.0
        except Exception:
            qqq_atr_pct = None
    state = self._micro_regime_engine.update(
        vix_current=vix_current,
        vix_open=vix_open_for_micro,
        qqq_current=qqq_current,
        qqq_open=qqq_open,
        current_time=current_time,
        macro_regime_score=macro_for_micro,
        vix_level_override=vix_level_override,
        qqq_atr_pct=qqq_atr_pct,
    )

    # V6.8 P0 FIX: If Micro returns NO_TRADE, skip entirely - no conviction override
    # Micro's NO_TRADE decision is final. Reasons include:
    # - VIX floor not met (apathy market)
    # - QQQ move too small (no edge)
    # - QQQ flat, whipsaw, or caution regime
    # With V6.8 lowered gates, Micro will trade more often without needing overrides.
    if state.recommended_strategy == IntradayStrategy.NO_TRADE:
        # Minimal, keyed throttle to preserve distinct MICRO_NO_TRADE reasons.
        should_log = True

        vix_change_pct = (
            (vix_current - vix_open_for_micro) / vix_open_for_micro * 100
            if vix_open_for_micro > 0
            else 0.0
        )
        qqq_move_pct = (qqq_current - qqq_open) / qqq_open * 100 if qqq_open > 0 else 0.0
        if abs(qqq_move_pct) < config.QQQ_NOISE_THRESHOLD:
            block_code = "QQQ_FLAT"
        elif state.micro_regime in (
            MicroRegime.CHOPPY_LOW,
            MicroRegime.RISK_OFF_LOW,
            MicroRegime.BREAKING,
            MicroRegime.UNSTABLE,
            MicroRegime.VOLATILE,
        ):
            block_code = "REGIME_NOT_TRADEABLE"
        elif state.micro_regime in (MicroRegime.FULL_PANIC, MicroRegime.CRASH) and (
            qqq_move_pct >= 0
        ):
            # V9.2: FULL_PANIC/CRASH are tradeable with QQQ-down confirmation.
            # Keep telemetry aligned with gating logic instead of reporting generic non-tradeable.
            block_code = "PANIC_QQQ_GATE"
        elif (
            state.micro_regime
            in (
                MicroRegime.NORMAL,
                MicroRegime.CAUTION_LOW,
                MicroRegime.CAUTIOUS,
                MicroRegime.TRANSITION,
                MicroRegime.ELEVATED,
            )
            and abs(vix_change_pct) <= config.VIX_STABLE_BAND_HIGH
        ):
            block_code = "VIX_STABLE_LOW_CONVICTION"
        else:
            block_code = "CONFIRMATION_FAIL"

        if current_time:
            try:
                throttle_key = f"{current_time[:10]}|{block_code}"
                last_log = self._last_micro_no_trade_log_by_key.get(throttle_key)
                if last_log and last_log[:10] == current_time[:10]:
                    curr_min = int(current_time[11:13]) * 60 + int(current_time[14:16])
                    last_min = int(last_log[11:13]) * 60 + int(last_log[14:16])
                    interval_min = int(getattr(config, "MICRO_NO_TRADE_LOG_INTERVAL_MINUTES", 5))
                    if curr_min - last_min < interval_min:
                        should_log = False
                if should_log:
                    self._last_micro_no_trade_log_by_key[throttle_key] = current_time
            except (ValueError, IndexError):
                pass

        if should_log:
            self.log(
                f"MICRO_NO_TRADE[{block_code}]: Regime={state.micro_regime.value} | "
                f"VIXchg={vix_change_pct:+.2f}% | QQQ={qqq_move_pct:+.2f}% | "
                f"Score={state.micro_score:.0f} | Dir=NONE | Why={state.recommended_reason}"
            )
        return (
            False,
            None,
            state,
            f"NO_TRADE: MICRO_BLOCK:{block_code} ({state.micro_regime.value}) | "
            f"Why={state.recommended_reason}",
        )

    # Step 2: Check conviction (now without state-based fallback)
    (
        has_conviction,
        conviction_direction,
        conviction_reason,
    ) = self._micro_regime_engine.has_conviction(
        uvxy_intraday_pct=uvxy_pct,
        vix_level=vix_level_override if vix_level_override else vix_current,
    )

    # Step 3: Resolve direction.
    # V10.10: MICRO sovereignty option bypasses macro resolver.
    recommended_direction_str = (
        "BULLISH"
        if state.recommended_direction == OptionDirection.CALL
        else "BEARISH"
        if state.recommended_direction == OptionDirection.PUT
        else None
    )
    if has_conviction and conviction_direction:
        use_conviction_direction = True
        if (
            recommended_direction_str is not None
            and conviction_direction != recommended_direction_str
        ):
            # V10.5: Require stronger UVXY shock when conviction conflicts with
            # Micro regime direction to reduce wrong-way overrides.
            base_extreme = float(getattr(config, "MICRO_UVXY_CONVICTION_EXTREME", 0.03))
            conflict_mult = float(getattr(config, "MICRO_CONVICTION_CONFLICT_MULT", 1.5))
            required_extreme = base_extreme * conflict_mult
            if abs(uvxy_pct) < required_extreme:
                use_conviction_direction = False
                self.log(
                    f"MICRO_CONVICTION_GATED: Conv={conviction_direction} vs Rec={recommended_direction_str} | "
                    f"|UVXY|={abs(uvxy_pct):.1%} < {required_extreme:.1%}",
                    trades_only=True,
                )
        if use_conviction_direction:
            engine_direction = conviction_direction
        else:
            engine_direction = recommended_direction_str
    else:
        engine_direction = recommended_direction_str

    if engine_direction not in ("BULLISH", "BEARISH"):
        self._record_regime_decision(
            engine="MICRO",
            decision="BLOCK",
            strategy_attempted="MICRO_INTRADAY_SIGNAL",
            gate_name="MICRO_NO_DIRECTION",
        )
        return (
            False,
            None,
            state,
            f"NO_TRADE: MICRO_NO_DIRECTION ({state.micro_regime.value})",
        )
    should_trade = True
    resolved_direction = engine_direction
    resolve_reason = f"MICRO_SOVEREIGN: {resolved_direction}"

    # Step 5: Determine final direction
    # V6.4 FIX: Use resolved_direction whenever set (includes FOLLOW_MACRO path)
    # Previous bug: `has_conviction and resolved_direction` ignored FOLLOW_MACRO cases
    # where resolved_direction was set but has_conviction=False
    if resolved_direction:
        if resolved_direction == "BULLISH":
            final_direction = OptionDirection.CALL
        else:  # BEARISH
            final_direction = OptionDirection.PUT
    else:
        # No resolved direction - use Micro's computed direction from state
        final_direction = state.recommended_direction

    # If still no direction, can't trade
    if final_direction is None:
        self._record_regime_decision(
            engine="MICRO",
            decision="BLOCK",
            strategy_attempted="MICRO_INTRADAY_SIGNAL",
            gate_name="MICRO_FINAL_DIRECTION_NONE",
        )
        return False, None, state, "NO_DIRECTION: Micro has no recommended direction"

    transition_ctx = self._get_regime_transition_context(macro_regime_score)
    if bool(getattr(config, "MICRO_TRANSITION_GUARD_ENABLED", False)):
        block_gate, block_reason = self.evaluate_transition_policy_block(
            engine="MICRO",
            direction=final_direction,
            transition_ctx=transition_ctx,
        )
        if block_gate:
            self._record_regime_decision(
                engine="MICRO",
                decision="BLOCK",
                strategy_attempted=f"MICRO_{final_direction.value}",
                gate_name=block_gate,
                threshold_snapshot={"overlay": transition_ctx.get("transition_overlay", "")},
                context=transition_ctx,
            )
            return (
                False,
                None,
                state,
                f"NO_TRADE: {block_gate} ({block_reason})",
            )

    # Build reason string for logging
    if has_conviction:
        reason = f"CONVICTION: {conviction_reason} | {resolve_reason}"
    else:
        reason = f"MICRO_DIRECTION: {final_direction.value} | {resolve_reason}"

    self._record_regime_decision(
        engine="MICRO",
        decision="ALLOW",
        strategy_attempted=f"MICRO_{final_direction.value}",
        gate_name="MICRO_DIRECTION_RESOLVED",
        threshold_snapshot={"has_conviction": bool(has_conviction)},
        context=transition_ctx,
    )
    return should_trade, final_direction, state, reason
