"""
Risk Engine - Circuit breakers and safeguards.

V2.1 5-Level Circuit Breaker System (graduated responses):
- Level 1: Daily loss -2% → reduce sizing 50%
- Level 2: Weekly loss -5% → reduce sizing 50%
- Level 3: Portfolio vol > 1.5% → block new entries
- Level 4: Correlation > 0.60 → reduce exposure
- Level 5: Greeks breach → close options positions

V1 Safeguards (still active):
- Kill Switch: -3% daily loss → liquidate ALL, reset cold start
- Panic Mode: SPY -4% intraday → liquidate longs only, keep hedges
- Gap Filter: SPY -1.5% gap → block intraday entries
- Vol Shock: 3× ATR bar → pause entries 15 min
- Time Guard: 13:55-14:10 → block all entries
- Split Guard: Corporate action → freeze affected symbol

Risk Engine operates at Level 2 in authority hierarchy (overrides all strategy signals).

Spec: docs/12-risk-engine.md, V2_1_COMPLETE_ARCHITECTURE.txt
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

    # V1 Safeguards
    KILL_SWITCH = "KILL_SWITCH"
    PANIC_MODE = "PANIC_MODE"
    WEEKLY_BREAKER = "WEEKLY_BREAKER"
    GAP_FILTER = "GAP_FILTER"
    VOL_SHOCK = "VOL_SHOCK"
    TIME_GUARD = "TIME_GUARD"
    SPLIT_GUARD = "SPLIT_GUARD"

    # V2.1 Circuit Breaker Levels
    CB_DAILY_LOSS = "CB_DAILY_LOSS"  # Level 1: -2% daily
    CB_PORTFOLIO_VOL = "CB_PORTFOLIO_VOL"  # Level 3: Vol > 1.5%
    CB_CORRELATION = "CB_CORRELATION"  # Level 4: Correlation > 0.60
    CB_GREEKS_BREACH = "CB_GREEKS_BREACH"  # Level 5: Greeks breach


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
class GreeksSnapshot:
    """
    Snapshot of options Greeks for risk monitoring.

    Attributes:
        delta: Delta exposure (-1 to +1).
        gamma: Gamma (rate of delta change).
        vega: Vega (IV sensitivity).
        theta: Theta (daily time decay).
    """

    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging."""
        return {
            "delta": self.delta,
            "gamma": self.gamma,
            "vega": self.vega,
            "theta": self.theta,
        }


@dataclass
class RiskCheckResult:
    """
    Result of a risk check.

    Attributes:
        can_enter_positions: Whether new entries are allowed.
        can_enter_intraday: Whether intraday (MR) entries are allowed.
        can_enter_options: Whether options entries are allowed (V2.1).
        sizing_multiplier: Position sizing multiplier (1.0 normal, 0.5 reduced).
        exposure_multiplier: Exposure multiplier for correlation risk (V2.1).
        symbols_to_liquidate: List of symbols to liquidate immediately.
        options_to_close: List of options positions to close (V2.1 Greeks breach).
        active_safeguards: List of currently active safeguards.
        reset_cold_start: Whether to reset days_running to 0.
        circuit_breaker_level: Highest active circuit breaker level (0-5).
    """

    can_enter_positions: bool = True
    can_enter_intraday: bool = True
    can_enter_options: bool = True
    sizing_multiplier: float = 1.0
    exposure_multiplier: float = 1.0
    symbols_to_liquidate: List[str] = field(default_factory=list)
    options_to_close: List[str] = field(default_factory=list)
    active_safeguards: List[SafeguardType] = field(default_factory=list)
    reset_cold_start: bool = False
    circuit_breaker_level: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/persistence."""
        return {
            "can_enter_positions": self.can_enter_positions,
            "can_enter_intraday": self.can_enter_intraday,
            "can_enter_options": self.can_enter_options,
            "sizing_multiplier": self.sizing_multiplier,
            "exposure_multiplier": self.exposure_multiplier,
            "symbols_to_liquidate": self.symbols_to_liquidate,
            "options_to_close": self.options_to_close,
            "active_safeguards": [s.value for s in self.active_safeguards],
            "reset_cold_start": self.reset_cold_start,
            "circuit_breaker_level": self.circuit_breaker_level,
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

        # V1 Safeguard states
        self._kill_switch_active: bool = False
        self._panic_mode_active: bool = False
        self._weekly_breaker_active: bool = False
        self._gap_filter_active: bool = False
        self._vol_shock_until: Optional[datetime] = None
        self._split_frozen_symbols: Set[str] = set()

        # V2.1 Circuit Breaker states
        self._cb_daily_loss_active: bool = False
        self._cb_portfolio_vol_active: bool = False
        self._cb_correlation_active: bool = False
        self._cb_greeks_breach_active: bool = False
        self._current_circuit_breaker_level: int = 0

        # V2.1 Circuit Breaker data
        self._daily_returns: List[float] = []  # For portfolio vol calculation
        self._position_correlations: Dict[str, float] = {}  # Symbol -> correlation
        self._current_greeks: Optional[GreeksSnapshot] = None

        # Persisted state
        self._last_kill_date: Optional[str] = None

        # V1 Config parameters
        self._kill_switch_pct = config.KILL_SWITCH_PCT
        self._panic_mode_pct = config.PANIC_MODE_PCT
        self._weekly_breaker_pct = config.WEEKLY_BREAKER_PCT
        self._gap_filter_pct = config.GAP_FILTER_PCT
        self._vol_shock_atr_mult = config.VOL_SHOCK_ATR_MULT
        self._vol_shock_pause_min = config.VOL_SHOCK_PAUSE_MIN
        self._time_guard_start = self._parse_time(config.TIME_GUARD_START)
        self._time_guard_end = self._parse_time(config.TIME_GUARD_END)

        # V2.1 Circuit Breaker config parameters
        self._cb_daily_loss_threshold = config.CB_DAILY_LOSS_THRESHOLD
        self._cb_daily_size_reduction = config.CB_DAILY_SIZE_REDUCTION
        self._cb_portfolio_vol_threshold = config.CB_PORTFOLIO_VOL_THRESHOLD
        self._cb_portfolio_vol_lookback = config.CB_PORTFOLIO_VOL_LOOKBACK
        self._cb_correlation_threshold = config.CB_CORRELATION_THRESHOLD
        self._cb_correlation_reduction = config.CB_CORRELATION_REDUCTION
        self._cb_delta_max = config.CB_DELTA_MAX
        self._cb_gamma_warning = config.CB_GAMMA_WARNING
        self._cb_vega_max = config.CB_VEGA_MAX
        self._cb_theta_warning = config.CB_THETA_WARNING

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

        Combines V1 Weekly Breaker and V2.1 Level 1 Daily Loss CB.

        Returns:
            1.0 for normal sizing, 0.5 if either breaker active.
        """
        multiplier = 1.0
        if self._cb_daily_loss_active:
            multiplier = min(multiplier, self._cb_daily_size_reduction)
        if self._weekly_breaker_active:
            multiplier = min(multiplier, 0.5)
        return multiplier

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
    # V2.1 Circuit Breaker Level 1: Daily Loss (-2%)
    # =========================================================================

    def check_cb_daily_loss(self, current_equity: float) -> bool:
        """
        Check if Level 1 circuit breaker should trigger.

        This is a softer check than Kill Switch. At -2% daily loss,
        reduce sizing but don't liquidate.

        Args:
            current_equity: Current portfolio value.

        Returns:
            True if Level 1 circuit breaker is active.
        """
        if self._cb_daily_loss_active:
            return True  # Already triggered today

        # Check vs prior close (primary baseline)
        if self._equity_prior_close > 0:
            loss_from_prior = (self._equity_prior_close - current_equity) / self._equity_prior_close
            if loss_from_prior >= self._cb_daily_loss_threshold:
                self._cb_daily_loss_active = True
                self._current_circuit_breaker_level = max(self._current_circuit_breaker_level, 1)
                self.log(
                    f"CB_LEVEL_1: TRIGGERED | "
                    f"Daily loss={loss_from_prior:.2%} >= {self._cb_daily_loss_threshold:.2%} | "
                    f"Sizing reduced to {self._cb_daily_size_reduction:.0%}"
                )
                return True

        # Check vs SOD
        if self._equity_sod > 0:
            loss_from_sod = (self._equity_sod - current_equity) / self._equity_sod
            if loss_from_sod >= self._cb_daily_loss_threshold:
                self._cb_daily_loss_active = True
                self._current_circuit_breaker_level = max(self._current_circuit_breaker_level, 1)
                self.log(
                    f"CB_LEVEL_1: TRIGGERED | "
                    f"SOD loss={loss_from_sod:.2%} >= {self._cb_daily_loss_threshold:.2%} | "
                    f"Sizing reduced to {self._cb_daily_size_reduction:.0%}"
                )
                return True

        return False

    def get_cb_daily_loss_status(self) -> SafeguardStatus:
        """Get current Level 1 circuit breaker status."""
        return SafeguardStatus(
            safeguard_type=SafeguardType.CB_DAILY_LOSS,
            is_active=self._cb_daily_loss_active,
            details=f"Sizing reduced to {self._cb_daily_size_reduction:.0%}"
            if self._cb_daily_loss_active
            else "",
        )

    # =========================================================================
    # V2.1 Circuit Breaker Level 3: Portfolio Volatility (>1.5%)
    # =========================================================================

    def update_daily_return(self, daily_return: float) -> None:
        """
        Add a daily return for portfolio volatility calculation.

        Args:
            daily_return: Daily portfolio return as decimal (e.g., 0.01 = 1%).
        """
        self._daily_returns.append(daily_return)
        # Keep only lookback period
        if len(self._daily_returns) > self._cb_portfolio_vol_lookback:
            self._daily_returns = self._daily_returns[-self._cb_portfolio_vol_lookback :]

    def calculate_portfolio_volatility(self) -> float:
        """
        Calculate portfolio volatility from recent daily returns.

        Returns:
            Daily volatility as decimal (e.g., 0.015 = 1.5%).
        """
        if len(self._daily_returns) < 5:
            return 0.0  # Not enough data

        import statistics

        try:
            return statistics.stdev(self._daily_returns)
        except statistics.StatisticsError:
            return 0.0

    def check_cb_portfolio_vol(self) -> bool:
        """
        Check if Level 3 circuit breaker should trigger.

        If portfolio volatility exceeds threshold, block new entries.

        Returns:
            True if Level 3 circuit breaker is active.
        """
        if self._cb_portfolio_vol_active:
            return True  # Already triggered

        portfolio_vol = self.calculate_portfolio_volatility()
        if portfolio_vol > self._cb_portfolio_vol_threshold:
            self._cb_portfolio_vol_active = True
            self._current_circuit_breaker_level = max(self._current_circuit_breaker_level, 3)
            self.log(
                f"CB_LEVEL_3: TRIGGERED | "
                f"Portfolio vol={portfolio_vol:.4f} > {self._cb_portfolio_vol_threshold:.4f} | "
                f"New entries blocked"
            )
            return True

        # Reset if volatility drops back below threshold
        if self._cb_portfolio_vol_active and portfolio_vol < self._cb_portfolio_vol_threshold * 0.8:
            self._cb_portfolio_vol_active = False
            self.log(
                f"CB_LEVEL_3: RESET | "
                f"Portfolio vol={portfolio_vol:.4f} < {self._cb_portfolio_vol_threshold * 0.8:.4f}"
            )

        return self._cb_portfolio_vol_active

    def get_cb_portfolio_vol_status(self) -> SafeguardStatus:
        """Get current Level 3 circuit breaker status."""
        vol = self.calculate_portfolio_volatility()
        return SafeguardStatus(
            safeguard_type=SafeguardType.CB_PORTFOLIO_VOL,
            is_active=self._cb_portfolio_vol_active,
            details=f"Portfolio vol={vol:.4f}, threshold={self._cb_portfolio_vol_threshold:.4f}"
            if self._cb_portfolio_vol_active
            else f"Current vol={vol:.4f}",
        )

    # =========================================================================
    # V2.1 Circuit Breaker Level 4: Correlation (>0.60)
    # =========================================================================

    def update_correlation(self, symbol: str, correlation: float) -> None:
        """
        Update correlation data for a symbol.

        Args:
            symbol: Symbol to update.
            correlation: Correlation coefficient with portfolio (-1 to 1).
        """
        self._position_correlations[symbol] = correlation

    def check_cb_correlation(self) -> bool:
        """
        Check if Level 4 circuit breaker should trigger.

        If average correlation between positions exceeds threshold,
        reduce exposure to prevent correlated drawdowns.

        Returns:
            True if Level 4 circuit breaker is active.
        """
        if not self._position_correlations:
            return False  # No correlation data

        # Calculate average absolute correlation
        correlations = list(self._position_correlations.values())
        avg_correlation = sum(abs(c) for c in correlations) / len(correlations)

        if avg_correlation > self._cb_correlation_threshold:
            if not self._cb_correlation_active:
                self._cb_correlation_active = True
                self._current_circuit_breaker_level = max(self._current_circuit_breaker_level, 4)
                self.log(
                    f"CB_LEVEL_4: TRIGGERED | "
                    f"Avg correlation={avg_correlation:.2f} > {self._cb_correlation_threshold:.2f} | "
                    f"Exposure reduced by {1 - self._cb_correlation_reduction:.0%}"
                )
            return True

        # Reset if correlation drops
        if self._cb_correlation_active and avg_correlation < self._cb_correlation_threshold * 0.8:
            self._cb_correlation_active = False
            self.log(
                f"CB_LEVEL_4: RESET | "
                f"Avg correlation={avg_correlation:.2f} < {self._cb_correlation_threshold * 0.8:.2f}"
            )

        return self._cb_correlation_active

    def get_correlation_exposure_multiplier(self) -> float:
        """
        Get exposure multiplier based on correlation circuit breaker.

        Returns:
            1.0 if normal, reduced if Level 4 active.
        """
        if self._cb_correlation_active:
            return self._cb_correlation_reduction
        return 1.0

    def get_cb_correlation_status(self) -> SafeguardStatus:
        """Get current Level 4 circuit breaker status."""
        correlations = list(self._position_correlations.values())
        avg_corr = sum(abs(c) for c in correlations) / len(correlations) if correlations else 0.0
        return SafeguardStatus(
            safeguard_type=SafeguardType.CB_CORRELATION,
            is_active=self._cb_correlation_active,
            details=f"Avg correlation={avg_corr:.2f}, exposure reduced"
            if self._cb_correlation_active
            else f"Avg correlation={avg_corr:.2f}",
        )

    # =========================================================================
    # V2.1 Circuit Breaker Level 5: Greeks Breach (Options)
    # =========================================================================

    def update_greeks(self, greeks: GreeksSnapshot) -> None:
        """
        Update current options Greeks for risk monitoring.

        Args:
            greeks: Current Greeks snapshot.
        """
        self._current_greeks = greeks

    def check_cb_greeks_breach(self) -> Tuple[bool, List[str]]:
        """
        Check if Level 5 circuit breaker should trigger.

        Monitors options Greeks for risk breaches:
        - Delta > max threshold
        - Gamma warning near expiry
        - Vega > max threshold
        - Theta decay warning

        Returns:
            Tuple of (is_breach, list of options to close).
        """
        options_to_close: List[str] = []

        if not self._current_greeks:
            return False, options_to_close

        greeks = self._current_greeks
        breach_reasons: List[str] = []

        # Check delta breach
        if abs(greeks.delta) > self._cb_delta_max:
            breach_reasons.append(f"Delta={greeks.delta:.2f} > {self._cb_delta_max}")

        # Check gamma warning
        if abs(greeks.gamma) > self._cb_gamma_warning:
            breach_reasons.append(f"Gamma={greeks.gamma:.2f} > {self._cb_gamma_warning}")

        # Check vega breach
        if abs(greeks.vega) > self._cb_vega_max:
            breach_reasons.append(f"Vega={greeks.vega:.2f} > {self._cb_vega_max}")

        # Check theta warning
        if greeks.theta < self._cb_theta_warning:
            breach_reasons.append(f"Theta={greeks.theta:.2f} < {self._cb_theta_warning}")

        if breach_reasons:
            if not self._cb_greeks_breach_active:
                self._cb_greeks_breach_active = True
                self._current_circuit_breaker_level = max(self._current_circuit_breaker_level, 5)
                self.log(
                    f"CB_LEVEL_5: TRIGGERED | "
                    f"Greeks breach: {', '.join(breach_reasons)} | "
                    f"Close options positions"
                )
            # When Greeks breach, close all options (caller determines specifics)
            options_to_close.append("ALL_OPTIONS")
            return True, options_to_close

        # Reset if all Greeks are within limits
        if self._cb_greeks_breach_active:
            self._cb_greeks_breach_active = False
            self.log("CB_LEVEL_5: RESET | All Greeks within limits")

        return False, options_to_close

    def get_cb_greeks_status(self) -> SafeguardStatus:
        """Get current Level 5 circuit breaker status."""
        if self._current_greeks:
            greeks = self._current_greeks
            details = (
                f"D={greeks.delta:.2f}, G={greeks.gamma:.2f}, "
                f"V={greeks.vega:.2f}, T={greeks.theta:.2f}"
            )
        else:
            details = "No Greeks data"
        return SafeguardStatus(
            safeguard_type=SafeguardType.CB_GREEKS_BREACH,
            is_active=self._cb_greeks_breach_active,
            details=details,
        )

    def get_current_circuit_breaker_level(self) -> int:
        """Get the highest active circuit breaker level (0-5)."""
        return self._current_circuit_breaker_level

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

        Checks are run in priority order:
        1. V1 Kill Switch (nuclear - liquidate ALL)
        2. V1 Panic Mode (SPY -4%)
        3. V2.1 Circuit Breaker Level 1 (Daily -2%)
        4. V2.1 Circuit Breaker Level 2 / V1 Weekly Breaker (-5% WTD)
        5. V2.1 Circuit Breaker Level 3 (Portfolio Vol)
        6. V2.1 Circuit Breaker Level 4 (Correlation)
        7. V2.1 Circuit Breaker Level 5 (Greeks Breach)
        8. V1 Vol Shock (3× ATR bar)
        9. V1 Gap Filter
        10. V1 Time Guard

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

        # 1. Kill Switch (highest priority - V1 nuclear option)
        if self.check_kill_switch(current_equity):
            result.can_enter_positions = False
            result.can_enter_intraday = False
            result.can_enter_options = False
            result.symbols_to_liquidate = ALL_TRADED_SYMBOLS.copy()
            result.reset_cold_start = True
            result.circuit_breaker_level = 5  # Treat as max level
            active_safeguards.append(SafeguardType.KILL_SWITCH)
            result.active_safeguards = active_safeguards
            return result  # Kill switch overrides everything

        # 2. Panic Mode (V1 - SPY -4%)
        if self.check_panic_mode(spy_price):
            result.can_enter_positions = False
            result.can_enter_intraday = False
            result.can_enter_options = False
            result.symbols_to_liquidate = LEVERAGED_LONG_SYMBOLS.copy()
            active_safeguards.append(SafeguardType.PANIC_MODE)

        # 3. V2.1 Circuit Breaker Level 1: Daily Loss (-2%)
        if self.check_cb_daily_loss(current_equity):
            # Level 1: Reduce sizing, don't liquidate
            result.sizing_multiplier = min(result.sizing_multiplier, self._cb_daily_size_reduction)
            result.circuit_breaker_level = max(result.circuit_breaker_level, 1)
            active_safeguards.append(SafeguardType.CB_DAILY_LOSS)

        # 4. Weekly Breaker / V2.1 Circuit Breaker Level 2
        if self.check_weekly_breaker(current_equity):
            result.sizing_multiplier = min(result.sizing_multiplier, 0.5)
            result.circuit_breaker_level = max(result.circuit_breaker_level, 2)
            active_safeguards.append(SafeguardType.WEEKLY_BREAKER)

        # 5. V2.1 Circuit Breaker Level 3: Portfolio Volatility
        if self.check_cb_portfolio_vol():
            result.can_enter_positions = False
            result.can_enter_intraday = False
            result.can_enter_options = False
            result.circuit_breaker_level = max(result.circuit_breaker_level, 3)
            active_safeguards.append(SafeguardType.CB_PORTFOLIO_VOL)

        # 6. V2.1 Circuit Breaker Level 4: Correlation
        if self.check_cb_correlation():
            result.exposure_multiplier = self.get_correlation_exposure_multiplier()
            result.circuit_breaker_level = max(result.circuit_breaker_level, 4)
            active_safeguards.append(SafeguardType.CB_CORRELATION)

        # 7. V2.1 Circuit Breaker Level 5: Greeks Breach
        greeks_breach, options_to_close = self.check_cb_greeks_breach()
        if greeks_breach:
            result.can_enter_options = False
            result.options_to_close = options_to_close
            result.circuit_breaker_level = max(result.circuit_breaker_level, 5)
            active_safeguards.append(SafeguardType.CB_GREEKS_BREACH)

        # 8. Vol Shock (V1)
        if self.check_vol_shock(spy_bar_range, current_time):
            result.can_enter_positions = False
            result.can_enter_intraday = False
            active_safeguards.append(SafeguardType.VOL_SHOCK)

        # 9. Gap Filter (V1 - already set earlier in the day)
        if self._gap_filter_active:
            result.can_enter_intraday = False
            active_safeguards.append(SafeguardType.GAP_FILTER)

        # 10. Time Guard (V1)
        if self.is_time_guard_active(current_time):
            result.can_enter_positions = False
            result.can_enter_intraday = False
            active_safeguards.append(SafeguardType.TIME_GUARD)

        # 11. Split Guard (handled per-symbol, not in combined check)

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
        # V1 safeguard resets
        self._kill_switch_active = False
        self._panic_mode_active = False
        self._gap_filter_active = False
        self._vol_shock_until = None
        self._split_frozen_symbols.clear()

        # V2.1 Circuit Breaker resets (daily)
        self._cb_daily_loss_active = False
        self._cb_greeks_breach_active = False
        self._current_circuit_breaker_level = 0
        # Note: Portfolio vol and correlation persist across days

        # Reset baselines (will be set by scheduling)
        self._equity_prior_close = 0.0
        self._equity_sod = 0.0
        self._spy_prior_close = 0.0
        self._spy_open = 0.0

        self.log("RISK: Daily state reset (V1 safeguards + V2.1 daily CBs)")

    # =========================================================================
    # State Persistence
    # =========================================================================

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """
        Get state dict for persistence.

        Returns:
            Dict with all state that should be persisted.
        """
        # CRITICAL FIX: Persist Greeks state for risk monitoring continuity
        greeks_data = None
        if self._current_greeks is not None:
            greeks_data = {
                "delta": self._current_greeks.delta,
                "gamma": self._current_greeks.gamma,
                "vega": self._current_greeks.vega,
                "theta": self._current_greeks.theta,
            }

        return {
            # V1 state
            "last_kill_date": self._last_kill_date,
            "week_start_equity": self._week_start_equity,
            "weekly_breaker_active": self._weekly_breaker_active,
            # V2.1 state
            "daily_returns": self._daily_returns,
            "position_correlations": self._position_correlations,
            "cb_portfolio_vol_active": self._cb_portfolio_vol_active,
            "cb_correlation_active": self._cb_correlation_active,
            # CRITICAL: Greeks state for Level 5 CB continuity
            "current_greeks": greeks_data,
            "cb_greeks_breach_active": self._cb_greeks_breach_active,
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """
        Load state from persistence.

        Args:
            state: Previously saved state dict.
        """
        # V1 state
        self._last_kill_date = state.get("last_kill_date")
        self._week_start_equity = state.get("week_start_equity", 0.0)
        self._weekly_breaker_active = state.get("weekly_breaker_active", False)

        # V2.1 state
        self._daily_returns = state.get("daily_returns", [])
        self._position_correlations = state.get("position_correlations", {})
        self._cb_portfolio_vol_active = state.get("cb_portfolio_vol_active", False)
        self._cb_correlation_active = state.get("cb_correlation_active", False)

        # CRITICAL FIX: Restore Greeks state for Level 5 CB continuity
        # Without this, Greeks monitoring is disabled for first few minutes after restart
        greeks_data = state.get("current_greeks")
        if greeks_data is not None:
            self._current_greeks = GreeksSnapshot(
                delta=greeks_data.get("delta", 0.0),
                gamma=greeks_data.get("gamma", 0.0),
                vega=greeks_data.get("vega", 0.0),
                theta=greeks_data.get("theta", 0.0),
            )
        else:
            self._current_greeks = None

        self._cb_greeks_breach_active = state.get("cb_greeks_breach_active", False)

        self.log(
            f"RISK: State loaded | "
            f"last_kill_date={self._last_kill_date} | "
            f"week_start_equity=${self._week_start_equity:,.2f} | "
            f"weekly_breaker={self._weekly_breaker_active} | "
            f"portfolio_vol_cb={self._cb_portfolio_vol_active} | "
            f"correlation_cb={self._cb_correlation_active} | "
            f"greeks_restored={'Yes' if self._current_greeks else 'No'}"
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
            # V1 Safeguards
            "kill_switch": self.get_kill_switch_status(),
            "panic_mode": self.get_panic_mode_status(),
            "weekly_breaker": self.get_weekly_breaker_status(),
            "gap_filter": self.get_gap_filter_status(),
            "vol_shock": self.get_vol_shock_status(current_time),
            "time_guard": self.get_time_guard_status(current_time),
            "split_guard": self.get_split_guard_status(),
            # V2.1 Circuit Breaker Levels
            "cb_daily_loss": self.get_cb_daily_loss_status(),
            "cb_portfolio_vol": self.get_cb_portfolio_vol_status(),
            "cb_correlation": self.get_cb_correlation_status(),
            "cb_greeks": self.get_cb_greeks_status(),
        }

    def can_enter_options(self, current_time: datetime) -> bool:
        """
        Quick check if options entries are allowed.

        Args:
            current_time: Current algorithm time.

        Returns:
            True if options entries are allowed.
        """
        if not self.can_enter_new_positions(current_time):
            return False
        if self._cb_portfolio_vol_active:
            return False
        if self._cb_greeks_breach_active:
            return False
        return True
