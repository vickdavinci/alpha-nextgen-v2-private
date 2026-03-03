"""Shared options engine trade-signal resolver logic."""

from __future__ import annotations

from typing import Optional, Tuple

import config


def resolve_trade_signal_impl(
    self,
    engine: str,
    engine_direction: Optional[str],
    engine_conviction: bool,
    macro_direction: str,
    conviction_strength: Optional[float] = None,
    engine_regime: Optional[str] = None,
    engine_recommended_direction: Optional[str] = None,
    overlay_state: Optional[str] = None,
    allow_macro_veto: bool = True,
) -> Tuple[bool, Optional[str], str]:
    """
    Resolve whether to trade based on engine signal vs macro state.
    """
    overlay = str(overlay_state or "").upper()

    # V6.22: Overlay precedence - block bullish VASS routes during STRESS.
    if engine == "VASS" and overlay == "STRESS":
        if engine_direction == "BULLISH":
            return (
                False,
                None,
                "NO_TRADE: E_OVERLAY_STRESS_BULL_BLOCK (VASS bullish blocked in STRESS)",
            )
        if engine_direction is None and macro_direction == "BULLISH":
            return (
                False,
                None,
                "NO_TRADE: E_OVERLAY_STRESS_BULL_BLOCK (Macro bullish blocked in STRESS)",
            )
    # D8: In EARLY_STRESS, require conviction before allowing bullish VASS direction.
    if (
        engine == "VASS"
        and overlay == "EARLY_STRESS"
        and engine_direction == "BULLISH"
        and not engine_conviction
        and bool(getattr(config, "VASS_EARLY_STRESS_BULL_REQUIRE_CONVICTION", True))
    ):
        return (
            False,
            None,
            "NO_TRADE: E_OVERLAY_EARLY_BULL_NO_CONVICTION",
        )

    # V10.7+: VASS direction precedence.
    # Conviction direction has priority when present; when absent, behavior is
    # governed by VASS_NO_CONVICTION_NO_TRADE (block vs. fall through to macro).
    if engine == "VASS" and bool(getattr(config, "VASS_USE_CONVICTION_ONLY_DIRECTION", False)):
        if engine_conviction and engine_direction in ("BULLISH", "BEARISH"):
            return (
                True,
                engine_direction,
                f"VASS_CONVICTION_DIRECTION: {engine_direction}",
            )

        if bool(getattr(config, "VASS_NO_CONVICTION_NO_TRADE", True)):
            return (
                False,
                None,
                "NO_TRADE: VASS_NO_CONVICTION",
            )

    # V10.9: In CAUTION_LOW, bearish MICRO requires conviction across all macro states.
    if (
        engine == "MICRO"
        and engine_regime == "CAUTION_LOW"
        and engine_direction == "BEARISH"
        and not engine_conviction
    ):
        return (
            False,
            None,
            "NO_TRADE: MICRO CAUTION_LOW bearish requires conviction",
        )

    # No engine direction = follow Macro if it has a clear direction
    if engine_direction is None:
        if macro_direction in ("BULLISH", "BEARISH"):
            return (
                True,
                macro_direction,
                f"FOLLOW_MACRO: {engine} has no direction, following Macro {macro_direction}",
            )
        else:
            return (
                False,
                None,
                f"NO_TRADE: {engine} has no direction, Macro is {macro_direction}",
            )

    # Case 1: Aligned
    if engine_direction == macro_direction:
        return True, engine_direction, f"ALIGNED: {engine} + Macro agree on {engine_direction}"

    # Case 2: Macro is NEUTRAL (no strong opinion)
    if macro_direction == "NEUTRAL":
        if engine_conviction:
            if not allow_macro_veto:
                return (
                    False,
                    None,
                    f"NO_TRADE: {engine} conviction present but hard-veto guard not satisfied",
                )
            # V6.9: Only allow MICRO to VETO NEUTRAL on extreme UVXY moves
            if engine == "MICRO" and conviction_strength is not None:
                if abs(conviction_strength) < config.MICRO_UVXY_CONVICTION_EXTREME:
                    return (
                        False,
                        None,
                        f"NO_TRADE: Macro NEUTRAL, MICRO conviction not extreme "
                        f"({conviction_strength:+.1%} < {config.MICRO_UVXY_CONVICTION_EXTREME:.0%})",
                    )
            # V6.15: In NEUTRAL macro, only allow MICRO veto when regime is tradeable
            # and the resolved conviction direction aligns with Micro's own recommendation.
            if engine == "MICRO":
                tradeable_regimes = {
                    "PERFECT_MR",
                    "GOOD_MR",
                    "NORMAL",
                    "RECOVERING",
                    "IMPROVING",
                    "PANIC_EASING",
                    "CALMING",
                    "CAUTION_LOW",
                    "CAUTIOUS",
                    "ELEVATED",
                    "WORSENING",
                    "TRANSITION",
                }
                if engine_regime not in tradeable_regimes:
                    return (
                        False,
                        None,
                        f"NO_TRADE: Macro NEUTRAL, MICRO regime not tradeable ({engine_regime})",
                    )
                if (
                    engine_recommended_direction is not None
                    and engine_direction != engine_recommended_direction
                ):
                    return (
                        False,
                        None,
                        "NO_TRADE: Macro NEUTRAL, MICRO conviction direction misaligned",
                    )
            return (
                True,
                engine_direction,
                f"VETO: {engine} conviction ({engine_direction}) overrides NEUTRAL Macro",
            )
        else:
            return (
                True,
                engine_direction,
                f"NEUTRAL_ALIGNED_HALF: Macro NEUTRAL, {engine} no conviction",
            )

    # Case 3: Misaligned with clear Macro direction
    # Micro owns intraday direction. Resolver acts as a risk gate here.
    # V6.14 OPT: In BEARISH macro, block bullish overrides unless conviction is extreme.
    if macro_direction == "BEARISH" and engine_direction == "BULLISH":
        if engine != "MICRO":
            return (
                False,
                None,
                "NO_TRADE: Macro BEARISH blocks CALL override (non-MICRO)",
            )
        if not engine_conviction:
            return (
                False,
                None,
                "NO_TRADE: Macro BEARISH blocks non-conviction MICRO CALL",
            )
        if (
            conviction_strength is None
            or abs(conviction_strength) < config.MICRO_UVXY_CONVICTION_EXTREME
        ):
            return (
                False,
                None,
                "NO_TRADE: Macro BEARISH requires extreme MICRO bullish conviction",
            )

    if engine == "MICRO" and not engine_conviction:
        return (
            False,
            None,
            f"NO_TRADE: MISALIGNED_NO_CONVICTION {engine}={engine_direction}, Macro={macro_direction}",
        )

    if engine_conviction:
        if not allow_macro_veto:
            return (
                False,
                None,
                f"NO_TRADE: {engine} conviction present but hard-veto guard not satisfied",
            )
        # V6.9: Never allow CALL overrides in BEARISH macro (prevent bull bias in bear markets)
        if engine != "MICRO" and macro_direction == "BEARISH" and engine_direction == "BULLISH":
            return (
                False,
                None,
                "NO_TRADE: Macro BEARISH blocks CALL override (V6.9)",
            )
        self.log(
            f"VETO: {engine} conviction ({engine_direction}) overrides Macro ({macro_direction})",
            trades_only=True,
        )
        return (
            True,
            engine_direction,
            f"VETO: {engine} conviction overrides Macro {macro_direction}",
        )
    else:
        return (
            False,
            None,
            f"NO_TRADE: Misaligned ({engine}={engine_direction}, Macro={macro_direction}), no conviction",
        )
