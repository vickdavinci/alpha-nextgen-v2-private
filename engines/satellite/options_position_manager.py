"""Options position lifecycle helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import config
from engines.satellite.options_primitives import (
    OptionContract,
    OptionsPosition,
    SpreadPosition,
    SpreadStrategy,
)
from models.enums import IntradayStrategy, OptionDirection, OptionsMode


def register_entry_impl(
    self,
    fill_price: float,
    entry_time: str,
    current_date: str,
    contract: Optional[OptionContract] = None,
    force_intraday: bool = False,
    symbol: Optional[str] = None,
    order_tag: Optional[str] = None,
) -> Optional[OptionsPosition]:
    """
    Register a new options position after fill.
    """
    pending_payload = None
    symbol_norm = self._symbol_str(symbol) if symbol else ""
    if symbol_norm:
        pending_payload = self._get_pending_engine_entry_payload(symbol=symbol_norm)

    # Use pending values from check_entry_signal
    if contract is None:
        contract = (
            pending_payload.get("contract")
            if pending_payload is not None
            else self._pending_contract
        )

    # V12.18 FIX: Validate fallback contract matches fill symbol.
    # When concurrent fills (e.g. MICRO + ITM on same bar) race through pending-entry
    # maintenance, one lane's pending payload can be cleared before register_entry
    # runs.  The fallback then picks up self._pending_contract from a DIFFERENT lane,
    # contaminating the position with the wrong contract/strategy/stops.
    if contract is not None and pending_payload is None and symbol_norm:
        fallback_sym = self._symbol_key(getattr(contract, "symbol", None))
        fill_sym = self._symbol_key(symbol_norm)
        if fallback_sym and fill_sym and fallback_sym != fill_sym:
            self.log(
                f"OPT: register_entry CROSS_LANE_GUARD | "
                f"Fill={fill_sym} != PendingContract={fallback_sym} | "
                f"Rejecting stale global pending to prevent phantom exit",
                trades_only=True,
            )
            contract = None

    # Guard: If no pending contract exists, we can't register entry
    # This can happen if fill occurs for an order placed outside our signal flow
    if contract is None:
        self.log("OPT: register_entry called but no pending contract - skipping")
        return None

    # Use pending values if set, otherwise defaults
    # Note: getattr defaults don't work when attr exists but is None
    entry_score = (
        pending_payload.get("entry_score")
        if pending_payload is not None and pending_payload.get("entry_score") is not None
        else self._pending_entry_score
        if self._pending_entry_score is not None
        else 3.0
    )
    num_contracts = (
        pending_payload.get("num_contracts")
        if pending_payload is not None and pending_payload.get("num_contracts") is not None
        else self._pending_num_contracts
        if self._pending_num_contracts is not None
        else 1
    )
    stop_pct = (
        pending_payload.get("stop_pct")
        if pending_payload is not None and pending_payload.get("stop_pct") is not None
        else self._pending_stop_pct
        if self._pending_stop_pct is not None
        else 0.20
    )

    recovered_strategy = None
    if force_intraday and pending_payload is None:
        inferred = self._infer_engine_strategy_from_order_tag(order_tag)
        if inferred and inferred != IntradayStrategy.NO_TRADE.value:
            recovered_strategy = inferred

    entry_strategy = recovered_strategy or (
        pending_payload.get("entry_strategy")
        if pending_payload is not None and pending_payload.get("entry_strategy")
        else self._pending_entry_strategy or "SWING_SINGLE"
    )
    if recovered_strategy is not None:
        self.log(
            f"INTRADAY_RECOVERY_STRATEGY: Symbol={self._symbol_str(contract.symbol)} | "
            f"Strategy={recovered_strategy} | Tag={str(order_tag or 'NO_TAG')[:120]}",
            trades_only=True,
        )

    # Recalculate stop and target based on actual fill price
    stop_price = fill_price * (1 - stop_pct)

    symbol_pending_intraday = pending_payload is not None
    # Lane-aware classification: a fill should only be treated as intraday when
    # this symbol is pending intraday (or explicit recovery is requested).
    # Avoids misclassifying unrelated fills when another lane has a pending entry.
    is_intraday_fill = (
        force_intraday
        or symbol_pending_intraday
        or (not symbol_norm and self._pending_intraday_entry)
    )
    if is_intraday_fill:
        contract_right = str(getattr(contract, "right", "") or "").upper()
        direction_hint = (
            "CALL" if contract_right == "CALL" else "PUT" if contract_right == "PUT" else None
        )
        target_pct, strategy_floor = self._get_engine_exit_profile(
            entry_strategy,
            direction=direction_hint,
        )
        current_dte = int(getattr(contract, "days_to_expiry", 0))
        target_pct = self._apply_engine_target_overrides(
            entry_strategy=entry_strategy,
            target_pct=float(target_pct),
            current_dte=current_dte,
        )
        stop_pct = self._apply_engine_stop_overrides(
            entry_strategy=entry_strategy,
            stop_pct=float(stop_pct),
            current_dte=current_dte,
        )
        target_price = fill_price * (1 + target_pct)
        stop_price = fill_price * (1 - stop_pct)
        if strategy_floor is not None and strategy_floor > 0:
            # V9.2 FIX: Use max(ATR stop, strategy floor) to preserve ATR adaptive
            # intelligence. The ATR stop accounts for market volatility (wider in
            # high-VIX), while the strategy floor prevents too-tight stops in calm
            # markets. Using max() means: in calm markets the floor protects, in
            # volatile markets the ATR stop widens appropriately.
            atr_stop_pct = stop_pct  # ATR-calculated value from signal time
            stop_pct = max(stop_pct, float(strategy_floor))
            stop_price = fill_price * (1 - stop_pct)
            if stop_pct > atr_stop_pct:
                self.log(
                    f"STOP_OVERRIDE: {entry_strategy} floor {strategy_floor:.0%} > "
                    f"ATR {atr_stop_pct:.0%} -> using floor"
                )
            else:
                self.log(
                    f"STOP_OVERRIDE: ATR {atr_stop_pct:.0%} >= "
                    f"{entry_strategy} floor {strategy_floor:.0%} -> keeping ATR"
                )
    else:
        target_price = fill_price * (1 + config.OPTIONS_PROFIT_TARGET_PCT)

    position = OptionsPosition(
        contract=contract,
        entry_price=fill_price,
        entry_time=entry_time,
        entry_score=entry_score,
        num_contracts=num_contracts,
        stop_price=stop_price,
        target_price=target_price,
        stop_pct=stop_pct,
        entry_strategy=entry_strategy,
        highest_price=fill_price,
    )

    # V2.3.2 FIX #4: Track position in correct variable based on mode
    if is_intraday_fill:
        lane = self._engine_lane_from_strategy(entry_strategy)
        self._set_engine_lane_position(lane, position)
        if symbol_norm:
            self._pop_pending_engine_entry_payload(symbol=symbol_norm, lane=lane)
        self._pending_intraday_entry = bool(self._pending_intraday_entries)
        self._pending_intraday_entry_since = (
            None if not self._pending_intraday_entries else self._pending_intraday_entry_since
        )
        self._pending_intraday_entry_engine = (
            None if not self._pending_intraday_entries else self._pending_intraday_entry_engine
        )
        # Count intraday trades only after a confirmed fill registration.
        intraday_dir = (
            OptionDirection.CALL
            if str(getattr(contract, "right", "")).upper() == "CALL"
            else OptionDirection.PUT
            if str(getattr(contract, "right", "")).upper() == "PUT"
            else None
        )
        self._increment_trade_counter(
            OptionsMode.INTRADAY, direction=intraday_dir, strategy=entry_strategy
        )
        if force_intraday and not self._pending_intraday_entry:
            self.log(
                f"OPT: INTRADAY_TAG_RECOVERY | Symbol={contract.symbol} | "
                f"Strategy={entry_strategy}",
                trades_only=True,
            )
        self.log(
            f"OPT: INTRADAY position registered (trade #{self._intraday_trades_today}, "
            f"force-close at {self._get_engine_force_exit_hhmm()[0]:02d}:{self._get_engine_force_exit_hhmm()[1]:02d})",
            trades_only=True,
        )
    else:
        self._position = position
        # V2.9: Increment swing counter (Bug #4 fix)
        # Intraday already counted on signal generation to prevent race condition
        self._increment_trade_counter(OptionsMode.SWING)

    # Update last trade date for backward compatibility
    self._last_trade_date = current_date

    self.log(
        f"OPT: POSITION_REGISTERED {contract.symbol} | "
        f"Entry=${fill_price:.2f} | "
        f"Target=${target_price:.2f} | "
        f"Stop=${stop_price:.2f} (-{stop_pct:.0%}) | "
        f"Strategy={entry_strategy} | Contracts={num_contracts} | "
        f"Score={entry_score:.2f}"
    )

    # Clear pending state
    self._pending_contract = None
    self._pending_entry_score = None
    self._pending_num_contracts = None
    self._pending_stop_pct = None
    self._pending_stop_price = None
    self._pending_target_price = None
    self._pending_entry_strategy = None

    return position


def register_spread_entry_impl(
    self,
    long_leg_fill_price: float,
    short_leg_fill_price: float,
    entry_time: str,
    current_date: str,
    regime_score: float,
) -> Optional[SpreadPosition]:
    """
    Register a new spread position after both legs fill.
    """
    if self._pending_spread_long_leg is None or self._pending_spread_short_leg is None:
        self.log("SPREAD: register_spread_entry called but no pending spread - skipping")
        return None

    # Calculate actual net debit from fills
    net_debit = long_leg_fill_price - short_leg_fill_price
    width = self._pending_spread_width or abs(
        self._pending_spread_short_leg.strike - self._pending_spread_long_leg.strike
    )
    # Debit spread: max profit = width - debit paid
    # Credit spread (stored as negative net_debit): max profit = credit received
    max_profit = width - net_debit if net_debit > 0 else abs(net_debit)

    num_spreads = self._pending_num_contracts or 1
    entry_score = self._pending_entry_score or 3.0

    spread = SpreadPosition(
        long_leg=self._pending_spread_long_leg,
        short_leg=self._pending_spread_short_leg,
        spread_type=self._pending_spread_type or "UNKNOWN",
        net_debit=net_debit,
        max_profit=max_profit,
        width=width,
        entry_time=entry_time,
        entry_score=entry_score,
        num_spreads=num_spreads,
        regime_at_entry=regime_score,
        entry_vix=self._pending_spread_entry_vix,
    )

    self._spread_neutrality_warn_by_key.pop(self._build_spread_key(spread), None)
    self._spread_positions.append(spread)
    self._spread_position = self._spread_positions[0] if self._spread_positions else None
    spread_dir = (
        OptionDirection.CALL
        if self._spread_direction_label(spread.spread_type) == "BULLISH"
        else OptionDirection.PUT
        if self._spread_direction_label(spread.spread_type) == "BEARISH"
        else None
    )
    signature = (
        self._build_vass_signature(
            spread_type=spread.spread_type,
            direction=spread_dir,
            long_leg_contract=spread.long_leg,
        )
        if spread.long_leg is not None
        else ""
    )
    try:
        entry_dt = datetime.strptime(entry_time[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        entry_dt = self.algorithm.Time if self.algorithm is not None else None
    self._record_vass_signature_entry(signature, entry_dt)
    self._record_vass_direction_day_entry(spread_dir, entry_dt)

    # V2.9: Update trade counter (Bug #4 fix) - Spreads are always swing mode
    self._increment_trade_counter(OptionsMode.SWING)

    spread_type_upper = str(spread.spread_type or "").upper()
    if spread_type_upper in {
        "BULL_PUT_CREDIT",
        "BEAR_CALL_CREDIT",
        SpreadStrategy.BULL_PUT_CREDIT.value,
        SpreadStrategy.BEAR_CALL_CREDIT.value,
    }:
        # Credit spread telemetry: target is close-cost threshold to realize configured profit.
        credit_target_pct = float(getattr(config, "CREDIT_SPREAD_PROFIT_TARGET", 0.50))
        target_close_value = abs(net_debit) - (max_profit * credit_target_pct)
        target_telemetry = f"TargetClose<=${target_close_value:.2f} ({credit_target_pct:.0%})"
    else:
        # Debit spread telemetry: mirror configured/adaptive target math from exit logic.
        base_profit_pct = float(getattr(config, "SPREAD_PROFIT_TARGET_PCT", 0.50))
        profit_multipliers = getattr(
            config, "SPREAD_PROFIT_REGIME_MULTIPLIERS", {75: 1.0, 50: 1.0, 40: 1.0, 0: 1.0}
        )
        profit_multiplier = 1.0
        for threshold in sorted(profit_multipliers.keys(), reverse=True):
            if regime_score >= threshold:
                profit_multiplier = profit_multipliers[threshold]
                break
        adaptive_profit_pct = base_profit_pct * profit_multiplier
        commission_cost = num_spreads * config.SPREAD_COMMISSION_PER_CONTRACT
        commission_per_share = commission_cost / (num_spreads * 100) if num_spreads > 0 else 0
        target_spread_value = net_debit + (max_profit * adaptive_profit_pct) + commission_per_share
        target_telemetry = f"Target=${target_spread_value:.2f} ({adaptive_profit_pct:.0%}, Comm ${commission_cost:.2f})"

    self.log(
        f"SPREAD: POSITION_REGISTERED | {spread.spread_type} | "
        f"Long={spread.long_leg.strike} @ ${long_leg_fill_price:.2f} | "
        f"Short={spread.short_leg.strike} @ ${short_leg_fill_price:.2f} | "
        f"Net Debit=${net_debit:.2f} | Max Profit=${max_profit:.2f} | "
        f"x{num_spreads} | {target_telemetry}",
        trades_only=True,
    )

    # Clear pending state
    self._pending_spread_long_leg = None
    self._pending_spread_short_leg = None
    self._pending_spread_type = None
    self._pending_net_debit = None
    self._pending_max_profit = None
    self._pending_spread_width = None
    self._pending_spread_entry_vix = None
    self._pending_spread_entry_since = None
    self._pending_num_contracts = None
    self._pending_entry_score = None
    self._rejection_margin_cap = None  # V2.21: Clear on successful fill

    return spread


def remove_position_impl(self, symbol: Optional[str] = None) -> Optional[OptionsPosition]:
    """
    Remove the current swing single-leg position after exit.
    """
    if self._position is None:
        return None

    if symbol:
        try:
            expected = self._symbol_str(self._position.contract.symbol)
            actual = self._symbol_str(symbol)
            if expected != actual:
                return None
        except Exception:
            return None

    position = self._position
    self._position = None
    self.log(f"OPT: POSITION_REMOVED {position.contract.symbol}", trades_only=True)
    return position


def remove_engine_position_impl(
    self, symbol: Optional[str] = None, engine: Optional[str] = None
) -> Optional[OptionsPosition]:
    """
    Remove the current intraday position after exit.
    """
    lane = None
    if symbol:
        lane = self._find_engine_lane_by_symbol(symbol)
        if lane is None:
            return None
    elif engine is not None:
        lane = str(engine).upper()
    else:
        lane = self.get_engine_position_lane()

    if not lane:
        return None
    lane_key = str(lane).upper()
    lane_positions = self._intraday_positions.get(lane_key) or []
    if not lane_positions:
        return None

    position = None
    if symbol:
        symbol_norm = self._symbol_str(symbol)
        for idx, pos in enumerate(list(lane_positions)):
            if (
                pos is not None
                and pos.contract is not None
                and self._symbol_str(pos.contract.symbol) == symbol_norm
            ):
                position = pos
                del lane_positions[idx]
                break
        if position is None:
            return None
    else:
        position = lane_positions.pop(0)

    self._intraday_positions[lane_key] = lane_positions
    self._refresh_legacy_engine_mirrors()
    try:
        removed_symbol_key = self._symbol_str(position.contract.symbol)
    except Exception:
        removed_symbol_key = None
    self._pending_intraday_exit_lanes.discard(lane_key)
    if removed_symbol_key:
        self._pending_intraday_exit_symbols.discard(removed_symbol_key)
    self._sync_pending_engine_exit_flags()
    try:
        strategy = str(getattr(position, "entry_strategy", "") or "UNKNOWN")
    except Exception:
        strategy = "UNKNOWN"
    self._last_intraday_close_strategy = strategy
    self._last_intraday_close_time = self.algorithm.Time if self.algorithm is not None else None
    self.log(
        f"OPT: INTRADAY_POSITION_REMOVED {position.contract.symbol} | " f"Strategy={strategy}",
        trades_only=True,
    )
    return position


def remove_spread_position_impl(self, symbol: Optional[str] = None) -> Optional[SpreadPosition]:
    """
    Remove the current spread position after exit and record post-trade cooldown state.
    """
    spreads = self.get_spread_positions()
    if spreads:
        spread = None
        if symbol:
            sym = str(symbol)
            for s in spreads:
                if str(s.long_leg.symbol) == sym or str(s.short_leg.symbol) == sym:
                    spread = s
                    break
        if spread is None:
            if symbol:
                self.log(
                    f"SPREAD: WARN remove no match for {symbol}, "
                    f"skip removal across {len(spreads)} active spreads",
                    trades_only=True,
                )
                return None
            if len(spreads) == 1:
                spread = spreads[0]
            else:
                self.log(
                    "SPREAD: WARN remove requested without symbol while multiple spreads active | "
                    f"Count={len(spreads)} | skip removal",
                    trades_only=True,
                )
                return None

        if self._spread_positions:
            self._spread_positions = [s for s in self._spread_positions if s is not spread]
        elif self._spread_position is spread:
            self._spread_position = None
        spread_key = self._build_spread_key(spread)
        self._spread_neutrality_warn_by_key.pop(spread_key, None)
        self._spread_exit_signal_cooldown.pop(spread_key, None)  # V9.4 P0: Clear cooldown
        self._spread_hold_guard_logged.discard(spread_key)

        self._spread_position = self._spread_positions[0] if self._spread_positions else None

        # V6.5 FIX: Reset gamma pin flag when position is closed
        if not self._spread_positions:
            self._gamma_pin_exit_triggered = False

        # V2.6 Bug #16: Record exit time for margin cooldown
        # After closing a spread, broker takes T+1 to settle margin
        # Use algorithm.Time for QC compliance (not system time)
        if self.algorithm is not None and hasattr(self.algorithm, "Time"):
            self._last_spread_exit_time = self.algorithm.Time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            # Fallback for testing without algorithm context
            self._last_spread_exit_time = "1970-01-01 00:00:00"

        self.log(
            f"SPREAD: POSITION_REMOVED | {spread.spread_type} | "
            f"Long={spread.long_leg.symbol} Short={spread.short_leg.symbol} | "
            f"Cooldown until {config.OPTIONS_POST_TRADE_COOLDOWN_MINUTES}min after exit",
            trades_only=True,
        )
        return spread
    return None


def clear_all_positions_impl(self) -> None:
    """
    Clear ALL position and pending-tracker state.
    """
    cleared = []

    if self._position is not None:
        self._position = None
        cleared.append("single-leg")

    if self.has_spread_position():
        self._spread_positions = []
        self._spread_position = None
        self._spread_neutrality_warn_by_key = {}
        self._spread_hold_guard_logged.clear()
        cleared.append("spread")

    if self._intraday_position is not None or any(
        len(v or []) > 0 for v in self._intraday_positions.values()
    ):
        self._intraday_position = None
        self._intraday_position_engine = None
        self._intraday_positions = {"MICRO": [], "ITM": []}
        cleared.append("intraday")

    # V2.16-BT: Also clear swing position (V2.1.1 dual-mode)
    if self._swing_position is not None:
        self._swing_position = None
        cleared.append("swing")

    # Clear ALL pending state - every _pending_* and _entry_* field from __init__
    # V2.30: Complete list (V2.29 missed _pending_stop_price, _pending_target_price,
    # _pending_intraday_exit). Stale pending state = zombie bugs.
    self._pending_contract = None
    self._pending_intraday_entry = False
    self._pending_intraday_entry_since = None
    self._pending_intraday_entry_engine = None
    self._pending_intraday_entries = {}
    self._pending_intraday_exit = False
    self._pending_intraday_exit_engine = None
    self._pending_intraday_exit_lanes = set()
    self._pending_intraday_exit_symbols = set()
    self._pending_spread_long_leg = None
    self._pending_spread_short_leg = None
    self._pending_spread_width = None
    self._pending_spread_entry_vix = None
    self._pending_spread_entry_since = None
    self._pending_spread_type = None
    self._pending_net_debit = None
    self._pending_max_profit = None
    self._pending_num_contracts = None
    self._pending_entry_score = None
    self._pending_stop_pct = None
    self._pending_stop_price = None
    self._pending_target_price = None
    self._pending_entry_strategy = None
    self._entry_attempted_today = False
    self._intraday_force_exit_hold_skip_log_date = {}
    self._last_intraday_close_time = None
    self._last_intraday_close_strategy = None

    if cleared:
        self.log(
            f"OPT: CLEAR_ALL_POSITIONS (kill switch) | Cleared: {', '.join(cleared)}",
            trades_only=True,
        )


def record_engine_result_impl(
    self,
    symbol: str,
    is_win: bool,
    current_time: Optional[str] = None,
    strategy: Optional[str] = None,
) -> None:
    """Track MICRO directional loss streaks/cooldowns (ITM is sovereign)."""
    try:
        strategy_name = self._canonical_engine_strategy_name(strategy)
        if strategy_name not in (
            IntradayStrategy.MICRO_DEBIT_FADE.value,
            IntradayStrategy.MICRO_OTM_MOMENTUM.value,
            IntradayStrategy.PROTECTIVE_PUTS.value,
        ):
            return

        symbol_text = str(symbol)
        import re

        is_call = re.search(r"\d{6}C\d{8}", symbol_text) is not None
        is_put = re.search(r"\d{6}P\d{8}", symbol_text) is not None

        if current_time:
            try:
                trade_date = datetime.strptime(current_time[:10], "%Y-%m-%d").date()
            except Exception:
                trade_date = None
        else:
            trade_date = None

        # expose directional cooldown info to MicroEntryEngine via state payload
        try:
            state = getattr(self._micro_regime_engine, "_state", None)
            if state is not None:
                state.put_cooldown_until_date = self._put_cooldown_until_date
                state.put_consecutive_losses = self._put_consecutive_losses
        except Exception:
            pass

        if is_call:
            if is_win:
                self._call_consecutive_losses = 0
            else:
                self._call_consecutive_losses += 1
                if getattr(config, "CALL_GATE_CONSECUTIVE_LOSS_ENABLED", True):
                    threshold = int(getattr(config, "CALL_GATE_CONSECUTIVE_LOSSES", 3))
                    if self._call_consecutive_losses >= threshold:
                        vix_for_cooldown = None
                        try:
                            state = getattr(self._micro_regime_engine, "_state", None)
                            if state is not None:
                                vix_for_cooldown = float(getattr(state, "vix_current", 0.0) or 0.0)
                        except Exception:
                            vix_for_cooldown = None

                        if vix_for_cooldown is None or vix_for_cooldown <= 0:
                            cooldown_days = int(getattr(config, "CALL_GATE_LOSS_COOLDOWN_DAYS", 1))
                        else:
                            low_vix_max = float(getattr(config, "VIX_LEVEL_LOW_MAX", 18.0))
                            med_vix_max = float(getattr(config, "VIX_LEVEL_MEDIUM_MAX", 25.0))
                            if vix_for_cooldown < low_vix_max:
                                cooldown_days = int(
                                    getattr(
                                        config,
                                        "CALL_GATE_LOSS_COOLDOWN_DAYS_LOW_VIX",
                                        getattr(config, "CALL_GATE_LOSS_COOLDOWN_DAYS", 1),
                                    )
                                )
                            elif vix_for_cooldown < med_vix_max:
                                cooldown_days = int(
                                    getattr(
                                        config,
                                        "CALL_GATE_LOSS_COOLDOWN_DAYS_MED_VIX",
                                        getattr(config, "CALL_GATE_LOSS_COOLDOWN_DAYS", 1),
                                    )
                                )
                            else:
                                cooldown_days = int(
                                    getattr(
                                        config,
                                        "CALL_GATE_LOSS_COOLDOWN_DAYS_HIGH_VIX",
                                        getattr(config, "CALL_GATE_LOSS_COOLDOWN_DAYS", 1),
                                    )
                                )

                        if trade_date is not None:
                            self._call_cooldown_until_date = self._add_trading_days_to_date(
                                trade_date, cooldown_days
                            )
                            self.log(
                                f"INTRADAY: CALL cooldown armed | LossStreak={self._call_consecutive_losses} | "
                                f"Until={self._call_cooldown_until_date.isoformat()}",
                                trades_only=True,
                            )

        if is_put:
            if is_win:
                self._put_consecutive_losses = 0
            else:
                self._put_consecutive_losses += 1
                if getattr(config, "PUT_GATE_CONSECUTIVE_LOSS_ENABLED", True):
                    threshold = int(getattr(config, "PUT_GATE_CONSECUTIVE_LOSSES", 3))
                    if self._put_consecutive_losses >= threshold and trade_date is not None:
                        vix_for_cooldown = None
                        try:
                            state = getattr(self._micro_regime_engine, "_state", None)
                            if state is not None:
                                vix_for_cooldown = float(getattr(state, "vix_current", 0.0) or 0.0)
                        except Exception:
                            vix_for_cooldown = None

                        if vix_for_cooldown is None or vix_for_cooldown <= 0:
                            cooldown_days = int(getattr(config, "PUT_GATE_LOSS_COOLDOWN_DAYS", 1))
                        else:
                            low_vix_max = float(getattr(config, "VIX_LEVEL_LOW_MAX", 18.0))
                            med_vix_max = float(getattr(config, "VIX_LEVEL_MEDIUM_MAX", 25.0))
                            if vix_for_cooldown < low_vix_max:
                                cooldown_days = int(
                                    getattr(
                                        config,
                                        "PUT_GATE_LOSS_COOLDOWN_DAYS_LOW_VIX",
                                        getattr(config, "PUT_GATE_LOSS_COOLDOWN_DAYS", 1),
                                    )
                                )
                            elif vix_for_cooldown < med_vix_max:
                                cooldown_days = int(
                                    getattr(
                                        config,
                                        "PUT_GATE_LOSS_COOLDOWN_DAYS_MED_VIX",
                                        getattr(config, "PUT_GATE_LOSS_COOLDOWN_DAYS", 1),
                                    )
                                )
                            else:
                                cooldown_days = int(
                                    getattr(
                                        config,
                                        "PUT_GATE_LOSS_COOLDOWN_DAYS_HIGH_VIX",
                                        getattr(config, "PUT_GATE_LOSS_COOLDOWN_DAYS", 1),
                                    )
                                )

                        self._put_cooldown_until_date = self._add_trading_days_to_date(
                            trade_date, cooldown_days
                        )
                        self.log(
                            f"INTRADAY: PUT cooldown armed | LossStreak={self._put_consecutive_losses} | "
                            f"Until={self._put_cooldown_until_date.isoformat()}",
                            trades_only=True,
                        )

        try:
            state = getattr(self._micro_regime_engine, "_state", None)
            if state is not None:
                state.put_cooldown_until_date = self._put_cooldown_until_date
                state.put_consecutive_losses = self._put_consecutive_losses
        except Exception:
            pass

    except Exception as e:
        self.log(f"INTRADAY: Failed to record directional result streak: {e}", trades_only=True)

    try:
        self._itm_horizon_engine.on_trade_closed(
            symbol=symbol,
            is_win=is_win,
            current_time=current_time,
            strategy=strategy,
            algorithm=self.algorithm,
        )
    except Exception as e:
        self.log(f"ITM_ENGINE: result tracking failed: {e}", trades_only=True)
