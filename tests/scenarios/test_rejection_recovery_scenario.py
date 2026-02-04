"""
V2.20: Scenario tests for Event-Driven State Recovery (Rejection Listener).

Tests the complete rejection recovery workflow:
1. Trend MOO rejection → pending_moo_symbols cleared
2. Options Intraday rejection → pending_intraday_entry cleared + counter decremented
3. Options Spread rejection → spread pending state + entry_attempted_today cleared

These tests verify that the wiring between engines and the rejection handler
works end-to-end, matching the user's verification requirements.
"""

import pytest

import config
from engines.core.cold_start_engine import ColdStartEngine
from engines.core.trend_engine import TrendEngine
from engines.satellite.mean_reversion_engine import MeanReversionEngine
from engines.satellite.options_engine import OptionsEngine


class TestTrendMOORejectionScenario:
    """Scenario: TNA MOO submitted at 15:45, rejected at 09:30 next day."""

    def test_trend_moo_rejection_full_cycle(self):
        """
        1. Mark TNA as pending MOO (simulating 15:45 submission).
        2. Simulate broker rejection.
        3. Verify pending_moo_symbols is empty.
        4. Verify new entry signal for TNA is not blocked by pending check.
        """
        engine = TrendEngine(algorithm=None)

        # Step 1: Mark TNA pending (simulates main.py calling mark_pending_moo)
        engine.mark_pending_moo("TNA")
        assert "TNA" in engine._pending_moo_symbols
        assert len(engine._pending_moo_symbols) == 1

        # Step 2: Simulate rejection via cancel_pending_moo
        engine.cancel_pending_moo("TNA")

        # Step 3: Verify pending cleared
        assert "TNA" not in engine._pending_moo_symbols
        assert len(engine._pending_moo_symbols) == 0

    def test_trend_moo_rejection_does_not_affect_other_symbols(self):
        """Rejecting TNA should not affect QLD pending status."""
        engine = TrendEngine(algorithm=None)

        engine.mark_pending_moo("TNA")
        engine.mark_pending_moo("QLD")
        assert len(engine._pending_moo_symbols) == 2

        # Only TNA rejected
        engine.cancel_pending_moo("TNA")

        assert "TNA" not in engine._pending_moo_symbols
        assert "QLD" in engine._pending_moo_symbols


class TestOptionsIntradayRejectionScenario:
    """Scenario: Intraday limit order rejected by broker."""

    def test_intraday_rejection_counter_recovery(self):
        """
        1. Set up options engine with pending intraday entry + counter=1.
        2. Simulate rejection via cancel_pending_intraday_entry.
        3. Verify pending cleared and counter decremented.
        4. Verify next signal can fire (pending_intraday_entry is False).
        """
        engine = OptionsEngine(algorithm=None)

        # Step 1: Simulate signal generation state
        engine._pending_intraday_entry = True
        engine._pending_contract = "QQQ 240119C00450000"
        engine._pending_num_contracts = 1
        engine._pending_stop_pct = 0.50
        engine._intraday_trades_today = 1
        engine._total_options_trades_today = 1
        engine._trades_today = 1

        # Step 2: Simulate rejection
        engine.cancel_pending_intraday_entry()

        # Step 3: Verify state cleared
        assert engine._pending_intraday_entry is False
        assert engine._pending_contract is None
        assert engine._pending_num_contracts is None
        assert engine._intraday_trades_today == 0
        assert engine._total_options_trades_today == 0
        assert engine._trades_today == 0

        # Step 4: Verify next signal can fire
        assert engine._pending_intraday_entry is False

    def test_intraday_rejection_allows_new_signal(self):
        """After rejection recovery, engine should accept new intraday signals."""
        engine = OptionsEngine(algorithm=None)

        # Set pending state
        engine._pending_intraday_entry = True
        engine._intraday_trades_today = 1
        engine._total_options_trades_today = 1
        engine._trades_today = 1

        # Recovery
        engine.cancel_pending_intraday_entry()

        # Verify trade limit not consumed
        assert engine._intraday_trades_today < config.INTRADAY_MAX_TRADES_PER_DAY


class TestSpreadRejectionScenario:
    """Scenario: Swing spread rejected by broker."""

    def test_spread_rejection_clears_all_state(self):
        """
        1. Set up options engine with pending spread entry.
        2. Simulate rejection via cancel_pending_spread_entry.
        3. Verify all spread fields cleared + entry_attempted_today reset.
        """
        engine = OptionsEngine(algorithm=None)

        # Step 1: Simulate spread signal generation state
        engine._pending_spread_long_leg = "QQQ 240315C00450000"
        engine._pending_spread_short_leg = "QQQ 240315C00460000"
        engine._pending_spread_type = "BULL_CALL"
        engine._pending_net_debit = 2.50
        engine._pending_max_profit = 7.50
        engine._pending_spread_width = 10.0
        engine._pending_num_contracts = 3
        engine._pending_entry_score = 4.2
        engine._entry_attempted_today = True

        # Step 2: Simulate rejection
        engine.cancel_pending_spread_entry()

        # Step 3: Verify all state cleared
        assert engine._pending_spread_long_leg is None
        assert engine._pending_spread_short_leg is None
        assert engine._pending_spread_type is None
        assert engine._pending_net_debit is None
        assert engine._pending_max_profit is None
        assert engine._pending_spread_width is None
        assert engine._pending_num_contracts is None
        assert engine._pending_entry_score is None
        assert engine._entry_attempted_today is False
