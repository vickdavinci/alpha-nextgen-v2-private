"""VASS risk firewall helpers extracted from options_engine."""

from __future__ import annotations

import config
from models.enums import Urgency
from models.target_weight import TargetWeight


def check_friday_firewall_exit_impl(
    self,
    current_vix: float,
    current_date: str,
    vix_close_all_threshold: float = 25.0,
    vix_keep_fresh_threshold: float = 15.0,
    spread_override: Optional[SpreadPosition] = None,
) -> Optional[List[TargetWeight]]:
    """
    V2.4.1: Friday Firewall - close swing options before weekend.

    Safety-first approach to weekend risk management:
    1. VIX > 25: Close ALL swing options (high volatility = high gap risk)
    2. Fresh trade (opened today) AND VIX >= 15: Close it (gambling protection)
    3. Fresh trade AND VIX < 15: Keep it (calm market exception)
    4. Older trades AND VIX <= 25: Keep them (already survived initial risk)

    Args:
        current_vix: Current VIX level.
        current_date: Current date string (YYYY-MM-DD format).
        vix_close_all_threshold: VIX level above which ALL positions close (default 25).
        vix_keep_fresh_threshold: VIX level below which fresh trades can stay (default 15).

    Returns:
        List of TargetWeights for positions to close, or None if no action needed.
    """
    exit_signals = []

    # Check spread position
    spread = spread_override or self.get_spread_position()
    if spread is not None:
        entry_date = (
            spread.entry_time.split()[0] if " " in spread.entry_time else spread.entry_time[:10]
        )
        is_fresh_trade = entry_date == current_date

        should_close = False
        close_reason = ""

        # Rule 1: VIX > threshold - close ALL
        if current_vix > vix_close_all_threshold:
            should_close = True
            close_reason = f"VIX_HIGH ({current_vix:.1f} > {vix_close_all_threshold})"

        # Rule 2: Fresh trade + VIX >= 15 - close (gambling protection)
        elif is_fresh_trade and current_vix >= vix_keep_fresh_threshold:
            should_close = True
            close_reason = (
                f"FRESH_TRADE_PROTECTION (VIX={current_vix:.1f} >= {vix_keep_fresh_threshold})"
            )

        # Rule 3: Fresh trade + VIX < 15 - keep (calm market)
        elif is_fresh_trade and current_vix < vix_keep_fresh_threshold:
            self.log(
                f"FRIDAY_FIREWALL: Keeping fresh spread (calm market) | "
                f"VIX={current_vix:.1f} < {vix_keep_fresh_threshold}"
            )

        # Rule 4: Older trade + VIX <= 25 - keep
        else:
            self.log(
                f"FRIDAY_FIREWALL: Keeping spread (established trade) | "
                f"Entry={entry_date} | VIX={current_vix:.1f}"
            )

        if should_close:
            self.log(
                f"FRIDAY_FIREWALL: Closing spread | {close_reason} | "
                f"Entry={entry_date} Fresh={is_fresh_trade}",
                trades_only=True,
            )
            # V2.5 FIX: Close both legs via COMBO order (atomic execution)
            exit_signals.append(
                TargetWeight(
                    symbol=self._symbol_str(spread.long_leg.symbol),
                    target_weight=0.0,
                    source="OPT",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"FRIDAY_FIREWALL: {close_reason}",
                    requested_quantity=spread.num_spreads,
                    metadata={
                        "spread_close_short": True,
                        "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                        "spread_short_leg_quantity": spread.num_spreads,
                        "spread_key": self._build_spread_key(spread),
                    },
                )
            )

    # Check single-leg position
    if self._position is not None:
        position = self._position
        entry_date = (
            position.entry_time.split()[0]
            if " " in position.entry_time
            else position.entry_time[:10]
        )
        is_fresh_trade = entry_date == current_date

        should_close = False
        close_reason = ""

        # Rule 1: VIX > threshold - close ALL
        if current_vix > vix_close_all_threshold:
            should_close = True
            close_reason = f"VIX_HIGH ({current_vix:.1f} > {vix_close_all_threshold})"

        # Rule 2: Fresh trade + VIX >= 15 - close
        elif is_fresh_trade and current_vix >= vix_keep_fresh_threshold:
            should_close = True
            close_reason = (
                f"FRESH_TRADE_PROTECTION (VIX={current_vix:.1f} >= {vix_keep_fresh_threshold})"
            )

        # Rule 3 & 4: Keep if calm or established
        elif is_fresh_trade and current_vix < vix_keep_fresh_threshold:
            self.log(
                f"FRIDAY_FIREWALL: Keeping fresh single-leg (calm market) | "
                f"VIX={current_vix:.1f} < {vix_keep_fresh_threshold}"
            )
        else:
            self.log(
                f"FRIDAY_FIREWALL: Keeping single-leg (established trade) | "
                f"Entry={entry_date} | VIX={current_vix:.1f}"
            )

        if should_close:
            self.log(
                f"FRIDAY_FIREWALL: Closing single-leg | {close_reason} | "
                f"Entry={entry_date} Fresh={is_fresh_trade}",
                trades_only=True,
            )
            exit_signals.append(
                TargetWeight(
                    symbol=self._symbol_str(position.contract.symbol),
                    target_weight=0.0,
                    source="OPT",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"FRIDAY_FIREWALL: {close_reason}",
                    requested_quantity=max(1, int(getattr(position, "num_contracts", 1))),
                )
            )

    return exit_signals if exit_signals else None


# =========================================================================
# V6.13 P0: OVERNIGHT GAP PROTECTION (All Days)
# =========================================================================


def check_overnight_gap_protection_exit_impl(
    self,
    current_vix: float,
    current_date: str,
) -> Optional[List[TargetWeight]]:
    """
    V6.13 P0: Close swing spreads before overnight risk when VIX is elevated.

    Rules:
    1) If VIX >= SWING_OVERNIGHT_VIX_CLOSE_ALL → close ALL spreads
    2) If trade opened today AND VIX >= SWING_OVERNIGHT_VIX_CLOSE_FRESH → close fresh spread
    """
    if not getattr(config, "SWING_OVERNIGHT_GAP_PROTECTION_ENABLED", True):
        return None

    spreads = self.get_spread_positions()
    if not spreads:
        return None

    close_all_threshold = getattr(config, "SWING_OVERNIGHT_VIX_CLOSE_ALL", 30.0)
    close_fresh_threshold = getattr(config, "SWING_OVERNIGHT_VIX_CLOSE_FRESH", 22.0)

    exit_signals: List[TargetWeight] = []
    for spread in spreads:
        entry_date = (
            spread.entry_time.split()[0] if " " in spread.entry_time else spread.entry_time[:10]
        )
        is_fresh_trade = entry_date == current_date

        reason = None
        if current_vix >= close_all_threshold:
            reason = f"OVERNIGHT_GAP_PROTECTION: VIX {current_vix:.1f} >= {close_all_threshold}"
        elif is_fresh_trade and current_vix >= close_fresh_threshold:
            reason = f"OVERNIGHT_GAP_PROTECTION: Fresh trade + VIX {current_vix:.1f} >= {close_fresh_threshold}"

        if reason is None:
            continue

        self.log(
            f"OVERNIGHT_GAP_PROTECTION: Closing spread | {reason} | "
            f"Entry={entry_date} Fresh={is_fresh_trade}",
            trades_only=True,
        )
        exit_signals.append(
            TargetWeight(
                symbol=self._symbol_str(spread.long_leg.symbol),
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=spread.num_spreads,
                metadata={
                    "spread_close_short": True,
                    "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                    "spread_short_leg_quantity": spread.num_spreads,
                    "spread_key": self._build_spread_key(spread),
                    "exit_type": "OVERNIGHT_GAP_PROTECTION",
                },
            )
        )

    return exit_signals if exit_signals else None
