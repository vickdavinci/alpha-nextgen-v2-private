"""Micro entry engine: isolates non-ITM_ENGINE MICRO entry gating and timing logic."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

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

    def _parse_time_minutes(self, current_time: Optional[str]) -> Optional[int]:
        if not current_time:
            return None
        try:
            hh = int(current_time[11:13])
            mm = int(current_time[14:16])
            return (hh * 60) + mm
        except Exception:
            return None

    def _resolve_transition_overlay(
        self, transition_ctx: Optional[Dict[str, Any]]
    ) -> Tuple[str, int]:
        if not isinstance(transition_ctx, dict):
            return ("STABLE", 999)
        overlay = str(transition_ctx.get("transition_overlay", "STABLE") or "STABLE").upper()
        try:
            bars_since_flip = int(transition_ctx.get("overlay_bars_since_flip", 999) or 999)
        except Exception:
            bars_since_flip = 999
        return (overlay, bars_since_flip)

    def _resolve_micro_otm_concurrent_cap(
        self,
        *,
        fallback_cap: int,
        state: Optional[Any],
        direction: Optional[OptionDirection],
        vix_current: Optional[float],
        transition_ctx: Optional[Dict[str, Any]],
    ) -> int:
        base_cap = max(
            1,
            int(getattr(config, "MICRO_OTM_MAX_CONCURRENT_POSITIONS_BASE", fallback_cap) or 1),
        )
        if not bool(getattr(config, "MICRO_OTM_ADAPTIVE_CONCURRENT_CAP_ENABLED", False)):
            return base_cap

        if direction != OptionDirection.CALL:
            return base_cap

        try:
            vix_val = float(vix_current) if vix_current is not None else 99.0
        except Exception:
            vix_val = 99.0

        low_vix_max = float(getattr(config, "MICRO_OTM_CONCURRENT_CAP_LOW_VIX_MAX", 16.0))
        if vix_val > low_vix_max:
            return base_cap

        micro_regime = getattr(state, "micro_regime", None)
        bullish_ok = micro_regime in {
            MicroRegime.PERFECT_MR,
            MicroRegime.GOOD_MR,
            MicroRegime.RECOVERING,
            MicroRegime.IMPROVING,
        }
        if not bullish_ok:
            return base_cap

        overlay, bars_since_flip = self._resolve_transition_overlay(transition_ctx)
        recovery_min_bars = int(getattr(config, "MICRO_OTM_CONCURRENT_CAP_RECOVERY_MIN_BARS", 6))
        if overlay == "DETERIORATION":
            return base_cap
        if overlay == "RECOVERY" and bars_since_flip < recovery_min_bars:
            return base_cap

        boosted_cap = int(
            getattr(config, "MICRO_OTM_MAX_CONCURRENT_POSITIONS_LOW_VIX_CALL", base_cap) or base_cap
        )
        return max(base_cap, boosted_cap)

    def validate_lane_caps(
        self,
        *,
        entry_strategy: IntradayStrategy,
        engine_positions: Dict[str, Any],
        has_pending_engine_entry: Callable[[Optional[str]], bool],
        itm_trades_today: int,
        micro_trades_today: int,
        lane_resolver: Callable[[str], str],
        lane_caps: Optional[Dict[str, int]] = None,
        daily_caps: Optional[Dict[str, int]] = None,
        state: Optional[Any] = None,
        direction: Optional[OptionDirection] = None,
        vix_current: Optional[float] = None,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str], Optional[str], str]:
        """Validate per-lane daily caps, pending entry lock, and concurrent caps."""
        pending_lane = lane_resolver(entry_strategy.value)
        lane_caps = lane_caps or {}
        daily_caps = daily_caps or {}

        # Engine-sovereign daily caps (avoid MICRO/ITM cross-throttling).
        if pending_lane == "ITM":
            itm_cap = int(daily_caps.get("ITM", getattr(config, "ITM_MAX_TRADES_PER_DAY", 0)) or 0)
            if itm_cap > 0 and itm_trades_today >= itm_cap:
                return (
                    False,
                    "R_ITM_DAILY_CAP",
                    f"ITM={itm_trades_today}/{itm_cap}",
                    pending_lane,
                )
        else:
            micro_cap = int(
                daily_caps.get("MICRO", getattr(config, "MICRO_MAX_TRADES_PER_DAY", 0)) or 0
            )
            if micro_cap > 0 and micro_trades_today >= micro_cap:
                return (
                    False,
                    "R_MICRO_DAILY_CAP",
                    f"MICRO={micro_trades_today}/{micro_cap}",
                    pending_lane,
                )

        if has_pending_engine_entry(pending_lane):
            return False, "E_INTRADAY_PENDING_ENTRY", pending_lane, pending_lane

        default_lane_cap = int(
            getattr(
                config,
                "ITM_MAX_CONCURRENT_POSITIONS"
                if pending_lane == "ITM"
                else "MICRO_MAX_CONCURRENT_POSITIONS",
                1,
            )
            or 0
        )
        lane_cap = int(lane_caps.get(pending_lane, default_lane_cap) or 0)
        if pending_lane != "ITM" and self._is_micro_otm_strategy(entry_strategy):
            lane_cap = self._resolve_micro_otm_concurrent_cap(
                fallback_cap=max(1, lane_cap),
                state=state,
                direction=direction,
                vix_current=vix_current,
                transition_ctx=transition_ctx,
            )
        current_lane_positions = len(engine_positions.get(pending_lane) or [])
        if lane_cap > 0 and current_lane_positions >= lane_cap:
            cap_code = "R_ITM_CONCURRENT_CAP" if pending_lane == "ITM" else "R_MICRO_CONCURRENT_CAP"
            return (
                False,
                cap_code,
                f"{pending_lane}={current_lane_positions}/{lane_cap}",
                pending_lane,
            )
        return True, None, None, pending_lane

    def validate_contract_friction(
        self,
        *,
        strategy_value: str,
        contract_spread_pct: float,
        entry_strategy: Optional[IntradayStrategy] = None,
        direction: Optional[OptionDirection] = None,
        vix_current: Optional[float] = None,
        current_time: Optional[str] = None,
        days_to_expiry: Optional[int] = None,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Validate strategy-aware bid/ask friction cap before entry."""
        strategy_for_friction = str(strategy_value or "").upper()
        if strategy_for_friction in (
            IntradayStrategy.ITM_MOMENTUM.value,
            IntradayStrategy.PROTECTIVE_PUTS.value,
        ):
            max_friction_pct = float(getattr(config, "INTRADAY_ITM_MAX_BID_ASK_SPREAD_PCT", 0.12))
        elif strategy_for_friction in (
            IntradayStrategy.MICRO_DEBIT_FADE.value,
            IntradayStrategy.DEBIT_FADE.value,
            IntradayStrategy.CREDIT_SPREAD.value,
        ):
            max_friction_pct = float(getattr(config, "INTRADAY_MICRO_MAX_BID_ASK_SPREAD_PCT", 0.10))
        elif strategy_for_friction == IntradayStrategy.MICRO_OTM_MOMENTUM.value:
            max_friction_pct = float(
                getattr(
                    config,
                    "INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT",
                    getattr(config, "INTRADAY_MICRO_MAX_BID_ASK_SPREAD_PCT", 0.10),
                )
            )
            dte = int(days_to_expiry) if days_to_expiry is not None else -1
            if dte == 0:
                max_friction_pct = min(
                    max_friction_pct,
                    float(
                        getattr(
                            config,
                            "INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT_0DTE",
                            max_friction_pct,
                        )
                    ),
                )

            minutes = self._parse_time_minutes(current_time)
            late_cfg = str(getattr(config, "INTRADAY_MICRO_OTM_FRICTION_LATE_START", "13:30"))
            try:
                lh, lm = late_cfg.split(":")
                late_minutes = (int(lh) * 60) + int(lm)
            except Exception:
                late_minutes = (13 * 60) + 30
            if minutes is not None and minutes >= late_minutes:
                max_friction_pct = min(
                    max_friction_pct,
                    float(
                        getattr(
                            config,
                            "INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT_LATE_DAY",
                            max_friction_pct,
                        )
                    ),
                )

            try:
                vix_val = float(vix_current) if vix_current is not None else None
            except Exception:
                vix_val = None
            stress_vix = float(getattr(config, "INTRADAY_MICRO_OTM_FRICTION_STRESS_VIX", 20.0))
            if vix_val is not None and vix_val >= stress_vix:
                max_friction_pct = min(
                    max_friction_pct,
                    float(
                        getattr(
                            config,
                            "INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT_STRESS",
                            max_friction_pct,
                        )
                    ),
                )

            overlay, bars_since_flip = self._resolve_transition_overlay(transition_ctx)
            if overlay in {"DETERIORATION", "RECOVERY"} and bars_since_flip < int(
                getattr(config, "MICRO_OTM_TRANSITION_BLOCK_BARS", 4)
            ):
                max_friction_pct = min(
                    max_friction_pct,
                    float(
                        getattr(
                            config,
                            "INTRADAY_MICRO_OTM_MAX_BID_ASK_SPREAD_PCT_EARLY_TRANSITION",
                            max_friction_pct,
                        )
                    ),
                )
        else:
            max_friction_pct = float(getattr(config, "OPTIONS_SPREAD_MAX_PCT", 0.14))

        if contract_spread_pct > max_friction_pct:
            detail = (
                f"{contract_spread_pct:.1%}>{max_friction_pct:.1%}|{strategy_for_friction}|"
                f"DTE={days_to_expiry if days_to_expiry is not None else 'NA'}"
            )
            return (
                False,
                "E_INTRADAY_FRICTION_CAP",
                detail,
            )
        return True, None, None

    def validate_contract_selection(
        self,
        *,
        entry_strategy: IntradayStrategy,
        best_contract: Any,
        direction: Optional[OptionDirection],
    ) -> Tuple[bool, Optional[str], Optional[str], str]:
        """Validate selected contract presence and directional alignment."""
        strategy_names = {
            IntradayStrategy.MICRO_DEBIT_FADE: "MICRO_FADE",
            IntradayStrategy.MICRO_OTM_MOMENTUM: "MICRO_OTM",
            IntradayStrategy.DEBIT_FADE: "MICRO_FADE",
            IntradayStrategy.ITM_MOMENTUM: "ITM_MOM",
            IntradayStrategy.CREDIT_SPREAD: "CREDIT",
            IntradayStrategy.PROTECTIVE_PUTS: "PROTECTIVE_PUTS",
        }
        strategy_name = strategy_names.get(entry_strategy, "UNKNOWN")

        if best_contract is None:
            return False, "E_INTRADAY_NO_CONTRACT", strategy_name, strategy_name

        if direction is not None and getattr(best_contract, "direction", None) != direction:
            return False, "E_INTRADAY_DIRECTION_MISMATCH", None, strategy_name

        return True, None, None, strategy_name

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
        transition_ctx: Optional[Dict[str, Any]] = None,
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
                soft_gate_applied = False
                if self._is_micro_otm_strategy(entry_strategy) and bool(
                    getattr(config, "MICRO_OTM_STRESS_SOFT_GATE_ENABLED", False)
                ):
                    soft_max_vix = float(
                        getattr(config, "MICRO_OTM_STRESS_SOFT_MAX_VIX", call_block_vix)
                    )
                    soft_size_mult = float(getattr(config, "MICRO_OTM_STRESS_SOFT_SIZE_MULT", 0.60))
                    overlay, bars_since_flip = self._resolve_transition_overlay(transition_ctx)
                    allow_overlays_cfg = getattr(
                        config,
                        "MICRO_OTM_STRESS_SOFT_ALLOW_OVERLAYS",
                        ("STABLE", "RECOVERY"),
                    )
                    allow_overlays = {
                        str(item).upper()
                        for item in (
                            allow_overlays_cfg
                            if isinstance(allow_overlays_cfg, (list, tuple, set))
                            else []
                        )
                    }
                    recovery_min_bars = int(
                        getattr(config, "MICRO_OTM_CONCURRENT_CAP_RECOVERY_MIN_BARS", 6)
                    )
                    can_soft_gate = (
                        vix_for_call < soft_max_vix
                        and overlay in allow_overlays
                        and (overlay != "RECOVERY" or bars_since_flip >= recovery_min_bars)
                    )
                    if can_soft_gate:
                        size_multiplier *= max(0.0, soft_size_mult)
                        self._log(
                            f"INTRADAY: CALL stress soft-gate | "
                            f"VIX={vix_for_call:.1f} [{call_block_vix:.1f}-{soft_max_vix:.1f}) | "
                            f"Overlay={overlay} Bars={bars_since_flip} | "
                            f"SizeMult={size_multiplier:.2f}",
                            trades_only=True,
                        )
                        soft_gate_applied = True
                if not soft_gate_applied:
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

    def run_engine_cycle(
        self,
        *,
        host: Any,
        chain: Any,
        qqq_price: float,
        regime_score: float,
        size_multiplier: float,
        effective_portfolio_value: float,
        vix_intraday: float,
        vix_level_cboe: Optional[float],
        transition_ctx: Optional[Dict[str, Any]],
        uvxy_pct: float,
        micro_intraday_cooldown_active: bool,
    ) -> Tuple[Optional[OptionDirection], str]:
        """Run MICRO intraday lane. Return value is kept for backward compatibility."""
        algorithm = getattr(host, "algorithm", None)
        if algorithm is None:
            return None, ""
        transition_ctx = transition_ctx or {}

        (
            should_trade,
            intraday_direction,
            micro_state,
            signal_reason,
        ) = host.generate_micro_engine_signal(
            vix_current=vix_intraday,
            vix_open=algorithm._vix_at_open,
            qqq_current=qqq_price,
            qqq_open=algorithm._qqq_at_open,
            uvxy_pct=uvxy_pct,
            macro_regime_score=regime_score,
            current_time=str(algorithm.Time),
            vix_level_override=vix_level_cboe,
            premarket_shock_pct=algorithm._get_premarket_shock_memory_pct(),
        )

        intraday_size_multiplier = size_multiplier
        if "NEUTRAL_ALIGNED_HALF" in signal_reason:
            intraday_size_multiplier *= config.NEUTRAL_ALIGNED_SIZE_MULT
        if "MISALIGNED_HALF" in signal_reason:
            intraday_size_multiplier *= getattr(config, "MICRO_MISALIGNED_SIZE_MULT", 0.50)

        intraday_signal_id = (
            f"MICRO-{algorithm.Time.strftime('%Y%m%d-%H%M')}-"
            f"{algorithm._diag_intraday_candidate_count + 1}"
        )
        intraday_strategy = None

        itm_dir = None
        itm_reason = ""
        forced_intraday_strategy = None

        if micro_intraday_cooldown_active:
            should_trade = False
            intraday_direction = None
            signal_reason = "R_COOLDOWN_INTRADAY_MICRO"

        if not should_trade:
            algorithm._log_high_frequency_event(
                config_flag="LOG_INTRADAY_BLOCKED_BACKTEST_ENABLED",
                category="INTRADAY_BLOCKED",
                reason_key=algorithm._canonical_options_reason_code(signal_reason),
                message=f"INTRADAY: Blocked - {signal_reason}",
            )
            intraday_direction = None
            retry_payload = None
            if not micro_intraday_cooldown_active and signal_reason != "R_COOLDOWN_INTRADAY_MICRO":
                retry_payload = algorithm._consume_engine_retry("MICRO")
            if retry_payload is not None:
                retry_direction, retry_reason_code = retry_payload
                should_trade = True
                intraday_direction = retry_direction
                signal_reason = (
                    f"RETRY_ONCE: {retry_reason_code} | "
                    f"Reusing prior direction={intraday_direction.value}"
                )
                retry_strategy = host.get_last_engine_strategy()
                if retry_strategy == IntradayStrategy.NO_TRADE:
                    retry_strategy = IntradayStrategy.MICRO_OTM_MOMENTUM
                forced_intraday_strategy = retry_strategy
                algorithm.Log(f"INTRADAY_RETRY: {signal_reason} | Strategy={retry_strategy.value}")
        else:
            if (
                intraday_direction == OptionDirection.CALL
                and algorithm._is_premarket_ladder_call_block_active()
            ):
                intraday_direction = None
                signal_reason = (
                    f"PREMARKET_LADDER_CALL_BLOCK: {algorithm._premarket_vix_ladder_reason}"
                )
                algorithm._log_high_frequency_event(
                    config_flag="LOG_INTRADAY_BLOCKED_BACKTEST_ENABLED",
                    category="INTRADAY_BLOCKED",
                    reason_key="PREMARKET_LADDER_CALL_BLOCK",
                    message=f"INTRADAY: Blocked - {signal_reason}",
                )
            else:
                candidate_strategy = forced_intraday_strategy or host.get_last_engine_strategy()
                intraday_strategy = candidate_strategy
                (
                    preflight_ok,
                    preflight_code,
                    preflight_detail,
                ) = host.preflight_engine_entry(
                    strategy=candidate_strategy,
                    direction=intraday_direction,
                    state=micro_state,
                    vix_current=(vix_level_cboe if vix_level_cboe is not None else vix_intraday),
                    transition_ctx=transition_ctx,
                )
                if not preflight_ok:
                    blocked_direction = intraday_direction
                    intraday_direction = None
                    detail = str(preflight_detail or "").strip()
                    signal_reason = f"{preflight_code}: {detail}" if detail else str(preflight_code)
                    algorithm._log_high_frequency_event(
                        config_flag="LOG_INTRADAY_BLOCKED_BACKTEST_ENABLED",
                        category="INTRADAY_BLOCKED",
                        reason_key=algorithm._canonical_options_reason_code(preflight_code),
                        message=f"INTRADAY: Blocked - {signal_reason}",
                    )
                    drop_code = algorithm._canonical_options_reason_code(
                        str(preflight_code or "E_PREFLIGHT_BLOCK")
                    )
                    drop_logged = algorithm._log_engine_signal_dropped(
                        signal_id=intraday_signal_id,
                        code=drop_code,
                        reason=signal_reason,
                        retry_hint="None",
                        direction=blocked_direction,
                        strategy=candidate_strategy,
                        contract_symbol="NONE",
                        validation_detail=detail,
                    )
                    if drop_logged:
                        algorithm._diag_intraday_dropped_count += 1
                        algorithm._inc_engine_counter(
                            algorithm._diag_intraday_dropped_by_engine,
                            candidate_strategy,
                        )
                        algorithm._inc_micro_dte_counter(
                            algorithm._diag_micro_dte_dropped,
                            None,
                        )
                        algorithm._record_micro_drop_reason_dte(drop_code, None)
                elif algorithm._mark_engine_signal_event("CANDIDATE", intraday_signal_id):
                    algorithm._diag_intraday_candidate_count += 1
                    algorithm._inc_engine_counter(
                        algorithm._diag_intraday_candidates_by_engine,
                        candidate_strategy,
                    )
                    algorithm._log_high_frequency_event(
                        config_flag="LOG_INTRADAY_CANDIDATE_BACKTEST_ENABLED",
                        category="INTRADAY_CANDIDATE",
                        reason_key=(
                            f"{candidate_strategy.value if candidate_strategy else 'NONE'}|"
                            f"{intraday_direction.value if intraday_direction else 'NONE'}"
                        ),
                        message=(
                            f"INTRADAY_SIGNAL_CANDIDATE: SignalId={intraday_signal_id} | {signal_reason} | "
                            f"Direction={intraday_direction.value if intraday_direction else 'NONE'}"
                        ),
                    )
                    algorithm._record_signal_lifecycle_event(
                        engine=algorithm._engine_bucket_from_strategy(candidate_strategy),
                        event="CANDIDATE",
                        signal_id=intraday_signal_id,
                        direction=intraday_direction.value if intraday_direction else "",
                        strategy=candidate_strategy.value if candidate_strategy else "",
                        code="R_OK",
                        gate_name="INTRADAY_SIGNAL_CANDIDATE",
                        reason=signal_reason,
                        contract_symbol="",
                    )

        if intraday_direction is None:
            intraday_contract = None
        else:
            algorithm.Log(
                f"INTRADAY: Proceeding with ladder size mult {algorithm._premarket_vix_size_mult:.2f}"
            )
            intraday_strategy = (
                intraday_strategy or forced_intraday_strategy or host.get_last_engine_strategy()
            )
            intraday_contract = algorithm._select_intraday_option_contract(
                chain,
                intraday_direction,
                strategy=intraday_strategy,
                vix_current=vix_intraday,
            )
            if intraday_contract is None:
                algorithm.Log(
                    f"INTRADAY: No contract selected | "
                    f"Dir={intraday_direction.value} | "
                    f"Strategy={intraday_strategy.value if intraday_strategy else 'NONE'}"
                )
                drop_logged = algorithm._log_engine_signal_dropped(
                    signal_id=intraday_signal_id,
                    code="E_NO_CONTRACT_SELECTED",
                    reason=signal_reason,
                    retry_hint="None",
                    direction=intraday_direction,
                    strategy=intraday_strategy,
                    contract_symbol="NONE",
                )
                if drop_logged:
                    algorithm._diag_intraday_dropped_count += 1
                    algorithm._inc_engine_counter(
                        algorithm._diag_intraday_dropped_by_engine,
                        intraday_strategy,
                    )
                    algorithm._inc_micro_dte_counter(
                        algorithm._diag_micro_dte_dropped,
                        None,
                    )
                    algorithm._record_micro_drop_reason_dte("E_NO_CONTRACT_SELECTED", None)

        if intraday_contract is not None and (
            intraday_contract.bid <= 0 or intraday_contract.ask <= 0
        ):
            algorithm.Log(
                f"INTRADAY_PRICE_REJECT: {intraday_contract.symbol} | "
                f"Bid={intraday_contract.bid} Ask={intraday_contract.ask}"
            )
            drop_logged = algorithm._log_engine_signal_dropped(
                signal_id=intraday_signal_id,
                code="E_BID_ASK_INVALID",
                reason=signal_reason,
                retry_hint="None",
                direction=intraday_direction,
                strategy=intraday_strategy,
                contract_symbol=str(intraday_contract.symbol),
            )
            if drop_logged:
                algorithm._diag_intraday_dropped_count += 1
                algorithm._inc_engine_counter(
                    algorithm._diag_intraday_dropped_by_engine,
                    intraday_strategy,
                )
                algorithm._inc_micro_dte_counter(
                    algorithm._diag_micro_dte_dropped,
                    getattr(intraday_contract, "days_to_expiry", None),
                )
                algorithm._record_micro_drop_reason_dte(
                    "E_BID_ASK_INVALID",
                    getattr(intraday_contract, "days_to_expiry", None),
                )
            intraday_contract = None

        if intraday_contract is not None:
            algorithm._inc_micro_dte_counter(
                algorithm._diag_micro_dte_candidates,
                getattr(intraday_contract, "days_to_expiry", None),
            )
            qqq_atr_value = algorithm.qqq_atr.Current.Value if algorithm.qqq_atr.IsReady else 0.0
            intraday_signal = host.check_engine_entry_signal(
                vix_current=vix_intraday,
                vix_open=algorithm._vix_at_open,
                qqq_current=qqq_price,
                qqq_open=algorithm._qqq_at_open,
                current_hour=algorithm.Time.hour,
                current_minute=algorithm.Time.minute,
                current_time=str(algorithm.Time),
                portfolio_value=effective_portfolio_value,
                raw_portfolio_value=float(algorithm.Portfolio.TotalPortfolioValue),
                best_contract=intraday_contract,
                size_multiplier=intraday_size_multiplier,
                macro_regime_score=regime_score,
                governor_scale=algorithm._governor_scale,
                direction=intraday_direction,
                forced_entry_strategy=forced_intraday_strategy,
                vix_level_override=vix_level_cboe,
                underlying_atr=qqq_atr_value,
                micro_state=micro_state,
                transition_ctx=transition_ctx,
            )
            if intraday_signal:
                intraday_signal = algorithm._attach_option_trace_metadata(
                    intraday_signal, source="MICRO"
                )
                if algorithm._mark_engine_signal_event("APPROVED", intraday_signal_id):
                    algorithm.Log(
                        f"INTRADAY_SIGNAL_APPROVED: SignalId={intraday_signal_id} | {signal_reason} | "
                        f"Direction={intraday_direction.value if intraday_direction else 'NONE'} | "
                        f"Strategy={intraday_strategy.value if intraday_strategy else 'NONE'} | "
                        f"Contract={intraday_contract.symbol if intraday_contract else 'NONE'}"
                    )
                    algorithm._diag_intraday_approved_count += 1
                    algorithm._inc_engine_counter(
                        algorithm._diag_intraday_approved_by_engine,
                        intraday_strategy,
                    )
                    algorithm._record_signal_lifecycle_event(
                        engine=algorithm._engine_bucket_from_strategy(intraday_strategy),
                        event="APPROVED",
                        signal_id=intraday_signal_id,
                        trace_id=intraday_signal.metadata.get("trace_id", "")
                        if intraday_signal.metadata
                        else "",
                        direction=intraday_direction.value if intraday_direction else "",
                        strategy=intraday_strategy.value if intraday_strategy else "",
                        code="R_OK",
                        gate_name="INTRADAY_SIGNAL_APPROVED",
                        reason=signal_reason,
                        contract_symbol=str(intraday_contract.symbol)
                        if intraday_contract is not None
                        else "",
                    )
                algorithm._inc_micro_dte_counter(
                    algorithm._diag_micro_dte_approved,
                    getattr(intraday_contract, "days_to_expiry", None),
                )
                intraday_trace_id = (
                    intraday_signal.metadata.get("trace_id", "") if intraday_signal.metadata else ""
                )
                algorithm.portfolio_router.receive_signal(intraday_signal)
                algorithm._process_immediate_signals()
                algorithm._clear_engine_retry("MICRO")
                if intraday_trace_id:
                    for rej in algorithm._get_recent_router_rejections():
                        if rej.trace_id == intraday_trace_id and rej.source_tag.startswith("MICRO"):
                            algorithm._diag_intraday_router_reject_count += 1
                            algorithm.Log(
                                f"INTRADAY_ROUTER_REJECTED: SignalId={intraday_signal_id} | "
                                f"Trace={rej.trace_id} | Code={rej.code} | Stage={rej.stage} | {rej.detail}"
                            )
                            host.cancel_pending_engine_entry(
                                engine=host._engine_lane_from_strategy(
                                    intraday_strategy.value if intraday_strategy else ""
                                ),
                                symbol=str(intraday_contract.symbol)
                                if intraday_contract is not None
                                else None,
                            )
                            reject_code = algorithm._canonical_options_reason_code(
                                str(rej.code or "E_INTRADAY_ROUTER_REJECT")
                            )
                            drop_logged = algorithm._log_engine_signal_dropped(
                                signal_id=intraday_signal_id,
                                code=reject_code,
                                reason=f"ROUTER_REJECT: {rej.stage} | {rej.detail}",
                                retry_hint="None",
                                direction=intraday_direction,
                                strategy=intraday_strategy,
                                contract_symbol=str(intraday_contract.symbol)
                                if intraday_contract is not None
                                else "NONE",
                            )
                            if drop_logged:
                                algorithm._diag_intraday_dropped_count += 1
                                algorithm._inc_engine_counter(
                                    algorithm._diag_intraday_dropped_by_engine,
                                    intraday_strategy,
                                )
                                algorithm._inc_micro_dte_counter(
                                    algorithm._diag_micro_dte_dropped,
                                    getattr(intraday_contract, "days_to_expiry", None)
                                    if intraday_contract is not None
                                    else None,
                                )
                                algorithm._record_micro_drop_reason_dte(
                                    reject_code,
                                    getattr(intraday_contract, "days_to_expiry", None)
                                    if intraday_contract is not None
                                    else None,
                                )
                            break
            else:
                if should_trade:
                    drop_code = "E_INTRADAY_NO_SIGNAL_UNCLASSIFIED"
                    (
                        intraday_validation_reason,
                        intraday_validation_detail,
                    ) = host.pop_last_engine_validation_failure(lane="MICRO")
                    (
                        can_retry_now,
                        retry_reason_now,
                    ) = host.can_enter_single_leg()
                    retry_code_now = (retry_reason_now or "").split(":", 1)[0].strip()
                    if intraday_validation_reason:
                        drop_code = intraday_validation_reason
                    elif not can_retry_now:
                        drop_code = retry_code_now or "R_SLOT_LIMIT"
                    elif micro_intraday_cooldown_active:
                        drop_code = "R_COOLDOWN_INTRADAY"
                    elif algorithm._margin_cb_in_progress or algorithm._margin_call_cooldown_until:
                        drop_code = "R_MARGIN_CB_ACTIVE"
                    elif intraday_strategy is not None and host.has_engine_position(
                        engine=host._engine_lane_from_strategy(intraday_strategy.value)
                    ):
                        drop_code = "R_DUPLICATE_INTRADAY_POSITION"
                    elif intraday_contract is None:
                        drop_code = "E_INTRADAY_NO_CONTRACT"
                    elif intraday_direction is None:
                        drop_code = "E_INTRADAY_NO_DIRECTION"

                    drop_code = algorithm._canonical_options_reason_code(drop_code)
                    drop_logged = algorithm._log_engine_signal_dropped(
                        signal_id=intraday_signal_id,
                        code=drop_code,
                        reason=signal_reason,
                        retry_hint=retry_reason_now,
                        direction=intraday_direction,
                        strategy=intraday_strategy,
                        contract_symbol=str(intraday_contract.symbol)
                        if intraday_contract
                        else "NONE",
                        validation_detail=intraday_validation_detail,
                    )
                    if drop_logged:
                        algorithm._diag_intraday_dropped_count += 1
                        algorithm._inc_engine_counter(
                            algorithm._diag_intraday_dropped_by_engine,
                            intraday_strategy,
                        )
                        algorithm._inc_micro_dte_counter(
                            algorithm._diag_micro_dte_dropped,
                            getattr(intraday_contract, "days_to_expiry", None)
                            if intraday_contract is not None
                            else None,
                        )
                        algorithm._record_micro_drop_reason_dte(
                            drop_code,
                            getattr(intraday_contract, "days_to_expiry", None)
                            if intraday_contract is not None
                            else None,
                        )
                    retry_context = f"{signal_reason or ''} | {intraday_validation_detail or ''} | {retry_reason_now or ''}"
                    if self._should_queue_engine_retry(drop_code, retry_context):
                        retry_expires = algorithm.Time + timedelta(minutes=20)
                        algorithm._queue_engine_retry(
                            lane="MICRO",
                            direction=intraday_direction,
                            reason_code=drop_code,
                            expires_at=retry_expires,
                        )
                        algorithm.Log(
                            f"INTRADAY_RETRY_QUEUED: Code={drop_code} | "
                            f"Expires={retry_expires.strftime('%H:%M')}"
                        )

        return itm_dir, itm_reason

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

        # ITM_MOMENTUM is handled by ITMHorizonEngine sovereign path.
        _ = itm_engine_mode
        _ = state
        return True, None

    @staticmethod
    def _should_queue_engine_retry(drop_code: str, context: str = "") -> bool:
        """Return whether a dropped candidate should queue one retry."""
        code = str(drop_code or "").upper()
        if code not in {
            "R_SLOT_TOTAL_MAX",
            "R_TRADE_DAILY_TOTAL_MAX",
            "R_SLOT_INTRADAY_MAX",
            "R_SLOT_SINGLE_LEG_MAX",  # legacy code
            "R_COOLDOWN_INTRADAY",
            "R_MARGIN_CB_ACTIVE",
        }:
            return False

        # Do not retry when blocker is the global daily options trade limit.
        # R_SLOT_TOTAL_MAX is also used for slot-cap compatibility, so inspect detail context.
        text = str(context or "").upper()
        if code == "R_TRADE_DAILY_TOTAL_MAX":
            return False
        if code == "R_SLOT_TOTAL_MAX" and "GLOBAL LIMIT REACHED" in text:
            return False
        return True
