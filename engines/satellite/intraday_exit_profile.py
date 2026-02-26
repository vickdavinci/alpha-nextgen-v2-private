"""Intraday exit profile helpers extracted from options_engine."""

from __future__ import annotations

import config
from models.enums import IntradayStrategy


def get_intraday_exit_profile_impl(self, entry_strategy: str) -> Tuple[float, Optional[float]]:
    """Return (target_pct, stop_pct_override) for strategy-aware intraday exits."""
    strategy = self._canonical_intraday_strategy_name(entry_strategy)
    if strategy == IntradayStrategy.ITM_MOMENTUM.value:
        if self._itm_horizon_engine.enabled():
            vix_for_itm = None
            try:
                vix_for_itm = float(self._iv_sensor.get_smoothed_vix())
            except Exception:
                vix_for_itm = None
            target, stop, _, _, _ = self._itm_horizon_engine.get_exit_profile(vix_for_itm)
            return (target, stop)
        return (
            float(getattr(config, "INTRADAY_ITM_TARGET", 0.35)),
            float(getattr(config, "INTRADAY_ITM_STOP", 0.35)),
        )
    if strategy == IntradayStrategy.MICRO_DEBIT_FADE.value:
        return (
            float(
                getattr(
                    config,
                    "MICRO_DEBIT_FADE_TARGET",
                    getattr(config, "INTRADAY_DEBIT_FADE_TARGET", 0.40),
                )
            ),
            float(
                getattr(
                    config,
                    "MICRO_DEBIT_FADE_STOP",
                    getattr(config, "INTRADAY_DEBIT_FADE_STOP", 0.25),
                )
            ),
        )
    if strategy == IntradayStrategy.MICRO_OTM_MOMENTUM.value:
        if bool(getattr(config, "MICRO_OTM_TIERED_RISK_ENABLED", False)):
            try:
                vix_val = float(self._iv_sensor.get_smoothed_vix())
            except Exception:
                vix_val = float(getattr(config, "MICRO_OTM_VIX_MED_MAX", 22.0))
            low_max = float(getattr(config, "MICRO_OTM_VIX_LOW_MAX", 16.0))
            med_max = float(getattr(config, "MICRO_OTM_VIX_MED_MAX", 22.0))
            if vix_val < low_max:
                return (
                    float(
                        getattr(
                            config,
                            "MICRO_OTM_PUT_TARGET_LOW_VIX",
                            getattr(config, "MICRO_OTM_TARGET_LOW_VIX", 0.45),
                        )
                    ),
                    float(
                        getattr(
                            config,
                            "MICRO_OTM_PUT_STOP_LOW_VIX",
                            getattr(config, "MICRO_OTM_STOP_LOW_VIX", 0.30),
                        )
                    ),
                )
            if vix_val < med_max:
                return (
                    float(
                        getattr(
                            config,
                            "MICRO_OTM_PUT_TARGET_MED_VIX",
                            getattr(config, "MICRO_OTM_TARGET_MED_VIX", 0.60),
                        )
                    ),
                    float(
                        getattr(
                            config,
                            "MICRO_OTM_PUT_STOP_MED_VIX",
                            getattr(config, "MICRO_OTM_STOP_MED_VIX", 0.35),
                        )
                    ),
                )
            return (
                float(
                    getattr(
                        config,
                        "MICRO_OTM_PUT_TARGET_HIGH_VIX",
                        getattr(config, "MICRO_OTM_TARGET_HIGH_VIX", 0.80),
                    )
                ),
                float(
                    getattr(
                        config,
                        "MICRO_OTM_PUT_STOP_HIGH_VIX",
                        getattr(config, "MICRO_OTM_STOP_HIGH_VIX", 0.40),
                    )
                ),
            )
        return (
            float(
                getattr(
                    config,
                    "MICRO_OTM_MOMENTUM_TARGET",
                    getattr(config, "INTRADAY_DEBIT_FADE_TARGET", 0.40),
                )
            ),
            float(
                getattr(
                    config,
                    "MICRO_OTM_MOMENTUM_STOP",
                    getattr(config, "INTRADAY_DEBIT_FADE_STOP", 0.25),
                )
            ),
        )
    if strategy == IntradayStrategy.PROTECTIVE_PUTS.value:
        return (
            float(getattr(config, "PROTECTIVE_PUTS_TARGET_PCT", 0.30)),
            float(getattr(config, "PROTECTIVE_PUTS_STOP_PCT", 0.30)),
        )
    # Swing single-leg fallback keeps existing defaults.
    return (float(getattr(config, "OPTIONS_PROFIT_TARGET_PCT", 0.60)), None)


def apply_intraday_target_overrides_impl(
    self,
    *,
    entry_strategy: str,
    target_pct: float,
    current_dte: Optional[int],
) -> float:
    """Apply strategy-specific target overrides (e.g., 0DTE fade profile)."""
    strategy = self._canonical_intraday_strategy_name(entry_strategy)
    if strategy == IntradayStrategy.MICRO_DEBIT_FADE.value and current_dte is not None:
        if int(current_dte) <= 0:
            return float(getattr(config, "MICRO_DEBIT_FADE_TARGET_0DTE", target_pct))
    return float(target_pct)


def apply_intraday_stop_overrides_impl(
    self,
    *,
    entry_strategy: str,
    stop_pct: float,
    current_dte: Optional[int],
) -> float:
    """Apply strategy-specific stop overrides (e.g., 0DTE fade profile)."""
    strategy = self._canonical_intraday_strategy_name(entry_strategy)
    if strategy == IntradayStrategy.MICRO_DEBIT_FADE.value and current_dte is not None:
        if int(current_dte) <= 0:
            return float(getattr(config, "MICRO_DEBIT_FADE_STOP_0DTE", stop_pct))
    return float(stop_pct)


def get_trail_config_impl(self, entry_strategy: str) -> Optional[Tuple[float, float]]:
    """Return (trigger_pct, trail_pct) for intraday strategy trailing stops."""
    strategy = self._canonical_intraday_strategy_name(entry_strategy)
    if strategy == IntradayStrategy.ITM_MOMENTUM.value:
        if self._itm_horizon_engine.enabled():
            vix_for_itm = None
            try:
                vix_for_itm = float(self._iv_sensor.get_smoothed_vix())
            except Exception:
                vix_for_itm = None
            _, _, trail_trigger, trail_pct, _ = self._itm_horizon_engine.get_exit_profile(
                vix_for_itm
            )
            return (trail_trigger, trail_pct)
        return (
            float(getattr(config, "INTRADAY_ITM_TRAIL_TRIGGER", 0.20)),
            float(getattr(config, "INTRADAY_ITM_TRAIL_PCT", 0.50)),
        )
    if strategy == IntradayStrategy.MICRO_DEBIT_FADE.value:
        return (
            float(
                getattr(
                    config,
                    "MICRO_DEBIT_FADE_TRAIL_TRIGGER",
                    getattr(config, "INTRADAY_DEBIT_FADE_TRAIL_TRIGGER", 0.25),
                )
            ),
            float(
                getattr(
                    config,
                    "MICRO_DEBIT_FADE_TRAIL_PCT",
                    getattr(config, "INTRADAY_DEBIT_FADE_TRAIL_PCT", 0.50),
                )
            ),
        )
    if strategy == IntradayStrategy.MICRO_OTM_MOMENTUM.value:
        if bool(getattr(config, "MICRO_OTM_TIERED_RISK_ENABLED", False)):
            try:
                vix_val = float(self._iv_sensor.get_smoothed_vix())
            except Exception:
                vix_val = float(getattr(config, "MICRO_OTM_VIX_MED_MAX", 22.0))
            low_max = float(getattr(config, "MICRO_OTM_VIX_LOW_MAX", 16.0))
            med_max = float(getattr(config, "MICRO_OTM_VIX_MED_MAX", 22.0))
            if vix_val < low_max:
                return (
                    float(
                        getattr(
                            config,
                            "MICRO_OTM_PUT_TRAIL_TRIGGER_LOW_VIX",
                            getattr(config, "MICRO_OTM_TRAIL_TRIGGER_LOW_VIX", 0.20),
                        )
                    ),
                    float(
                        getattr(
                            config,
                            "MICRO_OTM_PUT_TRAIL_PCT_LOW_VIX",
                            getattr(config, "MICRO_OTM_TRAIL_PCT_LOW_VIX", 0.35),
                        )
                    ),
                )
            if vix_val < med_max:
                return (
                    float(
                        getattr(
                            config,
                            "MICRO_OTM_PUT_TRAIL_TRIGGER_MED_VIX",
                            getattr(config, "MICRO_OTM_TRAIL_TRIGGER_MED_VIX", 0.28),
                        )
                    ),
                    float(
                        getattr(
                            config,
                            "MICRO_OTM_PUT_TRAIL_PCT_MED_VIX",
                            getattr(config, "MICRO_OTM_TRAIL_PCT_MED_VIX", 0.45),
                        )
                    ),
                )
            return (
                float(
                    getattr(
                        config,
                        "MICRO_OTM_PUT_TRAIL_TRIGGER_HIGH_VIX",
                        getattr(config, "MICRO_OTM_TRAIL_TRIGGER_HIGH_VIX", 0.25),
                    )
                ),
                float(
                    getattr(
                        config,
                        "MICRO_OTM_PUT_TRAIL_PCT_HIGH_VIX",
                        getattr(config, "MICRO_OTM_TRAIL_PCT_HIGH_VIX", 0.50),
                    )
                ),
            )
        return (
            float(
                getattr(
                    config,
                    "MICRO_OTM_MOMENTUM_TRAIL_TRIGGER",
                    getattr(config, "INTRADAY_DEBIT_FADE_TRAIL_TRIGGER", 0.25),
                )
            ),
            float(
                getattr(
                    config,
                    "MICRO_OTM_MOMENTUM_TRAIL_PCT",
                    getattr(config, "INTRADAY_DEBIT_FADE_TRAIL_PCT", 0.50),
                )
            ),
        )
    return None
