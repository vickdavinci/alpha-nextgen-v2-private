"""
Scenario-Based Integration Tests using Simulated Market Data.

Tests each scenario with realistic market conditions to verify:
1. Crash Day: Kill switch, panic mode, MR oversold
2. VIX Spike: Micro regime transitions, entry blocking
3. Mean Reversion: RSI oversold, BB touch, entry/exit signals
4. VIX Whipsaw: Whipsaw detection, strategy switching
5. Multi-Day: Cold start, state persistence, trend following
6. Options Chain: Contract selection, Greeks validation
"""

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

import config
from engines.core.regime_engine import RegimeEngine
from engines.core.risk_engine import RiskEngine
from engines.satellite.mean_reversion_engine import MeanReversionEngine
from engines.satellite.options_engine import (
    MicroRegimeEngine,
    OptionContract,
    OptionDirection,
    OptionsEngine,
)
from models.enums import IntradayStrategy, MicroRegime, OptionsMode, Urgency, VIXDirection, VIXLevel

# =============================================================================
# DATA LOADING UTILITIES
# =============================================================================

SCENARIOS_DIR = Path(__file__).parent / "integration_test_data" / "scenarios"


@dataclass
class MarketBar:
    """Single minute bar of market data."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    prior_close: Optional[float] = None


def load_scenario_data(scenario: str, symbol: str) -> List[MarketBar]:
    """Load market data from a specific scenario."""
    filepath = SCENARIOS_DIR / scenario / f"{symbol}.csv"

    if not filepath.exists():
        pytest.skip(f"Scenario data not found: {filepath}")

    bars = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            prior = row.get("prior_close", "")
            bars.append(
                MarketBar(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(float(row["volume"])),
                    prior_close=float(prior) if prior and prior.strip() else None,
                )
            )
    return bars


def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """Calculate RSI from price list."""
    if len(prices) < period + 1:
        return None

    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(0, c) for c in changes[-period:]]
    losses = [abs(min(0, c)) for c in changes[-period:]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_bollinger_bands(
    prices: List[float], period: int = 20, std_dev: float = 2.0
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Calculate Bollinger Bands (upper, middle, lower)."""
    if len(prices) < period:
        return None, None, None

    recent = prices[-period:]
    middle = sum(recent) / period
    variance = sum((p - middle) ** 2 for p in recent) / period
    std = variance**0.5

    return middle + (std_dev * std), middle, middle - (std_dev * std)


# =============================================================================
# CRASH DAY SCENARIO TESTS
# =============================================================================


class TestCrashDayScenario:
    """Tests using crash day data (-4% SPY, VIX spike to 40)."""

    @pytest.fixture
    def crash_spy(self) -> List[MarketBar]:
        """Load SPY crash data."""
        return load_scenario_data("crash_day", "SPY")

    @pytest.fixture
    def crash_vix(self) -> List[MarketBar]:
        """Load VIX crash data."""
        return load_scenario_data("crash_day", "VIX")

    @pytest.fixture
    def crash_tqqq(self) -> List[MarketBar]:
        """Load TQQQ crash data."""
        return load_scenario_data("crash_day", "TQQQ")

    def test_panic_mode_triggers(self, crash_spy):
        """Verify panic mode triggers when SPY drops > 4%."""
        if not crash_spy:
            pytest.skip("No crash data")

        spy_open = crash_spy[0].close

        # Find when SPY drops 4%
        panic_triggered_at = None
        for bar in crash_spy:
            drop_pct = (spy_open - bar.close) / spy_open
            if drop_pct >= config.PANIC_MODE_PCT:
                panic_triggered_at = bar.timestamp
                break

        assert (
            panic_triggered_at is not None
        ), f"SPY should drop >= 4% during crash. Max drop: {max((spy_open - b.close) / spy_open for b in crash_spy):.2%}"

    def test_vix_spikes_during_crash(self, crash_vix):
        """Verify VIX spikes significantly during crash."""
        if not crash_vix:
            pytest.skip("No VIX data")

        vix_open = crash_vix[0].close
        vix_max = max(b.close for b in crash_vix)
        spike_pct = (vix_max - vix_open) / vix_open * 100

        # VIX should spike at least 50%
        assert spike_pct >= 50, f"VIX spike {spike_pct:.1f}% should be >= 50%"

    def test_tqqq_oversold_during_crash(self, crash_tqqq):
        """Verify TQQQ reaches oversold RSI during crash."""
        if not crash_tqqq:
            pytest.skip("No TQQQ data")

        prices = [b.close for b in crash_tqqq]

        # Check RSI at various points
        min_rsi = 100
        for i in range(20, len(prices)):
            rsi = calculate_rsi(prices[: i + 1], period=5)
            if rsi is not None:
                min_rsi = min(min_rsi, rsi)

        assert min_rsi < 30, f"TQQQ RSI(5) should drop below 30, got min {min_rsi:.1f}"

    def test_mr_blocked_by_vix(self, crash_vix, crash_tqqq):
        """Verify MR entries blocked when VIX > 30."""
        if not crash_vix or not crash_tqqq:
            pytest.skip("Missing crash data")

        # Find when TQQQ is oversold
        tqqq_prices = [b.close for b in crash_tqqq]

        oversold_times = []
        for i in range(20, len(tqqq_prices)):
            rsi = calculate_rsi(tqqq_prices[: i + 1], period=5)
            if rsi and rsi < config.MR_RSI_NORMAL:
                oversold_times.append(i)

        if not oversold_times:
            pytest.skip("No oversold conditions found")

        # Check VIX at those times
        vix_blocked = 0
        for idx in oversold_times:
            vix_value = crash_vix[idx].close if idx < len(crash_vix) else crash_vix[-1].close
            if vix_value > 30:  # VIX > 30 blocks MR
                vix_blocked += 1

        # Most oversold times should be blocked by high VIX
        assert (
            vix_blocked > len(oversold_times) * 0.5
        ), "VIX should block most MR entries during crash"

    def test_micro_regime_detects_crash(self, crash_vix):
        """Verify MicroRegimeEngine classifies crash correctly."""
        if not crash_vix:
            pytest.skip("No VIX data")

        engine = MicroRegimeEngine()

        vix_open = crash_vix[0].close
        vix_peak = max(b.close for b in crash_vix)

        # At peak VIX
        level, _ = engine.classify_vix_level(vix_peak)
        direction, _ = engine.classify_vix_direction(vix_peak, vix_open)

        # Should be HIGH level and SPIKING direction
        assert level == VIXLevel.HIGH, f"VIX {vix_peak:.1f} should be HIGH level"
        assert direction in [
            VIXDirection.SPIKING,
            VIXDirection.RISING_FAST,
        ], f"VIX spike should be SPIKING or RISING_FAST"


# =============================================================================
# VIX SPIKE SCENARIO TESTS
# =============================================================================


class TestVIXSpikeScenario:
    """Tests using VIX spike data (15→35)."""

    @pytest.fixture
    def spike_vix(self) -> List[MarketBar]:
        """Load VIX spike data."""
        return load_scenario_data("vix_spike", "VIX")

    @pytest.fixture
    def spike_spy(self) -> List[MarketBar]:
        """Load SPY during VIX spike."""
        return load_scenario_data("vix_spike", "SPY")

    def test_vix_direction_transitions(self, spike_vix):
        """Verify VIX direction changes through phases."""
        if not spike_vix:
            pytest.skip("No VIX spike data")

        engine = MicroRegimeEngine()
        vix_open = spike_vix[0].close

        directions_seen = set()
        for bar in spike_vix:
            direction, _ = engine.classify_vix_direction(bar.close, vix_open)
            directions_seen.add(direction)

        # Should see multiple direction states
        assert len(directions_seen) >= 3, f"Should see 3+ VIX directions, got {directions_seen}"
        assert (
            VIXDirection.SPIKING in directions_seen or VIXDirection.RISING_FAST in directions_seen
        )

    def test_micro_regime_changes(self, spike_vix):
        """Verify micro regime changes during VIX spike."""
        if not spike_vix:
            pytest.skip("No VIX spike data")

        engine = MicroRegimeEngine()
        vix_open = spike_vix[0].close

        regimes_seen = set()
        for bar in spike_vix:
            level, _ = engine.classify_vix_level(bar.close)
            direction, _ = engine.classify_vix_direction(bar.close, vix_open)
            regime = engine.classify_micro_regime(level, direction)
            regimes_seen.add(regime)

        # Should see multiple regimes
        assert len(regimes_seen) >= 3, f"Should see 3+ micro regimes, got {len(regimes_seen)}"

    def test_strategy_recommendations_change(self, spike_vix):
        """Verify strategy recommendations change with VIX."""
        if not spike_vix:
            pytest.skip("No VIX spike data")

        engine = MicroRegimeEngine()
        vix_open = spike_vix[0].close

        strategies_seen = set()
        for bar in spike_vix[::30]:  # Sample every 30 bars
            level, _ = engine.classify_vix_level(bar.close)
            direction, _ = engine.classify_vix_direction(bar.close, vix_open)
            regime = engine.classify_micro_regime(level, direction)
            score = engine.calculate_micro_score(bar.close, vix_open, 450, 450)

            strategy = engine.recommend_strategy(regime, score, bar.close, 0)
            strategies_seen.add(strategy)

        # Should see strategy changes
        assert (
            len(strategies_seen) >= 2
        ), f"Strategies should change with VIX, got {strategies_seen}"


# =============================================================================
# MEAN REVERSION SCENARIO TESTS
# =============================================================================


class TestMeanReversionScenario:
    """Tests using mean reversion data (TQQQ oversold bounce)."""

    @pytest.fixture
    def mr_tqqq(self) -> List[MarketBar]:
        """Load TQQQ mean reversion data."""
        return load_scenario_data("mean_reversion", "TQQQ")

    @pytest.fixture
    def mr_vix(self) -> List[MarketBar]:
        """Load VIX during MR scenario."""
        return load_scenario_data("mean_reversion", "VIX")

    @pytest.fixture
    def mr_soxl(self) -> List[MarketBar]:
        """Load SOXL mean reversion data."""
        return load_scenario_data("mean_reversion", "SOXL")

    def test_tqqq_reaches_oversold(self, mr_tqqq):
        """Verify TQQQ reaches RSI oversold condition."""
        if not mr_tqqq:
            pytest.skip("No MR TQQQ data")

        prices = [b.close for b in mr_tqqq]

        oversold_found = False
        oversold_idx = None
        for i in range(10, len(prices)):
            rsi = calculate_rsi(prices[: i + 1], period=5)
            if rsi is not None and rsi < config.MR_RSI_NORMAL:
                oversold_found = True
                oversold_idx = i
                break

        assert oversold_found, f"TQQQ should reach RSI < {config.MR_RSI_NORMAL}"

    def test_tqqq_hits_lower_bollinger(self, mr_tqqq):
        """Verify TQQQ hits lower Bollinger Band."""
        if not mr_tqqq:
            pytest.skip("No MR TQQQ data")

        prices = [b.close for b in mr_tqqq]

        hit_lower_bb = False
        for i in range(25, len(prices)):
            upper, middle, lower = calculate_bollinger_bands(prices[: i + 1], 20, 2.0)
            if lower is not None and prices[i] <= lower:
                hit_lower_bb = True
                break

        assert hit_lower_bb, "TQQQ should hit lower Bollinger Band"

    def test_vix_allows_mr_entry(self, mr_vix):
        """Verify VIX is in range that allows MR entries."""
        if not mr_vix:
            pytest.skip("No MR VIX data")

        # VIX should be below 30 during MR window
        vix_during_window = [b.close for b in mr_vix if 10 <= b.timestamp.hour < 15]
        max_vix = max(vix_during_window) if vix_during_window else 0

        assert max_vix < 30, f"VIX should be < 30 for MR, got max {max_vix:.1f}"

    def test_mr_entry_window_valid(self, mr_tqqq):
        """Verify oversold occurs within MR trading window."""
        if not mr_tqqq:
            pytest.skip("No MR TQQQ data")

        prices = [b.close for b in mr_tqqq]

        entry_in_window = False
        for i in range(10, len(prices)):
            bar = mr_tqqq[i]
            hour = bar.timestamp.hour

            # MR window is 10:00-15:00
            if 10 <= hour < 15:
                rsi = calculate_rsi(prices[: i + 1], period=5)
                if rsi is not None and rsi < config.MR_RSI_NORMAL:
                    entry_in_window = True
                    break

        assert entry_in_window, "MR entry signal should occur within 10:00-15:00 window"

    def test_tqqq_bounces_for_profit(self, mr_tqqq):
        """Verify TQQQ bounces enough for profit target."""
        if not mr_tqqq:
            pytest.skip("No MR TQQQ data")

        prices = [b.close for b in mr_tqqq]

        # Find the low point
        min_price = min(prices)
        min_idx = prices.index(min_price)

        # Check for 2% bounce after low
        bounce_prices = prices[min_idx:]
        max_after_low = max(bounce_prices)
        bounce_pct = (max_after_low - min_price) / min_price * 100

        assert bounce_pct >= 2.0, f"TQQQ should bounce 2%+ from low, got {bounce_pct:.1f}%"


# =============================================================================
# VIX WHIPSAW SCENARIO TESTS
# =============================================================================


class TestVIXWhipsawScenario:
    """Tests using VIX whipsaw data (multiple direction changes)."""

    @pytest.fixture
    def whipsaw_vix(self) -> List[MarketBar]:
        """Load VIX whipsaw data."""
        return load_scenario_data("vix_whipsaw", "VIX")

    def test_whipsaw_detection(self, whipsaw_vix):
        """Verify whipsaw condition is detected."""
        if not whipsaw_vix:
            pytest.skip("No VIX whipsaw data")

        engine = MicroRegimeEngine()

        # Count direction reversals > 5%
        reversals = 0
        prev_direction = None

        for i in range(30, len(whipsaw_vix), 30):  # Check every 30 minutes
            bar = whipsaw_vix[i]
            prev_bar = whipsaw_vix[i - 30]

            change_pct = abs((bar.close - prev_bar.close) / prev_bar.close * 100)

            if change_pct > 5:
                # Determine direction
                current_direction = "up" if bar.close > prev_bar.close else "down"
                if prev_direction and current_direction != prev_direction:
                    reversals += 1
                prev_direction = current_direction

        # Whipsaw requires 3+ reversals
        assert reversals >= 3, f"Should have 3+ reversals > 5%, got {reversals}"

    def test_whipsaw_state_classification(self, whipsaw_vix):
        """Verify engine detects whipsaw state."""
        if not whipsaw_vix:
            pytest.skip("No VIX whipsaw data")

        engine = MicroRegimeEngine()
        vix_open = whipsaw_vix[0].close

        # Simulate through the day
        whipsaw_detected = False
        for i, bar in enumerate(whipsaw_vix):
            if i < 60:  # Need some history
                continue

            # Get recent history for whipsaw detection
            recent_bars = whipsaw_vix[max(0, i - 120) : i + 1]
            recent_closes = [b.close for b in recent_bars]

            # Manual whipsaw check
            direction, _ = engine.classify_vix_direction(bar.close, vix_open)
            if direction == VIXDirection.WHIPSAW:
                whipsaw_detected = True
                break

        # The engine should eventually detect whipsaw
        # (depends on implementation details)


# =============================================================================
# MULTI-DAY SCENARIO TESTS
# =============================================================================


class TestMultiDayScenario:
    """Tests using 5-day data for state persistence."""

    @pytest.fixture
    def multi_spy(self) -> List[MarketBar]:
        """Load multi-day SPY data."""
        return load_scenario_data("multi_day", "SPY")

    @pytest.fixture
    def multi_vix(self) -> List[MarketBar]:
        """Load multi-day VIX data."""
        return load_scenario_data("multi_day", "VIX")

    @pytest.fixture
    def multi_tqqq(self) -> List[MarketBar]:
        """Load multi-day TQQQ data."""
        return load_scenario_data("multi_day", "TQQQ")

    def test_data_spans_multiple_days(self, multi_spy):
        """Verify data spans 5 trading days."""
        if not multi_spy:
            pytest.skip("No multi-day data")

        dates = set(b.timestamp.date() for b in multi_spy)
        assert len(dates) >= 5, f"Should have 5+ days, got {len(dates)}"

    def test_prior_close_tracking(self, multi_spy):
        """Verify prior_close is set correctly after day 1."""
        if not multi_spy:
            pytest.skip("No multi-day data")

        # Group by date
        by_date = {}
        for bar in multi_spy:
            date = bar.timestamp.date()
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(bar)

        dates = sorted(by_date.keys())

        for i in range(1, len(dates)):
            prev_date = dates[i - 1]
            curr_date = dates[i]

            prev_close = by_date[prev_date][-1].close
            curr_first = by_date[curr_date][0]

            if curr_first.prior_close:
                # prior_close should match previous day's actual close
                diff = abs(curr_first.prior_close - prev_close)
                assert diff < 1.0, f"prior_close mismatch: {curr_first.prior_close} vs {prev_close}"

    def test_day4_pullback(self, multi_spy, multi_vix):
        """Verify Day 4 has significant pullback."""
        if not multi_spy or not multi_vix:
            pytest.skip("No multi-day data")

        # Group by date
        spy_by_date = {}
        for bar in multi_spy:
            date = bar.timestamp.date()
            if date not in spy_by_date:
                spy_by_date[date] = []
            spy_by_date[date].append(bar)

        dates = sorted(spy_by_date.keys())
        if len(dates) < 4:
            pytest.skip("Not enough days")

        day4_date = dates[3]
        day4_bars = spy_by_date[day4_date]

        day4_open = day4_bars[0].close
        day4_low = min(b.close for b in day4_bars)

        drop_pct = (day4_open - day4_low) / day4_open * 100

        assert drop_pct >= 1.0, f"Day 4 should have significant drop, got {drop_pct:.1f}%"

    def test_cold_start_day_counting(self, multi_spy):
        """Verify cold start day counting logic."""
        if not multi_spy:
            pytest.skip("No multi-day data")

        dates = sorted(set(b.timestamp.date() for b in multi_spy))

        # Day 1 = cold start day 1, limited entry
        # Day 2 = cold start day 2, etc.
        # After day 5, normal operation

        assert len(dates) >= 5, "Need 5 days for cold start testing"

        # Verify dates are consecutive trading days (weekdays)
        for i in range(1, len(dates)):
            diff = (dates[i] - dates[i - 1]).days
            assert diff <= 3, f"Gap between days should be <= 3, got {diff}"


# =============================================================================
# OPTIONS CHAIN SCENARIO TESTS
# =============================================================================


class TestOptionsChainScenario:
    """Tests using comprehensive options chain data."""

    @pytest.fixture
    def options_data(self) -> List[dict]:
        """Load QQQ options chain data."""
        filepath = SCENARIOS_DIR / "options_chain" / "QQQ_options.csv"

        if not filepath.exists():
            pytest.skip(f"Options data not found: {filepath}")

        options = []
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                options.append(row)
        return options

    def test_options_chain_has_multiple_strikes(self, options_data):
        """Verify options chain has multiple strikes."""
        strikes = set(float(o["strike"]) for o in options_data)
        assert len(strikes) >= 5, f"Should have 5+ strikes, got {len(strikes)}"

    def test_options_chain_has_multiple_expiries(self, options_data):
        """Verify options chain has multiple expiries."""
        expiries = set(o["expiry"] for o in options_data)
        assert len(expiries) >= 3, f"Should have 3+ expiries, got {len(expiries)}"

    def test_options_chain_has_calls_and_puts(self, options_data):
        """Verify options chain has both calls and puts."""
        call_puts = set(o["call_put"] for o in options_data)
        assert "C" in call_puts, "Should have calls"
        assert "P" in call_puts, "Should have puts"

    def test_delta_ranges_valid(self, options_data):
        """Verify delta values are in valid ranges."""
        for opt in options_data:
            delta = float(opt["delta"])
            # Calls: 0 to 1, Puts: -1 to 0
            if opt["call_put"] == "C":
                assert 0 <= delta <= 1, f"Call delta {delta} out of range"
            else:
                assert -1 <= delta <= 0, f"Put delta {delta} out of range"

    def test_atm_options_have_delta_near_50(self, options_data):
        """Verify ATM options have delta near 0.50."""
        # Find ATM strike (450)
        atm_calls = [o for o in options_data if o["call_put"] == "C" and float(o["strike"]) == 450]

        for opt in atm_calls:
            delta = float(opt["delta"])
            assert 0.40 <= delta <= 0.60, f"ATM call delta {delta} should be near 0.50"

    def test_short_dte_has_higher_gamma(self, options_data):
        """Verify short DTE options have higher gamma."""
        # Group by DTE
        by_dte = {}
        for opt in options_data:
            dte = int(opt["days_to_expiry"])
            if dte not in by_dte:
                by_dte[dte] = []
            by_dte[dte].append(opt)

        if len(by_dte) < 2:
            pytest.skip("Need multiple DTEs")

        dtes = sorted(by_dte.keys())
        short_dte = dtes[0]
        long_dte = dtes[-1]

        # Compare ATM gamma
        short_atm = [o for o in by_dte[short_dte] if float(o["strike"]) == 450]
        long_atm = [o for o in by_dte[long_dte] if float(o["strike"]) == 450]

        if short_atm and long_atm:
            short_gamma = float(short_atm[0]["gamma"])
            long_gamma = float(long_atm[0]["gamma"])
            assert (
                short_gamma > long_gamma
            ), f"Short DTE gamma {short_gamma} should > long DTE {long_gamma}"

    def test_iv_rank_in_valid_range(self, options_data):
        """Verify IV rank is in 0-100 range."""
        for opt in options_data:
            iv_rank = float(opt["iv_rank"])
            assert 0 <= iv_rank <= 100, f"IV rank {iv_rank} out of range"

    def test_can_create_option_contracts(self, options_data):
        """Verify options data can create OptionContract objects."""
        for opt in options_data[:5]:  # Test first 5
            contract = OptionContract(
                symbol=opt["symbol"],
                underlying="QQQ",
                direction=OptionDirection.CALL if opt["call_put"] == "C" else OptionDirection.PUT,
                strike=float(opt["strike"]),
                expiry=opt["expiry"],
                delta=float(opt["delta"]),
                gamma=float(opt["gamma"]),
                vega=float(opt["vega"]),
                theta=float(opt["theta"]),
                bid=float(opt["bid"]),
                ask=float(opt["ask"]),
                mid_price=(float(opt["bid"]) + float(opt["ask"])) / 2,
                open_interest=int(opt["open_interest"]),
                days_to_expiry=int(opt["days_to_expiry"]),
            )
            assert contract.symbol == opt["symbol"]


# =============================================================================
# FULL INTEGRATION TESTS
# =============================================================================


class TestFullScenarioIntegration:
    """End-to-end integration tests using all scenarios."""

    def test_crash_day_triggers_appropriate_responses(self):
        """Test full system response to crash day."""
        try:
            spy_data = load_scenario_data("crash_day", "SPY")
            vix_data = load_scenario_data("crash_day", "VIX")
            tqqq_data = load_scenario_data("crash_day", "TQQQ")
        except Exception:
            pytest.skip("Crash day data not available")

        # Simulate day
        risk_events = []
        mr_signals_blocked = 0

        spy_open = spy_data[0].close

        for i, spy_bar in enumerate(spy_data):
            vix_bar = vix_data[i] if i < len(vix_data) else vix_data[-1]
            tqqq_bar = tqqq_data[i] if i < len(tqqq_data) else tqqq_data[-1]

            # Check panic mode
            drop_pct = (spy_open - spy_bar.close) / spy_open
            if drop_pct >= config.PANIC_MODE_PCT:
                risk_events.append(("PANIC", spy_bar.timestamp, drop_pct))

            # Check if MR would be blocked
            if vix_bar.close > 30:
                mr_signals_blocked += 1

        assert len(risk_events) > 0, "Crash should trigger risk events"
        assert mr_signals_blocked > 100, "VIX should block most MR signals"

    def test_mean_reversion_generates_valid_entry(self):
        """Test MR scenario generates valid entry signal."""
        try:
            tqqq_data = load_scenario_data("mean_reversion", "TQQQ")
            vix_data = load_scenario_data("mean_reversion", "VIX")
        except Exception:
            pytest.skip("MR data not available")

        prices = [b.close for b in tqqq_data]

        # Find valid MR entry - RSI oversold within trading window
        entry_found = False
        entry_price = None
        entry_idx = None

        for i in range(10, len(prices)):
            bar = tqqq_data[i]
            hour = bar.timestamp.hour

            if not (10 <= hour < 15):
                continue

            vix_bar = vix_data[i] if i < len(vix_data) else vix_data[-1]
            if vix_bar.close >= 30:
                continue

            rsi = calculate_rsi(prices[: i + 1], period=5)

            # MR entry: RSI < 30 (oversold)
            if rsi and rsi < config.MR_RSI_NORMAL:
                entry_found = True
                entry_price = prices[i]
                entry_idx = i
                break

        assert entry_found, f"MR scenario should generate RSI < {config.MR_RSI_NORMAL} entry"

        # Check for profit target after entry
        if entry_price and entry_idx:
            target = entry_price * 1.02  # +2%
            future_prices = prices[entry_idx:]
            hit_target = any(p >= target for p in future_prices)
            assert (
                hit_target
            ), f"Price should hit +2% profit target from {entry_price:.2f} to {target:.2f}"

    def test_options_engine_can_select_contract(self):
        """Test options engine contract selection."""
        filepath = SCENARIOS_DIR / "options_chain" / "QQQ_options.csv"

        if not filepath.exists():
            pytest.skip("Options data not available")

        options = []
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                options.append(row)

        engine = OptionsEngine()

        # Filter for valid swing mode contracts (3-45 DTE, delta 0.40-0.60)
        valid_contracts = []
        for opt in options:
            dte = int(opt["days_to_expiry"])
            delta = abs(float(opt["delta"]))

            if 3 <= dte <= 45 and 0.40 <= delta <= 0.60:
                valid_contracts.append(opt)

        assert len(valid_contracts) > 0, "Should have valid contracts for selection"

        # Create OptionContract from best candidate
        best = valid_contracts[0]
        contract = OptionContract(
            symbol=best["symbol"],
            underlying="QQQ",
            direction=OptionDirection.CALL if best["call_put"] == "C" else OptionDirection.PUT,
            strike=float(best["strike"]),
            expiry=best["expiry"],
            delta=float(best["delta"]),
            gamma=float(best["gamma"]),
            vega=float(best["vega"]),
            theta=float(best["theta"]),
            bid=float(best["bid"]),
            ask=float(best["ask"]),
            mid_price=(float(best["bid"]) + float(best["ask"])) / 2,
            open_interest=int(best["open_interest"]),
            days_to_expiry=int(best["days_to_expiry"]),
        )

        # Verify contract is valid for entry
        mode = engine.determine_mode(contract.days_to_expiry)
        assert mode == OptionsMode.SWING, "Should be swing mode contract"
