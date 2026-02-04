"""
Unit tests for Risk Engine.

V1 Safeguards:
- Kill switch (-3% daily loss)
- Panic mode (SPY -4% intraday)
- Weekly breaker (-5% WTD)
- Gap filter (SPY -1.5% gap)
- Vol shock (3x ATR bar)
- Time guard (13:55-14:10)
- Split guard

V2.1 Circuit Breaker System (5 Levels):
- Level 1: Daily loss -2% → reduce sizing 50%
- Level 2: Weekly loss -5% → reduce sizing 50%
- Level 3: Portfolio vol > 1.5% → block new entries
- Level 4: Correlation > 0.60 → reduce exposure
- Level 5: Greeks breach → close options positions

Spec: docs/12-risk-engine.md, V2_1_COMPLETE_ARCHITECTURE.txt
"""

from datetime import datetime, timedelta

import pytest

from engines.core.risk_engine import (
    ALL_TRADED_SYMBOLS,
    HEDGE_SYMBOLS,
    LEVERAGED_LONG_SYMBOLS,
    YIELD_SYMBOLS,
    GreeksSnapshot,
    KSTier,
    RiskCheckResult,
    RiskEngine,
    SafeguardStatus,
    SafeguardType,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def risk_engine():
    """Create a RiskEngine instance for testing."""
    return RiskEngine()


@pytest.fixture
def configured_engine():
    """Create a RiskEngine with baselines configured."""
    engine = RiskEngine()
    engine.set_equity_prior_close(100_000.0)
    engine.set_equity_sod(100_000.0)
    engine.set_week_start_equity(100_000.0)
    engine.set_spy_prior_close(450.0)
    engine.set_spy_open(450.0)
    engine.set_spy_atr(0.75)
    return engine


# =============================================================================
# Symbol Classifications
# =============================================================================


class TestSymbolClassifications:
    """Tests for symbol classification constants."""

    def test_leveraged_long_symbols(self):
        """Test leveraged long symbols list."""
        assert LEVERAGED_LONG_SYMBOLS == ["TQQQ", "QLD", "SSO", "SOXL"]

    def test_hedge_symbols(self):
        """Test hedge symbols list."""
        assert HEDGE_SYMBOLS == ["TMF", "PSQ"]

    def test_yield_symbols(self):
        """Test yield symbols list."""
        assert YIELD_SYMBOLS == ["SHV"]

    def test_all_traded_symbols(self):
        """Test all traded symbols includes all categories."""
        expected = ["TQQQ", "QLD", "SSO", "SOXL", "TMF", "PSQ", "SHV"]
        assert ALL_TRADED_SYMBOLS == expected


# =============================================================================
# Kill Switch Tests
# =============================================================================


class TestKillSwitch:
    """Tests for Kill Switch circuit breaker."""

    def test_kill_switch_triggers_at_2_percent_tier1_from_prior_close(self, risk_engine):
        """Test graduated KS Tier 1 triggers at -2% from prior close (V2.28.1)."""
        risk_engine.set_equity_prior_close(100_000.0)

        # 1.9% loss - should not trigger
        assert risk_engine.check_kill_switch(98_100.0) is False

        # Exactly 2% loss - should trigger Tier 1 (REDUCE)
        assert risk_engine.check_kill_switch(98_000.0) is True
        assert risk_engine.get_ks_tier() == KSTier.REDUCE

    def test_kill_switch_triggers_at_2_percent_tier1_from_sod(self, risk_engine):
        """Test graduated KS Tier 1 triggers at -2% from SOD (V2.28.1)."""
        risk_engine.set_equity_sod(100_000.0)

        # 1.9% loss - should not trigger
        assert risk_engine.check_kill_switch(98_100.0) is False

        # Exactly 2% loss - should trigger Tier 1
        assert risk_engine.check_kill_switch(98_000.0) is True
        assert risk_engine.get_ks_tier() == KSTier.REDUCE

    def test_kill_switch_uses_either_baseline(self, risk_engine):
        """Test kill switch triggers from either baseline (V2.27 graduated)."""
        risk_engine.set_equity_prior_close(100_000.0)
        risk_engine.set_equity_sod(98_000.0)

        # No loss from SOD but 3%+ from prior close
        current = 96_900.0  # 3.1% from prior, ~1.1% from SOD
        assert risk_engine.check_kill_switch(current) is True

    def test_kill_switch_stays_active_once_triggered(self, risk_engine):
        """Test kill switch stays active once triggered (V2.27 graduated)."""
        risk_engine.set_equity_prior_close(100_000.0)

        # Trigger Tier 1 (3% loss)
        assert risk_engine.check_kill_switch(97_000.0) is True

        # Should still return True even if equity recovers (tier doesn't downgrade)
        assert risk_engine.check_kill_switch(100_000.0) is True

    def test_kill_switch_liquidates_all_positions(self, configured_engine):
        """Test Tier 3 kill switch includes all symbols in liquidation (V2.27)."""
        result = configured_engine.check_all(
            current_equity=92_000.0,  # 8% loss = Tier 3 FULL_EXIT
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert SafeguardType.KILL_SWITCH in result.active_safeguards
        assert set(result.symbols_to_liquidate) == set(ALL_TRADED_SYMBOLS)

    def test_kill_switch_blocks_new_entries(self, configured_engine):
        """Test Tier 2 blocks all new entries (V2.27 graduated)."""
        configured_engine.check_kill_switch(95_000.0)  # Trigger Tier 2 (5% loss)

        result = configured_engine.check_all(
            current_equity=95_000.0,
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert result.can_enter_positions is False
        assert result.can_enter_intraday is False
        assert result.can_enter_options is False

    def test_kill_switch_resets_cold_start_tier3(self, configured_engine):
        """Test Tier 3 kill switch resets days_running to 0 (V2.27)."""
        result = configured_engine.check_all(
            current_equity=92_000.0,  # 8% loss = Tier 3 FULL_EXIT
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert result.reset_cold_start is True

    def test_kill_switch_status(self, risk_engine):
        """Test kill switch status reporting (V2.27 graduated)."""
        risk_engine.set_equity_prior_close(100_000.0)

        # Before trigger
        status = risk_engine.get_kill_switch_status()
        assert status.is_active is False
        assert status.safeguard_type == SafeguardType.KILL_SWITCH

        # After trigger (3% loss = Tier 1)
        risk_engine.check_kill_switch(97_000.0)
        status = risk_engine.get_kill_switch_status()
        assert status.is_active is True


# =============================================================================
# Panic Mode Tests
# =============================================================================


class TestPanicMode:
    """Tests for Panic Mode circuit breaker."""

    def test_panic_mode_triggers_at_spy_minus_4_percent(self, risk_engine):
        """Test panic mode triggers when SPY drops 4% intraday."""
        risk_engine.set_spy_open(450.0)

        # 3.9% drop - should not trigger
        assert risk_engine.check_panic_mode(432.45) is False

        # Exactly 4% drop - should trigger
        assert risk_engine.check_panic_mode(432.0) is True

    def test_panic_mode_liquidates_longs_only(self, configured_engine):
        """Test panic mode liquidates long positions but keeps hedges."""
        result = configured_engine.check_all(
            current_equity=100_000.0,  # No portfolio loss
            spy_price=432.0,  # 4% SPY drop
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert SafeguardType.PANIC_MODE in result.active_safeguards
        assert set(result.symbols_to_liquidate) == set(LEVERAGED_LONG_SYMBOLS)
        assert "TMF" not in result.symbols_to_liquidate
        assert "PSQ" not in result.symbols_to_liquidate
        assert "SHV" not in result.symbols_to_liquidate

    def test_panic_mode_blocks_entries(self, configured_engine):
        """Test panic mode blocks new entries."""
        configured_engine.check_panic_mode(432.0)  # 4% drop

        current_time = datetime(2024, 1, 15, 10, 30)
        assert configured_engine.can_enter_new_positions(current_time) is False

    def test_panic_mode_stays_active_once_triggered(self, risk_engine):
        """Test panic mode stays active once triggered."""
        risk_engine.set_spy_open(450.0)

        # Trigger
        assert risk_engine.check_panic_mode(432.0) is True

        # Should stay active even if SPY recovers
        assert risk_engine.check_panic_mode(450.0) is True

    def test_panic_mode_does_not_reset_cold_start(self, configured_engine):
        """Test panic mode does NOT reset cold start (unlike kill switch)."""
        result = configured_engine.check_all(
            current_equity=100_000.0,
            spy_price=432.0,  # 4% SPY drop
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert SafeguardType.PANIC_MODE in result.active_safeguards
        assert result.reset_cold_start is False


# =============================================================================
# Weekly Breaker Tests
# =============================================================================


class TestWeeklyBreaker:
    """Tests for Weekly Breaker circuit breaker."""

    def test_weekly_breaker_triggers_at_5_percent_wtd(self, risk_engine):
        """Test weekly breaker triggers at -5% week-to-date."""
        risk_engine.set_week_start_equity(100_000.0)

        # 4.9% loss - should not trigger
        assert risk_engine.check_weekly_breaker(95_100.0) is False

        # Exactly 5% loss - should trigger
        assert risk_engine.check_weekly_breaker(95_000.0) is True

    def test_weekly_breaker_reduces_sizing(self, risk_engine):
        """Test weekly breaker reduces position sizing by 50%."""
        risk_engine.set_week_start_equity(100_000.0)

        # Before trigger
        assert risk_engine.get_sizing_multiplier() == 1.0

        # After trigger
        risk_engine.check_weekly_breaker(95_000.0)
        assert risk_engine.get_sizing_multiplier() == 0.5

    def test_weekly_breaker_does_not_liquidate(self, risk_engine):
        """Test weekly breaker does NOT force liquidation."""
        # Configure to trigger weekly breaker but NOT kill switch
        # Set daily baselines high so no kill switch at 95k
        risk_engine.set_equity_prior_close(96_000.0)  # 1% daily loss at 95k (< 3%)
        risk_engine.set_equity_sod(96_000.0)
        risk_engine.set_week_start_equity(100_000.0)  # 5% WTD loss at 95k
        risk_engine.set_spy_prior_close(450.0)
        risk_engine.set_spy_open(450.0)
        risk_engine.set_spy_atr(0.75)

        result = risk_engine.check_all(
            current_equity=95_000.0,  # 5% WTD loss but only 1% daily
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert SafeguardType.WEEKLY_BREAKER in result.active_safeguards
        assert len(result.symbols_to_liquidate) == 0

    def test_weekly_breaker_resets_on_monday(self, risk_engine):
        """Test weekly breaker resets when week_start_equity is set."""
        risk_engine.set_week_start_equity(100_000.0)
        risk_engine.check_weekly_breaker(95_000.0)  # Trigger
        assert risk_engine.get_sizing_multiplier() == 0.5

        # Monday reset
        risk_engine.set_week_start_equity(95_000.0)
        assert risk_engine.get_sizing_multiplier() == 1.0

    def test_weekly_breaker_allows_entries(self, configured_engine):
        """Test weekly breaker does NOT block entries, only reduces sizing."""
        configured_engine.check_weekly_breaker(95_000.0)  # Trigger

        current_time = datetime(2024, 1, 15, 10, 30)
        assert configured_engine.can_enter_new_positions(current_time) is True


# =============================================================================
# Gap Filter Tests
# =============================================================================


class TestGapFilter:
    """Tests for Gap Filter safeguard."""

    def test_gap_filter_triggers_at_1_5_percent(self, risk_engine):
        """Test gap filter triggers at SPY -1.5% gap."""
        risk_engine.set_spy_prior_close(450.0)

        # 1.4% gap - should not trigger
        assert risk_engine.check_gap_filter(443.70) is False

        # Exactly 1.5% gap - should trigger
        assert risk_engine.check_gap_filter(443.25) is True

    def test_gap_filter_blocks_mr_entries(self, risk_engine):
        """Test gap filter blocks Mean Reversion entries on gap days."""
        risk_engine.set_spy_prior_close(450.0)
        risk_engine.check_gap_filter(443.0)  # Trigger

        current_time = datetime(2024, 1, 15, 10, 30)
        assert risk_engine.can_enter_intraday(current_time) is False
        # But swing entries (already submitted MOO) are allowed - can_enter_new_positions
        # is not blocked by gap filter alone

    def test_gap_filter_allows_swing_entries(self, risk_engine):
        """Test gap filter does NOT block swing/MOO entries."""
        risk_engine.set_spy_prior_close(450.0)
        risk_engine.check_gap_filter(443.0)  # Trigger

        # can_enter_new_positions should still be True (only gap filter is active)
        current_time = datetime(2024, 1, 15, 10, 30)
        assert risk_engine.can_enter_new_positions(current_time) is True

    def test_gap_filter_lasts_all_day(self, risk_engine):
        """Test gap filter applies for entire trading day."""
        risk_engine.set_spy_prior_close(450.0)
        risk_engine.check_gap_filter(443.0)  # Trigger

        assert risk_engine.is_gap_filter_active() is True

        # Still active later in day
        assert risk_engine.is_gap_filter_active() is True


# =============================================================================
# Vol Shock Tests
# =============================================================================


class TestVolShock:
    """Tests for Vol Shock safeguard."""

    def test_vol_shock_triggers_at_3x_atr(self, risk_engine):
        """Test vol shock triggers at 3x ATR bar."""
        risk_engine.set_spy_atr(0.75)
        current_time = datetime(2024, 1, 15, 10, 30)

        # 2.9x ATR bar - should not trigger
        assert risk_engine.check_vol_shock(2.24, current_time) is False

        # 3.1x ATR bar - should trigger
        assert risk_engine.check_vol_shock(2.33, current_time) is True

    def test_vol_shock_pauses_entries_15_minutes(self, risk_engine):
        """Test vol shock pauses entries for 15 minutes."""
        risk_engine.set_spy_atr(0.75)
        trigger_time = datetime(2024, 1, 15, 10, 30)

        risk_engine.check_vol_shock(2.5, trigger_time)

        # Still active 10 minutes later
        assert risk_engine.is_vol_shock_active(trigger_time + timedelta(minutes=10)) is True

        # Not active 16 minutes later
        assert risk_engine.is_vol_shock_active(trigger_time + timedelta(minutes=16)) is False

    def test_vol_shock_allows_exits(self, configured_engine):
        """Test vol shock does NOT block exits."""
        trigger_time = datetime(2024, 1, 15, 10, 30)
        configured_engine.check_vol_shock(2.5, trigger_time)

        result = configured_engine.check_all(
            current_equity=100_000.0,
            spy_price=450.0,
            spy_bar_range=2.5,
            current_time=trigger_time,
        )

        # Entries blocked
        assert result.can_enter_positions is False
        # But no symbols to liquidate (exits still allowed)
        assert len(result.symbols_to_liquidate) == 0

    def test_vol_shock_extends_on_new_trigger(self, risk_engine):
        """Test vol shock window extends on subsequent triggers."""
        risk_engine.set_spy_atr(0.75)
        first_trigger = datetime(2024, 1, 15, 10, 30)

        risk_engine.check_vol_shock(2.5, first_trigger)

        # New trigger 10 minutes later should extend the window
        second_trigger = first_trigger + timedelta(minutes=10)
        risk_engine.check_vol_shock(2.5, second_trigger)

        # Should be active 20 minutes after first trigger (10 + 15 from second)
        check_time = first_trigger + timedelta(minutes=20)
        assert risk_engine.is_vol_shock_active(check_time) is True


# =============================================================================
# Time Guard Tests
# =============================================================================


class TestTimeGuard:
    """Tests for Time Guard safeguard."""

    def test_time_guard_active_during_window(self, risk_engine):
        """Test time guard active 13:55-14:10 ET."""
        # Before window
        assert risk_engine.is_time_guard_active(datetime(2024, 1, 15, 13, 54)) is False

        # At start of window
        assert risk_engine.is_time_guard_active(datetime(2024, 1, 15, 13, 55)) is True

        # During window
        assert risk_engine.is_time_guard_active(datetime(2024, 1, 15, 14, 0)) is True

        # At end of window (14:10 should be outside)
        assert risk_engine.is_time_guard_active(datetime(2024, 1, 15, 14, 10)) is False

    def test_time_guard_blocks_all_entries(self, configured_engine):
        """Test time guard blocks all entry signals."""
        time_during_guard = datetime(2024, 1, 15, 14, 0)

        result = configured_engine.check_all(
            current_equity=100_000.0,
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=time_during_guard,
        )

        assert SafeguardType.TIME_GUARD in result.active_safeguards
        assert result.can_enter_positions is False
        assert result.can_enter_intraday is False

    def test_time_guard_allows_exits(self, configured_engine):
        """Test time guard does NOT block exits."""
        time_during_guard = datetime(2024, 1, 15, 14, 0)

        result = configured_engine.check_all(
            current_equity=100_000.0,
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=time_during_guard,
        )

        # No liquidation triggered
        assert len(result.symbols_to_liquidate) == 0


# =============================================================================
# Split Guard Tests
# =============================================================================


class TestSplitGuard:
    """Tests for Split Guard safeguard."""

    def test_register_split_freezes_symbol(self, risk_engine):
        """Test registering a split freezes the symbol."""
        assert risk_engine.is_symbol_frozen("QLD") is False

        risk_engine.register_split("QLD")

        assert risk_engine.is_symbol_frozen("QLD") is True
        assert risk_engine.is_symbol_frozen("TQQQ") is False  # Other symbols unaffected

    def test_split_guard_status(self, risk_engine):
        """Test split guard status reporting."""
        status = risk_engine.get_split_guard_status()
        assert status.is_active is False

        risk_engine.register_split("QLD")
        risk_engine.register_split("SOXL")

        status = risk_engine.get_split_guard_status()
        assert status.is_active is True
        assert "QLD" in status.details
        assert "SOXL" in status.details

    def test_split_frozen_symbols_reset_daily(self, risk_engine):
        """Test frozen symbols reset on daily reset."""
        risk_engine.register_split("QLD")
        assert risk_engine.is_symbol_frozen("QLD") is True

        risk_engine.reset_daily_state()

        assert risk_engine.is_symbol_frozen("QLD") is False


# =============================================================================
# Combined Risk Check Tests
# =============================================================================


class TestCombinedRiskCheck:
    """Tests for combined risk check functionality."""

    def test_check_all_returns_result(self, configured_engine):
        """Test check_all returns proper RiskCheckResult."""
        result = configured_engine.check_all(
            current_equity=100_000.0,
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert isinstance(result, RiskCheckResult)
        assert result.can_enter_positions is True
        assert result.sizing_multiplier == 1.0

    def test_kill_switch_overrides_other_safeguards(self, configured_engine):
        """Test Tier 3 kill switch overrides all other safeguards (V2.27)."""
        result = configured_engine.check_all(
            current_equity=92_000.0,  # 8% loss triggers Tier 3 FULL_EXIT
            spy_price=432.0,  # 4% drop would trigger panic mode too
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 14, 0),  # Time guard window
        )

        # Only kill switch should be in the result (it returns early)
        assert SafeguardType.KILL_SWITCH in result.active_safeguards
        assert result.reset_cold_start is True
        assert set(result.symbols_to_liquidate) == set(ALL_TRADED_SYMBOLS)

    def test_multiple_safeguards_can_be_active(self, risk_engine):
        """Test multiple safeguards can be active simultaneously."""
        # Configure to trigger weekly breaker but NOT kill switch
        risk_engine.set_equity_prior_close(96_000.0)  # 1% daily loss at 95k
        risk_engine.set_equity_sod(96_000.0)
        risk_engine.set_week_start_equity(100_000.0)  # 5% WTD loss at 95k
        risk_engine.set_spy_prior_close(450.0)
        risk_engine.set_spy_open(450.0)
        risk_engine.set_spy_atr(0.75)

        risk_engine.check_gap_filter(443.0)  # Trigger gap filter

        result = risk_engine.check_all(
            current_equity=95_000.0,  # 5% WTD loss but only 1% daily
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 14, 0),  # Time guard window
        )

        assert SafeguardType.WEEKLY_BREAKER in result.active_safeguards
        assert SafeguardType.GAP_FILTER in result.active_safeguards
        assert SafeguardType.TIME_GUARD in result.active_safeguards
        assert result.sizing_multiplier == 0.5


# =============================================================================
# Quick Check Methods Tests
# =============================================================================


class TestQuickCheckMethods:
    """Tests for quick check methods."""

    def test_can_enter_new_positions_default(self, risk_engine):
        """Test can_enter_new_positions returns True by default."""
        current_time = datetime(2024, 1, 15, 10, 30)
        assert risk_engine.can_enter_new_positions(current_time) is True

    def test_can_enter_new_positions_blocked_by_kill_switch(self, risk_engine):
        """Test kill switch blocks can_enter_new_positions."""
        risk_engine.set_equity_prior_close(100_000.0)
        risk_engine.check_kill_switch(95_000.0)  # 5% loss (V2.3.17)

        current_time = datetime(2024, 1, 15, 10, 30)
        assert risk_engine.can_enter_new_positions(current_time) is False

    def test_can_enter_intraday_includes_gap_filter(self, risk_engine):
        """Test can_enter_intraday includes gap filter check."""
        risk_engine.set_spy_prior_close(450.0)
        risk_engine.check_gap_filter(443.0)  # Trigger

        current_time = datetime(2024, 1, 15, 10, 30)
        assert risk_engine.can_enter_new_positions(current_time) is True  # Swing OK
        assert risk_engine.can_enter_intraday(current_time) is False  # MR blocked


# =============================================================================
# Daily Reset Tests
# =============================================================================


class TestDailyReset:
    """Tests for daily state reset."""

    def test_reset_clears_all_flags(self, configured_engine):
        """Test daily reset clears all safeguard flags."""
        # Trigger various safeguards
        configured_engine.check_kill_switch(95_000.0)  # 5% loss (V2.3.17)
        configured_engine.check_panic_mode(432.0)
        configured_engine.check_gap_filter(443.0)
        configured_engine.register_split("QLD")

        # Reset
        configured_engine.reset_daily_state()

        # All flags should be cleared
        current_time = datetime(2024, 1, 15, 10, 30)
        assert configured_engine.can_enter_new_positions(current_time) is True
        assert configured_engine.can_enter_intraday(current_time) is True
        assert configured_engine.is_symbol_frozen("QLD") is False

    def test_reset_preserves_weekly_breaker(self, configured_engine):
        """Test daily reset does NOT clear weekly breaker."""
        configured_engine.check_weekly_breaker(95_000.0)  # Trigger

        configured_engine.reset_daily_state()

        # Weekly breaker should still be active
        assert configured_engine.get_sizing_multiplier() == 0.5


# =============================================================================
# State Persistence Tests
# =============================================================================


class TestStatePersistence:
    """Tests for state persistence."""

    def test_get_state_for_persistence(self, risk_engine):
        """Test state dict includes required fields."""
        risk_engine.set_week_start_equity(100_000.0)
        risk_engine.check_weekly_breaker(95_000.0)
        risk_engine.set_last_kill_date("2024-01-15")

        state = risk_engine.get_state_for_persistence()

        assert state["week_start_equity"] == 100_000.0
        assert state["weekly_breaker_active"] is True
        assert state["last_kill_date"] == "2024-01-15"

    def test_load_state(self, risk_engine):
        """Test loading state from persistence."""
        state = {
            "week_start_equity": 100_000.0,
            "weekly_breaker_active": True,
            "last_kill_date": "2024-01-15",
        }

        risk_engine.load_state(state)

        assert risk_engine.get_sizing_multiplier() == 0.5
        assert risk_engine.get_last_kill_date() == "2024-01-15"


# =============================================================================
# Status Reporting Tests
# =============================================================================


class TestStatusReporting:
    """Tests for status reporting methods."""

    def test_get_all_statuses(self, risk_engine):
        """Test get_all_statuses returns all safeguard statuses."""
        current_time = datetime(2024, 1, 15, 10, 30)
        statuses = risk_engine.get_all_statuses(current_time)

        assert "kill_switch" in statuses
        assert "panic_mode" in statuses
        assert "weekly_breaker" in statuses
        assert "gap_filter" in statuses
        assert "vol_shock" in statuses
        assert "time_guard" in statuses
        assert "split_guard" in statuses

    def test_safeguard_status_to_dict(self):
        """Test SafeguardStatus serialization."""
        status = SafeguardStatus(
            safeguard_type=SafeguardType.KILL_SWITCH,
            is_active=True,
            details="Test details",
        )

        d = status.to_dict()

        assert d["safeguard_type"] == "KILL_SWITCH"
        assert d["is_active"] is True
        assert d["details"] == "Test details"

    def test_risk_check_result_to_dict(self):
        """Test RiskCheckResult serialization."""
        result = RiskCheckResult(
            can_enter_positions=False,
            can_enter_intraday=False,
            sizing_multiplier=0.5,
            symbols_to_liquidate=["QLD", "SSO"],
            active_safeguards=[SafeguardType.PANIC_MODE, SafeguardType.WEEKLY_BREAKER],
            reset_cold_start=False,
        )

        d = result.to_dict()

        assert d["can_enter_positions"] is False
        assert d["sizing_multiplier"] == 0.5
        assert "QLD" in d["symbols_to_liquidate"]
        assert "PANIC_MODE" in d["active_safeguards"]


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_no_baselines_set(self, risk_engine):
        """Test behavior when no baselines are set."""
        # Should not trigger anything
        assert risk_engine.check_kill_switch(50_000.0) is False
        assert risk_engine.check_panic_mode(400.0) is False
        assert risk_engine.check_weekly_breaker(50_000.0) is False
        assert risk_engine.check_gap_filter(400.0) is False

    def test_zero_equity_baseline(self, risk_engine):
        """Test behavior with zero equity baseline."""
        risk_engine.set_equity_prior_close(0.0)
        assert risk_engine.check_kill_switch(50_000.0) is False

    def test_vol_shock_no_atr(self, risk_engine):
        """Test vol shock with no ATR set."""
        current_time = datetime(2024, 1, 15, 10, 30)
        assert risk_engine.check_vol_shock(10.0, current_time) is False

    def test_safeguard_type_enum_values(self):
        """Test SafeguardType enum has correct values."""
        assert SafeguardType.KILL_SWITCH.value == "KILL_SWITCH"
        assert SafeguardType.PANIC_MODE.value == "PANIC_MODE"
        assert SafeguardType.WEEKLY_BREAKER.value == "WEEKLY_BREAKER"
        assert SafeguardType.GAP_FILTER.value == "GAP_FILTER"
        assert SafeguardType.VOL_SHOCK.value == "VOL_SHOCK"
        assert SafeguardType.TIME_GUARD.value == "TIME_GUARD"
        assert SafeguardType.SPLIT_GUARD.value == "SPLIT_GUARD"
        # V2.1 Circuit Breaker types
        assert SafeguardType.CB_DAILY_LOSS.value == "CB_DAILY_LOSS"
        assert SafeguardType.CB_PORTFOLIO_VOL.value == "CB_PORTFOLIO_VOL"
        assert SafeguardType.CB_CORRELATION.value == "CB_CORRELATION"
        assert SafeguardType.CB_GREEKS_BREACH.value == "CB_GREEKS_BREACH"


# =============================================================================
# V2.1 Circuit Breaker Tests
# =============================================================================


class TestCBLevel1DailyLoss:
    """Tests for V2.1 Circuit Breaker Level 1: Daily Loss (-2%)."""

    def test_cb_daily_loss_triggers_at_2_percent(self, risk_engine):
        """Test Level 1 CB triggers at -2% daily loss."""
        risk_engine.set_equity_prior_close(100_000.0)

        # 1.9% loss - should not trigger
        assert risk_engine.check_cb_daily_loss(98_100.0) is False

        # Exactly 2% loss - should trigger
        assert risk_engine.check_cb_daily_loss(98_000.0) is True

    def test_cb_daily_loss_reduces_sizing(self, risk_engine):
        """Test Level 1 CB reduces sizing to 50%."""
        risk_engine.set_equity_prior_close(100_000.0)
        risk_engine.check_cb_daily_loss(98_000.0)

        assert risk_engine.get_sizing_multiplier() == 0.5

    def test_cb_daily_loss_before_kill_switch(self, configured_engine):
        """Test Level 1 CB (-2%) triggers alongside KS Tier 1 (-2%) (V2.28.1)."""
        # At 1.5% loss: Level 1 CB should not trigger, Kill Switch should not
        result = configured_engine.check_all(
            current_equity=98_500.0,
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert SafeguardType.CB_DAILY_LOSS not in result.active_safeguards
        assert SafeguardType.KILL_SWITCH not in result.active_safeguards

        # At 2.5% loss: Both CB Level 1 and KS Tier 1 trigger (both at 2%)
        result = configured_engine.check_all(
            current_equity=97_500.0,
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert SafeguardType.CB_DAILY_LOSS in result.active_safeguards
        assert SafeguardType.KILL_SWITCH in result.active_safeguards

    def test_cb_daily_loss_stays_active(self, risk_engine):
        """Test Level 1 CB stays active once triggered."""
        risk_engine.set_equity_prior_close(100_000.0)
        risk_engine.check_cb_daily_loss(98_000.0)

        # Even with equity recovery, stays active
        assert risk_engine.check_cb_daily_loss(99_000.0) is True

    def test_cb_daily_loss_resets_daily(self, configured_engine):
        """Test Level 1 CB resets on daily reset."""
        configured_engine.check_cb_daily_loss(98_000.0)
        assert configured_engine._cb_daily_loss_active is True

        configured_engine.reset_daily_state()
        assert configured_engine._cb_daily_loss_active is False


class TestCBLevel3PortfolioVol:
    """Tests for V2.1 Circuit Breaker Level 3: Portfolio Volatility (>1.5%)."""

    def test_portfolio_vol_calculation(self, risk_engine):
        """Test portfolio volatility calculation."""
        # Add daily returns (1.5% volatility threshold)
        returns = [0.01, -0.02, 0.015, -0.01, 0.005]  # Varied returns
        for r in returns:
            risk_engine.update_daily_return(r)

        vol = risk_engine.calculate_portfolio_volatility()
        assert vol > 0  # Should have positive volatility

    def test_cb_portfolio_vol_triggers_above_threshold(self, risk_engine):
        """Test Level 3 CB triggers when vol exceeds 1.5%."""
        # Add high volatility returns
        high_vol_returns = [0.03, -0.03, 0.04, -0.04, 0.05, -0.05, 0.03, -0.03]
        for r in high_vol_returns:
            risk_engine.update_daily_return(r)

        assert risk_engine.check_cb_portfolio_vol() is True

    def test_cb_portfolio_vol_blocks_entries(self, risk_engine):
        """Test Level 3 CB blocks all new entries."""
        # Trigger portfolio vol CB
        high_vol_returns = [0.03, -0.03, 0.04, -0.04, 0.05, -0.05, 0.03, -0.03]
        for r in high_vol_returns:
            risk_engine.update_daily_return(r)
        risk_engine.check_cb_portfolio_vol()

        current_time = datetime(2024, 1, 15, 10, 30)
        assert risk_engine.can_enter_options(current_time) is False

    def test_cb_portfolio_vol_in_check_all(self, configured_engine):
        """Test Level 3 CB is included in check_all result."""
        # Trigger portfolio vol CB
        high_vol_returns = [0.03, -0.03, 0.04, -0.04, 0.05, -0.05, 0.03, -0.03]
        for r in high_vol_returns:
            configured_engine.update_daily_return(r)

        result = configured_engine.check_all(
            current_equity=100_000.0,
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert SafeguardType.CB_PORTFOLIO_VOL in result.active_safeguards
        assert result.can_enter_positions is False
        assert result.circuit_breaker_level == 3


class TestCBLevel4Correlation:
    """Tests for V2.1 Circuit Breaker Level 4: Correlation (>0.60)."""

    def test_correlation_triggers_above_threshold(self, risk_engine):
        """Test Level 4 CB triggers when correlation exceeds 0.60."""
        # Add high correlation data
        risk_engine.update_correlation("QLD", 0.7)
        risk_engine.update_correlation("SSO", 0.75)
        risk_engine.update_correlation("TQQQ", 0.8)

        assert risk_engine.check_cb_correlation() is True

    def test_correlation_reduces_exposure(self, risk_engine):
        """Test Level 4 CB reduces exposure multiplier."""
        risk_engine.update_correlation("QLD", 0.7)
        risk_engine.update_correlation("SSO", 0.75)
        risk_engine.check_cb_correlation()

        assert risk_engine.get_correlation_exposure_multiplier() == 0.5

    def test_correlation_does_not_trigger_below_threshold(self, risk_engine):
        """Test Level 4 CB doesn't trigger below 0.60."""
        risk_engine.update_correlation("QLD", 0.4)
        risk_engine.update_correlation("SSO", 0.5)

        assert risk_engine.check_cb_correlation() is False
        assert risk_engine.get_correlation_exposure_multiplier() == 1.0

    def test_correlation_in_check_all(self, configured_engine):
        """Test Level 4 CB is included in check_all result."""
        configured_engine.update_correlation("QLD", 0.7)
        configured_engine.update_correlation("SSO", 0.75)

        result = configured_engine.check_all(
            current_equity=100_000.0,
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert SafeguardType.CB_CORRELATION in result.active_safeguards
        assert result.exposure_multiplier == 0.5
        assert result.circuit_breaker_level == 4


class TestCBLevel5GreeksBreach:
    """Tests for V2.1 Circuit Breaker Level 5: Greeks Breach."""

    def test_greeks_breach_delta(self, risk_engine):
        """Test Level 5 CB triggers on delta breach."""
        # GreeksSnapshot imported at module level

        greeks = GreeksSnapshot(delta=0.9, gamma=0.01, vega=0.1, theta=-0.01)
        risk_engine.update_greeks(greeks)

        is_breach, options = risk_engine.check_cb_greeks_breach()
        assert is_breach is True
        assert "ALL_OPTIONS" in options

    def test_greeks_breach_gamma(self, risk_engine):
        """Test Level 5 CB triggers on gamma warning."""
        # GreeksSnapshot imported at module level

        greeks = GreeksSnapshot(delta=0.5, gamma=0.1, vega=0.1, theta=-0.01)
        risk_engine.update_greeks(greeks)

        is_breach, options = risk_engine.check_cb_greeks_breach()
        assert is_breach is True

    def test_greeks_breach_theta(self, risk_engine):
        """Test Level 5 CB triggers on theta warning."""
        # GreeksSnapshot imported at module level

        greeks = GreeksSnapshot(delta=0.5, gamma=0.01, vega=0.1, theta=-0.05)
        risk_engine.update_greeks(greeks)

        is_breach, options = risk_engine.check_cb_greeks_breach()
        assert is_breach is True

    def test_greeks_no_breach_within_limits(self, risk_engine):
        """Test Level 5 CB doesn't trigger when Greeks within limits."""
        # GreeksSnapshot imported at module level

        greeks = GreeksSnapshot(delta=0.5, gamma=0.02, vega=0.2, theta=-0.01)
        risk_engine.update_greeks(greeks)

        is_breach, options = risk_engine.check_cb_greeks_breach()
        assert is_breach is False
        assert len(options) == 0

    def test_greeks_breach_in_check_all(self, configured_engine):
        """Test Level 5 CB is included in check_all result."""
        # GreeksSnapshot imported at module level

        greeks = GreeksSnapshot(delta=0.9, gamma=0.01, vega=0.1, theta=-0.01)
        configured_engine.update_greeks(greeks)

        result = configured_engine.check_all(
            current_equity=100_000.0,
            spy_price=450.0,
            spy_bar_range=0.5,
            current_time=datetime(2024, 1, 15, 10, 30),
        )

        assert SafeguardType.CB_GREEKS_BREACH in result.active_safeguards
        assert result.can_enter_options is False
        assert len(result.options_to_close) > 0
        assert result.circuit_breaker_level == 5


class TestCircuitBreakerLevelTracking:
    """Tests for circuit breaker level tracking."""

    def test_level_tracking_increases(self, configured_engine):
        """Test circuit breaker level increases with severity."""
        # Level 1: Daily loss
        configured_engine.check_cb_daily_loss(98_000.0)
        assert configured_engine.get_current_circuit_breaker_level() == 1

        # Level 3: Portfolio vol
        high_vol_returns = [0.03, -0.03, 0.04, -0.04, 0.05, -0.05, 0.03, -0.03]
        for r in high_vol_returns:
            configured_engine.update_daily_return(r)
        configured_engine.check_cb_portfolio_vol()
        assert configured_engine.get_current_circuit_breaker_level() == 3

        # Level 4: Correlation
        configured_engine.update_correlation("QLD", 0.7)
        configured_engine.update_correlation("SSO", 0.75)
        configured_engine.check_cb_correlation()
        assert configured_engine.get_current_circuit_breaker_level() == 4

    def test_level_resets_daily(self, configured_engine):
        """Test circuit breaker level resets on daily reset."""
        configured_engine.check_cb_daily_loss(98_000.0)
        assert configured_engine.get_current_circuit_breaker_level() == 1

        configured_engine.reset_daily_state()
        assert configured_engine.get_current_circuit_breaker_level() == 0


class TestCBStatePersistence:
    """Tests for V2.1 circuit breaker state persistence."""

    def test_v2_state_persistence(self, risk_engine):
        """Test V2.1 state is included in persistence."""
        # Set up V2.1 state
        risk_engine.update_daily_return(0.01)
        risk_engine.update_daily_return(-0.02)
        risk_engine.update_correlation("QLD", 0.7)
        risk_engine._cb_portfolio_vol_active = True
        risk_engine._cb_correlation_active = True

        state = risk_engine.get_state_for_persistence()

        assert "daily_returns" in state
        assert "position_correlations" in state
        assert "cb_portfolio_vol_active" in state
        assert "cb_correlation_active" in state

    def test_v2_state_load(self, risk_engine):
        """Test V2.1 state is loaded correctly."""
        state = {
            "last_kill_date": None,
            "week_start_equity": 100_000.0,
            "weekly_breaker_active": False,
            "daily_returns": [0.01, -0.02, 0.015],
            "position_correlations": {"QLD": 0.7, "SSO": 0.6},
            "cb_portfolio_vol_active": True,
            "cb_correlation_active": True,
        }

        risk_engine.load_state(state)

        assert len(risk_engine._daily_returns) == 3
        assert risk_engine._position_correlations["QLD"] == 0.7
        assert risk_engine._cb_portfolio_vol_active is True
        assert risk_engine._cb_correlation_active is True


class TestCBStatusReporting:
    """Tests for V2.1 circuit breaker status reporting."""

    def test_get_all_statuses_includes_v2(self, risk_engine):
        """Test get_all_statuses includes V2.1 circuit breakers."""
        current_time = datetime(2024, 1, 15, 10, 30)
        statuses = risk_engine.get_all_statuses(current_time)

        assert "cb_daily_loss" in statuses
        assert "cb_portfolio_vol" in statuses
        assert "cb_correlation" in statuses
        assert "cb_greeks" in statuses

    def test_cb_daily_loss_status(self, risk_engine):
        """Test CB daily loss status reporting."""
        risk_engine.set_equity_prior_close(100_000.0)
        risk_engine.check_cb_daily_loss(98_000.0)

        status = risk_engine.get_cb_daily_loss_status()
        assert status.safeguard_type == SafeguardType.CB_DAILY_LOSS
        assert status.is_active is True
        assert "50%" in status.details

    def test_cb_portfolio_vol_status(self, risk_engine):
        """Test CB portfolio vol status reporting."""
        status = risk_engine.get_cb_portfolio_vol_status()
        assert status.safeguard_type == SafeguardType.CB_PORTFOLIO_VOL
        # Should include current vol in details
        assert "vol" in status.details.lower()

    def test_cb_correlation_status(self, risk_engine):
        """Test CB correlation status reporting."""
        risk_engine.update_correlation("QLD", 0.5)
        status = risk_engine.get_cb_correlation_status()
        assert status.safeguard_type == SafeguardType.CB_CORRELATION
        assert "correlation" in status.details.lower()

    def test_cb_greeks_status(self, risk_engine):
        """Test CB Greeks status reporting."""
        # GreeksSnapshot imported at module level

        greeks = GreeksSnapshot(delta=0.5, gamma=0.02, vega=0.2, theta=-0.01)
        risk_engine.update_greeks(greeks)

        status = risk_engine.get_cb_greeks_status()
        assert status.safeguard_type == SafeguardType.CB_GREEKS_BREACH
        # Should include Greeks values in details
        assert "D=" in status.details or "delta" in status.details.lower()
