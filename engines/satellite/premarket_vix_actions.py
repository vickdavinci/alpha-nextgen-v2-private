from typing import Any

from AlgorithmImports import OptionRight, SecurityType

import config
from engines.satellite.options_engine import SpreadStrategy
from models.enums import OptionDirection, Urgency
from models.target_weight import TargetWeight


def apply_premarket_vix_actions(algo: Any) -> None:
    """Apply pre-market de-risk actions based on ladder level."""
    if not getattr(config, "PREMARKET_VIX_LADDER_ENABLED", True):
        return

    # Always flush stale intraday option carry first.
    if (
        getattr(config, "PREMARKET_FORCE_CLOSE_INTRADAY_STALE", True)
        and algo.options_engine.has_intraday_position()
    ):
        for intraday_pos in algo.options_engine.get_intraday_positions():
            if intraday_pos is None or intraday_pos.contract is None:
                continue
            intraday_symbol = algo._normalize_symbol_str(intraday_pos.contract.symbol)
            if algo._should_hold_intraday_symbol_overnight(intraday_symbol):
                algo.Log(
                    f"PREMARKET_LADDER: HOLD_SKIP intraday carry | {intraday_pos.contract.symbol}"
                )
                continue
            algo.Log(
                f"PREMARKET_LADDER: Closing stale intraday carry | {intraday_pos.contract.symbol}"
            )
            stale_symbol = intraday_symbol
            stale_qty = abs(algo._get_option_holding_quantity(stale_symbol))
            if stale_qty <= 0:
                stale_qty = max(1, int(getattr(intraday_pos, "num_contracts", 1)))
            algo.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=stale_symbol,
                    target_weight=0.0,
                    source="OPT_INTRADAY",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"PREMARKET_STALE_INTRADAY_CLOSE | {algo._premarket_vix_ladder_reason}",
                    requested_quantity=stale_qty,
                )
            )

    if algo._premarket_vix_ladder_level >= 3 and getattr(
        config, "PREMARKET_VIX_L3_CLOSE_ALL_OPTIONS", True
    ):
        queued = 0
        spreads = algo.options_engine.get_spread_positions()
        for spread in spreads:
            algo.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=algo._normalize_symbol_str(spread.long_leg.symbol),
                    target_weight=0.0,
                    source="OPT",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"PREMARKET_VIX_L3_FLATTEN | {algo._premarket_vix_ladder_reason}",
                    requested_quantity=spread.num_spreads,
                    metadata={
                        "spread_close_short": True,
                        "spread_short_leg_symbol": algo._normalize_symbol_str(
                            spread.short_leg.symbol
                        ),
                        "spread_short_leg_quantity": spread.num_spreads,
                        "spread_key": algo._build_spread_runtime_key(spread),
                        "exit_type": "PREMARKET_VIX_L3",
                        "spread_exit_code": "PREMARKET_VIX_L3",
                        "spread_exit_reason": "PREMARKET_VIX_L3_FLATTEN",
                        "spread_exit_emergency": True,
                    },
                )
            )
            queued += 1

        intraday_positions = algo.options_engine.get_intraday_positions()
        for intraday_pos in intraday_positions:
            if intraday_pos is None or intraday_pos.contract is None:
                continue
            l3_symbol = algo._normalize_symbol_str(intraday_pos.contract.symbol)
            l3_qty = abs(algo._get_option_holding_quantity(l3_symbol))
            if l3_qty <= 0:
                l3_qty = max(1, int(getattr(intraday_pos, "num_contracts", 1)))
            algo.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=l3_symbol,
                    target_weight=0.0,
                    source="OPT_INTRADAY",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"PREMARKET_VIX_L3_FLATTEN | {algo._premarket_vix_ladder_reason}",
                    requested_quantity=l3_qty,
                )
            )
            queued += 1

        tracked_symbols = set()
        for spread in spreads:
            tracked_symbols.add(spread.long_leg.symbol)
            tracked_symbols.add(spread.short_leg.symbol)
        for intraday_pos in intraday_positions:
            if intraday_pos is not None and intraday_pos.contract is not None:
                tracked_symbols.add(intraday_pos.contract.symbol)

        for kvp in algo.Portfolio:
            holding = kvp.Value
            if not holding.Invested or holding.Symbol.SecurityType != SecurityType.Option:
                continue
            if holding.Symbol in tracked_symbols:
                continue
            orphan_symbol = algo._normalize_symbol_str(holding.Symbol)
            algo.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=orphan_symbol,
                    target_weight=0.0,
                    source="OPT",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"PREMARKET_VIX_L3_ORPHAN_CLOSE | {algo._premarket_vix_ladder_reason}",
                    requested_quantity=abs(int(holding.Quantity)),
                )
            )
            queued += 1

        if queued > 0:
            algo.Log(
                f"PREMARKET_LADDER: L3 queued option flatten exits | Queued={queued} | "
                f"{algo._premarket_vix_ladder_reason}"
            )
        return

    if algo._premarket_vix_ladder_level >= 2 and getattr(
        config, "PREMARKET_VIX_L2_CLOSE_BULLISH_OPTIONS", True
    ):
        spreads = algo.options_engine.get_spread_positions()
        bullish_spreads = {
            "BULL_CALL",
            "BULL_CALL_DEBIT",
            "BULL_PUT_CREDIT",
            SpreadStrategy.BULL_CALL_DEBIT.value,
            SpreadStrategy.BULL_PUT_CREDIT.value,
        }
        for spread in spreads:
            if spread.spread_type not in bullish_spreads:
                continue
            algo.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=algo._normalize_symbol_str(spread.long_leg.symbol),
                    target_weight=0.0,
                    source="OPT",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"PREMARKET_VIX_L2_BULLISH_SPREAD | {algo._premarket_vix_ladder_reason}",
                    requested_quantity=spread.num_spreads,
                    metadata={
                        "spread_close_short": True,
                        "spread_short_leg_symbol": algo._normalize_symbol_str(
                            spread.short_leg.symbol
                        ),
                        "spread_short_leg_quantity": spread.num_spreads,
                        "spread_key": algo._build_spread_runtime_key(spread),
                        "exit_type": "PREMARKET_VIX_L2",
                        "spread_exit_code": "PREMARKET_VIX_L2",
                        "spread_exit_reason": "PREMARKET_VIX_L2_BULLISH_SPREAD",
                    },
                )
            )

        symbols_to_close = []
        for intraday_pos in algo.options_engine.get_intraday_positions():
            if intraday_pos is None or intraday_pos.contract is None:
                continue
            if intraday_pos.contract.direction == OptionDirection.CALL:
                symbols_to_close.append(intraday_pos.contract.symbol)

        for kvp in algo.Portfolio:
            holding = kvp.Value
            if not holding.Invested or holding.Symbol.SecurityType != SecurityType.Option:
                continue
            if holding.Symbol.ID.OptionRight == OptionRight.Call:
                symbols_to_close.append(holding.Symbol)

        if symbols_to_close:
            unique_symbols = list(dict.fromkeys(symbols_to_close))
            queued = 0
            for symbol in unique_symbols:
                close_symbol = algo._normalize_symbol_str(symbol)
                close_qty = abs(algo._get_option_holding_quantity(close_symbol))
                if close_qty <= 0:
                    continue
                algo.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=close_symbol,
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=(
                            "PREMARKET_VIX_L2_CALL_DELEVER | "
                            f"{algo._premarket_vix_ladder_reason}"
                        ),
                        requested_quantity=close_qty,
                    )
                )
                queued += 1
            if queued > 0:
                algo.Log(
                    f"PREMARKET_LADDER: L2 de-risked bullish options | Queued={queued} | "
                    f"{algo._premarket_vix_ladder_reason}"
                )
