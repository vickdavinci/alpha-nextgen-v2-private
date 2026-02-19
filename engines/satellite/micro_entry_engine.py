"""Micro entry engine: isolates non-ITM_ENGINE MICRO entry gating and timing logic."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional, Tuple

import config
from models.enums import IntradayStrategy, MicroRegime, OptionDirection


class MicroEntryEngine:
    """Encapsulates MICRO-specific pre-contract gates and time-window checks."""

    def __init__(self, log_func: Optional[Callable[[str, bool], None]] = None):
        self._log_func = log_func

    def _log(self, message: str, trades_only: bool = False) -> None:
        if self._log_func:
            self._log_func(message, trades_only)

    def _is_micro_fade_strategy(self, entry_strategy: IntradayStrategy) -> bool:
        return entry_strategy in (
            IntradayStrategy.MICRO_DEBIT_FADE,
            IntradayStrategy.DEBIT_FADE,
        )

    def _is_micro_otm_strategy(self, entry_strategy: IntradayStrategy) -> bool:
        return entry_strategy == IntradayStrategy.MICRO_OTM_MOMENTUM

    def apply_pre_contract_gates(
        self,
        *,
        state: Any,
        entry_strategy: IntradayStrategy,
        direction: OptionDirection,
        itm_engine_mode: bool,
        current_time: str,
        size_multiplier: float,
        macro_regime_score: float,
        qqq_current: float,
        vix_current: float,
        vix_level_override: Optional[float],
        algorithm: Any,
        iv_sensor: Any,
        call_cooldown_until_date: Optional[Any],
        call_consecutive_losses: int,
    ) -> Tuple[float, Optional[str], Optional[str]]:
        """Return (new_size_multiplier, fail_code, fail_detail)."""
        if itm_engine_mode:
            return size_multiplier, None, None

        # MICRO is sovereign from macro regime policy; keep signature stable
        # for compatibility with existing call sites.
        _ = macro_regime_score

        # V9: Keep CAUTION_LOW / TRANSITION participation but cut size.
        if state.micro_regime in (MicroRegime.CAUTION_LOW, MicroRegime.TRANSITION):
            caution_scale = float(
                getattr(
                    config,
                    "CAUTION_LOW_SIZE_MULT"
                    if state.micro_regime == MicroRegime.CAUTION_LOW
                    else "TRANSITION_SIZE_MULT",
                    0.50,
                )
            )
            adjusted = min(size_multiplier, caution_scale)
            if adjusted < size_multiplier:
                self._log(
                    f"INTRADAY: {state.micro_regime.value} size reduction | "
                    f"Size {size_multiplier:.0%}->{adjusted:.0%}",
                    trades_only=True,
                )
                size_multiplier = adjusted

        if direction == OptionDirection.CALL:
            # Gate 1: consecutive CALL-loss cooldown (adaptive pause)
            if getattr(config, "CALL_GATE_CONSECUTIVE_LOSS_ENABLED", True):
                trade_date = None
                try:
                    trade_date = datetime.strptime(current_time[:10], "%Y-%m-%d").date()
                except Exception:
                    trade_date = None
                if (
                    trade_date is not None
                    and call_cooldown_until_date is not None
                    and trade_date <= call_cooldown_until_date
                ):
                    self._log(
                        f"INTRADAY: CALL blocked - loss cooldown active | "
                        f"Date={trade_date.isoformat()} <= {call_cooldown_until_date.isoformat()} | "
                        f"LossStreak={call_consecutive_losses}"
                    )
                    return size_multiplier, "E_CALL_GATE_LOSS_COOLDOWN", None

            vix_for_call = vix_level_override if vix_level_override is not None else vix_current
            call_block_vix = getattr(config, "INTRADAY_CALL_BLOCK_VIX_MIN", 25.0)
            if vix_for_call >= call_block_vix:
                self._log(
                    f"INTRADAY: CALL blocked in stress | "
                    f"VIX={vix_for_call:.1f} >= {call_block_vix:.1f}"
                )
                return size_multiplier, "E_CALL_GATE_STRESS", None

            # Gate 2: trend filter (block CALLs below QQQ SMA20)
            if getattr(config, "CALL_GATE_MA20_ENABLED", True) and algorithm is not None:
                qqq_sma20 = getattr(algorithm, "qqq_sma20", None)
                if qqq_sma20 is not None and getattr(qqq_sma20, "IsReady", False):
                    sma20_value = float(qqq_sma20.Current.Value)
                    if qqq_current < sma20_value:
                        bypass_vix_max = float(
                            getattr(config, "CALL_GATE_MA20_BYPASS_VIX_MAX", 14.5)
                        )
                        bypass_size_mult = float(
                            getattr(config, "CALL_GATE_MA20_BYPASS_SIZE_MULT", 0.70)
                        )
                        bullish_regimes = {
                            MicroRegime.PERFECT_MR,
                            MicroRegime.GOOD_MR,
                            MicroRegime.NORMAL,
                            MicroRegime.RECOVERING,
                            MicroRegime.IMPROVING,
                        }
                        can_bypass = (
                            state.micro_regime in bullish_regimes and vix_for_call <= bypass_vix_max
                        )
                        if can_bypass:
                            size_multiplier *= bypass_size_mult
                            self._log(
                                f"INTRADAY: CALL below MA20 bypassed | "
                                f"QQQ={qqq_current:.2f} < SMA20={sma20_value:.2f} | "
                                f"Regime={state.micro_regime.value} | "
                                f"VIX={vix_for_call:.1f} <= {bypass_vix_max:.1f} | "
                                f"SizeMult={size_multiplier:.2f}",
                                trades_only=True,
                            )
                        else:
                            self._log(
                                f"INTRADAY: CALL blocked below MA20 | "
                                f"QQQ={qqq_current:.2f} < SMA20={sma20_value:.2f}"
                            )
                            return size_multiplier, "E_CALL_GATE_MA20", None

            # Gate 3: early fear build (5-day VIX trend rising)
            if getattr(config, "CALL_GATE_VIX_5D_RISING_ENABLED", True):
                vix_5d_change = (
                    iv_sensor.get_vix_5d_change()
                    if iv_sensor and iv_sensor.is_conviction_ready()
                    else None
                )
                if vix_5d_change is not None:
                    low_vix_max = float(getattr(config, "VIX_LEVEL_LOW_MAX", 18.0))
                    med_vix_max = float(getattr(config, "VIX_LEVEL_MEDIUM_MAX", 25.0))
                    if vix_for_call < low_vix_max:
                        vix_tier = "LOW"
                        gate_enabled = bool(
                            getattr(config, "CALL_GATE_VIX_5D_RISING_ENABLED_LOW_VIX", False)
                        )
                        vix_5d_gate = float(
                            getattr(
                                config,
                                "CALL_GATE_VIX_5D_RISING_PCT_LOW_VIX",
                                getattr(config, "CALL_GATE_VIX_5D_RISING_PCT", 0.14),
                            )
                        )
                    elif vix_for_call < med_vix_max:
                        vix_tier = "MED"
                        gate_enabled = bool(
                            getattr(config, "CALL_GATE_VIX_5D_RISING_ENABLED_MED_VIX", True)
                        )
                        vix_5d_gate = float(
                            getattr(
                                config,
                                "CALL_GATE_VIX_5D_RISING_PCT_MED_VIX",
                                getattr(config, "CALL_GATE_VIX_5D_RISING_PCT", 0.14),
                            )
                        )
                    else:
                        vix_tier = "HIGH"
                        gate_enabled = bool(
                            getattr(config, "CALL_GATE_VIX_5D_RISING_ENABLED_HIGH_VIX", True)
                        )
                        vix_5d_gate = float(
                            getattr(
                                config,
                                "CALL_GATE_VIX_5D_RISING_PCT_HIGH_VIX",
                                getattr(config, "CALL_GATE_VIX_5D_RISING_PCT", 0.14),
                            )
                        )

                    if gate_enabled and vix_5d_change >= vix_5d_gate:
                        self._log(
                            f"INTRADAY: CALL blocked by VIX 5d trend | "
                            f"Tier={vix_tier} | VIX={vix_for_call:.1f} | "
                            f"VIX5d={vix_5d_change:+.1%} >= {vix_5d_gate:.1%}"
                        )
                        return size_multiplier, "E_CALL_GATE_VIX5D", None

        if direction == OptionDirection.PUT:
            if getattr(config, "PUT_GATE_CONSECUTIVE_LOSS_ENABLED", True):
                trade_date = None
                try:
                    trade_date = datetime.strptime(current_time[:10], "%Y-%m-%d").date()
                except Exception:
                    trade_date = None
                if (
                    trade_date is not None
                    and getattr(state, "put_cooldown_until_date", None) is not None
                    and trade_date <= state.put_cooldown_until_date
                ):
                    self._log(
                        f"INTRADAY: PUT blocked - loss cooldown active | "
                        f"Date={trade_date.isoformat()} <= {state.put_cooldown_until_date.isoformat()} | "
                        f"LossStreak={getattr(state, 'put_consecutive_losses', 0)}"
                    )
                    return size_multiplier, "E_PUT_GATE_LOSS_COOLDOWN", None

            vix_for_put = vix_level_override if vix_level_override is not None else vix_current
            put_entry_vix_max = getattr(config, "PUT_ENTRY_VIX_MAX", 36.0)
            if vix_for_put > put_entry_vix_max:
                self._log(
                    f"INTRADAY: PUT blocked - VIX {vix_for_put:.1f} > max {put_entry_vix_max:.1f}"
                )
                return size_multiplier, "E_PUT_GATE_VIX_MAX", None
            put_reduce_start = getattr(config, "PUT_SIZE_REDUCTION_VIX_START", 30.0)
            put_reduce_factor = getattr(config, "PUT_SIZE_REDUCTION_FACTOR", 0.50)
            if vix_for_put >= put_reduce_start:
                size_multiplier *= put_reduce_factor
                self._log(
                    f"INTRADAY: PUT size reduced in high VIX | "
                    f"VIX={vix_for_put:.1f} >= {put_reduce_start:.1f} | "
                    f"Multiplier={size_multiplier:.2f}"
                )

        return size_multiplier, None, None

    def validate_time_window(
        self,
        *,
        entry_strategy: IntradayStrategy,
        itm_engine_mode: bool,
        state: Any,
        current_hour: int,
        current_minute: int,
    ) -> Tuple[bool, Optional[str]]:
        time_minutes = current_hour * 60 + current_minute
        if self._is_micro_fade_strategy(entry_strategy) or self._is_micro_otm_strategy(
            entry_strategy
        ):
            fade_start_cfg = str(
                getattr(
                    config,
                    "MICRO_DEBIT_FADE_START"
                    if self._is_micro_fade_strategy(entry_strategy)
                    else "MICRO_OTM_MOMENTUM_START",
                    config.INTRADAY_DEBIT_FADE_START,
                )
            )
            fade_end_cfg = str(
                getattr(
                    config,
                    "MICRO_DEBIT_FADE_END"
                    if self._is_micro_fade_strategy(entry_strategy)
                    else "MICRO_OTM_MOMENTUM_END",
                    config.INTRADAY_DEBIT_FADE_END,
                )
            )
            fade_start = fade_start_cfg.split(":")
            fade_end = fade_end_cfg.split(":")
            start_time = int(fade_start[0]) * 60 + int(fade_start[1])
            end_time = int(fade_end[0]) * 60 + int(fade_end[1])
            if not (start_time <= time_minutes <= end_time):
                self._log(
                    f"INTRADAY_TIME_REJECT: {entry_strategy.value} at {current_hour}:{current_minute:02d} "
                    f"outside window {fade_start_cfg}-{fade_end_cfg}"
                )
                return False, "E_INTRADAY_TIME_WINDOW"

        elif entry_strategy == IntradayStrategy.ITM_MOMENTUM:
            # ITM_ENGINE owns its own timing checks in ITMHorizonEngine.evaluate_entry().
            if itm_engine_mode:
                return True, None
            if state.micro_regime == MicroRegime.CAUTION_LOW:
                self._log(
                    "INTRADAY: ITM_MOMENTUM blocked in regime CAUTION_LOW",
                    trades_only=True,
                )
                return False, "E_ITM_MOMENTUM_REGIME_BLOCK"

            itm_start_cfg = config.INTRADAY_ITM_START
            itm_end_cfg = config.INTRADAY_ITM_END
            itm_start = itm_start_cfg.split(":")
            itm_end = itm_end_cfg.split(":")
            start_time = int(itm_start[0]) * 60 + int(itm_start[1])
            end_time = int(itm_end[0]) * 60 + int(itm_end[1])
            if not (start_time <= time_minutes <= end_time):
                self._log(
                    f"INTRADAY_TIME_REJECT: ITM_MOMENTUM at {current_hour}:{current_minute:02d} "
                    f"outside window {itm_start_cfg}-{itm_end_cfg}"
                )
                return False, "E_INTRADAY_TIME_WINDOW"

        return True, None
