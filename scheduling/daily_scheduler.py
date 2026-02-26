"""
Daily Scheduler - Orchestrates all scheduled events throughout the trading day.

Manages the complete daily timeline from pre-market through post-market:
- Pre-market setup (09:25)
- Market open processing (09:30, 09:31, 09:33)
- Cold start warm entry (10:00)
- Time guard (13:55 - 14:10)
- MR force close (15:45)
- EOD processing (15:45)
- State persistence (16:00)

Key Responsibilities:
1. Register scheduled events with QuantConnect
2. Track system state throughout the day
3. Coordinate event callbacks
4. Manage MR entry window and time guard

Spec: docs/14-daily-operations.md
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config


class SystemState(Enum):
    """
    Daily system state machine states.

    Transitions:
    PRE_MARKET -> MARKET_OPEN (09:30)
    MARKET_OPEN -> TRADING (09:33)
    TRADING -> TIME_GUARD (13:55)
    TIME_GUARD -> TRADING (14:10)
    TRADING -> EOD_PROCESSING (15:45)
    EOD_PROCESSING -> MARKET_CLOSED (16:00)
    MARKET_CLOSED -> PRE_MARKET (next day 09:00)
    """

    PRE_MARKET = "PRE_MARKET"
    MARKET_OPEN = "MARKET_OPEN"
    TRADING = "TRADING"
    TIME_GUARD = "TIME_GUARD"
    EOD_PROCESSING = "EOD_PROCESSING"
    MARKET_CLOSED = "MARKET_CLOSED"

    # Emergency states
    KILL_SWITCH = "KILL_SWITCH"
    PANIC_MODE = "PANIC_MODE"


class ScheduledEvent(Enum):
    """Scheduled event identifiers."""

    PRE_MARKET_SETUP = "PRE_MARKET_SETUP"
    MOO_FALLBACK = "MOO_FALLBACK"
    SOD_BASELINE = "SOD_BASELINE"
    WARM_ENTRY_CHECK = "WARM_ENTRY_CHECK"
    TIME_GUARD_START = "TIME_GUARD_START"
    TIME_GUARD_END = "TIME_GUARD_END"
    MR_FORCE_CLOSE = "MR_FORCE_CLOSE"
    EOD_PROCESSING = "EOD_PROCESSING"
    MARKET_CLOSE = "MARKET_CLOSE"
    WEEKLY_RESET = "WEEKLY_RESET"


@dataclass
class EventConfig:
    """Configuration for a scheduled event."""

    event: ScheduledEvent
    hour: int
    minute: int
    description: str
    days: str = "EveryDay"  # "EveryDay" or "Monday"


# Scheduled event configurations
# NOTE: EOD events (MR_FORCE_CLOSE, EOD_PROCESSING, MARKET_CLOSE) are scheduled
# dynamically in schedule_dynamic_eod_events() to handle early close days.
SCHEDULED_EVENTS: List[EventConfig] = [
    EventConfig(ScheduledEvent.PRE_MARKET_SETUP, 9, 25, "Set equity_prior_close baseline"),
    EventConfig(ScheduledEvent.MOO_FALLBACK, 9, 31, "Check MOO orders, execute fallbacks"),
    EventConfig(ScheduledEvent.SOD_BASELINE, 9, 33, "Set equity_sod, check gap filter"),
    EventConfig(ScheduledEvent.WARM_ENTRY_CHECK, 10, 0, "Cold start warm entry check"),
    EventConfig(ScheduledEvent.TIME_GUARD_START, 13, 55, "Block all entries"),
    EventConfig(ScheduledEvent.TIME_GUARD_END, 14, 10, "Resume entries"),
    # MR_FORCE_CLOSE - scheduled dynamically (market_close - 15 min)
    # EOD_PROCESSING - scheduled dynamically (market_close - 15 min)
    # MARKET_CLOSE - scheduled dynamically (at market_close)
    EventConfig(ScheduledEvent.WEEKLY_RESET, 9, 30, "Reset weekly breaker", "Monday"),
]


class DailyScheduler:
    """
    Orchestrates all scheduled events throughout the trading day.

    Manages:
    - Scheduled event registration
    - System state tracking
    - Event callback coordination
    - MR window and time guard status
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """
        Initialize DailyScheduler.

        Args:
            algorithm: QuantConnect algorithm instance (None for testing).
        """
        self.algorithm = algorithm
        self._state = SystemState.PRE_MARKET
        self._callbacks: Dict[ScheduledEvent, List[Callable[[], None]]] = {
            event: [] for event in ScheduledEvent
        }
        self._events_fired_today: List[ScheduledEvent] = []
        self._kill_switch_triggered = False
        self._panic_mode_triggered = False

        # Parse time strings from config
        self._time_guard_start = self._parse_time(config.TIME_GUARD_START)
        self._time_guard_end = self._parse_time(config.TIME_GUARD_END)
        self._mr_entry_close = (15, 0)  # MR entries close at 15:00
        self._mr_force_close = (15, 45)  # MR positions force close at 15:45
        # Default intraday options close (overridden by dynamic scheduling on early-close days).
        self._intraday_opt_close = self._parse_time(
            str(getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15"))
        )

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)  # type: ignore[attr-defined]

    def _parse_time(self, time_str: str) -> Tuple[int, int]:
        """Parse 'HH:MM' string into (hour, minute) tuple."""
        parts = time_str.split(":")
        return (int(parts[0]), int(parts[1]))

    def _time_to_minutes(self, hour: int, minute: int) -> int:
        """Convert hour:minute to total minutes since midnight."""
        return hour * 60 + minute

    def _get_current_time(self) -> Tuple[int, int]:
        """Get current time as (hour, minute) tuple."""
        if self.algorithm:
            t = self.algorithm.Time  # type: ignore[attr-defined]
            return (t.hour, t.minute)
        return (0, 0)

    def get_intraday_options_close_hhmm(self) -> Tuple[int, int]:
        """Return effective intraday options close cutoff (dynamic on early-close days)."""
        try:
            hh, mm = self._intraday_opt_close
            return int(hh), int(mm)
        except Exception:
            return self._parse_time(str(getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15")))

    def set_intraday_options_close_hhmm(self, hour: int, minute: int) -> None:
        """Update effective intraday options close cutoff for the current session."""
        try:
            hh = max(0, min(23, int(hour)))
            mm = max(0, min(59, int(minute)))
            self._intraday_opt_close = (hh, mm)
        except Exception:
            self._intraday_opt_close = self._parse_time(
                str(getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15"))
            )

    # =========================================================================
    # Event Registration
    # =========================================================================

    def register_events(self) -> None:
        """
        Register all scheduled events with QuantConnect.

        Should be called in Initialize().
        """
        if not self.algorithm:
            self.log("SCHEDULER: No algorithm, skipping event registration")
            return

        for event_config in SCHEDULED_EVENTS:
            self._register_event(event_config)

        # Registration logging disabled to save log space

    def _register_event(self, event_config: EventConfig) -> None:
        """Register a single scheduled event."""
        if not self.algorithm:
            return

        event = event_config.event

        # Create the callback function
        def make_callback(e: ScheduledEvent) -> Callable[[], None]:
            def callback() -> None:
                self._on_event(e)

            return callback

        callback = make_callback(event)

        # Determine date rule
        if event_config.days == "Monday":
            date_rule = self.algorithm.DateRules.Every(0)  # type: ignore[attr-defined] # Monday = 0
        else:
            date_rule = self.algorithm.DateRules.EveryDay()  # type: ignore[attr-defined]

        time_rule = self.algorithm.TimeRules.At(  # type: ignore[attr-defined]
            event_config.hour, event_config.minute
        )

        self.algorithm.Schedule.On(date_rule, time_rule, callback)  # type: ignore[attr-defined]
        # Registration logging disabled to save log space

    def schedule_dynamic_eod_events(self, market_close: datetime) -> None:
        """
        V3.0: Schedule EOD events dynamically based on actual market close time.

        Handles early close days (1:00 PM) by calculating EOD times relative
        to the actual market close rather than using fixed 15:45/16:00 times.

        Args:
            market_close: The actual market close time for today.

        Events scheduled:
            - MR_FORCE_CLOSE: market_close - EOD_OFFSET_MINUTES (default 15)
            - EOD_PROCESSING: market_close - EOD_OFFSET_MINUTES (default 15)
            - MARKET_CLOSE: at market_close
        """
        if not self.algorithm:
            self.log("SCHEDULER: No algorithm, skipping dynamic EOD scheduling")
            return

        # Get offset from config (default 15 minutes)
        eod_offset = getattr(config, "EOD_OFFSET_MINUTES", 15)
        intraday_opt_offset = getattr(config, "INTRADAY_OPTIONS_OFFSET_MINUTES", 45)

        # Calculate dynamic times
        eod_time = market_close - timedelta(minutes=eod_offset)
        opt_close_time = market_close - timedelta(minutes=intraday_opt_offset)

        # Store for time-based checks in other methods
        self._mr_force_close = (eod_time.hour, eod_time.minute)
        self._dynamic_market_close = (market_close.hour, market_close.minute)

        # Check if this is an early close day
        is_early_close = market_close.hour < 16
        close_type = "EARLY" if is_early_close else "NORMAL"

        self.log(
            f"EOD_SCHEDULE: {close_type} close at {market_close.strftime('%H:%M')} | "
            f"MR/EOD={eod_time.strftime('%H:%M')} | OptClose={opt_close_time.strftime('%H:%M')}"
        )

        # Get today's date for DateRules.On
        today = self.algorithm.Time  # type: ignore[attr-defined]

        # Create callback wrappers
        def mr_force_close_callback() -> None:
            self._on_event(ScheduledEvent.MR_FORCE_CLOSE)

        def eod_processing_callback() -> None:
            self._on_event(ScheduledEvent.EOD_PROCESSING)

        def market_close_callback() -> None:
            self._on_event(ScheduledEvent.MARKET_CLOSE)

        # Schedule MR_FORCE_CLOSE (market_close - offset)
        self.algorithm.Schedule.On(  # type: ignore[attr-defined]
            self.algorithm.DateRules.On(today.year, today.month, today.day),  # type: ignore[attr-defined]
            self.algorithm.TimeRules.At(eod_time.hour, eod_time.minute),  # type: ignore[attr-defined]
            mr_force_close_callback,
        )

        # Schedule EOD_PROCESSING (market_close - offset)
        self.algorithm.Schedule.On(  # type: ignore[attr-defined]
            self.algorithm.DateRules.On(today.year, today.month, today.day),  # type: ignore[attr-defined]
            self.algorithm.TimeRules.At(eod_time.hour, eod_time.minute),  # type: ignore[attr-defined]
            eod_processing_callback,
        )

        # Schedule MARKET_CLOSE (at market_close)
        self.algorithm.Schedule.On(  # type: ignore[attr-defined]
            self.algorithm.DateRules.On(today.year, today.month, today.day),  # type: ignore[attr-defined]
            self.algorithm.TimeRules.At(market_close.hour, market_close.minute),  # type: ignore[attr-defined]
            market_close_callback,
        )

        # Also schedule intraday options force close dynamically
        # This is handled separately in main.py but we store the time for reference
        self._intraday_opt_close = (opt_close_time.hour, opt_close_time.minute)

    def _on_event(self, event: ScheduledEvent) -> None:
        """Internal handler called when a scheduled event fires."""
        self._events_fired_today.append(event)
        # Event logging disabled to save log space

        # Update state based on event
        self._update_state_for_event(event)

        # Call registered callbacks
        for callback in self._callbacks.get(event, []):
            try:
                callback()
            except Exception as e:
                self.log(f"SCHEDULER: Callback error for {event.value}: {e}")

    def _update_state_for_event(self, event: ScheduledEvent) -> None:
        """Update system state based on event."""
        if self._kill_switch_triggered:
            self._state = SystemState.KILL_SWITCH
            return

        if event == ScheduledEvent.PRE_MARKET_SETUP:
            self._state = SystemState.PRE_MARKET
        elif event == ScheduledEvent.SOD_BASELINE:
            self._state = SystemState.TRADING
        elif event == ScheduledEvent.TIME_GUARD_START:
            self._state = SystemState.TIME_GUARD
        elif event == ScheduledEvent.TIME_GUARD_END:
            self._state = SystemState.TRADING
        elif event == ScheduledEvent.EOD_PROCESSING:
            self._state = SystemState.EOD_PROCESSING
        elif event == ScheduledEvent.MARKET_CLOSE:
            self._state = SystemState.MARKET_CLOSED

    # =========================================================================
    # Callback Registration
    # =========================================================================

    def on_pre_market_setup(self, callback: Callable[[], None]) -> None:
        """Register callback for pre-market setup (09:25)."""
        self._callbacks[ScheduledEvent.PRE_MARKET_SETUP].append(callback)

    def on_moo_fallback(self, callback: Callable[[], None]) -> None:
        """Register callback for MOO fallback check (09:31)."""
        self._callbacks[ScheduledEvent.MOO_FALLBACK].append(callback)

    def on_sod_baseline(self, callback: Callable[[], None]) -> None:
        """Register callback for SOD baseline (09:33)."""
        self._callbacks[ScheduledEvent.SOD_BASELINE].append(callback)

    def on_warm_entry_check(self, callback: Callable[[], None]) -> None:
        """Register callback for warm entry check (10:00)."""
        self._callbacks[ScheduledEvent.WARM_ENTRY_CHECK].append(callback)

    def on_time_guard_start(self, callback: Callable[[], None]) -> None:
        """Register callback for time guard start (13:55)."""
        self._callbacks[ScheduledEvent.TIME_GUARD_START].append(callback)

    def on_time_guard_end(self, callback: Callable[[], None]) -> None:
        """Register callback for time guard end (14:10)."""
        self._callbacks[ScheduledEvent.TIME_GUARD_END].append(callback)

    def on_mr_force_close(self, callback: Callable[[], None]) -> None:
        """Register callback for MR force close (15:45)."""
        self._callbacks[ScheduledEvent.MR_FORCE_CLOSE].append(callback)

    def on_eod_processing(self, callback: Callable[[], None]) -> None:
        """Register callback for EOD processing (15:45)."""
        self._callbacks[ScheduledEvent.EOD_PROCESSING].append(callback)

    def on_market_close(self, callback: Callable[[], None]) -> None:
        """Register callback for market close (16:00)."""
        self._callbacks[ScheduledEvent.MARKET_CLOSE].append(callback)

    def on_weekly_reset(self, callback: Callable[[], None]) -> None:
        """Register callback for weekly reset (Monday 09:30)."""
        self._callbacks[ScheduledEvent.WEEKLY_RESET].append(callback)

    # =========================================================================
    # State Queries
    # =========================================================================

    def get_state(self) -> SystemState:
        """Get current system state."""
        return self._state

    def is_trading(self) -> bool:
        """Check if in active trading state."""
        return self._state == SystemState.TRADING

    def is_time_guard_active(self) -> bool:
        """
        Check if time guard is currently active.

        Time guard blocks entries from 13:55 to 14:10 ET.
        """
        if self._state == SystemState.TIME_GUARD:
            return True

        # Also check by time in case state wasn't updated
        current = self._get_current_time()
        current_minutes = self._time_to_minutes(current[0], current[1])
        start_minutes = self._time_to_minutes(self._time_guard_start[0], self._time_guard_start[1])
        end_minutes = self._time_to_minutes(self._time_guard_end[0], self._time_guard_end[1])

        return start_minutes <= current_minutes < end_minutes

    def is_mr_entry_window_open(self) -> bool:
        """
        Check if MR entry window is open.

        MR entries allowed: 10:00 - 15:00 ET
        Blocked during time guard: 13:55 - 14:10 ET
        """
        if self._state in (
            SystemState.KILL_SWITCH,
            SystemState.EOD_PROCESSING,
            SystemState.MARKET_CLOSED,
            SystemState.PRE_MARKET,
        ):
            return False

        if self.is_time_guard_active():
            return False

        current = self._get_current_time()
        current_minutes = self._time_to_minutes(current[0], current[1])
        start_minutes = self._time_to_minutes(10, 0)
        end_minutes = self._time_to_minutes(15, 0)

        return start_minutes <= current_minutes < end_minutes

    def is_mr_exit_only(self) -> bool:
        """
        Check if MR is in exit-only mode.

        From 15:00 - 15:45, only exits are allowed (no new entries).
        """
        current = self._get_current_time()
        current_minutes = self._time_to_minutes(current[0], current[1])

        # Exit only from 15:00 to 15:45
        exit_only_start = self._time_to_minutes(15, 0)
        exit_only_end = self._time_to_minutes(15, 45)

        return exit_only_start <= current_minutes < exit_only_end

    def can_enter_new_positions(self) -> bool:
        """
        Check if new position entries are allowed.

        Blocked by:
        - Kill switch
        - Time guard
        - Outside MR window (for MR)
        - After 15:00 for MR
        """
        if self._state in (
            SystemState.KILL_SWITCH,
            SystemState.TIME_GUARD,
            SystemState.EOD_PROCESSING,
            SystemState.MARKET_CLOSED,
            SystemState.PRE_MARKET,
        ):
            return False

        return True

    def should_force_close_mr(self) -> bool:
        """
        Check if MR positions should be force closed.

        V3.0: Uses dynamic time from schedule_dynamic_eod_events() to handle
        early close days. Falls back to 15:45 if dynamic scheduling hasn't run.
        """
        current = self._get_current_time()
        current_minutes = self._time_to_minutes(current[0], current[1])
        # Use dynamic time if set, otherwise fall back to 15:45
        force_close_time = getattr(self, "_mr_force_close", (15, 45))
        force_close_minutes = self._time_to_minutes(force_close_time[0], force_close_time[1])

        return current_minutes >= force_close_minutes

    def is_eod_processing_time(self) -> bool:
        """Check if it's EOD processing time (15:45+)."""
        return self._state == SystemState.EOD_PROCESSING

    def is_market_closed(self) -> bool:
        """Check if market is closed."""
        return self._state == SystemState.MARKET_CLOSED

    # =========================================================================
    # Emergency State Management
    # =========================================================================

    def trigger_kill_switch(self) -> None:
        """Trigger kill switch state (disables all trading)."""
        self._kill_switch_triggered = True
        self._state = SystemState.KILL_SWITCH
        # Logging done in main.py

    def trigger_panic_mode(self) -> None:
        """Trigger panic mode (liquidate longs, keep hedges)."""
        self._panic_mode_triggered = True
        # Don't change to PANIC_MODE state - stay in TRADING but track flag
        # Logging done in main.py

    def is_kill_switch_triggered(self) -> bool:
        """Check if kill switch has been triggered today."""
        return self._kill_switch_triggered

    def is_panic_mode_triggered(self) -> bool:
        """Check if panic mode has been triggered today."""
        return self._panic_mode_triggered

    def clear_panic_mode(self) -> None:
        """Clear panic mode flag (positions liquidated)."""
        self._panic_mode_triggered = False

    # =========================================================================
    # Day Management
    # =========================================================================

    def reset_daily(self) -> None:
        """
        Reset daily state.

        Called at start of new trading day or in testing.
        """
        self._state = SystemState.PRE_MARKET
        self._events_fired_today.clear()
        self._kill_switch_triggered = False
        self._panic_mode_triggered = False
        # Reset intraday options cutoff to config default at day boundary.
        # Dynamic early-close refresh may fail on a given day; this prevents
        # stale carryover of prior session's cutoff.
        self._intraday_opt_close = self._parse_time(
            str(getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15"))
        )

    def get_events_fired_today(self) -> List[ScheduledEvent]:
        """Get list of events fired today."""
        return self._events_fired_today.copy()

    def has_event_fired(self, event: ScheduledEvent) -> bool:
        """Check if a specific event has fired today."""
        return event in self._events_fired_today

    # =========================================================================
    # Testing Support
    # =========================================================================

    def fire_event_for_testing(self, event: ScheduledEvent) -> None:
        """
        Manually fire an event (for testing).

        Args:
            event: Event to fire.
        """
        self._on_event(event)

    def set_state_for_testing(self, state: SystemState) -> None:
        """
        Set system state directly (for testing).

        Args:
            state: State to set.
        """
        self._state = state

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """Get scheduler statistics."""
        return {
            "state": self._state.value,
            "events_fired_today": [e.value for e in self._events_fired_today],
            "kill_switch_triggered": self._kill_switch_triggered,
            "panic_mode_triggered": self._panic_mode_triggered,
            "is_trading": self.is_trading(),
            "is_time_guard_active": self.is_time_guard_active(),
            "is_mr_entry_window_open": self.is_mr_entry_window_open(),
            "can_enter_new_positions": self.can_enter_new_positions(),
        }

    def get_day_summary(
        self,
        starting_equity: float,
        ending_equity: float,
        trades: List[str],
        safeguards: List[str],
        moo_orders: List[str],
        regime_score: float,
        regime_state: str,
        days_running: int,
    ) -> str:
        """
        Generate daily summary log message.

        Args:
            starting_equity: Equity at start of day.
            ending_equity: Equity at end of day.
            trades: List of trade descriptions.
            safeguards: List of safeguards triggered.
            moo_orders: List of MOO orders submitted.
            regime_score: Final regime score.
            regime_state: Final regime state name.
            days_running: Cold start days running.

        Returns:
            Formatted daily summary string.
        """
        pnl = ending_equity - starting_equity
        pnl_pct = (pnl / starting_equity * 100) if starting_equity > 0 else 0

        lines = [
            "=" * 60,
            f"DAILY SUMMARY - {self._get_date_string()}",
            "=" * 60,
            f"Starting Equity:  ${starting_equity:,.2f}",
            f"Ending Equity:    ${ending_equity:,.2f}",
            f"Daily P&L:        {'+' if pnl >= 0 else ''}${pnl:,.2f} ({pnl_pct:+.2f}%)",
            "",
            f"Regime Score:     {regime_score:.0f} ({regime_state})",
            f"Days Running:     {days_running}",
            "",
            "Trades Executed:",
        ]

        if trades:
            for trade in trades:
                lines.append(f"  - {trade}")
        else:
            lines.append("  - None")

        lines.append("")
        lines.append("Safeguards Triggered:")
        if safeguards:
            for safeguard in safeguards:
                lines.append(f"  - {safeguard}")
        else:
            lines.append("  - None")

        lines.append("")
        lines.append("MOO Orders for Tomorrow:")
        if moo_orders:
            for order in moo_orders:
                lines.append(f"  - {order}")
        else:
            lines.append("  - None")

        lines.append("=" * 60)

        return "\n".join(lines)

    def _get_date_string(self) -> str:
        """Get current date as string."""
        if self.algorithm:
            return self.algorithm.Time.strftime("%Y-%m-%d")  # type: ignore[attr-defined]
        return "TEST-DATE"
