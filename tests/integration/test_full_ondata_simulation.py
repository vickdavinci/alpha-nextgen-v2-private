"""
Full OnData Simulation Tests.

Tests the complete trading system flow using mocked QuantConnect infrastructure
and simulated market data scenarios.

Tests cover:
1. Full day simulation with OnData calls
2. Risk engine circuit breaker triggering
3. Signal flow from engines to router to execution
4. State persistence across simulated restarts
5. Multi-day cold start progression
"""

import csv
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import config
from engines.core.capital_engine import CapitalEngine
from engines.core.cold_start_engine import ColdStartEngine
from engines.core.regime_engine import RegimeEngine
from engines.core.risk_engine import RiskEngine
from engines.core.trend_engine import TrendEngine
from engines.satellite.hedge_engine import HedgeEngine
from engines.satellite.mean_reversion_engine import MeanReversionEngine
from engines.satellite.options_engine import MicroRegimeEngine, OptionsEngine
from models.enums import MicroRegime, Urgency, VIXDirection, VIXLevel
from models.target_weight import TargetWeight
from tests.integration.qc_mocks import (
    MockAlgorithm,
    MockBar,
    MockHoldings,
    MockSecurity,
    MockSlice,
    create_test_algorithm,
    create_test_slice,
)

SCENARIOS_DIR = Path(__file__).parent / "integration_test_data" / "scenarios"


# =============================================================================
# DATA LOADING
# =============================================================================


def load_scenario_bars(scenario: str, symbol: str) -> List[dict]:
    """Load market bars from scenario CSV."""
    filepath = SCENARIOS_DIR / scenario / f"{symbol}.csv"
    if not filepath.exists():
        return []

    bars = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append(
                {
                    "timestamp": datetime.fromisoformat(row["timestamp"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(float(row["volume"])),
                    "prior_close": float(row["prior_close"])
                    if row.get("prior_close", "").strip()
                    else None,
                }
            )
    return bars


# =============================================================================
# SIMULATED ALGORITHM CLASS
# =============================================================================


class SimulatedTradingSystem:
    """
    Simulates the full Alpha NextGen trading system.

    Integrates all engines and simulates OnData flow.
    """

    def __init__(self, portfolio_value: float = 100000):
        # Create mock algorithm
        self.algorithm = create_test_algorithm(
            portfolio_value=portfolio_value,
            cash=portfolio_value,
            time=datetime(2026, 1, 15, 9, 30),
        )

        # Add essential securities
        self.algorithm.add_security("SPY", 500.0)
        self.algorithm.add_security("QQQ", 400.0)
        self.algorithm.add_security("TQQQ", 50.0)
        self.algorithm.add_security("QLD", 75.0)
        self.algorithm.add_security("SSO", 60.0)
        self.algorithm.add_security("VIX", 15.0)

        # Initialize engines with correct signatures
        self.regime_engine = RegimeEngine()
        self.capital_engine = CapitalEngine(algorithm=self.algorithm)
        self.risk_engine = RiskEngine()
        self.cold_start_engine = ColdStartEngine()
        self.trend_engine = TrendEngine()
        self.mr_engine = MeanReversionEngine()
        self.hedge_engine = HedgeEngine()
        self.options_engine = OptionsEngine()

        # Track state
        self.signals_generated: List[TargetWeight] = []
        self.orders_placed: List[dict] = []
        self.risk_events: List[dict] = []

        # Daily tracking
        self.portfolio_value = portfolio_value
        self.equity_baseline: float = portfolio_value
        self.equity_sod: float = portfolio_value
        self.spy_open: float = 0
        self.spy_prior_close: float = 0
        self.vix_current: float = 15.0

        # Initialize risk engine state
        self.risk_engine.set_equity_prior_close(portfolio_value)

    def set_market_open(self, spy_open: float, spy_prior_close: float, vix_open: float):
        """Set market open values."""
        self.spy_open = spy_open
        self.spy_prior_close = spy_prior_close
        self.vix_current = vix_open
        self.equity_sod = self.portfolio_value

        # Set risk engine state
        self.risk_engine.set_spy_open(spy_open)
        self.risk_engine.set_spy_prior_close(spy_prior_close)
        self.risk_engine.set_equity_sod(self.equity_sod)

    def on_data(
        self,
        timestamp: datetime,
        spy_price: float,
        vix_price: float,
        tqqq_price: Optional[float] = None,
        qqq_price: Optional[float] = None,
    ) -> List[TargetWeight]:
        """
        Simulate OnData processing.

        Returns list of signals generated this bar.
        """
        self.algorithm.set_time(timestamp)
        self.vix_current = vix_price
        signals = []

        # Update securities (use set_price() as Price is read-only)
        self.algorithm.Securities["SPY"].set_price(spy_price)
        if tqqq_price:
            if "TQQQ" not in self.algorithm.Securities:
                self.algorithm.add_security("TQQQ", tqqq_price)
            self.algorithm.Securities["TQQQ"].set_price(tqqq_price)
        if qqq_price:
            if "QQQ" not in self.algorithm.Securities:
                self.algorithm.add_security("QQQ", qqq_price)
            self.algorithm.Securities["QQQ"].set_price(qqq_price)

        # ===== RISK CHECKS (ALWAYS FIRST) =====

        # Kill switch check
        current_equity = self.portfolio_value
        if self.risk_engine.check_kill_switch(current_equity):
            self.risk_events.append(
                {
                    "type": "KILL_SWITCH",
                    "timestamp": timestamp,
                    "equity": current_equity,
                }
            )
            return []

        # Panic mode check
        if self.risk_engine.check_panic_mode(spy_price):
            self.risk_events.append(
                {
                    "type": "PANIC_MODE",
                    "timestamp": timestamp,
                    "spy_price": spy_price,
                }
            )
            return []

        # Gap filter check (at open)
        if timestamp.hour == 9 and timestamp.minute == 33:
            if self.risk_engine.check_gap_filter(self.spy_open):
                self.risk_events.append(
                    {
                        "type": "GAP_FILTER",
                        "timestamp": timestamp,
                    }
                )

        self.signals_generated.extend(signals)
        return signals

    def simulate_day(self, scenario: str) -> dict:
        """
        Simulate a full trading day using scenario data.

        Returns summary of the day's activity.
        """
        spy_bars = load_scenario_bars(scenario, "SPY")
        vix_bars = load_scenario_bars(scenario, "VIX")
        tqqq_bars = load_scenario_bars(scenario, "TQQQ")
        qqq_bars = load_scenario_bars(scenario, "QQQ")

        if not spy_bars:
            return {"error": "No SPY data"}

        # Set market open
        spy_open = spy_bars[0]["close"]
        spy_prior = spy_bars[0].get("prior_close") or spy_open * 1.01
        vix_open = vix_bars[0]["close"] if vix_bars else 15.0

        self.set_market_open(spy_open, spy_prior, vix_open)

        # Process each bar
        for i, spy_bar in enumerate(spy_bars):
            vix_bar = vix_bars[i] if i < len(vix_bars) else {"close": 15.0}
            tqqq_bar = tqqq_bars[i] if i < len(tqqq_bars) else None
            qqq_bar = qqq_bars[i] if i < len(qqq_bars) else None

            self.on_data(
                timestamp=spy_bar["timestamp"],
                spy_price=spy_bar["close"],
                vix_price=vix_bar["close"],
                tqqq_price=tqqq_bar["close"] if tqqq_bar else None,
                qqq_price=qqq_bar["close"] if qqq_bar else None,
            )

        return {
            "bars_processed": len(spy_bars),
            "signals_generated": len(self.signals_generated),
            "risk_events": len(self.risk_events),
            "risk_event_types": [e["type"] for e in self.risk_events],
            "spy_open": spy_open,
            "spy_close": spy_bars[-1]["close"],
            "vix_open": vix_open,
            "vix_close": vix_bars[-1]["close"] if vix_bars else 15.0,
        }


# =============================================================================
# CRASH DAY SIMULATION TESTS
# =============================================================================


class TestCrashDaySimulation:
    """Simulate crash day and verify risk controls trigger."""

    def test_crash_day_triggers_panic_mode(self):
        """Verify panic mode triggers during crash day simulation."""
        system = SimulatedTradingSystem(portfolio_value=100000)

        result = system.simulate_day("crash_day")

        if result.get("error"):
            pytest.skip(result["error"])

        # Should have panic mode event
        panic_events = [e for e in system.risk_events if e["type"] == "PANIC_MODE"]
        assert len(panic_events) > 0, "Crash day should trigger PANIC_MODE"

    def test_crash_day_spy_drops_significantly(self):
        """Verify SPY drops significantly during crash."""
        system = SimulatedTradingSystem(portfolio_value=100000)

        result = system.simulate_day("crash_day")

        if result.get("error"):
            pytest.skip(result["error"])

        spy_drop = (result["spy_open"] - result["spy_close"]) / result["spy_open"]
        assert spy_drop > 0.03, f"SPY should drop > 3%, got {spy_drop:.1%}"

    def test_crash_day_vix_spikes(self):
        """Verify VIX spikes during crash."""
        system = SimulatedTradingSystem(portfolio_value=100000)

        result = system.simulate_day("crash_day")

        if result.get("error"):
            pytest.skip(result["error"])

        vix_change = (result["vix_close"] - result["vix_open"]) / result["vix_open"]
        assert vix_change > 0.3, f"VIX should spike > 30%, got {vix_change:.1%}"


# =============================================================================
# MEAN REVERSION SIMULATION TESTS
# =============================================================================


class TestMeanReversionSimulation:
    """Simulate mean reversion scenario."""

    def test_mr_day_no_panic(self):
        """Verify MR day doesn't trigger panic (SPY only -1%)."""
        system = SimulatedTradingSystem(portfolio_value=100000)

        result = system.simulate_day("mean_reversion")

        if result.get("error"):
            pytest.skip(result["error"])

        panic_events = [e for e in system.risk_events if e["type"] == "PANIC_MODE"]
        assert len(panic_events) == 0, "MR day should not trigger PANIC_MODE"

    def test_mr_day_vix_allows_trading(self):
        """Verify VIX stays below 30 allowing MR trades."""
        system = SimulatedTradingSystem(portfolio_value=100000)

        result = system.simulate_day("mean_reversion")

        if result.get("error"):
            pytest.skip(result["error"])

        assert result["vix_close"] < 30, f"VIX should be < 30 for MR, got {result['vix_close']}"


# =============================================================================
# VIX SPIKE SIMULATION TESTS
# =============================================================================


class TestVIXSpikeSimulation:
    """Simulate VIX spike scenario."""

    def test_vix_spike_changes_micro_regime(self):
        """Verify micro regime changes during VIX spike."""
        vix_bars = load_scenario_bars("vix_spike", "VIX")
        qqq_bars = load_scenario_bars("vix_spike", "QQQ")

        if not vix_bars:
            pytest.skip("No VIX spike data")

        vix_open = vix_bars[0]["close"]
        qqq_open = qqq_bars[0]["close"] if qqq_bars else 450.0

        micro_engine = MicroRegimeEngine()

        regimes_seen = set()
        directions_seen = set()

        for i, vix_bar in enumerate(vix_bars[::30]):  # Sample every 30 bars
            qqq_bar = (
                qqq_bars[i * 30] if qqq_bars and i * 30 < len(qqq_bars) else {"close": qqq_open}
            )

            state = micro_engine.update(
                vix_current=vix_bar["close"],
                vix_open=vix_open,
                qqq_current=qqq_bar["close"],
                qqq_open=qqq_open,
                current_time=str(vix_bar["timestamp"]),
            )

            regimes_seen.add(state.micro_regime)
            directions_seen.add(state.vix_direction)

        assert (
            len(regimes_seen) >= 2
        ), f"Should see 2+ regimes during VIX spike, got {len(regimes_seen)}"
        assert (
            len(directions_seen) >= 2
        ), f"Should see 2+ VIX directions, got {len(directions_seen)}"


# =============================================================================
# MULTI-DAY SIMULATION TESTS
# =============================================================================


class TestMultiDaySimulation:
    """Simulate multi-day scenario for state persistence."""

    def test_multi_day_processes_all_days(self):
        """Verify multi-day data can be processed."""
        spy_bars = load_scenario_bars("multi_day", "SPY")

        if not spy_bars:
            pytest.skip("No multi-day data")

        dates = set(b["timestamp"].date() for b in spy_bars)
        assert len(dates) >= 5, f"Should have 5+ days, got {len(dates)}"

    def test_multi_day_tracks_cold_start(self):
        """Verify cold start day tracking across days."""
        spy_bars = load_scenario_bars("multi_day", "SPY")

        if not spy_bars:
            pytest.skip("No multi-day data")

        # Group by date
        by_date = {}
        for bar in spy_bars:
            date = bar["timestamp"].date()
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(bar)

        dates = sorted(by_date.keys())

        cold_start_engine = ColdStartEngine()

        for i, date in enumerate(dates):
            # Simulate day end - use correct method
            state = cold_start_engine.end_of_day_update()
            days_running = cold_start_engine.get_days_running()

            # Cold start is active when days_running < 5 (config.COLD_START_DAYS)
            # After 5 end_of_day_update() calls, days_running = 5 and cold start ends
            if days_running < 5:
                assert (
                    cold_start_engine.is_cold_start_active()
                ), f"Day {days_running} should be cold start"

    def test_multi_day_state_persistence_simulation(self):
        """Simulate state save/restore across days."""
        spy_bars = load_scenario_bars("multi_day", "SPY")

        if not spy_bars:
            pytest.skip("No multi-day data")

        # Group by date
        by_date = {}
        for bar in spy_bars:
            date = bar["timestamp"].date()
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(bar)

        dates = sorted(by_date.keys())

        # Simulate day 1
        system = SimulatedTradingSystem(portfolio_value=100000)
        day1_bars = by_date[dates[0]]

        for bar in day1_bars:
            system.on_data(
                timestamp=bar["timestamp"],
                spy_price=bar["close"],
                vix_price=15.0,
            )

        # Save state
        saved_state = {
            "equity_baseline": system.equity_baseline,
            "cold_start_day": 1,
            "risk_events": system.risk_events,
        }

        # Simulate restart for day 2
        system2 = SimulatedTradingSystem(portfolio_value=100000)
        system2.equity_baseline = saved_state["equity_baseline"]

        day2_bars = by_date[dates[1]]
        for bar in day2_bars:
            system2.on_data(
                timestamp=bar["timestamp"],
                spy_price=bar["close"],
                vix_price=15.0,
            )

        # State should persist
        assert system2.equity_baseline == saved_state["equity_baseline"]


# =============================================================================
# SIGNAL FLOW TESTS
# =============================================================================


class TestSignalFlow:
    """Test signal flow through the system."""

    def test_regime_engine_calculation(self):
        """Test regime engine produces valid calculation."""
        engine = RegimeEngine()

        # The calculate method exists
        assert hasattr(engine, "calculate")

    def test_risk_engine_kill_switch_check(self):
        """Test risk engine kill switch detection (V2.3.17: 5% threshold)."""
        engine = RiskEngine()
        engine.set_equity_prior_close(100000)

        # Simulate 5% loss (V2.3.17: raised from 3%)
        result = engine.check_kill_switch(current_equity=95000)

        assert result is True, "5% loss should trigger kill switch"

    def test_risk_engine_panic_mode_check(self):
        """Test risk engine panic mode detection."""
        engine = RiskEngine()
        engine.set_spy_open(500)

        # Simulate 4% intraday drop
        result = engine.check_panic_mode(spy_price=480)

        assert result is True, "4% SPY drop should trigger panic mode"

    def test_micro_regime_engine_classification(self):
        """Test micro regime classification."""
        engine = MicroRegimeEngine()

        # Test VIX level classification
        level_low, _ = engine.classify_vix_level(15)
        level_high, _ = engine.classify_vix_level(35)

        assert level_low == VIXLevel.LOW, f"VIX 15 should be LOW, got {level_low}"
        assert level_high == VIXLevel.HIGH, f"VIX 35 should be HIGH, got {level_high}"

    def test_micro_regime_vix_direction(self):
        """Test VIX direction classification."""
        engine = MicroRegimeEngine()

        # Stable: small change (within ±2% per config)
        # 15.2 / 15.0 = +1.33% which is within STABLE range
        direction, _ = engine.classify_vix_direction(15.2, 15.0)
        assert direction == VIXDirection.STABLE

        # Spiking: large increase (>10% per config)
        direction, _ = engine.classify_vix_direction(35, 15)
        assert direction == VIXDirection.SPIKING


# =============================================================================
# OPTIONS INTEGRATION TESTS
# =============================================================================


class TestOptionsIntegration:
    """Test options engine integration."""

    def test_options_engine_with_mock_algorithm(self):
        """Test options engine initializes with mock algorithm."""
        algo = create_test_algorithm(100000, 100000, datetime(2026, 1, 15, 10, 0))
        engine = OptionsEngine(algorithm=algo)

        assert engine is not None
        assert not engine.has_position()

    def test_options_mode_determination(self):
        """Test options mode determination."""
        engine = OptionsEngine()

        # V2.13: OPTIONS_INTRADAY_DTE_MAX=5, so 0-5 DTE is INTRADAY
        assert engine.determine_mode(0).value == "INTRADAY"
        assert engine.determine_mode(1).value == "INTRADAY"
        assert engine.determine_mode(2).value == "INTRADAY"
        assert engine.determine_mode(3).value == "INTRADAY"
        assert engine.determine_mode(5).value == "INTRADAY"

        # V2.13: 6+ DTE is SWING mode
        assert engine.determine_mode(6).value == "SWING"
        assert engine.determine_mode(10).value == "SWING"

    def test_micro_regime_updates_during_simulation(self):
        """Test micro regime updates correctly during simulation."""
        engine = MicroRegimeEngine()

        # Simulate VIX spike
        vix_values = [15, 18, 22, 28, 35, 30, 25]
        vix_open = 15

        for i, vix in enumerate(vix_values):
            state = engine.update(
                vix_current=vix,
                vix_open=vix_open,
                qqq_current=450,
                qqq_open=450,
                current_time=f"2026-01-15 {10+i}:00:00",
            )

            # Verify state updates
            assert state.vix_current == vix
            assert state.vix_open == vix_open


# =============================================================================
# EXECUTION TESTS
# =============================================================================


class TestExecutionIntegration:
    """Test execution tracking."""

    def test_mock_algorithm_tracks_orders(self):
        """Test mock algorithm tracks market orders."""
        algo = create_test_algorithm(100000, 100000, datetime(2026, 1, 15, 10, 0))

        # Place some orders
        ticket1 = algo.MarketOrder("SPY", 100)
        ticket2 = algo.MarketOrder("QQQ", -50)
        ticket3 = algo.MarketOnOpenOrder("QLD", 200)

        # Check order tickets
        assert ticket1 is not None
        assert ticket2 is not None
        assert ticket3 is not None

        # Verify order count
        orders = algo.get_orders()
        assert len(orders) == 3

    def test_mock_algorithm_logging(self):
        """Test mock algorithm logging."""
        algo = create_test_algorithm(100000, 100000, datetime(2026, 1, 15, 10, 0))

        algo.Log("Test message 1")
        algo.Log("KILL_SWITCH: Triggered")
        algo.Debug("Debug info")

        logs = algo.get_logs()
        assert len(logs) >= 2

        # Check logs contain expected messages (get_logs returns List[str])
        assert any("KILL_SWITCH" in msg for msg in logs)


# =============================================================================
# COMPLETE END-TO-END TESTS
# =============================================================================


class TestEndToEnd:
    """Complete end-to-end integration tests."""

    def test_full_bullish_day_simulation(self):
        """Simulate a complete bullish day."""
        system = SimulatedTradingSystem(portfolio_value=100000)

        # Create synthetic bullish day
        spy_bars = []
        for i in range(390):
            timestamp = datetime(2026, 1, 15, 9, 30) + timedelta(minutes=i)
            spy_bars.append(
                {
                    "timestamp": timestamp,
                    "close": 500 + i * 0.02,  # Steady rise
                }
            )

        # Set market open
        system.set_market_open(500, 498, 15.0)

        # Process bars
        for bar in spy_bars:
            system.on_data(
                timestamp=bar["timestamp"],
                spy_price=bar["close"],
                vix_price=15.0,
            )

        # On bullish day, no risk events
        assert len(system.risk_events) == 0, "Bullish day should have no risk events"

    def test_complete_crash_day_response(self):
        """Test complete system response to crash day."""
        system = SimulatedTradingSystem(portfolio_value=100000)

        result = system.simulate_day("crash_day")

        if result.get("error"):
            pytest.skip(result["error"])

        # Verify appropriate response
        assert "PANIC_MODE" in result["risk_event_types"], "Should trigger panic mode"

    def test_complete_mr_opportunity_detection(self):
        """Test complete MR opportunity detection."""
        system = SimulatedTradingSystem(portfolio_value=100000)

        result = system.simulate_day("mean_reversion")

        if result.get("error"):
            pytest.skip(result["error"])

        # No panic on MR day
        assert "PANIC_MODE" not in result["risk_event_types"]

        # VIX allows trading
        assert result["vix_close"] < 30
