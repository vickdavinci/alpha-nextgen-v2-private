"""
Yield Sleeve - SHV idle cash management.

Puts unallocated cash to work earning yield through short-term treasuries (SHV).
Rather than holding idle cash earning minimal interest, invests in ultra-short
duration treasury ETF for ~5% yield with instant liquidity.

Features:
- $2,000 minimum to avoid inefficient small trades
- No maximum allocation (idle cash should earn yield)
- LIFO liquidation (first to sell when cash needed)
- Lockbox integration (locked capital in SHV but excluded from trading)
- EOD rebalancing only

Spec: docs/10-yield-sleeve.md
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config
from models.enums import Urgency
from models.target_weight import TargetWeight


@dataclass
class YieldState:
    """Current yield sleeve state."""

    shv_target_pct: float
    unallocated_cash: float
    locked_amount: float
    available_shv: float  # SHV holdings minus locked amount

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "shv_target_pct": self.shv_target_pct,
            "unallocated_cash": self.unallocated_cash,
            "locked_amount": self.locked_amount,
            "available_shv": self.available_shv,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "YieldState":
        """Deserialize from persistence."""
        return cls(
            shv_target_pct=data["shv_target_pct"],
            unallocated_cash=data["unallocated_cash"],
            locked_amount=data["locked_amount"],
            available_shv=data["available_shv"],
        )


class YieldSleeve:
    """
    SHV idle cash management engine.

    Invests unallocated cash in short-term treasuries (SHV) to earn yield.
    SHV is essentially "better cash" - same liquidity, better yield (~5% vs 0-0.5%).

    The Yield Sleeve has lowest priority in the Portfolio Router. When cash
    is needed for other positions, SHV is liquidated first.

    Note: This engine does NOT place orders. It only provides signals
    via TargetWeight objects for the Portfolio Router.
    """

    # Instrument managed by this engine
    INSTRUMENT: str = "SHV"

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """Initialize Yield Sleeve."""
        self.algorithm = algorithm
        self._last_state: Optional[YieldState] = None

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)

    def calculate_unallocated_cash(
        self,
        total_equity: float,
        non_shv_positions_value: float,
        current_shv_value: float,
    ) -> float:
        """
        Calculate unallocated cash that could be deployed to SHV.

        V2.3.17: Reserves CASH_BUFFER_PCT (10%) as "petty cash" to fund small trades
        without touching SHV. This reduces churn from Sniper (5%) and MR (5-10%) trades.

        V2.4.1: Additionally reserves OPTIONS_HARD_CASH_RESERVE_PCT (10%) as hard cash
        that is NEVER deployed to SHV. This cash stays as actual cash that options can
        always access, fixing SHV_MARGIN_LOCK where SHV becomes collateral for leveraged
        positions and cannot be sold.

        Args:
            total_equity: Total portfolio equity.
            non_shv_positions_value: Sum of all non-SHV position values.
            current_shv_value: Current SHV holdings value.

        Returns:
            Unallocated cash amount (after reserving buffers).
        """
        # V2.3.17: Reserve cash buffer to fund small trades without SHV liquidation
        cash_buffer = total_equity * config.CASH_BUFFER_PCT

        # V2.4.1: Reserve hard cash for options - NEVER deployed to SHV
        # This ensures options always have funds available regardless of margin state
        options_hard_cash = total_equity * config.OPTIONS_HARD_CASH_RESERVE_PCT

        # Total reserved = cash buffer + options hard cash
        total_reserved = cash_buffer + options_hard_cash

        # Unallocated = Total - All Positions (including SHV) - Reserved Cash
        unallocated = total_equity - non_shv_positions_value - current_shv_value - total_reserved

        if unallocated < 0:
            self.log(
                f"YIELD: RESERVE_ACTIVE | CashBuffer=${cash_buffer:,.0f} + "
                f"OptionsReserve=${options_hard_cash:,.0f} absorbing "
                f"${abs(unallocated):,.0f} shortfall"
            )

        return max(0.0, unallocated)

    def get_available_shv(
        self,
        current_shv_value: float,
        locked_amount: float,
    ) -> float:
        """
        Get SHV available for liquidation (excluding lockbox).

        Args:
            current_shv_value: Current SHV holdings value.
            locked_amount: Amount locked in virtual lockbox.

        Returns:
            SHV value available for liquidation.
        """
        return max(0.0, current_shv_value - locked_amount)

    def get_yield_signal(
        self,
        total_equity: float,
        tradeable_equity: float,
        non_shv_positions_value: float,
        current_shv_value: float,
        locked_amount: float = 0.0,
    ) -> Optional[TargetWeight]:
        """
        Generate yield allocation signal based on unallocated cash.

        This is the main entry point called at EOD (15:45 ET) to determine
        if SHV allocation should be adjusted.

        Args:
            total_equity: Total portfolio equity.
            tradeable_equity: Equity available for trading (after lockbox).
            non_shv_positions_value: Sum of all non-SHV position values.
            current_shv_value: Current SHV holdings value.
            locked_amount: Amount locked in virtual lockbox.

        Returns:
            TargetWeight signal for SHV, or None if below threshold.
        """
        # Calculate unallocated cash
        unallocated = self.calculate_unallocated_cash(
            total_equity=total_equity,
            non_shv_positions_value=non_shv_positions_value,
            current_shv_value=current_shv_value,
        )

        # V2.3.7 FIX: Cap unallocated to available margin to prevent "Insufficient buying power"
        # This fixes the "Cash Death Spiral" bug where SHV orders fail due to pending orders
        # consuming margin before SHV order executes
        if self.algorithm and hasattr(self.algorithm, "Portfolio"):
            try:
                margin_remaining = float(self.algorithm.Portfolio.MarginRemaining)
                # Use 95% of margin as buffer for price fluctuations
                max_buyable = margin_remaining * 0.95 if margin_remaining > 0 else 0
                if unallocated > max_buyable:
                    self.log(
                        f"YIELD: MARGIN_CAP | Unallocated ${unallocated:,.0f} "
                        f"capped to ${max_buyable:,.0f} (MarginRemaining=${margin_remaining:,.0f})"
                    )
                    unallocated = max_buyable
            except (TypeError, AttributeError):
                # Skip margin check if Portfolio is mocked in tests
                pass

        # Calculate available SHV (excluding lockbox)
        available_shv = self.get_available_shv(current_shv_value, locked_amount)

        # Store state
        self._last_state = YieldState(
            shv_target_pct=0.0,  # Will be updated below
            unallocated_cash=unallocated,
            locked_amount=locked_amount,
            available_shv=available_shv,
        )

        # Check minimum threshold
        if unallocated <= config.SHV_MIN_TRADE:
            self.log(
                f"YIELD: NO_SIGNAL | Unallocated ${unallocated:,.0f} "
                f"<= threshold ${config.SHV_MIN_TRADE:,}"
            )
            return None

        # Calculate target weight
        # Target = (current SHV + unallocated) / tradeable equity
        target_shv_value = current_shv_value + unallocated
        if tradeable_equity <= 0:
            self.log("YIELD: NO_SIGNAL | Tradeable equity <= 0")
            return None

        target_weight = target_shv_value / tradeable_equity

        # Cap at 1.0 (shouldn't happen but safety check)
        target_weight = min(1.0, target_weight)

        # Update state with calculated target
        self._last_state.shv_target_pct = target_weight

        reason = f"Unallocated cash ${unallocated:,.0f}"

        self.log(
            f"YIELD: SHV_SIGNAL | Target={target_weight:.1%} | {reason} | "
            f"Locked=${locked_amount:,.0f}"
        )

        return TargetWeight(
            symbol=self.INSTRUMENT,
            target_weight=target_weight,
            source="YIELD",
            urgency=Urgency.EOD,
            reason=reason,
        )

    def get_liquidation_signal(
        self,
        cash_needed: float,
        current_shv_value: float,
        locked_amount: float,
        tradeable_equity: float,
    ) -> Optional[TargetWeight]:
        """
        Generate signal to liquidate SHV for cash needs.

        Called by Portfolio Router when cash is needed for other positions.
        Only available (non-locked) SHV can be liquidated.

        Args:
            cash_needed: Amount of cash required.
            current_shv_value: Current SHV holdings value.
            locked_amount: Amount locked in virtual lockbox.
            tradeable_equity: Tradeable equity for weight calculation.

        Returns:
            TargetWeight signal to reduce SHV, or None if insufficient.
        """
        available_shv = self.get_available_shv(current_shv_value, locked_amount)

        if available_shv <= 0:
            self.log(
                f"YIELD: LIQUIDATION_BLOCKED | No available SHV | "
                f"Current=${current_shv_value:,.0f} | Locked=${locked_amount:,.0f}"
            )
            return None

        # Calculate how much SHV to sell
        sell_amount = min(cash_needed, available_shv)
        remaining_shv = current_shv_value - sell_amount

        # Calculate new target weight
        if tradeable_equity <= 0:
            target_weight = 0.0
        else:
            target_weight = remaining_shv / tradeable_equity

        reason = f"Liquidation for ${cash_needed:,.0f} needed, selling ${sell_amount:,.0f}"

        self.log(f"YIELD: LIQUIDATION_SIGNAL | Target={target_weight:.1%} | {reason}")

        return TargetWeight(
            symbol=self.INSTRUMENT,
            target_weight=target_weight,
            source="YIELD",
            urgency=Urgency.IMMEDIATE,  # Liquidation is immediate
            reason=reason,
        )

    def can_provide_cash(
        self,
        amount: float,
        current_shv_value: float,
        locked_amount: float,
    ) -> bool:
        """
        Check if SHV can provide the requested cash amount.

        Args:
            amount: Cash amount needed.
            current_shv_value: Current SHV holdings value.
            locked_amount: Amount locked in virtual lockbox.

        Returns:
            True if available SHV can cover the amount.
        """
        available = self.get_available_shv(current_shv_value, locked_amount)
        return available >= amount

    def get_max_liquidatable(
        self,
        current_shv_value: float,
        locked_amount: float,
    ) -> float:
        """
        Get maximum amount that can be liquidated from SHV.

        Args:
            current_shv_value: Current SHV holdings value.
            locked_amount: Amount locked in virtual lockbox.

        Returns:
            Maximum liquidatable amount.
        """
        return self.get_available_shv(current_shv_value, locked_amount)

    def get_last_state(self) -> Optional[YieldState]:
        """Get the last calculated state."""
        return self._last_state

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore."""
        return {"last_state": self._last_state.to_dict() if self._last_state else None}

    def restore_state(self, state: Dict[str, Any]) -> None:
        """Restore state from ObjectStore."""
        last_state_data = state.get("last_state")
        if last_state_data:
            self._last_state = YieldState.from_dict(last_state_data)
        else:
            self._last_state = None

    def reset(self) -> None:
        """Reset engine state."""
        self._last_state = None
        self.log("YIELD: Engine reset")

    def get_minimum_trade_threshold(self) -> float:
        """Get minimum unallocated cash threshold for SHV buy."""
        return config.SHV_MIN_TRADE
