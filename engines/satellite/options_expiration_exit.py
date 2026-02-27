"""Expiration force-exit helper extracted from options_engine."""

from __future__ import annotations

import config
from models.enums import IntradayStrategy, Urgency
from models.target_weight import TargetWeight


def check_expiring_options_force_exit_impl(
    self,
    current_date: str,
    current_hour: int,
    current_minute: int,
    current_price: float,
    contract_expiry_date: str,
    position: Optional[OptionsPosition] = None,
) -> Optional[TargetWeight]:
    """
    V2.4.4 P0: EXPIRATION HAMMER V2 - Force close ALL options expiring TODAY.

    CRITICAL SAFETY: ITM options held past 4 PM get auto-exercised by the broker,
    creating massive stock positions that can cause margin crises.

    V2.4.4 Change: Close ALL options on expiration day at 2:00 PM, regardless of
    whether they are ITM/OTM or any other condition. This prevents:
    - Auto-exercise of ITM options creating stock positions
    - OTM options expiring worthless (100% loss)
    - Any exercise-related margin disasters

    Example from V2.4.3 backtest:
    - 3 option exercises created $700K QQQ position on $50K account
    - 2,765 margin call orders, 2,786 invalid orders
    - Kill switch couldn't close options during margin crisis

    Args:
        current_date: Current date as string (YYYY-MM-DD).
        current_hour: Current hour (0-23) Eastern.
        current_minute: Current minute (0-59).
        current_price: Current option price.
        contract_expiry_date: Contract expiry date as string (YYYY-MM-DD).

    Returns:
        TargetWeight for forced exit, or None.
    """
    # Check if option expires TODAY
    if current_date != contract_expiry_date:
        return None

    # Position is expected from caller; keep only swing fallback for legacy paths.
    # Avoid defaulting to the first intraday lane position in multi-position mode.
    position = position or self._position
    if position is None:
        return None

    # MICRO intraday strategies are explicitly managed by intraday force-exit rules.
    # Keep noon expiration hammer active for PROTECTIVE_PUTS and non-MICRO positions.
    strategy_name = self._canonical_engine_strategy_name(getattr(position, "entry_strategy", None))
    if strategy_name in (
        IntradayStrategy.MICRO_DEBIT_FADE.value,
        IntradayStrategy.MICRO_OTM_MOMENTUM.value,
    ):
        symbol_str = self._symbol_str(getattr(position.contract, "symbol", ""))
        if symbol_str:
            last_logged = self._expiring_hammer_skip_log_date.get(symbol_str)
            if last_logged != current_date:
                self._expiring_hammer_skip_log_date[symbol_str] = current_date
                self.log(
                    f"EXPIRATION_HAMMER_V2_SKIP: {symbol_str} | "
                    f"Strategy={strategy_name} managed by intraday force exit",
                    trades_only=True,
                )
        return None

    # Strategy-aware expiration hammer cutoff.
    # MICRO intraday paths are skipped above; PROTECTIVE_PUTS uses a later cutoff.
    if strategy_name == IntradayStrategy.PROTECTIVE_PUTS.value:
        force_close_hour = int(
            getattr(config, "PROTECTIVE_PUTS_EXPIRING_TODAY_FORCE_CLOSE_HOUR", 15)
        )
        force_close_minute = int(
            getattr(config, "PROTECTIVE_PUTS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE", 15)
        )
    else:
        force_close_hour = config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR
        force_close_minute = config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE

    force_exit_time = current_hour > force_close_hour or (
        current_hour == force_close_hour and current_minute >= force_close_minute
    )

    if not force_exit_time:
        return None

    symbol = position.contract.symbol
    entry_price = position.entry_price
    source = (
        "OPT_INTRADAY"
        if self._find_engine_lane_by_symbol(self._symbol_str(symbol)) is not None
        else "OPT_SWING"
    )

    pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

    # V2.4.4: Stronger messaging - this is a mandatory close
    reason = (
        f"EXPIRATION_HAMMER_V2 {pnl_pct:+.1%} | "
        f"MANDATORY CLOSE - Option expires TODAY ({contract_expiry_date}) | "
        f"Price: ${current_price:.2f}"
    )
    self.log(
        f"EXPIRATION_HAMMER_V2: FORCE CLOSE {symbol} | {reason} | "
        f"P0 FIX: Unconditionally closing ALL expiring options at 2:00 PM",
        trades_only=True,
    )

    # Keep intraday software exit lock aligned for expiring-day close signals.
    if self._find_engine_lane_by_symbol(self._symbol_str(symbol)) is not None:
        if not self.mark_pending_engine_exit(self._symbol_str(symbol)):
            return None

    return TargetWeight(
        symbol=self._symbol_str(symbol),
        target_weight=0.0,
        source=source,
        urgency=Urgency.IMMEDIATE,
        reason=reason,
        requested_quantity=max(1, int(getattr(position, "num_contracts", 1))),
    )
