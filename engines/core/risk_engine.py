"""
Risk Engine - Circuit breakers and safeguards.

Implements defense-in-depth protection:
- Kill Switch: -3% daily loss → liquidate ALL, reset cold start
- Panic Mode: SPY -4% intraday → liquidate longs only, keep hedges
- Weekly Breaker: -5% WTD → reduce sizing by 50%
- Gap Filter: SPY -1.5% gap → block intraday entries
- Vol Shock: 3× ATR bar → pause entries 15 min
- Time Guard: 13:55-14:10 → block all entries
- Split Guard: Corporate action → freeze affected symbol

Risk Engine operates at Level 2 in authority hierarchy (overrides all strategy signals).

Spec: docs/12-risk-engine.md
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

import config

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm


class SafeguardType(Enum):
    """Types of risk safeguards."""

    KILL_SWITCH = "KILL_SWITCH"
    PANIC_MODE = "PANIC_MODE"
    WEEKLY_BREAKER = "WEEKLY_BREAKER"
    GAP_FILTER = "GAP_FILTER"
    VOL_SHOCK = "VOL_SHOCK"
    TIME_GUARD = "TIME_GUARD"
    SPLIT_GUARD = "SPLIT_GUARD"


# Symbol classifications for liquidation
LEVERAGED_LONG_SYMBOLS: List[str] = ["TQQQ", "QLD", "SSO", "SOXL"]
HEDGE_SYMBOLS: List[str] = ["TMF", "PSQ"]
YIELD_SYMBOLS: List[str] = ["SHV"]
ALL_TRADED_SYMBOLS: List[str] = LEVERAGED_LONG_SYMBOLS + HEDGE_SYMBOLS + YIELD_SYMBOLS


@dataclass
class SafeguardStatus:
    """
    Current status of a safeguard.

    Attributes:
        safeguard_type: Which safeguard this status represents.
        is_active: Whether the safeguard is currently triggered.
        triggered_at: When the safeguard was triggered.
        expires_at: When the safeguard will deactivate (for time-limited).
        details: Additional context about the trigger.
    """

    safeguard_type: SafeguardType
    is_active: bool = False
    triggered_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/persistence."""
        return {
            "safeguard_type": self.safeguard_type.value,
            "is_active": self.is_active,
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "details": self.details,
        }


@dataclass
class RiskCheckResult:
    """
    Result of a risk check.

    Attributes:
        can_enter_positions: Whether new entries are allowed.
        can_enter_intraday: Whether intraday (MR) entries are allowed.
        sizing_multiplier: Position sizing multiplier (1.0 normal, 0.5 reduced).
        symbols_to_liquidate: List of symbols to liquidate immediately.
        active_safeguards: List of currently active safeguards.
        reset_cold_start: Whether to reset days_running to 0.
    """

    can_enter_positions: bool = True
    can_enter_intraday: bool = True
    sizing_multiplier: float = 1.0
    symbols_to_liquidate: List[str] = field(default_factory=list)
    active_safeguards: List[SafeguardType] = field(default_factory=list)
    reset_cold_start: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/persistence."""
        return {
            "can_enter_positions": self.can_enter_positions,
            "can_enter_intraday": self.can_enter_intraday,
            "sizing_multiplier": self.sizing_multiplier,
            "symbols_to_liquidate": self.symbols_to_liquidate,
            "active_safeguards": [s.value for s in self.active_safeguards],
            "reset_cold_start": self.reset_cold_start,
        }


class RiskEngine:
    """
    Circuit breakers and safeguards for portfolio protection.

    Checks are run in priority order:
    1. Kill Switch (highest - checked first)
    2. Panic Mode
    3. Vol Shock
    4. Gap Filter (checked once at 09:33)
    5. Time Guard
    6. Split Guard
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """
        Initialize the Risk Engine.

        Args:
            algorithm: QuantConnect algorithm instance (optional for testing).
        """
        self.algorithm = algorithm

        # Baseline equity values
        self._equity_prior_close: float = 0.0
        self._equity_sod: float = 0.0  # Start of day (after MOO fills)
        self._week_start_equity: float = 0.0

        # SPY reference values
        self._spy_prior_close: float = 0.0
        self._spy_open: float = 0.0
        self._spy_atr: float = 0.0  # 14-period ATR on 1-min bars

        # Safeguard states
        self._kill_switch_active: bool = False
        self._panic_mode_active: bool = False
        self._weekly_breaker_active: bool = False
        self._gap_filter_active: bool = False
        self._vol_shock_until: Optional[datetime] = None
        self._split_frozen_symbols: Set[str] = set()

        # Persisted state
        self._last_kill_date: Optional[str] = None

        # Config parameters
        self._kill_switch_pct = config.KILL_SWITCH_PCT
        self._panic_mode_pct = config.PANIC_MODE_PCT
        self._weekly_breaker_pct = config.WEEKLY_BREAKER_PCT
        self._gap_filter_pct = config.GAP_FILTER_PCT
        self._vol_shock_atr_mult = config.VOL_SHOCK_ATR_MULT
        self._vol_shock_pause_min = config.VOL_SHOCK_PAUSE_MIN
        self._time_guard_start = self._parse_time(config.TIME_GUARD_START)
        self._time_guard_end = self._parse_time(config.TIME_GUARD_END)

    def _parse_time(self, time_str: str) -> Tuple[int, int]:
        """Parse time string 'HH:MM' into (hour, minute) tuple."""
        parts = time_str.split(":")
        return (int(parts[0]), int(parts[1]))

    def log(self, message: str) -> None:
        """Log a message via QC algorithm or print for testing."""
        if self.algorithm:
            self.algorithm.Log(message)  # type: ignore[attr-defined]

    # =========================================================================
    # Baseline Setters (called by scheduling)
    # =========================================================================

    def set_equity_prior_close(self, equity: float) -> None:
        """
        Set the prior close equity baseline (09:25 AM).

        Args:
            equity: Portfolio value at previous day's close.
        """
        self._equity_prior_close = equity
        self.log(f"RISK: Set equity_prior_close = ${equity:,.2f}")

    def set_equity_sod(self, equity: float) -> None:
        """
        Set the start-of-day equity baseline (09:33 AM after MOO fills).

        Args:
            equity: Portfolio value after opening auction.
        """
        self._equity_sod = equity
        self.log(f"RISK: Set equity_sod = ${equity:,.2f}")

    def set_week_start_equity(self, equity: float) -> None:
        """
        Set the week start equity baseline (Monday 09:30 AM).

        Args:
            equity: Portfolio value at week start.
        """
        self._week_start_equity = equity
        self._weekly_breaker_active = False  # Reset weekly breaker
        self.log(f"RISK: Set week_start_equity = ${equity:,.2f} (weekly breaker reset)")

    def set_spy_prior_close(self, price: float) -> None:
        """
        Set SPY prior close for gap filter calculation.

        Args:
            price: SPY closing price from previous day.
        """
        self._spy_prior_close = price
        self.log(f"RISK: Set spy_prior_close = ${price:.2f}")

    def set_spy_open(self, price: float) -> None:
        """
        Set SPY open price for panic mode calculation.

        Args:
            price: SPY opening price today.
        """
        self._spy_open = price
        self.log(f"RISK: Set spy_open = ${price:.2f}")

    def set_spy_atr(self, atr: float) -> None:
        """
        Set SPY ATR for vol shock calculation.

        Args:
            atr: 14-period ATR on 1-minute bars.
        """
        self._spy_atr = atr

    # =========================================================================
    # Kill Switch (-3% Daily)
    # =========================================================================

    def check_kill_switch(self, current_equity: float) -> bool:
        """
        Check if kill switch should trigger.

        Triggers if loss from EITHER baseline exceeds 3%.

        Args:
            current_equity: Current portfolio value.

        Returns:
            True if kill switch triggered.
        """
        if self._kill_switch_active:
            return True  # Already triggered today

        # Check vs prior close
        if self._equity_prior_close > 0:
            loss_from_prior = (self._equity_prior_close - current_equity) / self._equity_prior_close
            if loss_from_prior >= self._kill_switch_pct:
                self._trigger_kill_switch(
                    current_equity, "prior_close", self._equity_prior_close, loss_from_prior
                )
                return True

        # Check vs SOD
        if self._equity_sod > 0:
            loss_from_sod = (self._equity_sod - current_equity) / self._equity_sod
            if loss_from_sod >= self._kill_switch_pct:
                self._trigger_kill_switch(current_equity, "sod", self._equity_sod, loss_from_sod)
                return True

        return False

    def _trigger_kill_switch(
        self, current_equity: float, baseline_name: str, baseline_value: float, loss_pct: float
    ) -> None:
        """Record kill switch trigger."""
        self._kill_switch_active = True
        self.log(
            f"KILL_SWITCH: TRIGGERED | "
            f"Loss={loss_pct:.2%} from {baseline_name} | "
            f"Baseline=${baseline_value:,.2f} | "
            f"Current=${current_equity:,.2f}"
        )

    def get_kill_switch_status(self) -> SafeguardStatus:
        """Get current kill switch status."""
        return SafeguardStatus(
            safeguard_type=SafeguardType.KILL_SWITCH,
            is_active=self._kill_switch_active,
            details="All positions liquidated, trading disabled"
            if self._kill_switch_active
            else "",
        )

    # =========================================================================
    # Panic Mode (SPY -4% Intraday)
    # =========================================================================

    def check_panic_mode(self, spy_price: float) -> bool:
        """
        Check if panic mode should trigger.

        Triggers when SPY drops 4% or more from today's open.

        Args:
            spy_price: Current SPY price.

        Returns:
            True if panic mode triggered.
        """
        if self._panic_mode_active:
            return True  # Already triggered today

        if self._spy_open <= 0:
            return False  # Open not set yet

        drop_pct = (self._spy_open - spy_price) / self._spy_open
        if drop_pct >= self._panic_mode_pct:
            self._panic_mode_active = True
            self.log(
                f"PANIC_MODE: TRIGGERED | "
                f"SPY drop={drop_pct:.2%} | "
                f"Open=${self._spy_open:.2f} | "
                f"Current=${spy_price:.2f}"
            )
            return True

        return False

    def get_panic_mode_status(self) -> SafeguardStatus:
        """Get current panic mode status."""
        return SafeguardStatus(
            safeguard_type=SafeguardType.PANIC_MODE,
            is_active=self._panic_mode_active,
            details="Leveraged longs liquidated, hedges kept" if self._panic_mode_active else "",
        )

    # =========================================================================
    # Weekly Breaker (-5% WTD)
    # =========================================================================

    def check_weekly_breaker(self, current_equity: float) -> bool:
        """
        Check if weekly breaker should trigger.

        Triggers when week-to-date loss exceeds 5%.

        Args:
            current_equity: Current portfolio value.

        Returns:
            True if weekly breaker is active.
        """
        if self._weekly_breaker_active:
            return True  # Already triggered this week

        if self._week_start_equity <= 0:
            return False  # Week start not set

        wtd_loss = (self._week_start_equity - current_equity) / self._week_start_equity
        if wtd_loss >= self._weekly_breaker_pct:
            self._weekly_breaker_active = True
            self.log(
                f"WEEKLY_BREAKER: TRIGGERED | "
                f"WTD loss={wtd_loss:.2%} | "
                f"Week start=${self._week_start_equity:,.2f} | "
                f"Current=${current_equity:,.2f}"
            )
            return True

        return False

    def get_sizing_multiplier(self) -> float:
        """
        Get position sizing multiplier.

        Returns:
            1.0 for normal sizing, 0.5 if weekly breaker active.
        """
        if self._weekly_breaker_active:
            return 0.5
        return 1.0

    def get_weekly_breaker_status(self) -> SafeguardStatus:
        """Get current weekly breaker status."""
        return SafeguardStatus(
            safeguard_type=SafeguardType.WEEKLY_BREAKER,
            is_active=self._weekly_breaker_active,
            details="Position sizing reduced to 50%" if self._weekly_breaker_active else "",
        )

    # =========================================================================
    # Gap Filter (SPY -1.5% Gap)
    # =========================================================================

    def check_gap_filter(self, spy_open: float) -> bool:
        """
        Check if gap filter should activate.

        Called once at 09:33 AM. Triggers if SPY opens 1.5%+ below prior close.

        Args:
            spy_open: SPY opening price.

        Returns:
            True if gap filter is active.
        """
        if self._spy_prior_close <= 0:
            return False

        gap_pct = (self._spy_prior_close - spy_open) / self._spy_prior_close
        if gap_pct >= self._gap_filter_pct:
            self._gap_filter_active = True
            self.log(
                f"GAP_FILTER: ACTIVATED | "
                f"Gap={gap_pct:.2%} | "
                f"Prior close=${self._spy_prior_close:.2f} | "
                f"Open=${spy_open:.2f}"
            )
            return True

        return False

    def is_gap_filter_active(self) -> bool:
        """Check if gap filter is currently active."""
        return self._gap_filter_active

    def get_gap_filter_status(self) -> SafeguardStatus:
        """Get current gap filter status."""
        return SafeguardStatus(
            safeguard_type=SafeguardType.GAP_FILTER,
            is_active=self._gap_filter_active,
            details="Intraday (MR) entries blocked" if self._gap_filter_active else "",
        )

    # =========================================================================
    # Vol Shock (3× ATR Bar)
    # =========================================================================

    def check_vol_shock(self, bar_range: float, current_time: datetime) -> bool:
        """
        Check if vol shock should trigger.

        Triggers when SPY 1-minute bar range exceeds 3× ATR.
        A new trigger during an existing vol shock extends the window.

        Args:
            bar_range: High - Low of current 1-minute SPY bar.
            current_time: Current algorithm time.

        Returns:
            True if vol shock is active (new or ongoing).
        """
        if self._spy_atr <= 0:
            # Can't compute threshold, check existing window
            if self._vol_shock_until and current_time < self._vol_shock_until:
                return True
            return False

        threshold = self._vol_shock_atr_mult * self._spy_atr

        # Check for new trigger (even if already in vol shock)
        if bar_range > threshold:
            new_expiry = current_time + timedelta(minutes=self._vol_shock_pause_min)
            # Extend window if new trigger
            if self._vol_shock_until is None or new_expiry > self._vol_shock_until:
                self._vol_shock_until = new_expiry
                self.log(
                    f"VOL_SHOCK: TRIGGERED | "
                    f"Bar range=${bar_range:.4f} | "
                    f"Threshold=${threshold:.4f} (3×ATR) | "
                    f"Paused until {self._vol_shock_until.strftime('%H:%M')}"
                )
            return True

        # No new trigger, check if existing window still active
        if self._vol_shock_until and current_time < self._vol_shock_until:
            return True

        return False

    def is_vol_shock_active(self, current_time: datetime) -> bool:
        """Check if vol shock pause is currently active."""
        if self._vol_shock_until is None:
            return False
        return current_time < self._vol_shock_until

    def get_vol_shock_status(self, current_time: datetime) -> SafeguardStatus:
        """Get current vol shock status."""
        is_active = self.is_vol_shock_active(current_time)
        return SafeguardStatus(
            safeguard_type=SafeguardType.VOL_SHOCK,
            is_active=is_active,
            expires_at=self._vol_shock_until if is_active else None,
            details=f"Entries paused until {self._vol_shock_until.strftime('%H:%M')}"
            if is_active and self._vol_shock_until
            else "",
        )

    # =========================================================================
    # Time Guard (13:55 - 14:10 ET)
    # =========================================================================

    def is_time_guard_active(self, current_time: datetime) -> bool:
        """
        Check if time guard is active.

        Active between 13:55 and 14:10 ET every trading day.

        Args:
            current_time: Current algorithm time.

        Returns:
            True if within time guard window.
        """
        current_hour = current_time.hour
        current_minute = current_time.minute
        current_total = current_hour * 60 + current_minute

        start_total = self._time_guard_start[0] * 60 + self._time_guard_start[1]
        end_total = self._time_guard_end[0] * 60 + self._time_guard_end[1]

        return start_total <= current_total < end_total

    def get_time_guard_status(self, current_time: datetime) -> SafeguardStatus:
        """Get current time guard status."""
        is_active = self.is_time_guard_active(current_time)
        return SafeguardStatus(
            safeguard_type=SafeguardType.TIME_GUARD,
            is_active=is_active,
            details=f"All entries blocked until {self._time_guard_end[0]}:{self._time_guard_end[1]:02d}"
            if is_active
            else "",
        )

    # =========================================================================
    # Split Guard
    # =========================================================================

    def register_split(self, symbol: str) -> None:
        """
        Register a stock split for a symbol.

        Freezes trading on the symbol for the rest of the day.

        Args:
            symbol: Symbol that experienced a split.
        """
        self._split_frozen_symbols.add(symbol)
        self.log(f"SPLIT_GUARD: {symbol} frozen for remainder of day")

    def is_symbol_frozen(self, symbol: str) -> bool:
        """Check if a symbol is frozen due to split."""
        return symbol in self._split_frozen_symbols

    def get_split_guard_status(self) -> SafeguardStatus:
        """Get current split guard status."""
        is_active = len(self._split_frozen_symbols) > 0
        return SafeguardStatus(
            safeguard_type=SafeguardType.SPLIT_GUARD,
            is_active=is_active,
            details=f"Frozen symbols: {', '.join(sorted(self._split_frozen_symbols))}"
            if is_active
            else "",
        )

    # =========================================================================
    # Combined Risk Check
    # =========================================================================

    def check_all(
        self,
        current_equity: float,
        spy_price: float,
        spy_bar_range: float,
        current_time: datetime,
    ) -> RiskCheckResult:
        """
        Run all risk checks and return combined result.

        Checks are run in priority order.

        Args:
            current_equity: Current portfolio value.
            spy_price: Current SPY price.
            spy_bar_range: Current SPY 1-min bar range (high - low).
            current_time: Current algorithm time.

        Returns:
            RiskCheckResult with all status flags and required actions.
        """
        result = RiskCheckResult()
        active_safeguards: List[SafeguardType] = []

        # 1. Kill Switch (highest priority)
        if self.check_kill_switch(current_equity):
            result.can_enter_positions = False
            result.can_enter_intraday = False
            result.symbols_to_liquidate = ALL_TRADED_SYMBOLS.copy()
            result.reset_cold_start = True
            active_safeguards.append(SafeguardType.KILL_SWITCH)
            result.active_safeguards = active_safeguards
            return result  # Kill switch overrides everything

        # 2. Panic Mode
        if self.check_panic_mode(spy_price):
            result.can_enter_positions = False
            result.can_enter_intraday = False
            result.symbols_to_liquidate = LEVERAGED_LONG_SYMBOLS.copy()
            active_safeguards.append(SafeguardType.PANIC_MODE)

        # 3. Weekly Breaker
        if self.check_weekly_breaker(current_equity):
            result.sizing_multiplier = 0.5
            active_safeguards.append(SafeguardType.WEEKLY_BREAKER)

        # 4. Vol Shock
        if self.check_vol_shock(spy_bar_range, current_time):
            result.can_enter_positions = False
            result.can_enter_intraday = False
            active_safeguards.append(SafeguardType.VOL_SHOCK)

        # 5. Gap Filter (already set earlier in the day)
        if self._gap_filter_active:
            result.can_enter_intraday = False
            active_safeguards.append(SafeguardType.GAP_FILTER)

        # 6. Time Guard
        if self.is_time_guard_active(current_time):
            result.can_enter_positions = False
            result.can_enter_intraday = False
            active_safeguards.append(SafeguardType.TIME_GUARD)

        # 7. Split Guard (handled per-symbol, not in combined check)

        result.active_safeguards = active_safeguards
        return result

    def can_enter_new_positions(self, current_time: datetime) -> bool:
        """
        Quick check if new entries are allowed.

        Does NOT run full equity checks - use for quick gating.

        Args:
            current_time: Current algorithm time.

        Returns:
            True if entries are allowed based on time/state flags.
        """
        if self._kill_switch_active:
            return False
        if self._panic_mode_active:
            return False
        if self.is_vol_shock_active(current_time):
            return False
        if self.is_time_guard_active(current_time):
            return False
        return True

    def can_enter_intraday(self, current_time: datetime) -> bool:
        """
        Quick check if intraday (MR) entries are allowed.

        Includes gap filter check in addition to can_enter_new_positions.

        Args:
            current_time: Current algorithm time.

        Returns:
            True if intraday entries are allowed.
        """
        if not self.can_enter_new_positions(current_time):
            return False
        if self._gap_filter_active:
            return False
        return True

    # =========================================================================
    # Day Reset
    # =========================================================================

    def reset_daily_state(self) -> None:
        """
        Reset daily state variables.

        Called at start of new trading day.
        """
        self._kill_switch_active = False
        self._panic_mode_active = False
        self._gap_filter_active = False
        self._vol_shock_until = None
        self._split_frozen_symbols.clear()

        # Reset baselines (will be set by scheduling)
        self._equity_prior_close = 0.0
        self._equity_sod = 0.0
        self._spy_prior_close = 0.0
        self._spy_open = 0.0

        self.log("RISK: Daily state reset")

    # =========================================================================
    # State Persistence
    # =========================================================================

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """
        Get state dict for persistence.

        Returns:
            Dict with all state that should be persisted.
        """
        return {
            "last_kill_date": self._last_kill_date,
            "week_start_equity": self._week_start_equity,
            "weekly_breaker_active": self._weekly_breaker_active,
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """
        Load state from persistence.

        Args:
            state: Previously saved state dict.
        """
        self._last_kill_date = state.get("last_kill_date")
        self._week_start_equity = state.get("week_start_equity", 0.0)
        self._weekly_breaker_active = state.get("weekly_breaker_active", False)
        self.log(
            f"RISK: State loaded | "
            f"last_kill_date={self._last_kill_date} | "
            f"week_start_equity=${self._week_start_equity:,.2f} | "
            f"weekly_breaker={self._weekly_breaker_active}"
        )

    def set_last_kill_date(self, date_str: str) -> None:
        """
        Record the date of a kill switch trigger.

        Args:
            date_str: Date string (e.g., '2024-01-15').
        """
        self._last_kill_date = date_str
        self.log(f"RISK: Kill date recorded: {date_str}")

    def get_last_kill_date(self) -> Optional[str]:
        """Get the date of the most recent kill switch trigger."""
        return self._last_kill_date

    # =========================================================================
    # Status Summary
    # =========================================================================

    def get_all_statuses(self, current_time: datetime) -> Dict[str, SafeguardStatus]:
        """
        Get status of all safeguards.

        Args:
            current_time: Current algorithm time.

        Returns:
            Dict mapping safeguard name to its status.
        """
        return {
            "kill_switch": self.get_kill_switch_status(),
            "panic_mode": self.get_panic_mode_status(),
            "weekly_breaker": self.get_weekly_breaker_status(),
            "gap_filter": self.get_gap_filter_status(),
            "vol_shock": self.get_vol_shock_status(current_time),
            "time_guard": self.get_time_guard_status(current_time),
            "split_guard": self.get_split_guard_status(),
        }
