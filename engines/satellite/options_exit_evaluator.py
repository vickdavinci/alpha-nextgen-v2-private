"""Options exit signal evaluator extracted from options_engine."""

from __future__ import annotations

import config


def check_exit_signals_impl(
    self,
    current_price: float,
    current_dte: Optional[int] = None,
    position: "Optional[OptionsPosition]" = None,
) -> Optional[TargetWeight]:
    """
    Check for options exit signals.

    V2.3.10: Added DTE exit to prevent options being held to expiration.
    V6.22 FIX: Accept explicit position param so intraday positions get
    the same software stop/profit/DTE coverage as swing positions.

    Args:
        current_price: Current option price.
        current_dte: Optional current days to expiration.
        position: Explicit position to check. Falls back to self._position.

    Returns:
        TargetWeight for exit, or None if no exit signal.
    """
    pos = position if position is not None else self._position
    if pos is None:
        return None

    symbol = pos.contract.symbol
    symbol_str = self._symbol_str(symbol)
    entry_price = pos.entry_price
    is_intraday_pos = self._find_intraday_lane_by_symbol(symbol_str) is not None

    if is_intraday_pos and self.has_pending_intraday_exit(symbol=symbol_str):
        return None

    # Calculate P&L percentage
    pnl_pct = (current_price - entry_price) / entry_price
    strategy_name = self._canonical_intraday_strategy_name(getattr(pos, "entry_strategy", ""))
    held_minutes: Optional[float] = None
    if is_intraday_pos and self.algorithm is not None and hasattr(self.algorithm, "Time"):
        try:
            entry_dt = datetime.strptime(
                str(getattr(pos, "entry_time", ""))[:19], "%Y-%m-%d %H:%M:%S"
            )
            held_minutes = (self.algorithm.Time - entry_dt).total_seconds() / 60.0
        except Exception:
            held_minutes = None

    # Exit 0: Stagnation timer for MICRO intraday strategies.
    # If the trade stays near-flat for too long, exit before theta/chop bleeds it out.
    if (
        is_intraday_pos
        and strategy_name
        in (
            IntradayStrategy.MICRO_DEBIT_FADE.value,
            IntradayStrategy.MICRO_OTM_MOMENTUM.value,
        )
        and bool(getattr(config, "MICRO_STAGNATION_EXIT_ENABLED", False))
        and held_minutes is not None
    ):
        min_hold_minutes = float(getattr(config, "MICRO_STAGNATION_MIN_HOLD_MINUTES", 60))
        flat_band = float(getattr(config, "MICRO_STAGNATION_FLAT_BAND_PCT", 0.10))
        if held_minutes >= min_hold_minutes and abs(pnl_pct) <= flat_band:
            if not self.mark_pending_intraday_exit(symbol_str):
                return None
            reason = (
                f"MICRO_STAGNATION_EXIT {pnl_pct:+.1%} "
                f"(Held={held_minutes:.0f}m, FlatBand=+/-{flat_band:.0%})"
            )
            self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
            return TargetWeight(
                symbol=symbol_str,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
            )

    # Exit 1: Profit target hit (+50%)
    if current_price >= pos.target_price:
        if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
            return None
        reason = f"TARGET_HIT +{pnl_pct:.1%} (Price: ${current_price:.2f})"
        self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
        return TargetWeight(
            symbol=symbol_str,
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
        )

    # ITM_ENGINE anti-roundtrip floor: once meaningful MFE is reached, ratchet stop floor.
    if self._is_itm_momentum_strategy_name(getattr(pos, "entry_strategy", "")):
        peak_price = max(float(getattr(pos, "highest_price", 0.0) or 0.0), float(current_price))
        mfe_gain_pct = (peak_price - entry_price) / entry_price if entry_price > 0 else 0.0
        be_trigger = float(getattr(config, "ITM_PROFIT_LOCK_BREAKEVEN_TRIGGER", 0.20))
        be_floor_pct = float(getattr(config, "ITM_PROFIT_LOCK_BREAKEVEN_FLOOR_PCT", 0.01))
        strong_trigger = float(getattr(config, "ITM_PROFIT_LOCK_STRONG_TRIGGER", 0.35))
        strong_floor_pct = float(getattr(config, "ITM_PROFIT_LOCK_STRONG_FLOOR_PCT", 0.10))
        floor_pct = 0.0
        if mfe_gain_pct >= strong_trigger:
            floor_pct = strong_floor_pct
        elif mfe_gain_pct >= be_trigger:
            floor_pct = be_floor_pct
        if floor_pct > 0:
            floor_price = entry_price * (1.0 + floor_pct)
            if floor_price > pos.stop_price:
                pos.stop_price = floor_price

    # Exit 1.5: Strategy-aware trailing stop for intraday strategies
    if pos.entry_strategy and pos.entry_strategy.upper() != "PROTECTIVE_PUTS":
        if current_price > pos.highest_price:
            pos.highest_price = current_price
        trail_cfg = self._get_trail_config(pos.entry_strategy)
        if trail_cfg is not None:
            trail_trigger, trail_pct = trail_cfg
            gain_pct = (pos.highest_price - entry_price) / entry_price if entry_price > 0 else 0.0
            if gain_pct >= trail_trigger:
                trail_stop = pos.highest_price - ((pos.highest_price - entry_price) * trail_pct)
                if trail_stop > pos.stop_price:
                    pos.stop_price = trail_stop
                if current_price <= pos.stop_price:
                    if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
                        return None
                    reason = (
                        f"TRAIL_STOP {pnl_pct:.1%} (High=${pos.highest_price:.2f}, "
                        f"Trail=${pos.stop_price:.2f})"
                    )
                    self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
                    return TargetWeight(
                        symbol=symbol_str,
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=reason,
                        requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
                    )

    # Exit 2: Stop hit
    if current_price <= pos.stop_price:
        if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
            return None
        reason = f"STOP_HIT {pnl_pct:.1%} (Price: ${current_price:.2f})"
        self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
        return TargetWeight(
            symbol=symbol_str,
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
        )

    # OTM momentum theta guard: exit stale 0-1DTE trades that fail to reach a healthy cushion.
    if (
        is_intraday_pos
        and strategy_name == IntradayStrategy.MICRO_OTM_MOMENTUM.value
        and held_minutes is not None
    ):
        max_hold_minutes = float(getattr(config, "MICRO_OTM_MAX_HOLD_MINUTES", 0))
        profit_exempt = float(getattr(config, "MICRO_OTM_MAX_HOLD_PROFIT_EXEMPT_PCT", 0.35))
        if max_hold_minutes > 0 and held_minutes >= max_hold_minutes and pnl_pct < profit_exempt:
            if not self.mark_pending_intraday_exit(symbol_str):
                return None
            reason = (
                f"MICRO_OTM_MAX_HOLD {pnl_pct:+.1%} "
                f"(Held={held_minutes:.0f}m >= {max_hold_minutes:.0f}m, "
                f"Exempt>={profit_exempt:.0%})"
            )
            self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
            return TargetWeight(
                symbol=symbol_str,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
            )

    # DEBIT_FADE theta guard: mean-reversion trades should resolve quickly.
    if (
        is_intraday_pos
        and strategy_name == IntradayStrategy.MICRO_DEBIT_FADE.value
        and held_minutes is not None
    ):
        max_hold_minutes = float(getattr(config, "MICRO_DEBIT_FADE_MAX_HOLD_MINUTES", 0))
        profit_exempt = float(getattr(config, "MICRO_DEBIT_FADE_MAX_HOLD_PROFIT_EXEMPT_PCT", 0.20))
        if max_hold_minutes > 0 and held_minutes >= max_hold_minutes and pnl_pct < profit_exempt:
            if not self.mark_pending_intraday_exit(symbol_str):
                return None
            reason = (
                f"MICRO_FADE_MAX_HOLD {pnl_pct:+.1%} "
                f"(Held={held_minutes:.0f}m >= {max_hold_minutes:.0f}m, "
                f"Exempt>={profit_exempt:.0%})"
            )
            self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
            return TargetWeight(
                symbol=symbol_str,
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
            )

    # ITM_ENGINE max-hold guard (calendar-based safety cap).
    if self._itm_horizon_engine.enabled() and self._is_itm_momentum_strategy_name(
        getattr(pos, "entry_strategy", "")
    ):
        max_hold_days = self._itm_horizon_engine.get_max_hold_days()
        if max_hold_days > 0:
            try:
                entry_date = datetime.strptime(
                    str(getattr(pos, "entry_time", ""))[:10], "%Y-%m-%d"
                ).date()
                now_date = (
                    self.algorithm.Time.date()
                    if self.algorithm is not None and hasattr(self.algorithm, "Time")
                    else entry_date
                )
                held_days = 0
                cursor = entry_date
                while cursor < now_date:
                    cursor = cursor + timedelta(days=1)
                    if cursor.weekday() < 5:
                        held_days += 1
            except Exception:
                held_days = 0
            if held_days >= max_hold_days:
                if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
                    return None
                reason = f"ITM_ENGINE_MAX_HOLD ({held_days}d >= {max_hold_days}d) P&L={pnl_pct:.1%}"
                self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
                return TargetWeight(
                    symbol=symbol_str,
                    target_weight=0.0,
                    source="OPT",
                    urgency=Urgency.IMMEDIATE,
                    reason=reason,
                    requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
                )

    # V2.3.10: Exit 3 - DTE exit (prevent expiration/exercise)
    # Close single-leg options before expiration to avoid:
    # - OTM expiring worthless (100% loss)
    # - ITM being auto-exercised (creates stock position, margin crisis)
    dte_exit_threshold = int(getattr(config, "OPTIONS_SINGLE_LEG_DTE_EXIT", 4))
    if self._is_itm_momentum_strategy_name(getattr(pos, "entry_strategy", "")):
        if self._itm_horizon_engine.enabled():
            vix_for_itm = None
            try:
                vix_for_itm = float(self._iv_sensor.get_smoothed_vix())
            except Exception:
                vix_for_itm = None
            _, _, _, _, dte_exit_threshold = self._itm_horizon_engine.get_exit_profile(vix_for_itm)
        else:
            dte_exit_threshold = int(getattr(config, "INTRADAY_ITM_DTE_EXIT", dte_exit_threshold))
    elif strategy_name == IntradayStrategy.MICRO_DEBIT_FADE.value:
        dte_exit_threshold = int(getattr(config, "MICRO_DEBIT_FADE_DTE_EXIT", dte_exit_threshold))
    elif strategy_name == IntradayStrategy.MICRO_OTM_MOMENTUM.value:
        dte_exit_threshold = int(getattr(config, "MICRO_OTM_MOMENTUM_DTE_EXIT", dte_exit_threshold))
    elif strategy_name == IntradayStrategy.PROTECTIVE_PUTS.value:
        dte_exit_threshold = int(getattr(config, "PROTECTIVE_PUTS_DTE_EXIT", dte_exit_threshold))

    if current_dte is not None and current_dte <= dte_exit_threshold:
        if is_intraday_pos and not self.mark_pending_intraday_exit(symbol_str):
            return None
        reason = f"DTE_EXIT ({current_dte} DTE <= {dte_exit_threshold}) P&L={pnl_pct:.1%}"
        self.log(f"OPT: EXIT_SIGNAL {symbol} | {reason}", trades_only=True)
        return TargetWeight(
            symbol=symbol_str,
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=reason,
            requested_quantity=max(1, int(getattr(pos, "num_contracts", 1))),
        )

    return None
