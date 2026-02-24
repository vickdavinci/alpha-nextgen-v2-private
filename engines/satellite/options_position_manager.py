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
        pending_payload = self._get_pending_intraday_entry_payload(symbol=symbol_norm)

    # Use pending values from check_entry_signal
    if contract is None:
        contract = (
            pending_payload.get("contract")
            if pending_payload is not None
            else self._pending_contract
        )

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
        inferred = self._infer_intraday_strategy_from_order_tag(order_tag)
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

    is_intraday_fill = force_intraday or pending_payload is not None or self._pending_intraday_entry
    if is_intraday_fill:
        target_pct, strategy_floor = self._get_intraday_exit_profile(entry_strategy)
        current_dte = int(getattr(contract, "days_to_expiry", 0))
        target_pct = self._apply_intraday_target_overrides(
            entry_strategy=entry_strategy,
            target_pct=float(target_pct),
            current_dte=current_dte,
        )
        stop_pct = self._apply_intraday_stop_overrides(
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
        lane = self._intraday_engine_lane_from_strategy(entry_strategy)
        self._set_intraday_lane_position(lane, position)
        if symbol_norm:
            self._pop_pending_intraday_entry_payload(symbol=symbol_norm, lane=lane)
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
            f"force-close at {self._get_intraday_force_exit_hhmm()[0]:02d}:{self._get_intraday_force_exit_hhmm()[1]:02d})",
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
