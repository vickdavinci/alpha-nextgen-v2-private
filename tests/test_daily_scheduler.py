"""
Unit tests for Daily Scheduler.

Tests scheduled event orchestration:
- Event registration and callbacks
- System state transitions
- Time guard management
- MR entry window logic
- Kill switch and panic mode
- Daily summary generation

Spec: docs/14-daily-operations.md
"""

import pytest

from scheduling.daily_scheduler import (
    SCHEDULED_EVENTS,
    DailyScheduler,
    EventConfig,
    ScheduledEvent,
    SystemState,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def scheduler() -> DailyScheduler:
    """Create DailyScheduler without algorithm (testing mode)."""
    return DailyScheduler(algorithm=None)


# =============================================================================
# Initialization Tests
# =============================================================================


class TestInitialization:
    """Tests for scheduler initialization."""

    def test_initial_state_is_pre_market(self, scheduler: DailyScheduler) -> None:
        """Test that scheduler starts in PRE_MARKET state."""
        assert scheduler.get_state() == SystemState.PRE_MARKET

    def test_no_events_fired_initially(self, scheduler: DailyScheduler) -> None:
        """Test that no events have fired initially."""
        assert len(scheduler.get_events_fired_today()) == 0

    def test_kill_switch_not_triggered_initially(self, scheduler: DailyScheduler) -> None:
        """Test that kill switch is not triggered initially."""
        assert scheduler.is_kill_switch_triggered() is False

    def test_panic_mode_not_triggered_initially(self, scheduler: DailyScheduler) -> None:
        """Test that panic mode is not triggered initially."""
        assert scheduler.is_panic_mode_triggered() is False


# =============================================================================
# Event Configuration Tests
# =============================================================================


class TestEventConfiguration:
    """Tests for scheduled event configuration."""

    def test_all_events_have_configuration(self) -> None:
        """Test that all expected events are configured."""
        configured_events = {e.event for e in SCHEDULED_EVENTS}

        # Key events that must be configured
        required_events = {
            ScheduledEvent.PRE_MARKET_SETUP,
            ScheduledEvent.MOO_FALLBACK,
            ScheduledEvent.SOD_BASELINE,
            ScheduledEvent.WARM_ENTRY_CHECK,
            ScheduledEvent.TIME_GUARD_START,
            ScheduledEvent.TIME_GUARD_END,
            ScheduledEvent.MR_FORCE_CLOSE,
            ScheduledEvent.EOD_PROCESSING,
            ScheduledEvent.MARKET_CLOSE,
        }

        for event in required_events:
            assert event in configured_events, f"Missing configuration for {event.value}"

    def test_pre_market_setup_at_0925(self) -> None:
        """Test pre-market setup is at 09:25."""
        config = next(e for e in SCHEDULED_EVENTS if e.event == ScheduledEvent.PRE_MARKET_SETUP)
        assert config.hour == 9
        assert config.minute == 25

    def test_time_guard_start_at_1355(self) -> None:
        """Test time guard starts at 13:55."""
        config = next(e for e in SCHEDULED_EVENTS if e.event == ScheduledEvent.TIME_GUARD_START)
        assert config.hour == 13
        assert config.minute == 55

    def test_time_guard_end_at_1410(self) -> None:
        """Test time guard ends at 14:10."""
        config = next(e for e in SCHEDULED_EVENTS if e.event == ScheduledEvent.TIME_GUARD_END)
        assert config.hour == 14
        assert config.minute == 10

    def test_eod_processing_at_1545(self) -> None:
        """Test EOD processing is at 15:45."""
        config = next(e for e in SCHEDULED_EVENTS if e.event == ScheduledEvent.EOD_PROCESSING)
        assert config.hour == 15
        assert config.minute == 45


# =============================================================================
# State Transition Tests
# =============================================================================


class TestStateTransitions:
    """Tests for system state transitions."""

    def test_sod_baseline_transitions_to_trading(self, scheduler: DailyScheduler) -> None:
        """Test that SOD baseline event transitions to TRADING state."""
        scheduler.fire_event_for_testing(ScheduledEvent.SOD_BASELINE)
        assert scheduler.get_state() == SystemState.TRADING

    def test_time_guard_start_transitions_to_time_guard(self, scheduler: DailyScheduler) -> None:
        """Test that time guard start transitions to TIME_GUARD state."""
        scheduler.set_state_for_testing(SystemState.TRADING)
        scheduler.fire_event_for_testing(ScheduledEvent.TIME_GUARD_START)
        assert scheduler.get_state() == SystemState.TIME_GUARD

    def test_time_guard_end_transitions_to_trading(self, scheduler: DailyScheduler) -> None:
        """Test that time guard end transitions back to TRADING state."""
        scheduler.set_state_for_testing(SystemState.TIME_GUARD)
        scheduler.fire_event_for_testing(ScheduledEvent.TIME_GUARD_END)
        assert scheduler.get_state() == SystemState.TRADING

    def test_eod_processing_transitions_to_eod(self, scheduler: DailyScheduler) -> None:
        """Test that EOD processing transitions to EOD_PROCESSING state."""
        scheduler.set_state_for_testing(SystemState.TRADING)
        scheduler.fire_event_for_testing(ScheduledEvent.EOD_PROCESSING)
        assert scheduler.get_state() == SystemState.EOD_PROCESSING

    def test_market_close_transitions_to_closed(self, scheduler: DailyScheduler) -> None:
        """Test that market close transitions to MARKET_CLOSED state."""
        scheduler.set_state_for_testing(SystemState.EOD_PROCESSING)
        scheduler.fire_event_for_testing(ScheduledEvent.MARKET_CLOSE)
        assert scheduler.get_state() == SystemState.MARKET_CLOSED

    def test_kill_switch_overrides_other_states(self, scheduler: DailyScheduler) -> None:
        """Test that kill switch state takes precedence."""
        scheduler.set_state_for_testing(SystemState.TRADING)
        scheduler.trigger_kill_switch()
        assert scheduler.get_state() == SystemState.KILL_SWITCH

        # Even firing another event shouldn't change state
        scheduler.fire_event_for_testing(ScheduledEvent.EOD_PROCESSING)
        assert scheduler.get_state() == SystemState.KILL_SWITCH


# =============================================================================
# Callback Tests
# =============================================================================


class TestCallbacks:
    """Tests for event callbacks."""

    def test_register_callback(self, scheduler: DailyScheduler) -> None:
        """Test that callbacks can be registered."""
        callback_fired = []

        def callback() -> None:
            callback_fired.append(True)

        scheduler.on_pre_market_setup(callback)
        scheduler.fire_event_for_testing(ScheduledEvent.PRE_MARKET_SETUP)

        assert len(callback_fired) == 1

    def test_multiple_callbacks_for_same_event(self, scheduler: DailyScheduler) -> None:
        """Test that multiple callbacks can be registered for same event."""
        results = []

        scheduler.on_sod_baseline(lambda: results.append(1))
        scheduler.on_sod_baseline(lambda: results.append(2))
        scheduler.on_sod_baseline(lambda: results.append(3))

        scheduler.fire_event_for_testing(ScheduledEvent.SOD_BASELINE)

        assert results == [1, 2, 3]

    def test_callback_for_all_events(self, scheduler: DailyScheduler) -> None:
        """Test that callbacks can be registered for all event types."""
        fired_events = []

        scheduler.on_pre_market_setup(lambda: fired_events.append("PRE_MARKET"))
        scheduler.on_moo_fallback(lambda: fired_events.append("MOO_FALLBACK"))
        scheduler.on_sod_baseline(lambda: fired_events.append("SOD_BASELINE"))
        scheduler.on_warm_entry_check(lambda: fired_events.append("WARM_ENTRY"))
        scheduler.on_time_guard_start(lambda: fired_events.append("TIME_GUARD_START"))
        scheduler.on_time_guard_end(lambda: fired_events.append("TIME_GUARD_END"))
        scheduler.on_mr_force_close(lambda: fired_events.append("MR_FORCE_CLOSE"))
        scheduler.on_eod_processing(lambda: fired_events.append("EOD_PROCESSING"))
        scheduler.on_market_close(lambda: fired_events.append("MARKET_CLOSE"))
        scheduler.on_weekly_reset(lambda: fired_events.append("WEEKLY_RESET"))

        # Fire all events
        for event in ScheduledEvent:
            scheduler.fire_event_for_testing(event)

        assert len(fired_events) == 10


# =============================================================================
# State Query Tests
# =============================================================================


class TestStateQueries:
    """Tests for state query methods."""

    def test_is_trading(self, scheduler: DailyScheduler) -> None:
        """Test is_trading() query."""
        scheduler.set_state_for_testing(SystemState.PRE_MARKET)
        assert scheduler.is_trading() is False

        scheduler.set_state_for_testing(SystemState.TRADING)
        assert scheduler.is_trading() is True

        scheduler.set_state_for_testing(SystemState.TIME_GUARD)
        assert scheduler.is_trading() is False

    def test_is_time_guard_active_by_state(self, scheduler: DailyScheduler) -> None:
        """Test time guard detection by state."""
        scheduler.set_state_for_testing(SystemState.TRADING)
        assert scheduler.is_time_guard_active() is False

        scheduler.set_state_for_testing(SystemState.TIME_GUARD)
        assert scheduler.is_time_guard_active() is True

    def test_can_enter_new_positions(self, scheduler: DailyScheduler) -> None:
        """Test can_enter_new_positions() for different states."""
        # Can enter during trading
        scheduler.set_state_for_testing(SystemState.TRADING)
        assert scheduler.can_enter_new_positions() is True

        # Cannot enter during time guard
        scheduler.set_state_for_testing(SystemState.TIME_GUARD)
        assert scheduler.can_enter_new_positions() is False

        # Cannot enter after kill switch
        scheduler.set_state_for_testing(SystemState.KILL_SWITCH)
        assert scheduler.can_enter_new_positions() is False

        # Cannot enter during EOD processing
        scheduler.set_state_for_testing(SystemState.EOD_PROCESSING)
        assert scheduler.can_enter_new_positions() is False

        # Cannot enter when market closed
        scheduler.set_state_for_testing(SystemState.MARKET_CLOSED)
        assert scheduler.can_enter_new_positions() is False

    def test_is_eod_processing_time(self, scheduler: DailyScheduler) -> None:
        """Test EOD processing time detection."""
        scheduler.set_state_for_testing(SystemState.TRADING)
        assert scheduler.is_eod_processing_time() is False

        scheduler.set_state_for_testing(SystemState.EOD_PROCESSING)
        assert scheduler.is_eod_processing_time() is True

    def test_is_market_closed(self, scheduler: DailyScheduler) -> None:
        """Test market closed detection."""
        scheduler.set_state_for_testing(SystemState.TRADING)
        assert scheduler.is_market_closed() is False

        scheduler.set_state_for_testing(SystemState.MARKET_CLOSED)
        assert scheduler.is_market_closed() is True


# =============================================================================
# MR Window Tests
# =============================================================================


class TestMRWindow:
    """Tests for MR entry window logic."""

    def test_mr_window_closed_during_kill_switch(self, scheduler: DailyScheduler) -> None:
        """Test MR window is closed during kill switch."""
        scheduler.trigger_kill_switch()
        assert scheduler.is_mr_entry_window_open() is False

    def test_mr_window_closed_during_eod(self, scheduler: DailyScheduler) -> None:
        """Test MR window is closed during EOD processing."""
        scheduler.set_state_for_testing(SystemState.EOD_PROCESSING)
        assert scheduler.is_mr_entry_window_open() is False

    def test_mr_window_closed_when_market_closed(self, scheduler: DailyScheduler) -> None:
        """Test MR window is closed when market is closed."""
        scheduler.set_state_for_testing(SystemState.MARKET_CLOSED)
        assert scheduler.is_mr_entry_window_open() is False

    def test_mr_window_closed_during_time_guard(self, scheduler: DailyScheduler) -> None:
        """Test MR window is closed during time guard."""
        scheduler.set_state_for_testing(SystemState.TIME_GUARD)
        assert scheduler.is_mr_entry_window_open() is False


# =============================================================================
# Emergency State Tests
# =============================================================================


class TestEmergencyStates:
    """Tests for emergency state management."""

    def test_trigger_kill_switch(self, scheduler: DailyScheduler) -> None:
        """Test kill switch triggering."""
        scheduler.set_state_for_testing(SystemState.TRADING)

        scheduler.trigger_kill_switch()

        assert scheduler.is_kill_switch_triggered() is True
        assert scheduler.get_state() == SystemState.KILL_SWITCH

    def test_trigger_panic_mode(self, scheduler: DailyScheduler) -> None:
        """Test panic mode triggering."""
        scheduler.set_state_for_testing(SystemState.TRADING)

        scheduler.trigger_panic_mode()

        assert scheduler.is_panic_mode_triggered() is True
        # Panic mode doesn't change state, just sets flag
        assert scheduler.get_state() == SystemState.TRADING

    def test_clear_panic_mode(self, scheduler: DailyScheduler) -> None:
        """Test clearing panic mode."""
        scheduler.trigger_panic_mode()
        assert scheduler.is_panic_mode_triggered() is True

        scheduler.clear_panic_mode()
        assert scheduler.is_panic_mode_triggered() is False


# =============================================================================
# Daily Reset Tests
# =============================================================================


class TestDailyReset:
    """Tests for daily reset functionality."""

    def test_reset_clears_events(self, scheduler: DailyScheduler) -> None:
        """Test that reset clears fired events."""
        scheduler.fire_event_for_testing(ScheduledEvent.PRE_MARKET_SETUP)
        scheduler.fire_event_for_testing(ScheduledEvent.SOD_BASELINE)
        assert len(scheduler.get_events_fired_today()) == 2

        scheduler.reset_daily()

        assert len(scheduler.get_events_fired_today()) == 0

    def test_reset_clears_kill_switch(self, scheduler: DailyScheduler) -> None:
        """Test that reset clears kill switch."""
        scheduler.trigger_kill_switch()
        assert scheduler.is_kill_switch_triggered() is True

        scheduler.reset_daily()

        assert scheduler.is_kill_switch_triggered() is False

    def test_reset_clears_panic_mode(self, scheduler: DailyScheduler) -> None:
        """Test that reset clears panic mode."""
        scheduler.trigger_panic_mode()
        assert scheduler.is_panic_mode_triggered() is True

        scheduler.reset_daily()

        assert scheduler.is_panic_mode_triggered() is False

    def test_reset_sets_pre_market_state(self, scheduler: DailyScheduler) -> None:
        """Test that reset sets state to PRE_MARKET."""
        scheduler.set_state_for_testing(SystemState.MARKET_CLOSED)

        scheduler.reset_daily()

        assert scheduler.get_state() == SystemState.PRE_MARKET


# =============================================================================
# Event Tracking Tests
# =============================================================================


class TestEventTracking:
    """Tests for event tracking."""

    def test_events_tracked_when_fired(self, scheduler: DailyScheduler) -> None:
        """Test that fired events are tracked."""
        scheduler.fire_event_for_testing(ScheduledEvent.PRE_MARKET_SETUP)

        events = scheduler.get_events_fired_today()
        assert ScheduledEvent.PRE_MARKET_SETUP in events

    def test_has_event_fired(self, scheduler: DailyScheduler) -> None:
        """Test has_event_fired() method."""
        assert scheduler.has_event_fired(ScheduledEvent.PRE_MARKET_SETUP) is False

        scheduler.fire_event_for_testing(ScheduledEvent.PRE_MARKET_SETUP)

        assert scheduler.has_event_fired(ScheduledEvent.PRE_MARKET_SETUP) is True

    def test_multiple_events_tracked(self, scheduler: DailyScheduler) -> None:
        """Test multiple events are tracked."""
        scheduler.fire_event_for_testing(ScheduledEvent.PRE_MARKET_SETUP)
        scheduler.fire_event_for_testing(ScheduledEvent.SOD_BASELINE)
        scheduler.fire_event_for_testing(ScheduledEvent.WARM_ENTRY_CHECK)

        events = scheduler.get_events_fired_today()
        assert len(events) == 3
        assert ScheduledEvent.PRE_MARKET_SETUP in events
        assert ScheduledEvent.SOD_BASELINE in events
        assert ScheduledEvent.WARM_ENTRY_CHECK in events


# =============================================================================
# Daily Summary Tests
# =============================================================================


class TestDailySummary:
    """Tests for daily summary generation."""

    def test_summary_includes_equity(self, scheduler: DailyScheduler) -> None:
        """Test summary includes equity information."""
        summary = scheduler.get_day_summary(
            starting_equity=50000.0,
            ending_equity=51000.0,
            trades=[],
            safeguards=[],
            moo_orders=[],
            regime_score=58.0,
            regime_state="NEUTRAL",
            phase="SEED",
            days_running=5,
        )

        assert "$50,000.00" in summary
        assert "$51,000.00" in summary
        assert "+$1,000.00" in summary
        assert "+2.00%" in summary

    def test_summary_includes_regime_info(self, scheduler: DailyScheduler) -> None:
        """Test summary includes regime information."""
        summary = scheduler.get_day_summary(
            starting_equity=50000.0,
            ending_equity=50000.0,
            trades=[],
            safeguards=[],
            moo_orders=[],
            regime_score=75.0,
            regime_state="RISK_ON",
            phase="GROWTH",
            days_running=15,
        )

        assert "75 (RISK_ON)" in summary
        assert "GROWTH" in summary
        assert "15" in summary

    def test_summary_includes_trades(self, scheduler: DailyScheduler) -> None:
        """Test summary includes trade list."""
        trades = ["Buy 100 QLD @ $85.00", "Sell 50 TQQQ @ $46.00"]
        summary = scheduler.get_day_summary(
            starting_equity=50000.0,
            ending_equity=50500.0,
            trades=trades,
            safeguards=[],
            moo_orders=[],
            regime_score=58.0,
            regime_state="NEUTRAL",
            phase="SEED",
            days_running=5,
        )

        assert "Buy 100 QLD @ $85.00" in summary
        assert "Sell 50 TQQQ @ $46.00" in summary

    def test_summary_includes_safeguards(self, scheduler: DailyScheduler) -> None:
        """Test summary includes safeguard list."""
        safeguards = ["Vol Shock at 10:42"]
        summary = scheduler.get_day_summary(
            starting_equity=50000.0,
            ending_equity=49500.0,
            trades=[],
            safeguards=safeguards,
            moo_orders=[],
            regime_score=35.0,
            regime_state="DEFENSIVE",
            phase="SEED",
            days_running=5,
        )

        assert "Vol Shock at 10:42" in summary


# =============================================================================
# Statistics Tests
# =============================================================================


class TestStatistics:
    """Tests for scheduler statistics."""

    def test_get_statistics(self, scheduler: DailyScheduler) -> None:
        """Test getting statistics."""
        scheduler.set_state_for_testing(SystemState.TRADING)
        scheduler.fire_event_for_testing(ScheduledEvent.SOD_BASELINE)

        stats = scheduler.get_statistics()

        assert stats["state"] == "TRADING"
        assert "SOD_BASELINE" in stats["events_fired_today"]
        assert stats["kill_switch_triggered"] is False
        assert stats["panic_mode_triggered"] is False
        assert stats["is_trading"] is True
