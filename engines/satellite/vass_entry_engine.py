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
        self._consecutive_losses: int = 0
        self._loss_breaker_pause_until: Optional[str] = None  # YYYY-MM-DD

    def _log(self, message: str, trades_only: bool = False) -> None:
        if self._log_func:
            self._log_func(message, trades_only)

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

    def build_dte_fallbacks(self, dte_min: int, dte_max: int) -> list[Tuple[int, int]]:
        """Build ordered DTE fallback windows for VASS entry selection."""
        ranges = [(dte_min, dte_max)]
        fallback_min = max(5, dte_min - 2)
        fallback_max = min(45, dte_max + 14)
        if (fallback_min, fallback_max) != (dte_min, dte_max):
            ranges.append((fallback_min, fallback_max))
        return ranges

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

        strategy, vass_dte_min, vass_dte_max, is_credit = host.resolve_vass_strategy(
            direction=direction_str,
            overlay_state=overlay_state,
        )
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
        algorithm._diag_vass_signal_seq = int(getattr(algorithm, "_diag_vass_signal_seq", 0)) + 1
        vass_signal_id = (
            f"VASS-{algorithm.Time.strftime('%Y%m%d-%H%M')}-{algorithm._diag_vass_signal_seq}"
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

        iv_rank = algorithm._calculate_iv_rank(chain)
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
            iv_env = host.get_iv_environment() if host.is_iv_sensor_ready() else "UNKNOWN"
            skip_reasons = {
                "R_SLOT_SWING_MAX",
                "R_SLOT_TOTAL_MAX",
                "R_SLOT_DIRECTION_MAX",
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
            elif validation_reason == "R_COOLDOWN_DIRECTIONAL":
                reason_text = "Skipped - entry attempt limit reached"
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
        )
        dte_ranges = host.build_vass_dte_fallbacks(vass_dte_min, vass_dte_max)
        dte_min_all = min(r[0] for r in dte_ranges)
        dte_max_all = max(r[1] for r in dte_ranges)
        can_swing_vass, swing_reason_vass = host.can_enter_swing(
            direction=direction, overlay_state=overlay_state
        )
        if not can_swing_vass:
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
        algorithm._diag_vass_signal_seq = int(getattr(algorithm, "_diag_vass_signal_seq", 0)) + 1
        vass_signal_id = (
            f"VASS-{algorithm.Time.strftime('%Y%m%d-%H%M')}-{algorithm._diag_vass_signal_seq}"
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
                    f"IV_Env={host.get_iv_environment()} | "
                    f"VIX={current_vix:.1f} | Regime={regime_score:.0f} | "
                    f"Contracts_checked={len(candidate_contracts)} | "
                    f"Strategy={'CREDIT' if is_credit else 'DEBIT'} | "
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
        if not bool(getattr(config, "VASS_DIRECTION_DAY_GAP_ENABLED", True)):
            return None
        if direction is None:
            return None

        dir_label = "BULLISH" if direction == OptionDirection.CALL else "BEARISH"
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

    def reset_daily(self) -> None:
        """No-op for now; guards are multi-day by design."""
        pass

    def reset(self) -> None:
        self._last_entry_at_by_signature = {}
        self._cooldown_until_by_signature = {}
        self._last_entry_date_by_direction = {}
        self._consecutive_losses = 0
        self._loss_breaker_pause_until = None
