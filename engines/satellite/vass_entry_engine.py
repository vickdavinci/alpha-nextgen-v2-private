"""VASS entry engine: isolates strategy routing and anti-cluster guards for swing spreads."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

import config
from models.enums import OptionDirection


class VASSEntryEngine:
    """Encapsulates VASS entry routing, filters, and per-signature/day guards."""

    def __init__(self, log_func: Optional[Callable[[str, bool], None]] = None):
        self._log_func = log_func
        self._last_entry_at_by_signature: Dict[str, datetime] = {}
        self._cooldown_until_by_signature: Dict[str, datetime] = {}
        self._last_entry_date_by_direction: Dict[str, str] = {}
        self._last_entry_dt_by_direction: Dict[str, datetime] = {}
        self._last_rejection_log_by_key: Dict[str, datetime] = {}
        self._slot_backoff_until_by_key: Dict[str, datetime] = {}
        self._consecutive_losses: int = 0
        self._loss_breaker_pause_until: Optional[str] = None  # YYYY-MM-DD

    def _log(self, message: str, trades_only: bool = False) -> None:
        if self._log_func:
            self._log_func(message, trades_only)

    def should_log_rejection(self, *, now: datetime, reason_key: str) -> bool:
        """Per-reason throttle for VASS skip/rejection logs."""
        interval_min = int(getattr(config, "VASS_LOG_REJECTION_INTERVAL_MINUTES", 15))
        last = self._last_rejection_log_by_key.get(reason_key)
        if last is not None:
            elapsed = (now - last).total_seconds() / 60.0
            if elapsed < interval_min:
                return False
        self._last_rejection_log_by_key[reason_key] = now
        return True

    def _slot_backoff_key(
        self,
        *,
        direction: Optional[OptionDirection],
        overlay_state: Optional[str],
    ) -> str:
        direction_key = direction.value if direction is not None else "NONE"
        overlay_key = str(overlay_state or "NA").upper()
        return f"{direction_key}|{overlay_key}"

    def _is_slot_backoff_active(self, *, now: datetime, key: str) -> bool:
        if not bool(getattr(config, "VASS_SLOT_BACKOFF_ENABLED", False)):
            return False
        until = self._slot_backoff_until_by_key.get(key)
        if until is None:
            return False
        if now < until:
            return True
        self._slot_backoff_until_by_key.pop(key, None)
        return False

    def _arm_slot_backoff(self, *, now: datetime, key: str) -> None:
        if not bool(getattr(config, "VASS_SLOT_BACKOFF_ENABLED", False)):
            return
        minutes = max(1, int(getattr(config, "VASS_SLOT_BACKOFF_MINUTES", 20)))
        self._slot_backoff_until_by_key[key] = now + timedelta(minutes=minutes)

    def select_strategy(
        self,
        *,
        direction: str,
        iv_environment: str,
        is_intraday: bool,
        spread_strategy_enum: Any,
    ) -> Tuple[Any, int, int]:
        """Return (SpreadStrategy, dte_min, dte_max) for VASS routing."""
        medium_credit_prefer = bool(getattr(config, "VASS_MEDIUM_IV_PREFER_CREDIT", True))
        bull_medium_strategy = (
            spread_strategy_enum.BULL_PUT_CREDIT
            if medium_credit_prefer
            else spread_strategy_enum.BULL_CALL_DEBIT
        )
        bear_medium_strategy = (
            spread_strategy_enum.BEAR_CALL_CREDIT
            if medium_credit_prefer
            else spread_strategy_enum.BEAR_PUT_DEBIT
        )
        matrix = {
            ("BULLISH", "LOW"): (
                spread_strategy_enum.BULL_CALL_DEBIT,
                config.VASS_LOW_IV_DTE_MIN,
                config.VASS_LOW_IV_DTE_MAX,
            ),
            ("BULLISH", "MEDIUM"): (
                bull_medium_strategy,
                config.VASS_MEDIUM_IV_DTE_MIN,
                config.VASS_MEDIUM_IV_DTE_MAX,
            ),
            ("BULLISH", "HIGH"): (
                spread_strategy_enum.BULL_PUT_CREDIT,
                config.VASS_HIGH_IV_DTE_MIN,
                config.VASS_HIGH_IV_DTE_MAX,
            ),
            ("BEARISH", "LOW"): (
                spread_strategy_enum.BEAR_PUT_DEBIT,
                config.VASS_LOW_IV_DTE_MIN,
                config.VASS_LOW_IV_DTE_MAX,
            ),
            ("BEARISH", "MEDIUM"): (
                bear_medium_strategy,
                config.VASS_MEDIUM_IV_DTE_MIN,
                config.VASS_MEDIUM_IV_DTE_MAX,
            ),
            ("BEARISH", "HIGH"): (
                spread_strategy_enum.BEAR_CALL_CREDIT,
                config.VASS_HIGH_IV_DTE_MIN,
                config.VASS_HIGH_IV_DTE_MAX,
            ),
        }

        key = (direction, iv_environment)
        if key in matrix:
            strategy, dte_min, dte_max = matrix[key]
            self._log(
                f"VASS: {direction} + {iv_environment} IV -> {strategy.value} | "
                f"DTE={dte_min}-{dte_max} | Intraday={is_intraday}"
            )
            return strategy, dte_min, dte_max

        self._log(f"VASS: Unknown key {key}, defaulting to MEDIUM debit spread")
        if direction == "BULLISH":
            return (
                spread_strategy_enum.BULL_CALL_DEBIT,
                config.VASS_MEDIUM_IV_DTE_MIN,
                config.VASS_MEDIUM_IV_DTE_MAX,
            )
        return (
            spread_strategy_enum.BEAR_PUT_DEBIT,
            config.VASS_MEDIUM_IV_DTE_MIN,
            config.VASS_MEDIUM_IV_DTE_MAX,
        )

    def resolve_strategy_with_overlay(
        self,
        *,
        direction: str,
        overlay_state: Optional[str],
        iv_environment: str,
        spread_strategy_enum: Any,
        is_credit_strategy_func: Callable[[Any], bool],
    ) -> Tuple[Any, int, int, bool]:
        """Resolve VASS strategy/DTE with EARLY_STRESS strategy remap."""
        strategy, dte_min, dte_max = self.select_strategy(
            direction=direction,
            iv_environment=iv_environment,
            is_intraday=False,
            spread_strategy_enum=spread_strategy_enum,
        )
        overlay = str(overlay_state or "").upper()
        if overlay == "EARLY_STRESS":
            if (
                direction == "BULLISH"
                and strategy == spread_strategy_enum.BULL_CALL_DEBIT
                and bool(getattr(config, "VASS_EARLY_STRESS_BULL_STRATEGY_TO_CREDIT", True))
            ):
                self._log(
                    "VASS_EARLY_STRESS_REMIX: BULL_CALL_DEBIT->BULL_PUT_CREDIT | "
                    f"IV={iv_environment}"
                )
                strategy = spread_strategy_enum.BULL_PUT_CREDIT
                dte_min = int(getattr(config, "VASS_HIGH_IV_DTE_MIN", dte_min))
                dte_max = int(getattr(config, "VASS_HIGH_IV_DTE_MAX", dte_max))
            if (
                direction == "BEARISH"
                and strategy == spread_strategy_enum.BEAR_PUT_DEBIT
                and iv_environment in {"MEDIUM", "HIGH"}
                and bool(getattr(config, "VASS_EARLY_STRESS_BEAR_PREFER_CREDIT", True))
            ):
                self._log(
                    "VASS_EARLY_STRESS_REMIX: BEAR_PUT_DEBIT->BEAR_CALL_CREDIT | "
                    f"IV={iv_environment}"
                )
                strategy = spread_strategy_enum.BEAR_CALL_CREDIT
                dte_min = int(getattr(config, "VASS_HIGH_IV_DTE_MIN", dte_min))
                dte_max = int(getattr(config, "VASS_HIGH_IV_DTE_MAX", dte_max))
        is_credit = bool(is_credit_strategy_func(strategy))
        return strategy, dte_min, dte_max, is_credit

    def strategy_option_right(self, strategy: Optional[Any]) -> Optional[str]:
        """Return required option right key (CALL/PUT) for a VASS spread strategy."""
        if strategy is None:
            return None
        strategy_value = str(getattr(strategy, "value", strategy))
        if strategy_value in {"BULL_CALL_DEBIT", "BEAR_CALL_CREDIT"}:
            return "CALL"
        if strategy_value in {"BEAR_PUT_DEBIT", "BULL_PUT_CREDIT"}:
            return "PUT"
        return None

    def can_enter_swing(
        self,
        *,
        host: Any,
        direction: Optional[OptionDirection] = None,
        overlay_state: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Check whether a new VASS swing entry is allowed under slot caps."""
        _, swing_count, total_count = host.count_options_positions()

        effective_cap = host._get_effective_total_cap()
        if total_count >= effective_cap:
            return (
                False,
                f"R_SLOT_TOTAL_MAX: {total_count} >= {effective_cap}",
            )

        swing_cap = int(getattr(config, "OPTIONS_MAX_SWING_POSITIONS", 4))
        if hasattr(host, "_get_effective_swing_position_cap"):
            swing_cap = int(host._get_effective_swing_position_cap() or swing_cap)
        if swing_count >= swing_cap:
            return (
                False,
                f"R_SLOT_SWING_MAX: {swing_count} >= {swing_cap}",
            )

        # V12.7: Explicit VASS concurrent spread cap (separate from global swing slots).
        if hasattr(host, "_get_effective_vass_concurrent_cap"):
            vass_concurrent_cap = int(host._get_effective_vass_concurrent_cap() or 0)
        else:
            vass_concurrent_cap = int(getattr(config, "VASS_MAX_CONCURRENT_SPREADS", 0))
            hard_cap = int(getattr(config, "VASS_MAX_CONCURRENT_SPREADS_HARD_CAP", 0))
            if hard_cap > 0:
                if vass_concurrent_cap <= 0:
                    vass_concurrent_cap = hard_cap
                else:
                    vass_concurrent_cap = min(vass_concurrent_cap, hard_cap)
        if vass_concurrent_cap > 0:
            vass_open = len(host.get_spread_positions() or [])
            if vass_open >= vass_concurrent_cap:
                return (
                    False,
                    f"R_SLOT_VASS_CONCURRENT_MAX: {vass_open} >= {vass_concurrent_cap}",
                )

        # Directional slot cap (stage-1 guardrail + stress-overlay shaping).
        if direction is not None:
            wanted_dir = "BULLISH" if direction == OptionDirection.CALL else "BEARISH"
            dir_count = host.get_open_spread_count_by_direction(wanted_dir)
            default_cap = int(getattr(config, "OPTIONS_MAX_SWING_PER_DIRECTION", 2))
            bullish_cap = max(
                int(getattr(config, "OPTIONS_MAX_SWING_BULLISH_POSITIONS", default_cap)),
                0,
            )
            bearish_cap = max(
                int(getattr(config, "OPTIONS_MAX_SWING_BEARISH_POSITIONS", default_cap)),
                0,
            )
            dir_cap = bullish_cap if wanted_dir == "BULLISH" else bearish_cap
            overlay = str(overlay_state or "").upper()
            if overlay == "STRESS":
                if wanted_dir == "BULLISH":
                    dir_cap = int(getattr(config, "MAX_BULLISH_SPREADS_STRESS", 0))
                else:
                    dir_cap = int(getattr(config, "MAX_BEARISH_SPREADS_STRESS", dir_cap))
            elif overlay == "EARLY_STRESS" and wanted_dir == "BULLISH":
                dir_cap = min(dir_cap, int(getattr(config, "MAX_BULLISH_SPREADS_EARLY_STRESS", 1)))
            if dir_count >= dir_cap:
                if overlay in {"STRESS", "EARLY_STRESS"}:
                    return (
                        False,
                        f"R_SLOT_DIRECTION_OVERLAY: {overlay} {wanted_dir} {dir_count} >= {dir_cap}",
                    )
                return (
                    False,
                    f"R_SLOT_DIRECTION_MAX: {wanted_dir} {dir_count} >= {dir_cap}",
                )

        return True, "R_OK"

    def build_dte_fallbacks(self, dte_min: int, dte_max: int) -> list[Tuple[int, int]]:
        """Build ordered DTE fallback windows for VASS entry selection."""
        ranges = [(dte_min, dte_max)]
        fallback_min = max(5, dte_min - 2)
        fallback_max = min(45, dte_max + 14)
        if (fallback_min, fallback_max) != (dte_min, dte_max):
            ranges.append((fallback_min, fallback_max))
        return ranges

    def build_candidate_contracts(
        self,
        *,
        host: Any,
        chain: Any,
        direction: OptionDirection,
        dte_min: Optional[int] = None,
        dte_max: Optional[int] = None,
        option_right: Optional[Any] = None,
        contract_model_cls: Optional[Any] = None,
    ) -> list[Any]:
        """Build VASS candidate contracts from option chain filters."""
        if contract_model_cls is None:
            return []

        candidates: list[Any] = []
        right_key = None
        if option_right is not None:
            option_right_text = str(option_right).upper()
            if "CALL" in option_right_text:
                right_key = "CALL"
            elif "PUT" in option_right_text:
                right_key = "PUT"

        algorithm = getattr(host, "algorithm", None)
        engine_now = algorithm.Time if algorithm is not None else datetime.utcnow()
        effective_dte_min = dte_min if dte_min is not None else int(config.SPREAD_DTE_MIN)
        effective_dte_max = dte_max if dte_max is not None else int(config.SPREAD_DTE_MAX)

        for contract in chain:
            # Check option right (strategy-aware). Falls back to direction-based filter.
            if option_right is not None:
                contract_right_text = str(getattr(contract, "Right", "")).upper()
                if right_key == "CALL" and "CALL" not in contract_right_text:
                    continue
                if right_key == "PUT" and "PUT" not in contract_right_text:
                    continue
                if right_key is None and str(getattr(contract, "Right", "")) != str(option_right):
                    continue
                if right_key == "CALL":
                    opt_direction = OptionDirection.CALL
                elif right_key == "PUT":
                    opt_direction = OptionDirection.PUT
                else:
                    opt_direction = direction
            else:
                right_name = str(getattr(contract, "Right", "")).upper()
                if direction == OptionDirection.CALL:
                    if "CALL" not in right_name:
                        continue
                else:
                    if "PUT" not in right_name:
                        continue
                opt_direction = direction

            dte = (contract.Expiry - engine_now).days
            if dte < effective_dte_min or dte > effective_dte_max:
                continue

            bid, ask = host.get_contract_prices(contract)
            if ask <= 0:
                continue
            mid_price = (bid + ask) / 2 if bid > 0 else ask

            greeks = getattr(contract, "Greeks", None)
            delta_val = greeks.Delta if greeks else 0.0
            gamma_val = greeks.Gamma if greeks else 0.0
            theta_val = greeks.Theta if greeks else 0.0
            vega_val = greeks.Vega if greeks else 0.0

            candidates.append(
                contract_model_cls(
                    symbol=str(contract.Symbol),
                    underlying="QQQ",
                    direction=opt_direction,
                    strike=float(contract.Strike),
                    expiry=str(contract.Expiry.date()),
                    delta=delta_val,
                    gamma=gamma_val,
                    theta=theta_val,
                    vega=vega_val,
                    bid=bid,
                    ask=ask,
                    mid_price=mid_price,
                    open_interest=int(contract.OpenInterest),
                    days_to_expiry=dte,
                )
            )

        return candidates

    def _parse_hhmm_to_minutes(self, hhmm: str, default_minutes: int) -> int:
        """Parse HH:MM into minutes-from-midnight; fallback to default on parse failure."""
        try:
            parts = str(hhmm).split(":")
            if len(parts) != 2:
                return default_minutes
            hh = int(parts[0])
            mm = int(parts[1])
            if hh < 0 or hh > 23 or mm < 0 or mm > 59:
                return default_minutes
            return hh * 60 + mm
        except Exception:
            return default_minutes

    def check_swing_filters(
        self,
        *,
        direction: OptionDirection,
        spy_gap_pct: float,
        spy_intraday_change_pct: float,
        vix_intraday_change_pct: float,
        current_hour: int,
        current_minute: int,
        enforce_time_window: bool = True,
    ) -> Tuple[bool, str]:
        """Simple intraday filters for swing-mode entries."""
        time_minutes = current_hour * 60 + current_minute
        start_minutes = self._parse_hhmm_to_minutes(
            str(getattr(config, "SWING_TIME_WINDOW_START", "10:00")), 10 * 60
        )
        end_minutes = self._parse_hhmm_to_minutes(
            str(getattr(config, "SWING_TIME_WINDOW_END", "14:30")), 14 * 60 + 30
        )
        if enforce_time_window and not (start_minutes <= time_minutes <= end_minutes):
            return False, "TIME_WINDOW"

        if abs(spy_gap_pct) > config.SWING_GAP_THRESHOLD:
            if direction == OptionDirection.CALL and spy_gap_pct > 0:
                return False, f"Gap up {spy_gap_pct:.1f}% - reversal risk for calls"
            if direction == OptionDirection.PUT and spy_gap_pct < 0:
                return False, f"Gap down {spy_gap_pct:.1f}% - bounce risk for puts"

        if spy_intraday_change_pct < config.SWING_EXTREME_SPY_DROP:
            return False, f"SPY extreme drop {spy_intraday_change_pct:.1f}% - pause entries"

        if vix_intraday_change_pct > config.SWING_EXTREME_VIX_SPIKE:
            return False, f"VIX spike +{vix_intraday_change_pct:.1f}% - pause entries"

        return True, ""

    def check_bull_debit_trend_confirmation(
        self,
        *,
        vix_current: Optional[float],
        current_price: float,
        qqq_open: Optional[float],
        qqq_sma20: Optional[float],
        qqq_sma20_ready: bool,
        relax_recovery: bool = False,
        relaxed_day_min_change_pct: Optional[float] = None,
        ma20_tolerance_pct: Optional[float] = None,
    ) -> Tuple[bool, str, str]:
        """Scoped trend confirmation for bullish debit spreads in low/medium-IV tape."""
        if not bool(getattr(config, "VASS_BULL_DEBIT_TREND_CONFIRM_ENABLED", False)):
            return True, "R_OK", "DISABLED"

        scoped_max_vix = float(
            getattr(
                config,
                "VASS_BULL_DEBIT_TREND_CONFIRM_MAX_VIX",
                float(getattr(config, "VASS_IV_HIGH_THRESHOLD", 22.0)),
            )
        )
        if vix_current is not None and float(vix_current) >= scoped_max_vix:
            return (
                True,
                "R_OK",
                f"SCOPE_BYPASS: VIX {float(vix_current):.1f} >= {scoped_max_vix:.1f}",
            )

        if (
            bool(getattr(config, "VASS_BULL_DEBIT_REQUIRE_MA20", True))
            and qqq_sma20_ready
            and qqq_sma20 is not None
        ):
            ma20_value = float(qqq_sma20)
            effective_ma20_floor = ma20_value
            if relax_recovery:
                tolerance = float(
                    ma20_tolerance_pct
                    if ma20_tolerance_pct is not None
                    else getattr(config, "VASS_RECOVERY_RELAX_MA20_TOLERANCE_PCT", 0.003)
                )
                if tolerance > 0:
                    effective_ma20_floor = ma20_value * (1.0 - tolerance)
            if current_price <= effective_ma20_floor:
                return (
                    False,
                    "R_BULL_DEBIT_TREND_MA20",
                    f"QQQ {current_price:.2f} <= MA20 floor {effective_ma20_floor:.2f} "
                    f"(MA20 {ma20_value:.2f})",
                )

        if bool(getattr(config, "VASS_BULL_DEBIT_REQUIRE_POSITIVE_DAY", True)):
            if qqq_open is not None and float(qqq_open) > 0:
                day_change_pct = ((current_price - float(qqq_open)) / float(qqq_open)) * 100.0
                min_day_change = float(
                    relaxed_day_min_change_pct
                    if (relax_recovery and relaxed_day_min_change_pct is not None)
                    else getattr(config, "VASS_BULL_DEBIT_MIN_DAY_CHANGE_PCT", 0.20)
                )
                if day_change_pct < min_day_change:
                    return (
                        False,
                        "R_BULL_DEBIT_TREND_DAY",
                        f"QQQ day {day_change_pct:+.2f}% < {min_day_change:+.2f}% "
                        f"(QQQ={current_price:.2f}, Open={float(qqq_open):.2f})",
                    )

        return True, "R_OK", "PASS"

    def run_eod_entry_cycle(
        self,
        *,
        host: Any,
        chain: Any,
        regime_score: float,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        iv_rank: float,
        size_multiplier: float,
        is_eod_scan: bool,
    ) -> None:
        """Resolve VASS direction context and dispatch directional spread scans."""
        algorithm = getattr(host, "algorithm", None)
        if algorithm is None:
            return

        transition_ctx = (
            algorithm._get_transition_execution_context()
            if hasattr(algorithm, "_get_transition_execution_context")
            else host._get_regime_transition_context(regime_score)
        )
        regime_score = float(
            transition_ctx.get(
                "transition_score", algorithm._get_decision_regime_score_for_options()
            )
            or algorithm._get_decision_regime_score_for_options()
        )
        context = host.resolve_vass_direction_context(
            regime_score=regime_score,
            size_multiplier=size_multiplier,
            bull_profile_log_prefix="VASS_BULL_PROFILE_BLOCK",
            clamp_log_prefix="VASS_CLAMP_BLOCK",
            shock_log_prefix="VASS_SHOCK_OVERRIDE_EOD",
            transition_ctx=transition_ctx,
        )
        if context is None:
            return
        (
            direction,
            direction_str,
            _overlay_state,
            size_multiplier,
            vass_has_conviction,
            vass_reason,
            macro_direction,
            resolve_reason,
            resolved_direction,
        ) = context

        if direction == OptionDirection.CALL and algorithm._is_premarket_ladder_call_block_active():
            algorithm.Log(
                "OPTIONS_EOD: CALL blocked by pre-market ladder | "
                f"{algorithm._premarket_vix_ladder_reason}"
            )
            return

        if vass_has_conviction:
            algorithm.Log(
                f"OPTIONS_VASS_CONVICTION: {vass_reason} | Macro={macro_direction} | "
                f"Resolved={resolved_direction} | {resolve_reason}"
            )

        host.scan_spread_for_direction(
            chain=chain,
            direction=direction,
            direction_str=direction_str,
            regime_score=regime_score,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            iv_rank=iv_rank,
            size_multiplier=size_multiplier,
            is_eod_scan=is_eod_scan,
        )

    def resolve_direction_context(
        self,
        *,
        host: Any,
        regime_score: float,
        size_multiplier: float,
        bull_profile_log_prefix: str,
        clamp_log_prefix: str,
        shock_log_prefix: str,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[OptionDirection, str, Any, float, bool, str, str, str, str]]:
        """
        Resolve VASS direction + sizing context with shared guard rails.

        Returns:
            Tuple of (direction, direction_str, overlay_state, size_multiplier,
            has_conviction, conviction_reason, macro_direction, resolve_reason,
            resolved_direction_str) or None when blocked/no-trade.
        """
        algorithm = getattr(host, "algorithm", None)
        if algorithm is None:
            return None

        current_date_str = algorithm.Time.strftime("%Y-%m-%d")
        if isinstance(transition_ctx, dict):
            ctx = dict(transition_ctx)
        elif hasattr(algorithm, "_get_transition_execution_context"):
            try:
                ctx = dict(algorithm._get_transition_execution_context() or {})
            except Exception:
                ctx = host._get_regime_transition_context(regime_score)
        else:
            ctx = host._get_regime_transition_context(regime_score)

        regime_for_vass = float(ctx.get("transition_score", regime_score) or regime_score)
        block_gate, block_reason = host.evaluate_transition_policy_block(
            engine="VASS",
            direction=None,
            transition_ctx=ctx,
        )
        if block_gate:
            host._record_regime_decision(
                engine="VASS",
                decision="BLOCK",
                strategy_attempted="VASS_DIRECTION",
                gate_name=block_gate,
                threshold_snapshot={"overlay": ctx.get("transition_overlay", "")},
                context=ctx,
            )
            algorithm.Log(
                f"VASS_TRANSITION_BLOCK: {block_reason} | "
                f"Eff={float(ctx.get('effective_score', regime_score)):.1f} | "
                f"Delta={float(ctx.get('delta', 0.0)):+.1f} | "
                f"MOM={float(ctx.get('momentum_roc', 0.0)):+.2%}"
            )
            return None

        try:
            vix_level_for_vass = float(algorithm._get_vix_level())
        except Exception:
            vix_level_for_vass = 20.0
        host.update_iv_sensor(vix_level_for_vass, current_date_str)
        has_conviction, conviction_direction, conviction_reason = host.get_iv_conviction()
        macro_direction = host.get_macro_direction(regime_for_vass)
        allow_macro_veto = True
        if has_conviction and conviction_direction == "BEARISH":
            allow_macro_veto, veto_reason = host.get_iv_bearish_veto_status()
            if not allow_macro_veto:
                has_conviction = False
                conviction_direction = None
                conviction_reason = f"{conviction_reason} | HARD_VETO_BLOCK={veto_reason}"
        overlay_state = host.get_regime_overlay_state(
            vix_current=vix_level_for_vass, regime_score=regime_for_vass
        )
        should_trade, resolved_direction, resolve_reason = host.resolve_trade_signal(
            engine="VASS",
            engine_direction=conviction_direction,
            engine_conviction=has_conviction,
            macro_direction=macro_direction,
            conviction_strength=None,
            overlay_state=overlay_state,
            allow_macro_veto=allow_macro_veto,
        )
        if not should_trade:
            host._record_regime_decision(
                engine="VASS",
                decision="BLOCK",
                strategy_attempted="VASS_DIRECTION",
                gate_name="VASS_RESOLVER_NO_TRADE",
                threshold_snapshot={"resolve_reason": resolve_reason},
                context=ctx,
            )
            if "E_OVERLAY_STRESS_BULL_BLOCK" in str(resolve_reason):
                if hasattr(algorithm, "_diag_overlay_block_count"):
                    algorithm._diag_overlay_block_count = (
                        int(getattr(algorithm, "_diag_overlay_block_count", 0)) + 1
                    )
            return None

        current_vix = float(getattr(algorithm, "_current_vix", vix_level_for_vass) or 0.0)
        if (
            bool(getattr(config, "VASS_BULL_PROFILE_BEARISH_BLOCK_ENABLED", True))
            and resolved_direction == "BEARISH"
            and float(regime_for_vass)
            >= float(getattr(config, "VASS_BULL_PROFILE_REGIME_MIN", 70.0))
            and str(overlay_state).upper() in {"NORMAL", "RECOVERY"}
        ):
            host._record_regime_decision(
                engine="VASS",
                decision="BLOCK",
                strategy_attempted="VASS_BEARISH",
                gate_name="VASS_BULL_PROFILE_BEARISH_BLOCK",
                threshold_snapshot={
                    "regime_min": float(getattr(config, "VASS_BULL_PROFILE_REGIME_MIN", 70.0))
                },
                context=ctx,
            )
            algorithm.Log(
                f"{bull_profile_log_prefix}: Bearish VASS blocked in strong bull profile | "
                f"Regime={float(regime_for_vass):.1f} | Overlay={overlay_state}"
            )
            return None

        if (
            resolved_direction == "BULLISH"
            and str(macro_direction).upper() == "NEUTRAL"
            and current_vix >= float(getattr(config, "VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX", 20.0))
        ):
            host._record_regime_decision(
                engine="VASS",
                decision="BLOCK",
                strategy_attempted="VASS_BULLISH",
                gate_name="VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX",
                threshold_snapshot={
                    "vix_limit": float(getattr(config, "VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX", 20.0))
                },
                context=ctx,
            )
            algorithm.Log(
                f"{clamp_log_prefix}: Neutral macro + elevated VIX blocks bullish override | "
                f"VIX={current_vix:.1f} >= "
                f"{float(getattr(config, 'VASS_NEUTRAL_BULL_OVERRIDE_MAX_VIX', 20.0)):.1f} | "
                f"Resolve={resolve_reason}"
            )
            return None

        resolved_option_dir = (
            OptionDirection.CALL if resolved_direction == "BULLISH" else OptionDirection.PUT
        )
        block_gate, block_reason = host.evaluate_transition_policy_block(
            engine="VASS",
            direction=resolved_option_dir,
            transition_ctx=ctx,
        )
        if block_gate:
            host._record_regime_decision(
                engine="VASS",
                decision="BLOCK",
                strategy_attempted=f"VASS_{resolved_direction}",
                gate_name=block_gate,
                context=ctx,
            )
            algorithm.Log(
                f"VASS_TRANSITION_BLOCK: {block_reason} | "
                f"Eff={float(ctx.get('effective_score', regime_for_vass)):.1f} | "
                f"Delta={float(ctx.get('delta', 0.0)):+.1f} | "
                f"MOM={float(ctx.get('momentum_roc', 0.0)):+.2%}"
            )
            return None

        if "NEUTRAL_ALIGNED_HALF" in str(resolve_reason):
            size_multiplier *= config.NEUTRAL_ALIGNED_SIZE_MULT

        if (
            getattr(config, "SHOCK_MEMORY_FORCE_BEARISH_VASS", True)
            and hasattr(algorithm, "_is_premarket_shock_memory_active")
            and algorithm._is_premarket_shock_memory_active()
            and resolved_direction == "BULLISH"
        ):
            resolved_direction = "BEARISH"
            resolve_reason = f"{resolve_reason} | SHOCK_MEMORY_FORCE_BEARISH"
            shock_pct = 0.0
            if hasattr(algorithm, "_get_premarket_shock_memory_pct"):
                try:
                    shock_pct = float(algorithm._get_premarket_shock_memory_pct())
                except Exception:
                    shock_pct = 0.0
            algorithm.Log(
                f"{shock_log_prefix}: Forcing BEARISH | "
                f"Shock={shock_pct:+.1%} | "
                f"Reason={resolve_reason}"
            )

        if resolved_direction == "BULLISH":
            direction = OptionDirection.CALL
            direction_str = "BULLISH"
        else:
            direction = OptionDirection.PUT
            direction_str = "BEARISH"

        host._record_regime_decision(
            engine="VASS",
            decision="ALLOW",
            strategy_attempted=f"VASS_{direction_str}",
            gate_name="VASS_DIRECTION_RESOLVED",
            threshold_snapshot={
                "macro_direction": str(macro_direction),
                "overlay_state": str(overlay_state),
            },
            context=ctx,
        )

        return (
            direction,
            direction_str,
            overlay_state,
            size_multiplier,
            has_conviction,
            conviction_reason,
            str(macro_direction),
            resolve_reason,
            resolved_direction,
        )

    def run_intraday_entry_cycle(
        self,
        *,
        host: Any,
        chain: Any,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        size_multiplier: float,
        effective_portfolio_value: float,
        margin_remaining: float,
        transition_ctx: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Run intraday VASS spread lane end-to-end via OptionsEngine host."""
        algorithm = getattr(host, "algorithm", None)
        if algorithm is None:
            return
        transition_ctx = transition_ctx or {}

        swing_cooldown_active = (
            algorithm._options_swing_cooldown_until is not None
            and algorithm.Time < algorithm._options_swing_cooldown_until
        )
        spread_cooldown_active = (
            algorithm._options_spread_cooldown_until is not None
            and algorithm.Time < algorithm._options_spread_cooldown_until
        )
        if swing_cooldown_active and spread_cooldown_active:
            return

        if (
            hasattr(algorithm, "_last_swing_scan_time")
            and algorithm._last_swing_scan_time is not None
        ):
            minutes_since = (algorithm.Time - algorithm._last_swing_scan_time).total_seconds() / 60
            if minutes_since < 15:
                return
        algorithm._last_swing_scan_time = algorithm.Time

        regime_score = float(
            transition_ctx.get(
                "transition_score", algorithm._get_decision_regime_score_for_options()
            )
            or algorithm._get_decision_regime_score_for_options()
        )
        context = host.resolve_vass_direction_context(
            regime_score=regime_score,
            size_multiplier=size_multiplier,
            bull_profile_log_prefix="VASS_BULL_PROFILE_BLOCK_INTRADAY",
            clamp_log_prefix="VASS_CLAMP_BLOCK_INTRADAY",
            shock_log_prefix="VASS_SHOCK_OVERRIDE",
            transition_ctx=transition_ctx,
        )
        if context is None:
            return
        (
            direction,
            direction_str,
            overlay_state,
            size_multiplier,
            vass_has_conviction,
            vass_reason,
            macro_direction,
            resolve_reason,
            resolved_direction,
        ) = context

        if direction == OptionDirection.CALL and algorithm._is_premarket_ladder_call_block_active():
            if algorithm.Time.minute % 15 == 0:
                until_h, until_m = algorithm._premarket_vix_call_block_until
                algorithm.Log(
                    f"VASS_BLOCKED: CALL blocked until {until_h:02d}:{until_m:02d} | "
                    f"{algorithm._premarket_vix_ladder_reason}"
                )
            return

        if vass_has_conviction:
            algorithm.Log(
                f"OPTIONS_VASS_CONVICTION_INTRADAY: {vass_reason} | Macro={macro_direction} | "
                f"Resolved={resolved_direction} | {resolve_reason}"
            )

        spy_open = float(getattr(host, "_spy_at_open", 0.0) or 0.0)
        spy_gap_pct = float(getattr(host, "_spy_gap_pct", 0.0) or 0.0)
        try:
            spy_current = float(algorithm.Securities[algorithm.spy].Price)
        except Exception:
            spy_current = spy_open
        spy_intraday_change_pct = (
            ((spy_current - spy_open) / spy_open) * 100.0 if spy_open > 0 else 0.0
        )
        vix_at_open = float(getattr(algorithm, "_vix_at_open", 0.0) or 0.0)
        vix_intraday_change_pct = (
            ((float(algorithm._current_vix) - vix_at_open) / vix_at_open) * 100.0
            if vix_at_open > 0
            else 0.0
        )
        swing_filters_ok, swing_filter_reason = host.check_swing_filters(
            direction=direction,
            spy_gap_pct=spy_gap_pct,
            spy_intraday_change_pct=spy_intraday_change_pct,
            vix_intraday_change_pct=vix_intraday_change_pct,
            current_hour=algorithm.Time.hour,
            current_minute=algorithm.Time.minute,
        )
        if not swing_filters_ok:
            algorithm._diag_vass_block_count += 1
            if algorithm.Time.minute % 15 == 0:
                algorithm.Log(
                    f"VASS_SKIPPED: Direction={direction.value} | "
                    f"VIX={algorithm._current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"ReasonCode=SWING_FILTER | ValidationFail={swing_filter_reason}"
                )
            return

        iv_rank = algorithm._calculate_iv_rank(chain)
        strategy, vass_dte_min, vass_dte_max, is_credit = host.resolve_vass_strategy(
            direction=direction_str,
            overlay_state=overlay_state,
            iv_rank=iv_rank,
        )
        algorithm._diag_vass_signal_seq = int(getattr(algorithm, "_diag_vass_signal_seq", 0)) + 1
        vass_signal_id = (
            f"VASS-{algorithm.Time.strftime('%Y%m%d-%H%M')}-{algorithm._diag_vass_signal_seq}"
        )
        slot_backoff_key = self._slot_backoff_key(
            direction=direction,
            overlay_state=overlay_state,
        )
        if self._is_slot_backoff_active(now=algorithm.Time, key=slot_backoff_key):
            reason_code = "R_SLOT_BACKOFF"
            algorithm._record_vass_reject_reason(reason_code)
            throttle_key = f"{reason_code}|{direction.value}|INTRADAY|{slot_backoff_key}"
            if host.should_log_vass_rejection(throttle_key):
                algorithm.Log(
                    f"VASS_SKIPPED: Direction={direction.value} | "
                    f"VIX={algorithm._current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"ReasonCode={reason_code} | BackoffKey={slot_backoff_key}"
                )
            algorithm._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code=reason_code,
                gate_name="VASS_SLOT_BACKOFF",
                reason=f"Slot backoff active for {slot_backoff_key}",
                contract_symbol="",
            )
            return
        dte_ranges = host.build_vass_dte_fallbacks(vass_dte_min, vass_dte_max)
        dte_min_all = min(r[0] for r in dte_ranges)
        dte_max_all = max(r[1] for r in dte_ranges)
        required_right = host.strategy_option_right(strategy)

        candidate_contracts = host.build_vass_candidate_contracts(
            chain=chain,
            direction=direction,
            dte_min=dte_min_all,
            dte_max=dte_max_all,
            option_right=required_right,
        )
        if len(candidate_contracts) < 2:
            algorithm._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code="INSUFFICIENT_CANDIDATES",
                gate_name="VASS_CANDIDATE_CONTRACTS",
                reason="No contracts met spread criteria",
                contract_symbol="",
            )
            return
        algorithm._record_signal_lifecycle_event(
            engine="VASS",
            event="CANDIDATE",
            signal_id=vass_signal_id,
            direction=direction.value if direction else "",
            strategy=strategy.value if strategy else "",
            code="R_OK",
            gate_name="VASS_SIGNAL_CANDIDATE",
            reason=f"Contracts={len(candidate_contracts)}",
            contract_symbol="",
        )

        signal = None
        rejection_code = "UNKNOWN"

        if spread_cooldown_active:
            rejection_code = "SPREAD_COOLDOWN_ACTIVE"
        elif host.has_pending_spread_entry():
            rejection_code = "PENDING_SPREAD_ENTRY"
        else:
            host.pop_last_entry_validation_failure()
            host.pop_last_credit_failure_stats()
            host.pop_last_spread_failure_stats()
            signal, rejection_code = host.build_vass_spread_signal(
                chain=chain,
                candidate_contracts=candidate_contracts,
                direction=direction,
                regime_score=regime_score,
                qqq_price=qqq_price,
                adx_value=adx_value,
                ma200_value=ma200_value,
                ma50_value=ma50_value,
                iv_rank=iv_rank,
                size_multiplier=size_multiplier,
                portfolio_value=effective_portfolio_value,
                margin_remaining=margin_remaining,
                strategy=strategy,
                vass_dte_min=vass_dte_min,
                vass_dte_max=vass_dte_max,
                dte_ranges=dte_ranges,
                is_credit=is_credit,
                is_eod_scan=False,
                fallback_log_prefix="VASS_FALLBACK_INTRADAY",
            )

        if signal:
            algorithm._diag_spread_entry_signal_count += 1
            algorithm.Log(
                f"VASS_ENTRY: {signal.metadata.get('vass_strategy', 'UNKNOWN') if signal.metadata else 'UNKNOWN'} | "
                f"{signal.symbol} | {signal.reason}"
            )
            signal = algorithm._attach_option_trace_metadata(signal, source="VASS")
            vass_trace_id = signal.metadata.get("trace_id", "") if signal.metadata else ""
            algorithm._record_signal_lifecycle_event(
                engine="VASS",
                event="APPROVED",
                signal_id=vass_signal_id,
                trace_id=vass_trace_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code="R_OK",
                gate_name="VASS_ENTRY",
                reason=str(signal.reason or ""),
                contract_symbol=str(signal.symbol),
            )
            signal = algorithm._apply_spread_margin_guard(signal, source_tag="VASS_INTRADAY_SPREAD")
            if signal is None:
                algorithm._record_signal_lifecycle_event(
                    engine="VASS",
                    event="DROPPED",
                    signal_id=vass_signal_id,
                    trace_id=vass_trace_id,
                    direction=direction.value if direction else "",
                    strategy=strategy.value if strategy else "",
                    code="R_MARGIN_PRECHECK",
                    gate_name="VASS_MARGIN_GUARD",
                    reason="Signal dropped by spread margin guard",
                    contract_symbol="",
                )
                return
            short_symbol = (
                signal.metadata.get("spread_short_leg_symbol", "") if signal.metadata else ""
            )
            long_symbol = str(signal.symbol) if signal.symbol else ""
            if short_symbol and long_symbol:
                algorithm._pending_spread_orders[short_symbol] = long_symbol
                algorithm._pending_spread_orders_reverse[long_symbol] = short_symbol
            algorithm._diag_spread_entry_submit_count += 1
            algorithm.portfolio_router.receive_signal(signal)
            algorithm._process_immediate_signals()
            if vass_trace_id:
                for rej in algorithm._get_recent_router_rejections():
                    if rej.trace_id == vass_trace_id and rej.source_tag.startswith("VASS"):
                        algorithm.Log(
                            f"VASS_ROUTER_REJECTED: Trace={rej.trace_id} | "
                            f"Code={rej.code} | Stage={rej.stage} | {rej.detail}"
                        )
                        algorithm._record_signal_lifecycle_event(
                            engine="VASS",
                            event="ROUTER_REJECTED",
                            signal_id=vass_signal_id,
                            trace_id=vass_trace_id,
                            direction=direction.value if direction else "",
                            strategy=strategy.value if strategy else "",
                            code=str(rej.code or "E_ROUTER_REJECT"),
                            gate_name=str(rej.stage or "ROUTER"),
                            reason=str(rej.detail or ""),
                            contract_symbol=str(signal.symbol),
                        )
                        break
            return

        if signal is None and not spread_cooldown_active:
            algorithm._diag_vass_block_count += 1
            fail_stats = (
                host.pop_last_credit_failure_stats()
                if is_credit
                else host.pop_last_spread_failure_stats()
            )
            validation_reason = host.pop_last_entry_validation_failure()
            iv_env = (
                host.get_iv_environment(iv_rank=iv_rank) if host.is_iv_sensor_ready() else "UNKNOWN"
            )
            skip_reasons = {
                "R_SLOT_SWING_MAX",
                "R_SLOT_TOTAL_MAX",
                "R_SLOT_DIRECTION_MAX",
                "R_ATTEMPT_BUDGET_EXHAUSTED",
                "R_POST_EXIT_MARGIN_COOLDOWN",
                "R_COOLDOWN_DIRECTIONAL",
            }
            log_prefix = "VASS_SKIPPED" if (validation_reason in skip_reasons) else "VASS_REJECTION"
            reason_code = algorithm._canonical_options_reason_code(
                validation_reason or rejection_code
            )
            algorithm._record_vass_reject_reason(reason_code)
            throttle_key = (
                f"{reason_code}|{direction.value}|"
                f"{'CREDIT' if is_credit else 'DEBIT'}|"
                f"{validation_reason or ''}"
            )
            should_log = host.should_log_vass_rejection(throttle_key)
            reason_text = "No contracts met spread criteria (DTE/delta/credit)"
            if validation_reason in {
                "R_SLOT_SWING_MAX",
                "R_SLOT_DIRECTION_MAX",
            }:
                reason_text = "Skipped - existing spread position"
            elif validation_reason == "R_SLOT_TOTAL_MAX":
                reason_text = "Skipped - total options slot limit reached"
            elif validation_reason == "R_ATTEMPT_BUDGET_EXHAUSTED":
                reason_text = "Skipped - entry attempt limit reached"
            elif validation_reason == "R_POST_EXIT_MARGIN_COOLDOWN":
                reason_text = "Skipped - post-exit margin cooldown active"
            elif validation_reason == "R_COOLDOWN_DIRECTIONAL":
                reason_text = "Skipped - cooldown active"
            elif validation_reason == "R_MARGIN_PRECHECK":
                reason_text = "Skipped - margin precheck failed"
            elif validation_reason and validation_reason.startswith("R_CONTRACT_QUALITY:"):
                reason_text = "Rejected - contract quality: " + validation_reason.split(":", 1)[1]
            elif validation_reason == "WIN_RATE_GATE_BLOCK":
                reason_text = "Skipped - win-rate gate shutoff active"
            elif validation_reason == "TRADE_LIMIT_BLOCK":
                reason_text = "Skipped - daily trade limit reached"

            if should_log:
                algorithm.Log(
                    f"{log_prefix}: Direction={direction.value} | "
                    f"IV_Env={iv_env} | VIX={algorithm._current_vix:.1f} | "
                    f"Regime={regime_score:.0f} | "
                    f"Contracts_checked={len(candidate_contracts)} | "
                    f"Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"AttemptedStrategy={strategy.value if strategy else 'NONE'} | "
                    f"RouteType={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"DTE_Ranges={dte_ranges} | "
                    f"ReasonCode={reason_code} | "
                    f"Reason={reason_text}"
                    + (f" | FailStats={fail_stats}" if fail_stats else "")
                    + (
                        f" | ValidationFail={validation_reason}"
                        if (not fail_stats and validation_reason)
                        else ""
                    )
                )
            algorithm._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code=reason_code,
                gate_name=str(validation_reason or rejection_code),
                reason=reason_text,
                contract_symbol="",
            )

        can_swing, _ = host.can_enter_swing()
        if config.SWING_FALLBACK_ENABLED and can_swing and not swing_cooldown_active:
            best_contract = algorithm._select_swing_option_contract(chain, direction)
            if best_contract is not None and best_contract.bid > 0 and best_contract.ask > 0:
                signal = host.check_entry_signal(
                    adx_value=adx_value,
                    current_price=qqq_price,
                    ma200_value=ma200_value,
                    iv_rank=iv_rank,
                    best_contract=best_contract,
                    current_hour=algorithm.Time.hour,
                    current_minute=algorithm.Time.minute,
                    current_date=str(algorithm.Time.date()),
                    portfolio_value=algorithm.Portfolio.TotalPortfolioValue,
                    regime_score=regime_score,
                    gap_filter_triggered=algorithm.risk_engine.is_gap_filter_active(),
                    vol_shock_active=algorithm.risk_engine.is_vol_shock_active(algorithm.Time),
                    size_multiplier=size_multiplier,
                    direction=direction,
                )

                if signal:
                    algorithm.Log(
                        f"SWING_FALLBACK: Single-leg {direction.value} after spread failure"
                    )
                    algorithm.portfolio_router.receive_signal(signal)
                    algorithm._process_immediate_signals()
        elif not config.SWING_FALLBACK_ENABLED and can_swing:
            throttle_min = int(getattr(config, "SPREAD_CONSTRUCTION_FAIL_LOG_INTERVAL_MINUTES", 60))
            is_live = bool(hasattr(algorithm, "LiveMode") and algorithm.LiveMode)
            backtest_logs_enabled = bool(
                getattr(config, "SPREAD_CONSTRUCTION_FAIL_LOG_BACKTEST_ENABLED", False)
            )
            if (not is_live) and (not backtest_logs_enabled):
                return
            should_log = (
                algorithm._last_spread_construct_fail_log_at is None
                or (algorithm.Time - algorithm._last_spread_construct_fail_log_at).total_seconds()
                / 60.0
                >= throttle_min
            )
            if should_log:
                algorithm.Log(
                    "SWING: Spread construction failed - staying cash (fallback disabled)"
                )
                algorithm._last_spread_construct_fail_log_at = algorithm.Time

    def scan_spread_for_direction(
        self,
        *,
        host: Any,
        chain: Any,
        direction: OptionDirection,
        direction_str: str,
        regime_score: float,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        iv_rank: float,
        size_multiplier: float,
        is_eod_scan: bool,
    ) -> None:
        """Scan for VASS spread entry in a specific direction via OptionsEngine host."""
        algorithm = getattr(host, "algorithm", None)
        if algorithm is None:
            return
        current_vix = float(getattr(algorithm, "_current_vix", 20.0) or 20.0)
        overlay_state = host.get_regime_overlay_state(
            vix_current=current_vix, regime_score=regime_score
        )
        strategy, vass_dte_min, vass_dte_max, is_credit = host.resolve_vass_strategy(
            direction=direction_str,
            overlay_state=overlay_state,
            iv_rank=iv_rank,
        )
        algorithm._diag_vass_signal_seq = int(getattr(algorithm, "_diag_vass_signal_seq", 0)) + 1
        vass_signal_id = (
            f"VASS-{algorithm.Time.strftime('%Y%m%d-%H%M')}-{algorithm._diag_vass_signal_seq}"
        )
        slot_backoff_key = self._slot_backoff_key(
            direction=direction,
            overlay_state=overlay_state,
        )
        if self._is_slot_backoff_active(
            now=algorithm.Time,
            key=slot_backoff_key,
        ):
            reason_code = "R_SLOT_BACKOFF"
            algorithm._record_vass_reject_reason(reason_code)
            throttle_key = (
                f"{reason_code}|{direction.value}|"
                f"{'CREDIT' if is_credit else 'DEBIT'}|{slot_backoff_key}"
            )
            if host.should_log_vass_rejection(throttle_key):
                algorithm.Log(
                    f"VASS_SKIPPED: Direction={direction.value} | "
                    f"IV_Env={host.get_iv_environment(iv_rank=iv_rank)} | "
                    f"VIX={current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"ReasonCode={reason_code} | BackoffKey={slot_backoff_key}"
                )
            algorithm._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code=reason_code,
                gate_name="VASS_SLOT_BACKOFF",
                reason=f"Slot backoff active for {slot_backoff_key}",
                contract_symbol="",
            )
            return
        dte_ranges = host.build_vass_dte_fallbacks(vass_dte_min, vass_dte_max)
        dte_min_all = min(r[0] for r in dte_ranges)
        dte_max_all = max(r[1] for r in dte_ranges)
        can_swing_vass, swing_reason_vass = host.can_enter_swing(
            direction=direction, overlay_state=overlay_state
        )
        if not can_swing_vass:
            reason_code = str(swing_reason_vass or "").split(":", 1)[0].strip().upper()
            if reason_code.startswith("R_SLOT_"):
                self._arm_slot_backoff(
                    now=algorithm.Time,
                    key=slot_backoff_key,
                )
            if "R_SLOT_DIRECTION_OVERLAY" in swing_reason_vass:
                algorithm._diag_overlay_slot_block_count += 1
            algorithm._record_vass_reject_reason("SWING_SLOT_BLOCK")
            throttle_key = (
                f"SWING_SLOT_BLOCK|{direction.value}|"
                f"{'CREDIT' if is_credit else 'DEBIT'}|{swing_reason_vass}"
            )
            if host.should_log_vass_rejection(throttle_key):
                algorithm.Log(
                    f"VASS_SKIPPED: Direction={direction.value} | IV_Env=NA | "
                    f"VIX={current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"Contracts_checked=0 | Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"DTE_Ranges={dte_ranges} | ReasonCode=SWING_SLOT_BLOCK | "
                    f"Reason=Swing entry not allowed | ValidationFail={swing_reason_vass}"
                )
            return

        spy_open = float(getattr(host, "_spy_at_open", 0.0) or 0.0)
        spy_gap_pct = float(getattr(host, "_spy_gap_pct", 0.0) or 0.0)
        try:
            spy_current = float(algorithm.Securities[algorithm.spy].Price)
        except Exception:
            spy_current = spy_open
        spy_intraday_change_pct = (
            ((spy_current - spy_open) / spy_open) * 100.0 if spy_open > 0 else 0.0
        )
        vix_at_open = float(getattr(algorithm, "_vix_at_open", 0.0) or 0.0)
        vix_intraday_change_pct = (
            ((current_vix - vix_at_open) / vix_at_open) * 100.0 if vix_at_open > 0 else 0.0
        )
        swing_filters_ok, swing_filter_reason = host.check_swing_filters(
            direction=direction,
            spy_gap_pct=spy_gap_pct,
            spy_intraday_change_pct=spy_intraday_change_pct,
            vix_intraday_change_pct=vix_intraday_change_pct,
            current_hour=algorithm.Time.hour,
            current_minute=algorithm.Time.minute,
            is_eod_scan=is_eod_scan,
        )
        if not swing_filters_ok:
            algorithm._diag_vass_block_count += 1
            algorithm._record_vass_reject_reason("SWING_FILTER")
            throttle_key = (
                f"SWING_FILTER|{direction.value}|"
                f"{'CREDIT' if is_credit else 'DEBIT'}|{swing_filter_reason}"
            )
            if host.should_log_vass_rejection(throttle_key):
                algorithm.Log(
                    f"VASS_SKIPPED: Direction={direction.value} | "
                    f"VIX={current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"ReasonCode=SWING_FILTER | ValidationFail={swing_filter_reason}"
                )
            return

        required_right = host.strategy_option_right(strategy)
        candidate_contracts = host.build_vass_candidate_contracts(
            chain=chain,
            direction=direction,
            dte_min=dte_min_all,
            dte_max=dte_max_all,
            option_right=required_right,
        )
        if len(candidate_contracts) < 2:
            algorithm._record_vass_reject_reason("INSUFFICIENT_CANDIDATES")
            throttle_key = (
                f"INSUFFICIENT_CANDIDATES|{direction.value}|"
                f"{'CREDIT' if is_credit else 'DEBIT'}|{dte_min_all}-{dte_max_all}"
            )
            if host.should_log_vass_rejection(throttle_key):
                algorithm.Log(
                    f"VASS_REJECTION: Direction={direction.value} | "
                    f"IV_Env={host.get_iv_environment(iv_rank=iv_rank)} | "
                    f"VIX={current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"Contracts_checked={len(candidate_contracts)} | "
                    f"Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"AttemptedStrategy={strategy.value if strategy else 'NONE'} | "
                    f"RouteType={'CREDIT' if is_credit else 'DEBIT'} | "
                    f"DTE_Ranges={dte_ranges} | ReasonCode=INSUFFICIENT_CANDIDATES"
                )
            algorithm._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code="INSUFFICIENT_CANDIDATES",
                gate_name="VASS_CANDIDATE_CONTRACTS",
                reason="No contracts met spread criteria",
                contract_symbol="",
            )
            return
        algorithm._record_signal_lifecycle_event(
            engine="VASS",
            event="CANDIDATE",
            signal_id=vass_signal_id,
            direction=direction.value if direction else "",
            strategy=strategy.value if strategy else "",
            code="R_OK",
            gate_name="VASS_SIGNAL_CANDIDATE",
            reason=f"Contracts={len(candidate_contracts)}",
            contract_symbol="",
        )

        tradeable_eq = algorithm.capital_engine.calculate(
            algorithm.Portfolio.TotalPortfolioValue
        ).tradeable_eq
        margin_remaining = algorithm.portfolio_router.get_effective_margin_remaining()
        if host.has_pending_spread_entry():
            algorithm.Log("VASS: Pending spread entry exists - skipping new spread signal")
            algorithm._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code="R_PENDING_SPREAD_ENTRY",
                gate_name="PENDING_SPREAD_ENTRY",
                reason="Pending spread entry exists",
                contract_symbol="",
            )
            return
        signal, rejection_code = host.build_vass_spread_signal(
            chain=chain,
            candidate_contracts=candidate_contracts,
            direction=direction,
            regime_score=regime_score,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            iv_rank=iv_rank,
            size_multiplier=size_multiplier,
            portfolio_value=tradeable_eq,
            margin_remaining=margin_remaining,
            strategy=strategy,
            vass_dte_min=vass_dte_min,
            vass_dte_max=vass_dte_max,
            dte_ranges=dte_ranges,
            is_credit=is_credit,
            is_eod_scan=is_eod_scan,
            fallback_log_prefix="VASS_FALLBACK",
        )

        if signal:
            algorithm.Log(
                f"VASS_ENTRY: {signal.metadata.get('vass_strategy', 'UNKNOWN') if signal.metadata else 'UNKNOWN'} | "
                f"{signal.symbol} | {signal.reason}"
            )
            signal = algorithm._attach_option_trace_metadata(signal, source="VASS")
            vass_trace_id = signal.metadata.get("trace_id", "") if signal.metadata else ""
            algorithm._record_signal_lifecycle_event(
                engine="VASS",
                event="APPROVED",
                signal_id=vass_signal_id,
                trace_id=vass_trace_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code="R_OK",
                gate_name="VASS_ENTRY",
                reason=str(signal.reason or ""),
                contract_symbol=str(signal.symbol),
            )
            signal = algorithm._apply_spread_margin_guard(signal, source_tag="VASS_SPREAD")
            if signal is None:
                algorithm._record_signal_lifecycle_event(
                    engine="VASS",
                    event="DROPPED",
                    signal_id=vass_signal_id,
                    trace_id=vass_trace_id,
                    direction=direction.value if direction else "",
                    strategy=strategy.value if strategy else "",
                    code="R_MARGIN_PRECHECK",
                    gate_name="VASS_MARGIN_GUARD",
                    reason="Signal dropped by spread margin guard",
                    contract_symbol="",
                )
                return

            short_symbol = (
                signal.metadata.get("spread_short_leg_symbol", "") if signal.metadata else ""
            )
            long_symbol = str(signal.symbol) if signal.symbol else ""
            if short_symbol and long_symbol:
                algorithm._pending_spread_orders[short_symbol] = long_symbol
                algorithm._pending_spread_orders_reverse[long_symbol] = short_symbol
                algorithm.Log(
                    f"SPREAD: Tracking order pair | Short={short_symbol[-15:]} <-> Long={long_symbol[-15:]}"
                )

            algorithm.portfolio_router.receive_signal(signal)
        else:
            algorithm._record_signal_lifecycle_event(
                engine="VASS",
                event="DROPPED",
                signal_id=vass_signal_id,
                direction=direction.value if direction else "",
                strategy=strategy.value if strategy else "",
                code=algorithm._canonical_options_reason_code(rejection_code or "UNKNOWN"),
                gate_name=str(rejection_code or "UNKNOWN"),
                reason="No spread signal produced",
                contract_symbol="",
            )

    def select_spread_legs(
        self,
        *,
        host: Any,
        contracts: list[Any],
        direction: OptionDirection,
        target_width: Optional[float] = None,
        current_time: Optional[str] = None,
        dte_min: Optional[int] = None,
        dte_max: Optional[int] = None,
        set_cooldown: bool = True,
        log_filters: bool = True,
        debug_stats: Optional[Dict[str, Any]] = None,
        current_price: Optional[float] = None,
    ) -> Optional[tuple[Any, Any]]:
        """Select long and short legs for debit spread construction."""
        # V2.4.3: Check FAILURE cooldown first (penalty after failed construction)
        if current_time:
            try:
                # V6.12: Direction-scoped cooldown if available
                if hasattr(host, "_spread_failure_cooldown_until_by_dir") and direction:
                    dir_key = direction.value if hasattr(direction, "value") else str(direction)
                    until = host._spread_failure_cooldown_until_by_dir.get(dir_key)
                    if until and current_time < until:
                        return None
                    elif until:
                        host._spread_failure_cooldown_until_by_dir.pop(dir_key, None)
                elif host._spread_failure_cooldown_until:
                    if current_time < host._spread_failure_cooldown_until:
                        return None
                    else:
                        host._spread_failure_cooldown_until = None
            except (ValueError, TypeError):
                pass

        # V2.3.21: Throttle spread scanning to reduce log noise
        if current_time and host._last_spread_scan_time:
            try:
                current_min_str = current_time[11:16]
                last_min_str = host._last_spread_scan_time[11:16]
                curr_h, curr_m = int(current_min_str[:2]), int(current_min_str[3:5])
                last_h, last_m = int(last_min_str[:2]), int(last_min_str[3:5])
                curr_total = curr_h * 60 + curr_m
                last_total = last_h * 60 + last_m
                if current_time[:10] == host._last_spread_scan_time[:10]:
                    elapsed = curr_total - last_total
                    if elapsed < config.SPREAD_SCAN_THROTTLE_MINUTES:
                        return None
            except (ValueError, IndexError):
                pass

        if current_time:
            host._last_spread_scan_time = current_time

        if not contracts:
            host.log("SPREAD: No contracts available for spread selection")
            return None

        # V12.12: dynamic width scaling from percentage-of-underlying
        dyn_widths = host._get_dynamic_spread_widths(current_price)
        if target_width is None:
            target_width = dyn_widths["width_target"]

        effective_width_min = host._get_effective_spread_width_min(current_price=current_price)
        filtered = [c for c in contracts if c.direction == direction]
        if len(filtered) < 2:
            host.log(f"SPREAD: Not enough {direction.value} contracts for spread")
            if set_cooldown:
                host._set_spread_failure_cooldown(current_time, direction=direction)
            return None

        is_call = direction == OptionDirection.CALL
        if is_call:
            long_delta_min_base = config.SPREAD_LONG_LEG_DELTA_MIN
            long_delta_max_base = config.SPREAD_LONG_LEG_DELTA_MAX
            short_delta_min = config.SPREAD_SHORT_LEG_DELTA_MIN
            short_delta_max = config.SPREAD_SHORT_LEG_DELTA_MAX
            oi_min_long = config.OPTIONS_MIN_OPEN_INTEREST
            spread_max_long = config.OPTIONS_SPREAD_MAX_PCT
            spread_warn_short = config.OPTIONS_SPREAD_WARNING_PCT
        else:
            long_delta_min_base = config.SPREAD_LONG_LEG_DELTA_MIN_PUT
            long_delta_max_base = config.SPREAD_LONG_LEG_DELTA_MAX_PUT
            short_delta_min = config.SPREAD_SHORT_LEG_DELTA_MIN_PUT
            short_delta_max = config.SPREAD_SHORT_LEG_DELTA_MAX_PUT
            oi_min_long = config.OPTIONS_MIN_OPEN_INTEREST_PUT
            spread_max_long = config.OPTIONS_SPREAD_MAX_PCT_PUT
            spread_warn_short = config.OPTIONS_SPREAD_WARNING_PCT_PUT

        long_candidates = []
        elastic_widen_used = 0.0
        effective_dte_min = dte_min if dte_min is not None else config.SPREAD_DTE_MIN
        effective_dte_max = dte_max if dte_max is not None else config.SPREAD_DTE_MAX
        dte_filtered = [
            c for c in filtered if effective_dte_min <= c.days_to_expiry <= effective_dte_max
        ]
        dte_pass = len(dte_filtered)

        for widen in config.ELASTIC_DELTA_STEPS:
            delta_min = max(config.ELASTIC_DELTA_FLOOR, long_delta_min_base - widen)
            delta_max = min(config.ELASTIC_DELTA_CEILING, long_delta_max_base + widen)

            delta_pass = 0
            oi_pass = 0
            spread_pass = 0
            long_candidates = []

            for contract in dte_filtered:
                delta_abs = abs(contract.delta)
                if delta_min <= delta_abs <= delta_max:
                    delta_pass += 1
                    if contract.open_interest >= oi_min_long:
                        oi_pass += 1
                        if contract.spread_pct <= spread_max_long:
                            spread_pass += 1
                            long_candidates.append(contract)

            if long_candidates:
                elastic_widen_used = widen
                break

        if log_filters:
            host.log(
                f"SPREAD_FILTER: LongLeg | Total={len(filtered)} | "
                f"DTE_pass={dte_pass} | Delta_pass={delta_pass} | "
                f"OI_pass={oi_pass} | Spread_pass={spread_pass}"
                + (f" | Elastic_widen=±{elastic_widen_used:.2f}" if elastic_widen_used > 0 else "")
            )
        if debug_stats is not None:
            debug_stats.update(
                {
                    "dte_pass": dte_pass,
                    "delta_pass": delta_pass,
                    "oi_pass": oi_pass,
                    "spread_pass": spread_pass,
                    "elastic_widen": elastic_widen_used,
                    "dte_range": f"{effective_dte_min}-{effective_dte_max}",
                }
            )

        if not long_candidates:
            host.log(
                f"SPREAD: No valid long leg | DTE={effective_dte_min}-{effective_dte_max} | "
                f"Delta={long_delta_min_base}-{long_delta_max_base} | "
                f"Elastic steps tried={len(config.ELASTIC_DELTA_STEPS)}"
            )
            if set_cooldown:
                host._set_spread_failure_cooldown(current_time, direction=direction)
            return None

        delta_target = (
            getattr(config, "SPREAD_LONG_LEG_DELTA_TARGET_CALL", 0.50)
            if is_call
            else getattr(config, "SPREAD_LONG_LEG_DELTA_TARGET_PUT", 0.70)
        )
        long_candidates.sort(key=lambda contract: abs(abs(contract.delta) - delta_target))
        long_leg = long_candidates[0]

        short_candidates = []
        for contract in filtered:
            if contract.strike == long_leg.strike:
                continue
            if contract.days_to_expiry != long_leg.days_to_expiry:
                continue
            if is_call:
                if contract.strike <= long_leg.strike:
                    continue
            else:
                if contract.strike >= long_leg.strike:
                    continue

            width = abs(contract.strike - long_leg.strike)
            dyn_width_max = dyn_widths["width_max"]
            if config.SPREAD_SHORT_LEG_BY_WIDTH:
                if width < effective_width_min or width > dyn_width_max:
                    continue
                delta_abs = abs(contract.delta) if contract.delta else 0.30
            else:
                delta_abs = abs(contract.delta)
                if not (short_delta_min <= delta_abs <= short_delta_max):
                    continue

            if contract.open_interest >= oi_min_long // 2:
                if contract.spread_pct <= spread_warn_short:
                    short_candidates.append((contract, width, delta_abs))

        if not short_candidates:
            host.log(
                f"SPREAD: No valid short leg | LongStrike={long_leg.strike} | "
                f"WidthRange=${effective_width_min:.0f}-${dyn_widths['width_max']:.0f}"
            )
            if set_cooldown:
                host._set_spread_failure_cooldown(current_time, direction=direction)
            return None

        effective_max = dyn_widths["width_effective_max"]
        preferred = [item for item in short_candidates if item[1] <= effective_max]
        if not preferred:
            preferred = short_candidates

        vix_for_pref = None
        try:
            vix_for_pref = float(host._iv_sensor.get_smoothed_vix())
        except Exception:
            vix_for_pref = None

        if vix_for_pref is None:
            pref_min, pref_max, pref_target = (0.18, 0.45, 0.30)
        elif vix_for_pref >= 35.0:
            pref_min, pref_max, pref_target = (0.12, 0.32, 0.22)
        elif vix_for_pref >= 25.0:
            pref_min, pref_max, pref_target = (0.14, 0.36, 0.25)
        elif vix_for_pref >= 18.0:
            pref_min, pref_max, pref_target = (0.16, 0.42, 0.28)
        else:
            pref_min, pref_max, pref_target = (0.20, 0.50, 0.35)

        def rr_sort_key(
            item: tuple[Any, float, float]
        ) -> tuple[float, float, float, float, float, float]:
            candidate, width, delta_abs = item
            estimated_debit = long_leg.mid_price - candidate.mid_price
            debit_to_width = estimated_debit / width if width > 0 else 1.0
            debit_bucket = round(max(0.0, debit_to_width), 2)
            long_delta_abs = abs(float(getattr(long_leg, "delta", 0.0) or 0.0))
            # PoP proxy at breakeven: interpolate between long- and short-leg ITM probabilities.
            pop_proxy = long_delta_abs - debit_to_width * max(0.0, long_delta_abs - delta_abs)
            pop_proxy = max(0.0, min(1.0, pop_proxy))
            if pref_min <= delta_abs <= pref_max:
                delta_band_penalty = 0.0
            else:
                delta_band_penalty = min(abs(delta_abs - pref_min), abs(delta_abs - pref_max))
            if bool(getattr(config, "VASS_SHORT_LEG_SORT_POP_FIRST", True)):
                return (
                    -pop_proxy,
                    debit_bucket,
                    delta_band_penalty,
                    abs(delta_abs - pref_target),
                    debit_to_width,
                    -width,
                )
            return (
                debit_bucket,
                delta_band_penalty,
                abs(delta_abs - pref_target),
                debit_to_width,
                -width,
                -pop_proxy,
            )

        preferred.sort(key=rr_sort_key)
        if bool(getattr(config, "VASS_EV_DIAGNOSTICS_ENABLED", False)):
            top_n = max(1, int(getattr(config, "VASS_EV_DIAGNOSTICS_TOP_N", 3)))
            diag_rows = []
            for candidate, width, delta_abs in preferred[:top_n]:
                est_debit = long_leg.mid_price - candidate.mid_price
                debit_to_width = est_debit / width if width > 0 else 0.0
                net_delta = max(0.0, abs(long_leg.delta) - abs(candidate.delta))
                diag_rows.append(
                    f"Short={candidate.strike:.1f}|W={width:.1f}|DW={debit_to_width:.1%}|"
                    f"ShortD={delta_abs:.2f}|NetD={net_delta:.2f}"
                )
            host.log(
                f"SPREAD_EV_DIAG: Long={long_leg.strike:.1f} D={abs(long_leg.delta):.2f} | "
                + " || ".join(diag_rows)
            )
        short_leg = preferred[0][0]
        actual_width = abs(short_leg.strike - long_leg.strike)
        chosen_debit = long_leg.mid_price - short_leg.mid_price
        chosen_dw = chosen_debit / actual_width if actual_width > 0 else 1.0

        host.log(
            f"SPREAD: Selected legs | Long={long_leg.strike} (delta={long_leg.delta:.2f}) | "
            f"Short={short_leg.strike} (delta={short_leg.delta:.2f}) | Width=${actual_width:.0f} | "
            f"DW~{chosen_dw:.1%} | DeltaPref={pref_min:.2f}-{pref_max:.2f} (target {pref_target:.2f})"
        )

        return (long_leg, short_leg)

    def select_credit_spread_legs(
        self,
        *,
        host: Any,
        contracts: list[Any],
        strategy: Any,
        dte_min: int,
        dte_max: int,
        current_time: Optional[str] = None,
        set_cooldown: bool = True,
        log_filters: bool = True,
        debug_stats: Optional[Dict[str, Any]] = None,
        current_price: Optional[float] = None,
    ) -> Optional[tuple[Any, Any]]:
        """Select short and long legs for credit spread construction."""
        strategy_value = str(getattr(strategy, "value", strategy))
        cooldown_key = strategy_value
        legacy_credit_key = "CREDIT"
        direction_alias_key = "CALL" if strategy_value == "BULL_PUT_CREDIT" else "PUT"
        # V12.12: dynamic width scaling
        dyn_widths = host._get_dynamic_spread_widths(current_price)
        credit_width_target = dyn_widths["credit_width_target"]

        if current_time:
            try:
                if hasattr(host, "_spread_failure_cooldown_until_by_dir"):
                    until = host._spread_failure_cooldown_until_by_dir.get(cooldown_key)
                    if until is None:
                        until = host._spread_failure_cooldown_until_by_dir.get(legacy_credit_key)
                    if until is None:
                        until = host._spread_failure_cooldown_until_by_dir.get(direction_alias_key)
                    if until and current_time < until:
                        return None
                    elif until:
                        host._spread_failure_cooldown_until_by_dir.pop(cooldown_key, None)
                        host._spread_failure_cooldown_until_by_dir.pop(legacy_credit_key, None)
                        host._spread_failure_cooldown_until_by_dir.pop(direction_alias_key, None)
                elif host._spread_failure_cooldown_until:
                    if current_time < host._spread_failure_cooldown_until:
                        return None
                    else:
                        host._spread_failure_cooldown_until = None
            except (ValueError, TypeError):
                pass

        if not contracts:
            host.log("VASS: No contracts provided for credit spread selection")
            return None

        effective_width_min = host._get_effective_spread_width_min(current_price=current_price)
        dte_filtered = [
            contract for contract in contracts if dte_min <= contract.days_to_expiry <= dte_max
        ]

        if log_filters:
            host.log(
                f"SPREAD_FILTER: CreditSpread | Total={len(contracts)} | "
                f"DTE_pass={len(dte_filtered)} (range={dte_min}-{dte_max}) | "
                f"Strategy={strategy_value}"
            )
        if debug_stats is not None:
            debug_stats.update(
                {
                    "dte_pass": len(dte_filtered),
                    "dte_range": f"{dte_min}-{dte_max}",
                }
            )

        if not dte_filtered:
            host.log(
                f"VASS: No contracts in DTE range {dte_min}-{dte_max} "
                f"(available: {[c.days_to_expiry for c in contracts[:5]]}...)"
            )
            if set_cooldown:
                host._set_spread_failure_cooldown(current_time, direction=cooldown_key)
            return None

        if strategy_value == "BULL_PUT_CREDIT":
            puts = [
                contract for contract in dte_filtered if contract.direction == OptionDirection.PUT
            ]

            if not puts:
                host.log("VASS: No PUT contracts available for Bull Put Credit")
                if set_cooldown:
                    host._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            short_candidates = []
            delta_pass_count = 0
            credit_pass_count = 0
            oi_pass_count = 0
            spread_pass_count = 0
            elastic_widen_used = 0.0

            for widen in config.ELASTIC_DELTA_STEPS:
                smoothed_vix = host._iv_sensor.get_smoothed_vix()
                high_vix_thr = float(
                    getattr(config, "CREDIT_SPREAD_SHORT_LEG_HIGH_VIX_THRESHOLD", 25.0)
                )
                base_delta_max = float(getattr(config, "CREDIT_SPREAD_SHORT_LEG_DELTA_MAX", 0.45))
                if smoothed_vix > high_vix_thr:
                    base_delta_max = float(
                        getattr(
                            config, "CREDIT_SPREAD_SHORT_LEG_DELTA_MAX_HIGH_VIX", base_delta_max
                        )
                    )
                delta_min = max(
                    config.ELASTIC_DELTA_FLOOR,
                    config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN - widen,
                )
                delta_max = min(
                    config.ELASTIC_DELTA_CEILING,
                    base_delta_max + widen,
                )

                delta_pass_count = sum(
                    1 for put in puts if delta_min <= abs(put.delta) <= delta_max
                )
                effective_min_credit = config.CREDIT_SPREAD_MIN_CREDIT
                if smoothed_vix > config.CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD:
                    effective_min_credit = config.CREDIT_SPREAD_MIN_CREDIT_HIGH_IV

                short_candidates = []
                credit_pass_count = 0
                oi_pass_count = 0
                spread_pass_count = 0
                for put in puts:
                    if not (delta_min <= abs(put.delta) <= delta_max):
                        continue
                    if put.bid < effective_min_credit:
                        continue
                    credit_pass_count += 1
                    if put.open_interest < config.CREDIT_SPREAD_MIN_OPEN_INTEREST:
                        continue
                    oi_pass_count += 1
                    if put.spread_pct > config.CREDIT_SPREAD_MAX_SPREAD_PCT:
                        continue
                    spread_pass_count += 1
                    short_candidates.append(put)

                if short_candidates:
                    elastic_widen_used = widen
                    break

            host.log(
                f"SPREAD_FILTER: BullPut ShortLeg | Puts={len(puts)} | "
                f"Delta_pass={delta_pass_count} | Credit_pass={credit_pass_count} | "
                f"OI_pass={oi_pass_count} | Spread_pass={spread_pass_count} | "
                f"Delta_range={config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX}"
                + (f" | Elastic_widen=±{elastic_widen_used:.2f}" if elastic_widen_used > 0 else "")
                + f" | Min_credit=${effective_min_credit}"
                + (
                    f" (IV-adaptive, VIX={smoothed_vix:.1f})"
                    if effective_min_credit != config.CREDIT_SPREAD_MIN_CREDIT
                    else ""
                )
            )
            if debug_stats is not None:
                debug_stats.update(
                    {
                        "delta_pass": delta_pass_count,
                        "credit_pass": credit_pass_count,
                        "oi_pass": oi_pass_count,
                        "spread_pass": spread_pass_count,
                        "elastic_widen": elastic_widen_used,
                        "min_credit": effective_min_credit,
                    }
                )

            if not short_candidates:
                host.log(
                    f"VASS: No short put candidates | "
                    f"Delta range: {config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                    f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX} | "
                    f"Elastic steps tried={len(config.ELASTIC_DELTA_STEPS)} | "
                    f"Min credit: ${config.CREDIT_SPREAD_MIN_CREDIT} | "
                    f"Min OI: {config.CREDIT_SPREAD_MIN_OPEN_INTEREST} | "
                    f"Max spread%: {config.CREDIT_SPREAD_MAX_SPREAD_PCT:.2f}"
                )
                if set_cooldown:
                    host._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            short_candidates.sort(key=lambda contract: contract.bid, reverse=True)
            short_leg = short_candidates[0]

            target_long_strike = short_leg.strike - credit_width_target
            long_candidates = [
                put
                for put in puts
                if put.strike == target_long_strike
                and put.expiry == short_leg.expiry
                and put.ask > 0
                and put.open_interest >= max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)
                and put.spread_pct <= config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT
            ]

            if not long_candidates:
                long_candidates = [
                    put
                    for put in puts
                    if put.strike < short_leg.strike
                    and put.expiry == short_leg.expiry
                    and effective_width_min
                    <= (short_leg.strike - put.strike)
                    <= dyn_widths["width_max"]
                    and put.ask > 0
                    and put.open_interest >= max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)
                    and put.spread_pct <= config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT
                ]
                if long_candidates:
                    long_candidates.sort(key=lambda contract: contract.strike, reverse=True)

            if not long_candidates:
                host.log(
                    f"VASS: No long put candidates for protection | "
                    f"Short strike={short_leg.strike} | Target=${target_long_strike} | "
                    f"Min OI={max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)} | "
                    f"Max spread%={config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT:.2f}"
                )
                if set_cooldown:
                    host._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            long_leg = long_candidates[0]
            width = short_leg.strike - long_leg.strike
            credit = short_leg.bid - long_leg.ask

            host.log(
                f"VASS: BULL_PUT_CREDIT selected | "
                f"Sell {short_leg.strike}P @ ${short_leg.bid:.2f} | "
                f"Buy {long_leg.strike}P @ ${long_leg.ask:.2f} | "
                f"Width=${width:.0f} | Credit=${credit:.2f}"
            )

            return (short_leg, long_leg)

        if strategy_value == "BEAR_CALL_CREDIT":
            calls = [
                contract for contract in dte_filtered if contract.direction == OptionDirection.CALL
            ]

            if not calls:
                host.log("VASS: No CALL contracts available for Bear Call Credit")
                if set_cooldown:
                    host._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            short_candidates = []
            delta_pass_count = 0
            credit_pass_count = 0
            oi_pass_count = 0
            spread_pass_count = 0
            elastic_widen_used = 0.0

            for widen in config.ELASTIC_DELTA_STEPS:
                smoothed_vix = host._iv_sensor.get_smoothed_vix()
                high_vix_thr = float(
                    getattr(config, "CREDIT_SPREAD_SHORT_LEG_HIGH_VIX_THRESHOLD", 25.0)
                )
                base_delta_max = float(getattr(config, "CREDIT_SPREAD_SHORT_LEG_DELTA_MAX", 0.45))
                if smoothed_vix > high_vix_thr:
                    base_delta_max = float(
                        getattr(
                            config, "CREDIT_SPREAD_SHORT_LEG_DELTA_MAX_HIGH_VIX", base_delta_max
                        )
                    )
                delta_min = max(
                    config.ELASTIC_DELTA_FLOOR,
                    config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN - widen,
                )
                delta_max = min(
                    config.ELASTIC_DELTA_CEILING,
                    base_delta_max + widen,
                )

                delta_pass_count = sum(
                    1 for call in calls if delta_min <= abs(call.delta) <= delta_max
                )
                effective_min_credit = config.CREDIT_SPREAD_MIN_CREDIT
                if smoothed_vix > config.CREDIT_SPREAD_HIGH_IV_VIX_THRESHOLD:
                    effective_min_credit = config.CREDIT_SPREAD_MIN_CREDIT_HIGH_IV

                short_candidates = []
                credit_pass_count = 0
                oi_pass_count = 0
                spread_pass_count = 0
                for call in calls:
                    if not (delta_min <= abs(call.delta) <= delta_max):
                        continue
                    if call.bid < effective_min_credit:
                        continue
                    credit_pass_count += 1
                    if call.open_interest < config.CREDIT_SPREAD_MIN_OPEN_INTEREST:
                        continue
                    oi_pass_count += 1
                    if call.spread_pct > config.CREDIT_SPREAD_MAX_SPREAD_PCT:
                        continue
                    spread_pass_count += 1
                    short_candidates.append(call)

                if short_candidates:
                    elastic_widen_used = widen
                    break

            host.log(
                f"SPREAD_FILTER: BearCall ShortLeg | Calls={len(calls)} | "
                f"Delta_pass={delta_pass_count} | Credit_pass={credit_pass_count} | "
                f"OI_pass={oi_pass_count} | Spread_pass={spread_pass_count} | "
                f"Delta_range={config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX}"
                + (f" | Elastic_widen=±{elastic_widen_used:.2f}" if elastic_widen_used > 0 else "")
                + f" | Min_credit=${effective_min_credit}"
                + (
                    f" (IV-adaptive, VIX={smoothed_vix:.1f})"
                    if effective_min_credit != config.CREDIT_SPREAD_MIN_CREDIT
                    else ""
                )
            )
            if debug_stats is not None:
                debug_stats.update(
                    {
                        "delta_pass": delta_pass_count,
                        "credit_pass": credit_pass_count,
                        "oi_pass": oi_pass_count,
                        "spread_pass": spread_pass_count,
                        "elastic_widen": elastic_widen_used,
                        "min_credit": effective_min_credit,
                    }
                )

            if not short_candidates:
                host.log(
                    f"VASS: No short call candidates | "
                    f"Delta range: {config.CREDIT_SPREAD_SHORT_LEG_DELTA_MIN}-"
                    f"{config.CREDIT_SPREAD_SHORT_LEG_DELTA_MAX} | "
                    f"Elastic steps tried={len(config.ELASTIC_DELTA_STEPS)} | "
                    f"Min credit: ${config.CREDIT_SPREAD_MIN_CREDIT} | "
                    f"Min OI: {config.CREDIT_SPREAD_MIN_OPEN_INTEREST} | "
                    f"Max spread%: {config.CREDIT_SPREAD_MAX_SPREAD_PCT:.2f}"
                )
                if set_cooldown:
                    host._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            short_candidates.sort(key=lambda contract: contract.bid, reverse=True)
            short_leg = short_candidates[0]

            target_long_strike = short_leg.strike + credit_width_target
            long_candidates = [
                call
                for call in calls
                if call.strike == target_long_strike
                and call.expiry == short_leg.expiry
                and call.ask > 0
                and call.open_interest >= max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)
                and call.spread_pct <= config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT
            ]

            if not long_candidates:
                long_candidates = [
                    call
                    for call in calls
                    if call.strike > short_leg.strike
                    and call.expiry == short_leg.expiry
                    and effective_width_min
                    <= (call.strike - short_leg.strike)
                    <= dyn_widths["width_max"]
                    and call.ask > 0
                    and call.open_interest >= max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)
                    and call.spread_pct <= config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT
                ]
                if long_candidates:
                    long_candidates.sort(key=lambda contract: contract.strike)

            if not long_candidates:
                host.log(
                    f"VASS: No long call candidates for protection | "
                    f"Short strike={short_leg.strike} | Target=${target_long_strike} | "
                    f"Min OI={max(1, config.CREDIT_SPREAD_MIN_OPEN_INTEREST // 2)} | "
                    f"Max spread%={config.CREDIT_SPREAD_LONG_LEG_MAX_SPREAD_PCT:.2f}"
                )
                if set_cooldown:
                    host._set_spread_failure_cooldown(current_time, direction=cooldown_key)
                return None

            long_leg = long_candidates[0]
            width = long_leg.strike - short_leg.strike
            credit = short_leg.bid - long_leg.ask

            host.log(
                f"VASS: BEAR_CALL_CREDIT selected | "
                f"Sell {short_leg.strike}C @ ${short_leg.bid:.2f} | "
                f"Buy {long_leg.strike}C @ ${long_leg.ask:.2f} | "
                f"Width=${width:.0f} | Credit=${credit:.2f}"
            )

            return (short_leg, long_leg)

        host.log(f"VASS: Strategy {strategy} is not a credit spread")
        return None

    def select_spread_legs_with_fallback(
        self,
        *,
        host: Any,
        contracts: list[Any],
        direction: OptionDirection,
        dte_ranges: list[Tuple[int, int]],
        target_width: Optional[float] = None,
        current_time: Optional[str] = None,
        set_cooldown: bool = True,
        current_price: Optional[float] = None,
    ) -> Optional[tuple[Any, Any]]:
        """Try multiple DTE ranges before applying debit spread failure cooldown."""
        if not dte_ranges:
            return host.select_spread_legs(
                contracts=contracts,
                direction=direction,
                target_width=target_width,
                current_time=current_time,
                current_price=current_price,
            )

        failure_stats = []
        for dte_min, dte_max in dte_ranges:
            stats: Dict[str, Any] = {}
            spread_legs = host.select_spread_legs(
                contracts=contracts,
                direction=direction,
                target_width=target_width,
                current_time=current_time,
                dte_min=dte_min,
                dte_max=dte_max,
                set_cooldown=False,
                log_filters=False,
                debug_stats=stats,
                current_price=current_price,
            )
            if spread_legs is not None:
                if dte_min is not None and dte_max is not None:
                    host.log(
                        f"SPREAD: Fallback DTE used | Range={dte_min}-{dte_max} | "
                        f"Direction={direction.value}"
                    )
                return spread_legs
            if stats:
                failure_stats.append(stats)

        if set_cooldown:
            host._set_spread_failure_cooldown(current_time, direction=direction)
        if failure_stats:
            summary = "; ".join(
                [
                    f"{s.get('dte_range')}|DTE={s.get('dte_pass')}|"
                    f"Delta={s.get('delta_pass')}|OI={s.get('oi_pass')}|"
                    f"Spread={s.get('spread_pass')}|Widen={s.get('elastic_widen')}"
                    for s in failure_stats
                ]
            )
            host._last_spread_failure_stats = summary
        return None

    def select_credit_spread_legs_with_fallback(
        self,
        *,
        host: Any,
        contracts: list[Any],
        strategy: Any,
        dte_ranges: list[Tuple[int, int]],
        current_time: Optional[str] = None,
        set_cooldown: bool = True,
        current_price: Optional[float] = None,
    ) -> Optional[tuple[Any, Any]]:
        """Try multiple DTE ranges before applying credit spread failure cooldown."""
        if not dte_ranges:
            return host.select_credit_spread_legs(
                contracts=contracts,
                strategy=strategy,
                dte_min=config.CREDIT_SPREAD_DTE_MIN,
                dte_max=config.CREDIT_SPREAD_DTE_MAX,
                current_time=current_time,
                current_price=current_price,
            )

        failure_stats = []
        for dte_min, dte_max in dte_ranges:
            stats: Dict[str, Any] = {}
            spread_legs = host.select_credit_spread_legs(
                contracts=contracts,
                strategy=strategy,
                dte_min=dte_min,
                dte_max=dte_max,
                current_time=current_time,
                set_cooldown=False,
                log_filters=False,
                debug_stats=stats,
                current_price=current_price,
            )
            if spread_legs is not None:
                host.log(
                    f"VASS: Credit fallback DTE used | Range={dte_min}-{dte_max} | "
                    f"Strategy={strategy.value}"
                )
                return spread_legs
            if stats:
                failure_stats.append(stats)

        cooldown_key = strategy.value if hasattr(strategy, "value") else str(strategy)
        if set_cooldown:
            host._set_spread_failure_cooldown(current_time, direction=cooldown_key)
        if failure_stats:
            summary = "; ".join(
                [
                    f"{s.get('dte_range')}|DTE={s.get('dte_pass')}|"
                    f"Delta={s.get('delta_pass')}|Credit={s.get('credit_pass')}|"
                    f"OI={s.get('oi_pass')}|Spread={s.get('spread_pass')}|"
                    f"Widen={s.get('elastic_widen')}|MinCred={s.get('min_credit')}"
                    for s in failure_stats
                ]
            )
            host._last_credit_failure_stats = summary
        return None

    def pop_last_spread_failure_stats(self, *, host: Any) -> Optional[str]:
        """Pop and clear last debit spread construction failure diagnostics."""
        stats = host._last_spread_failure_stats
        host._last_spread_failure_stats = None
        return stats

    def pop_last_credit_failure_stats(self, *, host: Any) -> Optional[str]:
        """Pop and clear last credit spread construction failure diagnostics."""
        stats = host._last_credit_failure_stats
        host._last_credit_failure_stats = None
        return stats

    def set_last_entry_validation_failure(self, *, host: Any, reason: Optional[str]) -> None:
        """Set last VASS entry validation failure reason for downstream logging."""
        host._last_entry_validation_failure = reason

    def pop_last_entry_validation_failure(self, *, host: Any) -> Optional[str]:
        """Pop and clear last VASS entry validation failure reason."""
        reason = host._last_entry_validation_failure
        host._last_entry_validation_failure = None
        return reason

    def build_spread_signal(
        self,
        *,
        host: Any,
        chain: Any,
        candidate_contracts: list[Any],
        direction: OptionDirection,
        regime_score: float,
        qqq_price: float,
        adx_value: float,
        ma200_value: float,
        ma50_value: float,
        iv_rank: float,
        size_multiplier: float,
        portfolio_value: float,
        margin_remaining: float,
        strategy: Any,
        vass_dte_min: int,
        vass_dte_max: int,
        dte_ranges: list[Tuple[int, int]],
        is_credit: bool,
        is_eod_scan: bool,
        fallback_log_prefix: str,
    ) -> Tuple[Optional[Any], str]:
        """Build VASS spread entry signal from pre-filtered candidates."""
        algorithm = getattr(host, "algorithm", None)
        if algorithm is None:
            return None, "NO_ALGO"

        rejection_code = "UNKNOWN"
        signal: Optional[Any] = None
        dte_min_all = min(r[0] for r in dte_ranges)
        dte_max_all = max(r[1] for r in dte_ranges)
        now_str = str(algorithm.Time)
        max_attempts = max(1, int(getattr(config, "VASS_ROUTE_MAX_CANDIDATE_ATTEMPTS", 3)))
        allow_opposite_fallback = bool(
            getattr(config, "VASS_OPPOSITE_ROUTE_FALLBACK_ENABLED", True)
        )

        def _is_quality_failure(reason: Optional[str]) -> bool:
            return bool(reason and str(reason).startswith("R_CONTRACT_QUALITY:"))

        def _strategy_value(strategy_obj: Any) -> str:
            return str(getattr(strategy_obj, "value", strategy_obj))

        def _ev_pre_gate_failure(route_strategy: Any) -> Optional[str]:
            """Fast EV pre-gate to avoid running leg-construction in weak contexts."""
            if not bool(getattr(config, "VASS_EV_PRE_GATE_ENABLED", False)):
                return None

            route_value = _strategy_value(route_strategy).upper()
            is_debit_route = "DEBIT" in route_value
            if is_debit_route:
                max_debit_iv_rank = float(getattr(config, "VASS_EV_PRE_DEBIT_IV_RANK_MAX", 100.0))
                if float(iv_rank) > max_debit_iv_rank:
                    return (
                        f"R_EV_PRE_DEBIT_IV_RANK_HIGH:{float(iv_rank):.1f}>{max_debit_iv_rank:.1f}"
                    )

            if route_value == "BULL_CALL_DEBIT":
                min_bull_regime = float(getattr(config, "VASS_EV_PRE_BULL_REGIME_MIN", 0.0))
                if float(regime_score) < min_bull_regime:
                    return (
                        f"R_EV_PRE_BULL_REGIME_LOW:{float(regime_score):.1f}<"
                        f"{min_bull_regime:.1f}"
                    )
            elif route_value == "BEAR_PUT_DEBIT":
                max_bear_regime = float(getattr(config, "VASS_EV_PRE_BEAR_REGIME_MAX", 100.0))
                if float(regime_score) > max_bear_regime:
                    return (
                        f"R_EV_PRE_BEAR_REGIME_HIGH:{float(regime_score):.1f}>"
                        f"{max_bear_regime:.1f}"
                    )
            return None

        def _is_structural_ev_failure(reason: Optional[str]) -> bool:
            """Detect non-recoverable structural failures where opposite-route retry is churn."""
            if not reason:
                return False
            reason_u = str(reason).upper()
            if reason_u.startswith("R_EV_PRE_"):
                return True
            if not reason_u.startswith("R_CONTRACT_QUALITY:"):
                return False
            detail = reason_u.split(":", 1)[1] if ":" in reason_u else ""
            structural_quality = {
                "DEBIT_TO_WIDTH_TOO_HIGH",
                "DEBIT_TO_WIDTH_TOO_LOW",
                "DEBIT_ABSOLUTE_CAP_EXCEEDED",
                "FRICTION_TO_TARGET_TOO_HIGH",
                "COMMISSION_TO_MAX_PROFIT_TOO_HIGH",
                "ENTRY_SCORE_BELOW_MIN",
                "LONG_DELTA_BELOW_MIN",
                "LONG_DELTA_ABOVE_MAX",
                "BULL_NET_DELTA_TOO_LOW",
            }
            return detail in structural_quality

        def _opposite_strategy(primary: Any) -> Optional[Any]:
            mapping = {
                "BULL_CALL_DEBIT": "BULL_PUT_CREDIT",
                "BEAR_PUT_DEBIT": "BEAR_CALL_CREDIT",
                "BULL_PUT_CREDIT": "BULL_CALL_DEBIT",
                "BEAR_CALL_CREDIT": "BEAR_PUT_DEBIT",
            }
            target = mapping.get(_strategy_value(primary))
            if target is None:
                return None
            enum_cls = type(primary)
            try:
                return enum_cls(target)
            except Exception:
                try:
                    return enum_cls[target]
                except Exception:
                    return None

        def _attempt_debit_route(
            route_contracts: list[Any],
            route_strategy: Any,
        ) -> Tuple[Optional[Any], str, Optional[str]]:
            pool = list(route_contracts)
            last_validation_reason: Optional[str] = None
            route_rejection = "DEBIT_LEG_SELECTION_FAILED"

            if _strategy_value(route_strategy) == "BULL_CALL_DEBIT":
                qqq_open = float(getattr(algorithm, "_qqq_at_open", 0.0) or 0.0)
                if qqq_open <= 0:
                    try:
                        qqq_open = float(algorithm.Securities[algorithm.qqq].Open)
                    except Exception:
                        qqq_open = 0.0

                qqq_sma20 = getattr(algorithm, "qqq_sma20", None)
                qqq_sma20_ready = bool(
                    qqq_sma20 is not None and getattr(qqq_sma20, "IsReady", False)
                )
                qqq_sma20_value = (
                    float(qqq_sma20.Current.Value)
                    if qqq_sma20_ready and getattr(qqq_sma20, "Current", None) is not None
                    else None
                )

                trend_ok, trend_code, trend_detail = host.check_vass_bull_debit_trend_confirmation(
                    vix_current=float(getattr(algorithm, "_current_vix", 0.0) or 0.0),
                    current_price=qqq_price,
                    qqq_open=qqq_open if qqq_open > 0 else None,
                    qqq_sma20=qqq_sma20_value,
                    qqq_sma20_ready=qqq_sma20_ready,
                )
                if not trend_ok:
                    host.set_last_entry_validation_failure(trend_code)
                    algorithm._log_high_frequency_event(
                        config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                        category="VASS_FALLBACK",
                        reason_key=f"TREND_CONFIRM_BLOCK|{trend_code}",
                        message=(
                            f"{fallback_log_prefix}: BULL_CALL trend confirmation blocked | "
                            f"{trend_code} | {trend_detail}"
                        ),
                    )
                    return None, "DEBIT_TREND_CONFIRM_BLOCK", trend_code

            for attempt in range(max_attempts):
                if len(pool) < 2:
                    break
                spread_legs = host.select_spread_legs_with_fallback(
                    contracts=pool,
                    direction=direction,
                    current_time=now_str,
                    dte_ranges=dte_ranges,
                    set_cooldown=(attempt == max_attempts - 1),
                    current_price=qqq_price,
                )
                if spread_legs is None:
                    break

                long_leg, short_leg = spread_legs
                route_rejection = "DEBIT_ENTRY_VALIDATION_FAILED"

                if not (long_leg.ask > 0 and short_leg.bid > 0 and short_leg.ask > 0):
                    route_rejection = "DEBIT_ENTRY_QUOTES_INVALID"
                    drop_syms = {str(long_leg.symbol), str(short_leg.symbol)}
                    pool = [c for c in pool if str(c.symbol) not in drop_syms]
                    continue

                route_signal = host.check_spread_entry_signal(
                    regime_score=regime_score,
                    vix_current=float(getattr(algorithm, "_current_vix", 0.0) or 0.0),
                    adx_value=adx_value,
                    current_price=qqq_price,
                    ma200_value=ma200_value,
                    ma50_value=ma50_value,
                    iv_rank=iv_rank,
                    current_hour=algorithm.Time.hour,
                    current_minute=algorithm.Time.minute,
                    current_date=str(algorithm.Time.date()),
                    portfolio_value=portfolio_value,
                    long_leg_contract=long_leg,
                    short_leg_contract=short_leg,
                    gap_filter_triggered=algorithm.risk_engine.is_gap_filter_active(),
                    vol_shock_active=algorithm.risk_engine.is_vol_shock_active(algorithm.Time),
                    size_multiplier=size_multiplier,
                    margin_remaining=margin_remaining,
                    dte_min=vass_dte_min,
                    dte_max=vass_dte_max,
                    is_eod_scan=is_eod_scan,
                    direction=direction,
                    candidate_contracts=pool,
                )
                if route_signal is not None:
                    return route_signal, route_rejection, None

                last_validation_reason = host.pop_last_entry_validation_failure()
                retryable_quality = (
                    _is_quality_failure(last_validation_reason) and attempt < max_attempts - 1
                )
                if retryable_quality:
                    algorithm._log_high_frequency_event(
                        config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                        category="VASS_FALLBACK",
                        reason_key="DEBIT_QUALITY_RETRY",
                        message=(
                            f"{fallback_log_prefix}: DEBIT quality reject ({last_validation_reason}) | "
                            f"Trying alternate candidate {attempt + 2}/{max_attempts}"
                        ),
                    )
                    pool = [c for c in pool if str(c.symbol) != str(short_leg.symbol)]
                    continue
                break

            return None, route_rejection, last_validation_reason

        def _attempt_credit_route(
            route_contracts: list[Any],
            route_strategy: Any,
        ) -> Tuple[Optional[Any], str, Optional[str]]:
            pool = list(route_contracts)
            last_validation_reason: Optional[str] = None
            route_rejection = "CREDIT_LEG_SELECTION_FAILED"

            for attempt in range(max_attempts):
                if len(pool) < 2:
                    break
                spread_legs = host.select_credit_spread_legs_with_fallback(
                    contracts=pool,
                    strategy=route_strategy,
                    dte_ranges=dte_ranges,
                    current_time=now_str,
                    set_cooldown=(attempt == max_attempts - 1),
                    current_price=qqq_price,
                )
                if spread_legs is None:
                    break

                short_leg, long_leg = spread_legs
                route_rejection = "CREDIT_ENTRY_VALIDATION_FAILED"

                if not (short_leg.bid > 0 and short_leg.ask > 0 and long_leg.ask > 0):
                    route_rejection = "CREDIT_ENTRY_QUOTES_INVALID"
                    drop_syms = {str(short_leg.symbol), str(long_leg.symbol)}
                    pool = [c for c in pool if str(c.symbol) not in drop_syms]
                    continue

                route_signal = host.check_credit_spread_entry_signal(
                    regime_score=regime_score,
                    vix_current=float(getattr(algorithm, "_current_vix", 0.0) or 0.0),
                    adx_value=adx_value,
                    current_price=qqq_price,
                    ma200_value=ma200_value,
                    iv_rank=iv_rank,
                    current_hour=algorithm.Time.hour,
                    current_minute=algorithm.Time.minute,
                    current_date=str(algorithm.Time.date()),
                    portfolio_value=portfolio_value,
                    short_leg_contract=short_leg,
                    long_leg_contract=long_leg,
                    strategy=route_strategy,
                    gap_filter_triggered=algorithm.risk_engine.is_gap_filter_active(),
                    vol_shock_active=algorithm.risk_engine.is_vol_shock_active(algorithm.Time),
                    size_multiplier=size_multiplier,
                    margin_remaining=margin_remaining,
                    is_eod_scan=is_eod_scan,
                    direction=direction,
                )
                if route_signal is not None:
                    return route_signal, route_rejection, None

                last_validation_reason = host.pop_last_entry_validation_failure()
                retryable_quality = (
                    _is_quality_failure(last_validation_reason) and attempt < max_attempts - 1
                )
                if retryable_quality:
                    algorithm._log_high_frequency_event(
                        config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                        category="VASS_FALLBACK",
                        reason_key="CREDIT_QUALITY_RETRY",
                        message=(
                            f"{fallback_log_prefix}: CREDIT quality reject ({last_validation_reason}) | "
                            f"Trying alternate candidate {attempt + 2}/{max_attempts}"
                        ),
                    )
                    pool = [c for c in pool if str(c.symbol) != str(short_leg.symbol)]
                    continue
                break

            return None, route_rejection, last_validation_reason

        last_validation_reason: Optional[str] = None
        pre_gate_reason = _ev_pre_gate_failure(strategy)
        if pre_gate_reason is not None:
            host.set_last_entry_validation_failure(pre_gate_reason)
            return None, pre_gate_reason

        if is_credit:
            signal, rejection_code, last_validation_reason = _attempt_credit_route(
                candidate_contracts, strategy
            )
        else:
            signal, rejection_code, last_validation_reason = _attempt_debit_route(
                candidate_contracts, strategy
            )

        if signal is not None:
            return signal, rejection_code

        if (
            allow_opposite_fallback
            and bool(getattr(config, "VASS_OPPOSITE_ROUTE_BLOCK_ON_STRUCTURAL_FAIL", True))
            and _is_structural_ev_failure(last_validation_reason)
        ):
            allow_opposite_fallback = False
            rejection_code = "R_OPPOSITE_ROUTE_BLOCKED_EV"
            algorithm._log_high_frequency_event(
                config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                category="VASS_FALLBACK",
                reason_key="SKIP_OPPOSITE_STRUCTURAL_EV",
                message=(
                    f"{fallback_log_prefix}: Skip opposite route after structural failure | "
                    f"Reason={last_validation_reason}"
                ),
            )

        if allow_opposite_fallback:
            fallback_strategy = _opposite_strategy(strategy)
            if fallback_strategy is not None:
                if (
                    _strategy_value(strategy) == "BEAR_PUT_DEBIT"
                    and _strategy_value(fallback_strategy) == "BEAR_CALL_CREDIT"
                ):
                    if not bool(
                        getattr(config, "VASS_BEARISH_FALLBACK_TO_BEAR_CALL_CREDIT", False)
                    ):
                        rejection_code = "R_BEAR_FALLBACK_DISABLED"
                        algorithm._log_high_frequency_event(
                            config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                            category="VASS_FALLBACK",
                            reason_key="BEAR_FALLBACK_DISABLED",
                            message=(
                                f"{fallback_log_prefix}: Skip opposite {fallback_strategy.value} | "
                                "Policy disabled"
                            ),
                        )
                        fallback_strategy = None
                    else:
                        max_regime = float(getattr(config, "VASS_BEAR_FALLBACK_MAX_REGIME", 40.0))
                        min_vix = float(getattr(config, "VASS_BEAR_FALLBACK_MIN_VIX", 0.0))
                        vix_now = float(getattr(algorithm, "_current_vix", 0.0) or 0.0)
                        if regime_score > max_regime or (min_vix > 0 and vix_now < min_vix):
                            rejection_code = "R_BEAR_FALLBACK_POLICY"
                            algorithm._log_high_frequency_event(
                                config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                                category="VASS_FALLBACK",
                                reason_key="BEAR_FALLBACK_POLICY",
                                message=(
                                    f"{fallback_log_prefix}: Skip opposite {fallback_strategy.value} | "
                                    f"Regime={regime_score:.1f}>{max_regime:.1f} or "
                                    f"VIX={vix_now:.1f}<{min_vix:.1f}"
                                ),
                            )
                            fallback_strategy = None

            if fallback_strategy is not None:
                fallback_pre_gate_reason = _ev_pre_gate_failure(fallback_strategy)
                if fallback_pre_gate_reason is not None:
                    rejection_code = fallback_pre_gate_reason
                    algorithm._log_high_frequency_event(
                        config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                        category="VASS_FALLBACK",
                        reason_key="SKIP_OPPOSITE_EV_PRE_GATE",
                        message=(
                            f"{fallback_log_prefix}: Opposite route pre-gated | "
                            f"Strategy={getattr(fallback_strategy, 'value', fallback_strategy)} | "
                            f"Reason={fallback_pre_gate_reason}"
                        ),
                    )
                    fallback_strategy = None

            if fallback_strategy is not None:
                fallback_is_credit = host.is_credit_strategy(fallback_strategy)
                fallback_right = host.strategy_option_right(fallback_strategy)
                fallback_contracts = host.build_vass_candidate_contracts(
                    chain=chain,
                    direction=direction,
                    dte_min=dte_min_all,
                    dte_max=dte_max_all,
                    option_right=fallback_right,
                )
                if len(fallback_contracts) >= 2:
                    algorithm._log_high_frequency_event(
                        config_flag="LOG_VASS_FALLBACK_BACKTEST_ENABLED",
                        category="VASS_FALLBACK",
                        reason_key=(
                            f"TRY_OPPOSITE|{strategy.value}|"
                            f"{getattr(fallback_strategy, 'value', fallback_strategy)}"
                        ),
                        message=(
                            f"{fallback_log_prefix}: Primary {strategy.value} failed | "
                            f"Trying opposite {getattr(fallback_strategy, 'value', fallback_strategy)}"
                        ),
                    )
                    if fallback_is_credit:
                        signal, rejection_code, last_validation_reason = _attempt_credit_route(
                            fallback_contracts, fallback_strategy
                        )
                    else:
                        signal, rejection_code, last_validation_reason = _attempt_debit_route(
                            fallback_contracts, fallback_strategy
                        )
                    if signal is not None:
                        return signal, rejection_code
                else:
                    rejection_code = "OPPOSITE_ROUTE_INSUFFICIENT_CANDIDATES"

        if last_validation_reason is not None:
            host.set_last_entry_validation_failure(last_validation_reason)
        return None, rejection_code

    def _add_trading_days(self, start: datetime, days: int) -> datetime:
        """Add trading days (skip weekends) to a datetime."""
        result = start
        remaining = max(0, int(days))
        while remaining > 0:
            result += timedelta(days=1)
            if result.weekday() < 5:
                remaining -= 1
        return result

    def should_block_for_loss_breaker(self, current_date: str) -> bool:
        """Return True when VASS loss breaker pause is active for current trading date."""
        if not bool(getattr(config, "VASS_LOSS_BREAKER_ENABLED", False)):
            return False
        pause_until = self._loss_breaker_pause_until
        if not pause_until:
            return False
        try:
            trade_date = datetime.strptime(str(current_date)[:10], "%Y-%m-%d").date()
            pause_date = datetime.strptime(str(pause_until)[:10], "%Y-%m-%d").date()
            if trade_date <= pause_date:
                return True
            self._loss_breaker_pause_until = None
            return False
        except Exception:
            self._loss_breaker_pause_until = None
            return False

    def record_spread_result(self, *, is_win: bool, now_dt: Optional[datetime]) -> Optional[str]:
        """Update VASS breaker state from spread result; returns pause-until when armed."""
        if not bool(getattr(config, "VASS_LOSS_BREAKER_ENABLED", False)):
            return None
        if is_win:
            self._consecutive_losses = 0
            self._loss_breaker_pause_until = None
            return None

        self._consecutive_losses += 1
        threshold = int(getattr(config, "VASS_LOSS_BREAKER_THRESHOLD", 3))
        if self._consecutive_losses < threshold or now_dt is None:
            return None

        pause_days = max(1, int(getattr(config, "VASS_LOSS_BREAKER_PAUSE_DAYS", 1)))
        pause_until = self._add_trading_days(now_dt, pause_days)
        self._loss_breaker_pause_until = pause_until.date().isoformat()
        self._consecutive_losses = 0
        return self._loss_breaker_pause_until

    def check_similar_entry_guard(
        self,
        *,
        signature: str,
        now_dt: Optional[datetime],
    ) -> Optional[str]:
        """Return rejection code when same-signature entry is blocked."""
        if not signature or now_dt is None:
            return None

        min_gap_min = int(getattr(config, "VASS_SIMILAR_ENTRY_MIN_GAP_MINUTES", 15))
        last_entry = self._last_entry_at_by_signature.get(signature)
        if last_entry is not None:
            elapsed_min = (now_dt - last_entry).total_seconds() / 60.0
            if 0 <= elapsed_min < min_gap_min:
                self._log(
                    f"VASS_SIGNATURE_BLOCK: Burst guard | Sig={signature} | "
                    f"Elapsed={elapsed_min:.1f}m < {min_gap_min}m"
                )
                return "E_VASS_SIMILAR_15M_BLOCK"

        cooldown_until = self._cooldown_until_by_signature.get(signature)
        if cooldown_until is not None and now_dt < cooldown_until:
            self._log(
                f"VASS_SIGNATURE_BLOCK: Cooldown guard | Sig={signature} | "
                f"Now={now_dt} < Until={cooldown_until}"
            )
            return "E_VASS_SIMILAR_3D_COOLDOWN"

        if self._last_entry_at_by_signature:
            stale_cutoff = now_dt - timedelta(days=10)
            stale = [k for k, ts in self._last_entry_at_by_signature.items() if ts < stale_cutoff]
            for key in stale:
                self._last_entry_at_by_signature.pop(key, None)
                self._cooldown_until_by_signature.pop(key, None)
        return None

    def build_signature(
        self,
        *,
        spread_type: str,
        direction: Optional[OptionDirection],
        long_leg_contract: Any,
    ) -> str:
        """Build same-trade signature key for VASS anti-cluster guard."""
        strategy = str(spread_type or "UNKNOWN").upper()
        direction_key = direction.value if direction is not None else "NONE"
        use_expiry = bool(getattr(config, "VASS_SIMILAR_ENTRY_USE_EXPIRY_BUCKET", True))
        if use_expiry and getattr(long_leg_contract, "expiry", None):
            expiry_bucket = str(long_leg_contract.expiry)
        else:
            expiry_bucket = f"DTE:{int(getattr(long_leg_contract, 'days_to_expiry', -1))}"
        return f"{strategy}|{direction_key}|{expiry_bucket}"

    def parse_scan_dt(
        self,
        *,
        date_text: str,
        hour: int,
        minute: int,
        algorithm: Any = None,
    ) -> Optional[datetime]:
        """Parse scan timestamp; fallback to algorithm clock when parse fails."""
        try:
            return datetime.strptime(
                f"{date_text} {int(hour):02d}:{int(minute):02d}:00", "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            pass
        if algorithm is not None:
            try:
                return algorithm.Time
            except Exception:
                return None
        return None

    def record_signature_entry(self, *, signature: str, entry_dt: Optional[datetime]) -> None:
        if not signature or entry_dt is None:
            return
        cooldown_days = int(getattr(config, "VASS_SIMILAR_ENTRY_COOLDOWN_DAYS", 3))
        self._last_entry_at_by_signature[signature] = entry_dt
        self._cooldown_until_by_signature[signature] = entry_dt + timedelta(days=cooldown_days)

    def check_direction_day_gap(
        self,
        *,
        direction: Optional[OptionDirection],
        current_date: str,
        algorithm: Any,
    ) -> Optional[str]:
        if direction is None:
            return None

        dir_label = "BULLISH" if direction == OptionDirection.CALL else "BEARISH"
        now_dt = None
        if algorithm is not None:
            try:
                now_dt = algorithm.Time
            except Exception:
                now_dt = None

        if bool(getattr(config, "VASS_DIRECTION_MIN_GAP_ENABLED", True)):
            min_gap_min = int(getattr(config, "VASS_DIRECTION_MIN_GAP_MINUTES", 0))
            if min_gap_min > 0 and now_dt is not None:
                last_dt = self._last_entry_dt_by_direction.get(dir_label)
                if last_dt is not None:
                    elapsed_min = (now_dt - last_dt).total_seconds() / 60.0
                    if 0 <= elapsed_min < min_gap_min:
                        return (
                            f"R_DIRECTION_MIN_GAP: {dir_label} elapsed {elapsed_min:.1f}m "
                            f"< {min_gap_min}m"
                        )

        if not bool(getattr(config, "VASS_DIRECTION_DAY_GAP_ENABLED", True)):
            return None

        today = str(current_date or "")[:10]
        if not today:
            try:
                today = str(algorithm.Time.date()) if algorithm is not None else ""
            except Exception:
                today = ""
        if not today:
            return None

        last_date = self._last_entry_date_by_direction.get(dir_label)
        if last_date == today:
            return f"R_DIRECTION_DAY_GAP: {dir_label} already entered on {today}"
        return None

    def record_direction_day_entry(
        self,
        *,
        direction: Optional[OptionDirection],
        entry_dt: Optional[datetime],
    ) -> None:
        if direction is None or entry_dt is None:
            return
        dir_label = "BULLISH" if direction == OptionDirection.CALL else "BEARISH"
        self._last_entry_date_by_direction[dir_label] = str(entry_dt.date())
        self._last_entry_dt_by_direction[dir_label] = entry_dt

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_entry_at_by_signature": {
                k: v.strftime("%Y-%m-%d %H:%M:%S")
                for k, v in self._last_entry_at_by_signature.items()
            },
            "cooldown_until_by_signature": {
                k: v.strftime("%Y-%m-%d %H:%M:%S")
                for k, v in self._cooldown_until_by_signature.items()
            },
            "last_entry_date_by_direction": dict(self._last_entry_date_by_direction),
            "last_entry_dt_by_direction": {
                k: v.strftime("%Y-%m-%d %H:%M:%S")
                for k, v in self._last_entry_dt_by_direction.items()
            },
            "slot_backoff_until_by_key": {
                k: v.strftime("%Y-%m-%d %H:%M:%S")
                for k, v in self._slot_backoff_until_by_key.items()
            },
            "consecutive_losses": self._consecutive_losses,
            "loss_breaker_pause_until": self._loss_breaker_pause_until,
        }

    def from_dict(self, state: Dict[str, Any]) -> None:
        self._last_entry_at_by_signature = {}
        self._cooldown_until_by_signature = {}
        self._last_entry_date_by_direction = {
            str(k).upper(): str(v)[:10]
            for k, v in (state.get("last_entry_date_by_direction", {}) or {}).items()
            if str(k).upper() in {"BULLISH", "BEARISH"} and str(v)
        }
        self._last_entry_dt_by_direction = {}
        self._slot_backoff_until_by_key = {}
        for k, v in (state.get("last_entry_dt_by_direction", {}) or {}).items():
            key = str(k).upper()
            if key not in {"BULLISH", "BEARISH"}:
                continue
            try:
                self._last_entry_dt_by_direction[key] = datetime.strptime(
                    str(v)[:19], "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                continue
        self._consecutive_losses = int(state.get("consecutive_losses", 0) or 0)
        raw_pause = state.get("loss_breaker_pause_until")
        self._loss_breaker_pause_until = str(raw_pause)[:10] if raw_pause else None
        for k, v in (state.get("last_entry_at_by_signature", {}) or {}).items():
            try:
                self._last_entry_at_by_signature[str(k)] = datetime.strptime(
                    str(v)[:19], "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                continue
        for k, v in (state.get("cooldown_until_by_signature", {}) or {}).items():
            try:
                self._cooldown_until_by_signature[str(k)] = datetime.strptime(
                    str(v)[:19], "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                continue
        for k, v in (state.get("slot_backoff_until_by_key", {}) or {}).items():
            try:
                self._slot_backoff_until_by_key[str(k)] = datetime.strptime(
                    str(v)[:19], "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                continue

    def reset_daily(self) -> None:
        """Clear intraday slot-backoff state."""
        self._slot_backoff_until_by_key = {}

    def reset(self) -> None:
        self._last_entry_at_by_signature = {}
        self._cooldown_until_by_signature = {}
        self._last_entry_date_by_direction = {}
        self._last_entry_dt_by_direction = {}
        self._slot_backoff_until_by_key = {}
        self._consecutive_losses = 0
        self._loss_breaker_pause_until = None
