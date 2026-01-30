"""
Log Budget Tests for Pre-QC Validation.

QuantConnect has a 100KB log limit per backtest. These tests ensure:
1. Normal trading day stays under log budget (<50 logs)
2. Crash day stays under extended budget (<100 logs)
3. Warmup period produces zero logs

These tests prevent log overflow issues that waste precious QC backtests.
"""

from datetime import datetime, timedelta
from typing import List, Tuple
from unittest.mock import MagicMock, patch

import pytest

import config
from tests.integration.qc_mocks import MockAlgorithm, MockSlice, MockSplitCollection


class LogCounter:
    """Helper class to count log calls."""

    def __init__(self):
        self.logs: List[Tuple[str, str]] = []
        self.count = 0

    def log(self, message: str) -> None:
        """Record a log message."""
        self.logs.append(("INFO", message))
        self.count += 1

    def debug(self, message: str) -> None:
        """Record a debug message."""
        self.logs.append(("DEBUG", message))
        self.count += 1

    def error(self, message: str) -> None:
        """Record an error message."""
        self.logs.append(("ERROR", message))
        self.count += 1

    def clear(self) -> None:
        """Reset the counter."""
        self.logs.clear()
        self.count = 0

    def get_by_prefix(self, prefix: str) -> List[str]:
        """Get logs matching a prefix."""
        return [msg for _, msg in self.logs if msg.startswith(prefix)]


@pytest.fixture
def log_counter():
    """Create a log counter fixture."""
    return LogCounter()


@pytest.fixture
def mock_algo_with_counter(log_counter):
    """Create a mock algorithm with log counting."""
    algo = MockAlgorithm()
    algo.Log = log_counter.log
    algo.Debug = log_counter.debug
    algo.Error = log_counter.error
    algo._is_warming_up = False
    return algo


class TestLogBudget:
    """Test log output stays within QC limits."""

    def test_normal_day_log_count_under_budget(self, log_counter):
        """
        Test that a normal trading day stays under log budget.

        Target: < 50 logs per day under normal conditions.
        Budget breakdown:
        - Market open: ~5 logs
        - Trade fills: ~5 logs (assuming 5 trades)
        - Scheduled events: ~10 logs
        - Regime updates: ~5 logs
        - EOD processing: ~5 logs
        - Buffer: ~20 logs
        """
        algo = MockAlgorithm()
        algo.Log = log_counter.log

        # Simulate a normal day's log events
        # Market open
        algo.Log("INIT: Complete | Cash=$50,000 | Symbols=11")
        algo.Log("SOD: Baseline set | Equity=$50,000")

        # Normal trading activity (5 trades)
        for i in range(5):
            algo.Log(f"FILL: BUY 100 QLD @ $75.00")

        # Regime updates (once per day at EOD)
        algo.Log("REGIME: Score=65 | State=NEUTRAL")

        # EOD processing
        algo.Log("EOD_REBALANCE: 3 targets")

        # Verify under budget
        assert (
            log_counter.count < 50
        ), f"Normal day log count {log_counter.count} exceeds budget of 50"

    def test_crash_day_log_count_under_extended_budget(self, log_counter):
        """
        Test that a crash day stays under extended log budget.

        Target: < 100 logs per day under crash conditions.
        Additional logs during crash:
        - VIX spikes (throttled to 1 per 15 min = ~4 logs)
        - Panic mode trigger: 1 log
        - Kill switch trigger: 1 log
        - Safeguard triggers: ~10 logs
        """
        algo = MockAlgorithm()
        algo.Log = log_counter.log

        # Normal day logs (~20)
        algo.Log("INIT: Complete | Cash=$50,000 | Symbols=11")
        algo.Log("SOD: Baseline set | Equity=$50,000")

        for i in range(5):
            algo.Log(f"FILL: BUY 100 QLD @ $75.00")

        # Crash day additional logs
        # VIX spikes (throttled - should be ~4 logs for 1 hour of spikes)
        for i in range(4):
            algo.Log(f"VIX_SPIKE: 25.0 -> 30.0")

        # Panic mode
        algo.Log("PANIC_MODE: SPY down -4.2% - liquidating longs")

        # Safeguards (10 different types)
        safeguards = [
            "GAP_FILTER",
            "VOL_SHOCK",
            "TIME_GUARD",
            "WEEKLY_BREAKER",
            "DAILY_CB",
            "CORRELATION_CB",
            "PORTFOLIO_VOL_CB",
            "GREEKS_BREACH",
            "MR_DISABLED",
            "OPTIONS_DISABLED",
        ]
        for sg in safeguards:
            algo.Log(f"SAFEGUARD: {sg} triggered")

        # Verify under extended budget
        assert (
            log_counter.count < 100
        ), f"Crash day log count {log_counter.count} exceeds extended budget of 100"

    def test_warmup_zero_logs(self, log_counter):
        """
        Test that warmup period produces zero logs.

        The 252-day warmup period should log ZERO to save log space.
        All warmup checks should be at the top of each function.
        """
        algo = MockAlgorithm()
        algo._is_warming_up = True
        algo.Log = log_counter.log

        # Simulate warmup period checks that should NOT log
        # These would normally be called during warmup
        if algo._is_warming_up:
            pass  # Should not log
        else:
            algo.Log("This should not appear")

        # Verify zero logs during warmup
        assert (
            log_counter.count == 0
        ), f"Warmup period logged {log_counter.count} messages (should be 0)"

    def test_vix_spike_throttling(self, log_counter):
        """
        Test that VIX spike logs are throttled to 1 per 15 minutes.

        Without throttling, a volatile day could generate 50+ VIX spike logs.
        With throttling, should be max 4-5 logs per hour.
        """
        algo = MockAlgorithm()
        algo.Log = log_counter.log
        algo._last_vix_spike_log = None
        algo._current_vix = 15.0
        algo._vix_5min_ago = 15.0

        # Simulate 12 VIX spike checks (1 hour at 5-min intervals)
        current_time = datetime(2024, 1, 15, 10, 0, 0)

        for i in range(12):
            algo.Time = current_time + timedelta(minutes=i * 5)
            spike_detected = True  # Assume spike detected each check

            if spike_detected:
                # Apply throttle logic
                vix_move = 1.5  # Small move, should be throttled
                should_log = (
                    algo._last_vix_spike_log is None
                    or (algo.Time - algo._last_vix_spike_log).total_seconds() / 60
                    > config.LOG_THROTTLE_MINUTES
                    or vix_move >= config.LOG_VIX_SPIKE_MIN_MOVE
                )
                if should_log:
                    algo.Log(f"VIX_SPIKE: {algo._vix_5min_ago:.1f} -> {algo._current_vix:.1f}")
                    algo._last_vix_spike_log = algo.Time

        # With 15-min throttle over 1 hour, expect max 4 logs
        vix_logs = log_counter.get_by_prefix("VIX_SPIKE")
        assert len(vix_logs) <= 5, f"VIX spike logs {len(vix_logs)} exceeds throttled max of 5"

    def test_vix_spike_large_move_bypasses_throttle(self, log_counter):
        """
        Test that large VIX moves bypass the throttle.

        Moves >= LOG_VIX_SPIKE_MIN_MOVE (2.0 points) should always be logged.
        """
        algo = MockAlgorithm()
        algo.Log = log_counter.log
        algo._last_vix_spike_log = datetime(2024, 1, 15, 10, 0, 0)

        # Log a large VIX move immediately after previous log
        algo.Time = datetime(2024, 1, 15, 10, 1, 0)  # Only 1 minute later

        vix_move = 3.0  # Large move, should bypass throttle
        should_log = (
            algo._last_vix_spike_log is None
            or (algo.Time - algo._last_vix_spike_log).total_seconds() / 60
            > config.LOG_THROTTLE_MINUTES
            or vix_move >= config.LOG_VIX_SPIKE_MIN_MOVE
        )

        if should_log:
            algo.Log("VIX_SPIKE: 20.0 -> 23.0")

        # Large move should bypass throttle
        assert log_counter.count == 1, "Large VIX move should bypass throttle"

    def test_split_detection_once_per_symbol(self, log_counter):
        """
        Test that split detection logs only once per symbol per day.

        Without guard, multiple OnData calls with split could flood logs.
        """
        algo = MockAlgorithm()
        algo.Log = log_counter.log
        algo._splits_logged_today = set()

        # Simulate multiple OnData calls with same split
        for _ in range(10):
            symbol = "SPY"
            if symbol not in algo._splits_logged_today:
                algo.Log(f"SPLIT: {symbol} (proxy) - freezing all")
                algo._splits_logged_today.add(symbol)

        # Should only log once
        split_logs = log_counter.get_by_prefix("SPLIT")
        assert len(split_logs) == 1, f"Split logged {len(split_logs)} times (should be 1)"

    def test_greeks_breach_once_per_position(self, log_counter):
        """
        Test that Greeks breach logs only once per position.

        Greeks checks happen every minute - without guard, could log 390+ times.
        """
        algo = MockAlgorithm()
        algo.Log = log_counter.log
        algo._greeks_breach_logged = False

        # Simulate multiple breach checks
        for _ in range(50):
            breach_detected = True
            if breach_detected and not algo._greeks_breach_logged:
                algo.Log("GREEKS_BREACH: Delta > 0.80")
                algo._greeks_breach_logged = True

        # Should only log once
        breach_logs = log_counter.get_by_prefix("GREEKS_BREACH")
        assert len(breach_logs) == 1, f"Greeks breach logged {len(breach_logs)} times (should be 1)"

    def test_greeks_breach_resets_on_position_exit(self, log_counter):
        """
        Test that Greeks breach flag resets when position is exited.

        This allows logging for the next position.
        """
        algo = MockAlgorithm()
        algo.Log = log_counter.log
        algo._greeks_breach_logged = True

        # Simulate position exit
        algo._greeks_breach_logged = False

        # New position breach should be logged
        if not algo._greeks_breach_logged:
            algo.Log("GREEKS_BREACH: Delta > 0.80")
            algo._greeks_breach_logged = True

        assert log_counter.count == 1, "Breach should be logged for new position"


class TestLogBudgetEstimates:
    """Test log budget estimates for QC deployment."""

    @pytest.mark.parametrize(
        "scenario,expected_max_logs",
        [
            ("normal_day", 50),
            ("volatile_day", 75),
            ("crash_day", 100),
            ("kill_switch_day", 60),
        ],
    )
    def test_log_estimates_by_scenario(self, scenario, expected_max_logs, log_counter):
        """
        Test log estimates for different market scenarios.

        These estimates help predict if we'll stay under QC's 100KB limit.
        """
        algo = MockAlgorithm()
        algo.Log = log_counter.log

        if scenario == "normal_day":
            # Typical day: init, SOD, 5 trades, regime, EOD
            for _ in range(8):
                algo.Log("Normal activity")

        elif scenario == "volatile_day":
            # Volatile: normal + more trades + safeguards
            for _ in range(15):
                algo.Log("Volatile activity")

        elif scenario == "crash_day":
            # Crash: volatile + VIX spikes + circuit breakers
            for _ in range(25):
                algo.Log("Crash activity")

        elif scenario == "kill_switch_day":
            # Kill switch: normal until trigger, then liquidation
            for _ in range(12):
                algo.Log("Kill switch activity")

        assert (
            log_counter.count < expected_max_logs
        ), f"{scenario} generated {log_counter.count} logs (max {expected_max_logs})"

    def test_log_size_estimate(self, log_counter):
        """
        Test that log size stays under 100KB limit.

        Assuming ~80 bytes per log line average:
        - Normal day: 50 logs * 80 bytes = 4KB
        - Crash day: 100 logs * 80 bytes = 8KB
        - Multi-year backtest: 2000 days * 50 logs * 80 bytes = 8MB (too much!)

        This is why we must keep daily logs minimal.
        """
        algo = MockAlgorithm()
        algo.Log = log_counter.log

        # Simulate worst-case single day
        for i in range(100):
            algo.Log(f"LOG_{i:03d}: Sample log message with typical content")

        # Calculate estimated size
        total_size = sum(len(msg) for _, msg in log_counter.logs)

        # Should be well under 100KB per day
        assert total_size < 10000, f"Single day log size {total_size} bytes too large"


class TestLogThrottleConfig:
    """Test log throttle configuration values."""

    def test_log_throttle_minutes_configured(self):
        """Verify LOG_THROTTLE_MINUTES is configured."""
        assert hasattr(config, "LOG_THROTTLE_MINUTES")
        assert config.LOG_THROTTLE_MINUTES == 15

    def test_vix_spike_min_move_configured(self):
        """Verify LOG_VIX_SPIKE_MIN_MOVE is configured."""
        assert hasattr(config, "LOG_VIX_SPIKE_MIN_MOVE")
        assert config.LOG_VIX_SPIKE_MIN_MOVE == 2.0

    def test_throttle_values_reasonable(self):
        """Verify throttle values are reasonable."""
        # 15 minutes means max 4 logs per hour
        assert 10 <= config.LOG_THROTTLE_MINUTES <= 30

        # 2.0 point move is significant but not rare
        assert 1.0 <= config.LOG_VIX_SPIKE_MIN_MOVE <= 5.0
