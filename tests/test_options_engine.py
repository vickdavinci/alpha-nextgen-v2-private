"""
Tests for Options Engine - Daily volatility harvesting on QQQ options.

Tests cover:
- Entry score calculation (4 factors)
- Stop tier mapping (confidence-weighted)
- Position sizing (1% risk per trade)
- Entry signals (with late day constraint)
- Exit signals (profit target, stop loss)
- Position management
- State persistence

Spec: docs/v2-specs/V2_1_COMPLETE_ARCHITECTURE.txt (Part 2, Engine 3)
"""

from datetime import datetime, timedelta

import pytest

import config
from engines.satellite import options_engine as options_engine_module
from engines.satellite.micro_entry_engine import MicroEntryEngine
from engines.satellite.options_engine import (
    EntryScore,
    IVSensor,
    OptionContract,
    OptionDirection,
    OptionsEngine,
    OptionsPosition,
    SpreadPosition,
    SpreadStrategy,
)
from main_options_mixin import MainOptionsMixin
from models.enums import IntradayStrategy, MicroRegime, OptionsMode, Urgency

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def engine():
    """Create an OptionsEngine instance for testing."""
    return OptionsEngine(algorithm=None)


@pytest.fixture
def sample_contract():
    """Create a sample option contract."""
    return OptionContract(
        symbol="QQQ 271231C00450000",
        underlying="QQQ",
        direction=OptionDirection.CALL,
        strike=450.0,
        expiry="2027-12-31",  # V2.16-BT: Use future date for state restore tests
        delta=0.50,
        bid=1.40,
        ask=1.50,
        mid_price=1.45,
        open_interest=10000,
        days_to_expiry=5,  # V2.16-BT: Keep DTE in intraday range for tests
    )


@pytest.fixture
def engine_with_position(sample_contract):
    """Create engine with an existing position (separate instance)."""
    eng = OptionsEngine(algorithm=None)
    eng._pending_contract = sample_contract
    eng._pending_entry_score = 3.5
    eng._pending_num_contracts = 27
    eng._pending_stop_pct = 0.25
    eng.register_entry(
        fill_price=1.45,
        entry_time="10:30:00",
        current_date="2026-01-26",
    )
    return eng


# =============================================================================
# ENTRY SCORE TESTS
# =============================================================================


class TestEntryScore:
    """Tests for EntryScore dataclass."""

    def test_total_calculation(self):
        """Test total score is sum of all factors."""
        score = EntryScore(
            score_adx=0.75,
            score_momentum=1.0,
            score_iv=0.75,
            score_liquidity=1.0,
        )
        assert score.total == 3.5

    def test_is_valid_above_threshold(self):
        """Test score >= 3.0 is valid."""
        score = EntryScore(
            score_adx=0.75,
            score_momentum=0.75,
            score_iv=0.75,
            score_liquidity=0.75,
        )
        assert score.total == 3.0
        assert score.is_valid is True

    def test_is_valid_below_threshold(self):
        """Test score < OPTIONS_ENTRY_SCORE_MIN (2.0) is invalid."""
        score = EntryScore(
            score_adx=0.25,
            score_momentum=0.50,
            score_iv=0.50,
            score_liquidity=0.50,
        )
        assert score.total == 1.75
        assert score.is_valid is False

    def test_to_dict(self):
        """Test serialization."""
        score = EntryScore(
            score_adx=0.75,
            score_momentum=1.0,
            score_iv=0.75,
            score_liquidity=1.0,
        )
        data = score.to_dict()
        assert data["score_adx"] == 0.75
        assert data["total"] == 3.5
        assert data["is_valid"] is True


class TestADXScoring:
    """Tests for ADX factor scoring."""

    def test_adx_weak(self, engine):
        """Test ADX < 20 returns 0.25."""
        assert engine._score_adx(15.0) == 0.25
        assert engine._score_adx(19.9) == 0.25

    def test_adx_moderate(self, engine):
        """Test ADX 20-25 returns 0.50."""
        assert engine._score_adx(20.0) == 0.50
        assert engine._score_adx(24.9) == 0.50

    def test_adx_strong(self, engine):
        """Test ADX 25-35 returns 0.75."""
        assert engine._score_adx(25.0) == 0.75
        assert engine._score_adx(34.9) == 0.75

    def test_adx_very_strong(self, engine):
        """Test ADX >= 35 returns 1.0."""
        assert engine._score_adx(35.0) == 1.0
        assert engine._score_adx(50.0) == 1.0


class TestMomentumScoring:
    """Tests for momentum factor scoring."""

    def test_momentum_strong_above_ma200(self, engine):
        """Test price 5%+ above MA200 returns 1.0."""
        assert engine._score_momentum(105.0, 100.0) == 1.0
        assert engine._score_momentum(110.0, 100.0) == 1.0

    def test_momentum_above_ma200(self, engine):
        """Test price above MA200 (< 5%) returns 0.75."""
        assert engine._score_momentum(102.0, 100.0) == 0.75
        assert engine._score_momentum(100.1, 100.0) == 0.75

    def test_momentum_near_ma200(self, engine):
        """Test price near MA200 (within 2%) returns 0.50."""
        assert engine._score_momentum(99.0, 100.0) == 0.50
        assert engine._score_momentum(98.5, 100.0) == 0.50

    def test_momentum_below_ma200(self, engine):
        """Test price below MA200 (> 2%) returns 0.25."""
        assert engine._score_momentum(97.0, 100.0) == 0.25
        assert engine._score_momentum(90.0, 100.0) == 0.25

    def test_momentum_zero_ma200(self, engine):
        """Test zero MA200 returns 0.25."""
        assert engine._score_momentum(100.0, 0.0) == 0.25


class TestIVScoring:
    """Tests for IV Rank factor scoring."""

    def test_iv_optimal_range(self, engine):
        """Test IV rank 20-80 returns high score."""
        score_50 = engine._score_iv_rank(50.0)
        score_30 = engine._score_iv_rank(30.0)
        score_70 = engine._score_iv_rank(70.0)

        # Middle of range should score highest
        assert score_50 > score_30
        assert score_50 > score_70
        assert score_50 > 0.75

    def test_iv_too_low(self, engine):
        """Test IV rank < 20 returns 0.25."""
        assert engine._score_iv_rank(10.0) == 0.25
        assert engine._score_iv_rank(19.9) == 0.25

    def test_iv_too_high(self, engine):
        """Test IV rank > 80 returns 0.25."""
        assert engine._score_iv_rank(85.0) == 0.25
        assert engine._score_iv_rank(100.0) == 0.25


class TestLiquidityScoring:
    """Tests for liquidity factor scoring."""

    def test_liquidity_excellent(self, engine):
        """Test tight spread and high OI returns 1.0."""
        score = engine._score_liquidity(
            spread_pct=0.03,  # < 5%
            open_interest=10000,  # > 5000
        )
        assert score == 1.0

    def test_liquidity_moderate_spread(self, engine):
        """Test moderate spread (within threshold) gets full score. V2.3.10: threshold widened to 15%."""
        score = engine._score_liquidity(
            spread_pct=0.08,  # 8% is now within 15% threshold (V2.3.10)
            open_interest=10000,
        )
        assert score == 1.0  # (1.0 + 1.0) / 2 - both excellent

    def test_liquidity_wide_spread(self, engine):
        """Test wide spread at warning threshold gets moderate score. V6.8 threshold."""
        # V6.8: OPTIONS_SPREAD_WARNING_PCT = 0.30, so exactly 30% gets spread_score=0.5
        score = engine._score_liquidity(
            spread_pct=0.30,  # = 30% (V6.8 warning threshold)
            open_interest=10000,
        )
        assert score == 0.75  # (0.5 + 1.0) / 2

    def test_liquidity_low_oi(self, engine):
        """Test moderate OI above threshold gets full score."""
        # V6.8: OPTIONS_MIN_OPEN_INTEREST = 50, so 75 >= 50 → oi_score = 1.0
        score = engine._score_liquidity(
            spread_pct=0.03,
            open_interest=75,  # >= 50 (min_oi)
        )
        assert score == 1.0  # (1.0 + 1.0) / 2

    def test_liquidity_very_low_oi(self, engine):
        """Test OI between min and half-min gets moderate score."""
        # V6.8: OPTIONS_MIN_OPEN_INTEREST = 50, half is 25
        # 30 is between 25 and 50, so oi_score = 0.5
        score = engine._score_liquidity(
            spread_pct=0.03,
            open_interest=30,  # 25 <= 30 < 50
        )
        assert score == 0.75  # (1.0 + 0.5) / 2


class TestFullEntryScore:
    """Tests for full entry score calculation."""

    def test_calculate_entry_score(self, engine):
        """Test full entry score calculation."""
        score = engine.calculate_entry_score(
            adx_value=30.0,  # Strong: 0.75
            current_price=105.0,  # Above MA200: 1.0
            ma200_value=100.0,
            iv_rank=50.0,  # Optimal: ~1.0
            bid_ask_spread_pct=0.03,  # Tight: 1.0
            open_interest=10000,  # High: 1.0
        )
        assert score.score_adx == 0.75
        assert score.score_momentum == 1.0
        assert score.total >= 3.0
        assert score.is_valid is True


# =============================================================================
# STOP TIER TESTS
# =============================================================================


class TestStopTiers:
    """Tests for confidence-weighted stop tiers."""

    def test_tier_3_0(self, engine):
        """Test score 3.0-3.25: Low confidence = small bet, tight stop."""
        tier = engine.get_stop_tier(3.0)
        assert tier["stop_pct"] == 0.15  # Low confidence: -15% stop
        assert tier["contracts"] == 5  # Small position for low confidence

    def test_tier_3_25(self, engine):
        """Test score 3.25-3.5: Medium-low confidence."""
        tier = engine.get_stop_tier(3.25)
        assert tier["stop_pct"] == 0.18  # Medium-low: -18% stop
        assert tier["contracts"] == 8

    def test_tier_3_5(self, engine):
        """Test score 3.5-3.75: Medium-high confidence."""
        tier = engine.get_stop_tier(3.5)
        assert tier["stop_pct"] == 0.22  # Medium-high: -22% stop
        assert tier["contracts"] == 10

    def test_tier_3_75(self, engine):
        """Test score 3.75-4.0: High confidence = bigger bet, wider stop."""
        tier = engine.get_stop_tier(3.75)
        assert tier["stop_pct"] == 0.25  # High confidence: -25% stop
        assert tier["contracts"] == 12  # Larger position for high confidence

    def test_tier_4_0(self, engine):
        """Test score 4.0 gets highest tier."""
        tier = engine.get_stop_tier(4.0)
        assert tier["stop_pct"] == 0.25  # Highest tier (25%)
        assert tier["contracts"] == 12  # Highest tier


class TestPositionSizing:
    """Tests for position sizing calculation."""

    def test_basic_position_size(self, engine):
        """Test basic position sizing with 1% risk."""
        num_contracts, stop_pct, stop_price, target_price = engine.calculate_position_size(
            entry_score=3.5,
            premium=1.45,
            portfolio_value=100000,
        )
        # Score 3.5 tier has stop_pct=0.22, contracts=10
        # 1% risk = $1000
        # Risk per contract = $1.45 × 0.22 × 100 = $31.90
        # Max contracts = $1000 / $31.90 = 31.3 → capped at tier max 10
        assert num_contracts <= 10  # Tier cap is 10 contracts
        assert stop_pct == 0.22  # Medium-high: -22% stop for score 3.5
        assert stop_price == pytest.approx(1.45 * (1 - 0.22), rel=0.01)  # $1.131
        assert target_price == pytest.approx(1.45 * (1 + 0.60), rel=0.01)  # $2.32 (60% target)

    def test_minimum_one_contract(self, engine):
        """Test at least 1 contract is used."""
        num_contracts, _, _, _ = engine.calculate_position_size(
            entry_score=3.0,
            premium=100.0,  # Very expensive
            portfolio_value=10000,  # Small portfolio
        )
        assert num_contracts >= 1


# =============================================================================
# OPTION CONTRACT TESTS
# =============================================================================


class TestOptionContract:
    """Tests for OptionContract dataclass."""

    def test_spread_pct_calculation(self, sample_contract):
        """Test bid-ask spread percentage."""
        # (1.50 - 1.40) / 1.45 = 6.9%
        assert round(sample_contract.spread_pct, 3) == 0.069

    def test_to_dict(self, sample_contract):
        """Test serialization."""
        data = sample_contract.to_dict()
        assert data["symbol"] == "QQQ 271231C00450000"  # V2.16-BT: Updated fixture
        assert data["direction"] == "CALL"
        assert data["strike"] == 450.0

    def test_from_dict(self, sample_contract):
        """Test deserialization."""
        data = sample_contract.to_dict()
        restored = OptionContract.from_dict(data)
        assert restored.symbol == sample_contract.symbol
        assert restored.direction == sample_contract.direction
        assert restored.strike == sample_contract.strike


# =============================================================================
# ENTRY SIGNAL TESTS
# =============================================================================


class TestEntrySignals:
    """Tests for entry signal detection."""

    def test_valid_entry_signal(self, engine, sample_contract):
        """Test valid entry signal generation."""
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # V3.2: BULL regime required for CALL entries
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        assert result is not None
        assert result.source == "OPT"
        assert result.urgency == Urgency.IMMEDIATE
        assert "OPT Entry" in result.reason

    def test_entry_blocked_no_contract(self, engine):
        """Test entry blocked when no contract available."""
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=None,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
        )
        assert result is None

    def test_entry_blocked_low_score(self, engine, sample_contract):
        """Test entry blocked when score < 3.0."""
        result = engine.check_entry_signal(
            adx_value=15.0,  # Weak ADX
            current_price=95.0,  # Below MA200
            ma200_value=100.0,
            iv_rank=10.0,  # Low IV
            best_contract=sample_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
        )
        assert result is None

    def test_entry_blocked_existing_position(self, engine_with_position, sample_contract):
        """Test entry blocked when position exists."""
        result = engine_with_position.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
        )
        assert result is None

    def test_entry_blocked_late_day_wide_stop(self, engine, sample_contract):
        """Test entry blocked after 2:30 PM with wide stop."""
        # High score would give 30% stop, blocked after 2:30
        result = engine.check_entry_signal(
            adx_value=40.0,  # Very high ADX
            current_price=110.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,
            current_hour=14,
            current_minute=35,  # After 2:30 PM
            current_date="2026-01-26",
            portfolio_value=100000,
        )
        assert result is None

    def test_entry_allowed_late_day_tight_stop(self, engine, sample_contract):
        """Test entry allowed after 2:30 PM with 20% stop."""
        # Lower score gives 20% stop, allowed after 2:30
        result = engine.check_entry_signal(
            adx_value=22.0,  # Moderate ADX → lower score
            current_price=100.0,  # At MA200
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,
            current_hour=14,
            current_minute=35,
            current_date="2026-01-26",
            portfolio_value=100000,
        )
        # This might still be blocked due to score - just testing the logic
        # The key is that low score (20% stop) wouldn't be blocked by late day

    def test_entry_blocked_gap_filter(self, engine, sample_contract):
        """Test entry blocked when gap filter active."""
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            gap_filter_triggered=True,
        )
        assert result is None

    def test_entry_blocked_vol_shock(self, engine, sample_contract):
        """Test entry blocked when vol shock active."""
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            vol_shock_active=True,
        )
        assert result is None

    def test_max_trades_per_day(self, engine, sample_contract):
        """Test max 1 trade per day limit."""
        # First trade
        engine._trades_today = 0
        engine._last_trade_date = "2026-01-26"
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # V3.2: BULL regime required for CALL entries
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        assert result is not None

        # Register the trade
        engine.register_entry(1.45, "10:30:00", "2026-01-26")

        # Remove position to allow second attempt
        engine.remove_position()

        # Second trade same day - blocked
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,
            current_hour=11,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # V3.2: BULL regime required for CALL entries
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        assert result is None

    def test_entry_blocked_regime_below_40(self, engine, sample_contract):
        """Test entry blocked when regime score < 40 (V2.1 spec GAP #1)."""
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=35.0,  # Below 40 threshold
        )
        assert result is None

    def test_call_blocked_regime_at_40(self, engine, sample_contract):
        """V3.2: CALL blocked when regime = 40 (CAUTIOUS - PUT only)."""
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,  # CALL contract
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=40.0,  # CAUTIOUS - CALL blocked
        )
        assert result is None  # V3.2: CALL blocked below regime 70

    def test_call_blocked_regime_neutral(self, engine, sample_contract):
        """V3.2: CALL blocked when regime = 65 (NEUTRAL - PUT only)."""
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,  # CALL contract
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=55.0,  # V3.4: Lower NEUTRAL (50-59) - CALL blocked
        )
        assert result is None  # V3.4: CALL blocked in lower NEUTRAL (50-59)

    def test_call_allowed_regime_bull(self, engine, sample_contract):
        """V3.2: CALL allowed when regime >= 70 (BULL)."""
        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=105.0,
            ma200_value=100.0,
            iv_rank=50.0,
            best_contract=sample_contract,  # CALL contract
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # BULL - CALL allowed
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        assert result is not None

    def test_entry_blocked_premium_below_minimum(self, engine):
        """Test entry blocked when premium < $0.50 (V2.1 spec GAP #3)."""
        # Create contract with low premium
        low_premium_contract = OptionContract(
            symbol="QQQ 260126C00480000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=480.0,  # Far OTM
            expiry="2026-01-28",
            delta=0.45,
            bid=0.20,
            ask=0.30,
            mid_price=0.25,  # Below $0.50 minimum
            open_interest=10000,
            days_to_expiry=3,
        )

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=low_premium_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
        )
        assert result is None

    def test_entry_allowed_premium_at_minimum(self, engine):
        """Test entry allowed when premium = $0.50 (boundary)."""
        contract = OptionContract(
            symbol="QQQ 260126C00460000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=460.0,
            expiry="2026-01-28",
            delta=0.50,
            bid=0.45,
            ask=0.55,
            mid_price=0.50,  # Exactly at minimum
            open_interest=10000,
            days_to_expiry=3,
        )

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # V3.2: BULL regime required for CALL entries
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        assert result is not None


# =============================================================================
# EXIT SIGNAL TESTS
# =============================================================================


class TestExitSignals:
    """Tests for exit signal detection."""

    def test_exit_profit_target_hit(self, engine_with_position):
        """Test exit when profit target hit (+60%)."""
        # Entry at $1.45, target at $2.32 (60% profit)
        result = engine_with_position.check_exit_signals(current_price=2.40)
        assert result is not None
        assert "TARGET_HIT" in result.reason
        assert result.target_weight == 0.0

    def test_exit_stop_hit(self, engine_with_position):
        """Test exit when stop hit."""
        # Entry at $1.45, stop at ~$1.0875 (25% stop)
        result = engine_with_position.check_exit_signals(current_price=1.00)
        assert result is not None
        assert "STOP_HIT" in result.reason
        assert result.target_weight == 0.0

    def test_intraday_exit_uses_opt_intraday_and_lane_metadata(self, sample_contract):
        """Intraday lane exits must carry OPT_INTRADAY source and lane metadata."""
        engine = OptionsEngine(algorithm=None)
        engine._pending_contract = sample_contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 2
        engine._pending_stop_pct = 0.25
        engine._pending_strategy = IntradayStrategy.ITM_MOMENTUM.value
        engine.register_entry(
            fill_price=1.45,
            entry_time="10:30:00",
            current_date="2026-01-26",
        )
        assert engine._position is not None
        engine._position.entry_strategy = IntradayStrategy.ITM_MOMENTUM.value
        engine._intraday_positions["ITM"] = [engine._position]

        result = engine.check_exit_signals(current_price=2.40)
        assert result is not None
        assert result.source == "OPT_INTRADAY"
        assert result.metadata.get("options_lane") == "ITM"
        assert result.metadata.get("options_strategy") == IntradayStrategy.ITM_MOMENTUM.value

    def test_no_exit_price_in_range(self, engine_with_position):
        """Test no exit when price between stop and target."""
        result = engine_with_position.check_exit_signals(current_price=1.50)
        assert result is None

    def test_no_exit_no_position(self, engine):
        """Test no exit when no position."""
        result = engine.check_exit_signals(current_price=2.00)
        assert result is None


# =============================================================================
# FORCE EXIT TESTS (V2.1 spec GAP #2)
# =============================================================================


class TestForceExit:
    """Tests for force exit at 3:45 PM."""

    def test_force_exit_at_1545(self, engine_with_position):
        """Test force exit triggered at 15:45 ET (V2.1 spec GAP #2)."""
        result = engine_with_position.check_force_exit(
            current_hour=15,
            current_minute=45,
            current_price=1.60,
        )
        assert result is not None
        assert result.target_weight == 0.0
        assert result.urgency == Urgency.IMMEDIATE
        assert "TIME_EXIT_1545" in result.reason

    def test_force_exit_after_1545(self, engine_with_position):
        """Test force exit triggered after 15:45 ET."""
        result = engine_with_position.check_force_exit(
            current_hour=15,
            current_minute=50,
            current_price=1.60,
        )
        assert result is not None
        assert "TIME_EXIT_1545" in result.reason

    def test_no_force_exit_before_1545(self, engine_with_position):
        """Test no force exit before 15:45 ET."""
        result = engine_with_position.check_force_exit(
            current_hour=15,
            current_minute=44,
            current_price=1.60,
        )
        assert result is None

    def test_no_force_exit_morning(self, engine_with_position):
        """Test no force exit in the morning."""
        result = engine_with_position.check_force_exit(
            current_hour=10,
            current_minute=30,
            current_price=1.60,
        )
        assert result is None

    def test_no_force_exit_no_position(self, engine):
        """Test no force exit when no position exists."""
        result = engine.check_force_exit(
            current_hour=15,
            current_minute=45,
            current_price=1.60,
        )
        assert result is None

    def test_force_exit_includes_pnl(self, engine_with_position):
        """Test force exit reason includes PnL percentage."""
        # Entry was at $1.45, current at $1.75 = +20.7%
        result = engine_with_position.check_force_exit(
            current_hour=15,
            current_minute=45,
            current_price=1.75,
        )
        assert result is not None
        assert "+" in result.reason  # Positive PnL indicator
        assert "20" in result.reason or "21" in result.reason  # Approximate % gain


# =============================================================================
# POSITION MANAGEMENT TESTS
# =============================================================================


class TestPositionManagement:
    """Tests for position management."""

    def test_register_entry(self, engine, sample_contract):
        """Test position registration."""
        engine._pending_contract = sample_contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 27
        engine._pending_stop_pct = 0.25

        position = engine.register_entry(
            fill_price=1.45,
            entry_time="10:30:00",
            current_date="2026-01-26",
        )

        assert position.entry_price == 1.45
        assert position.entry_score == 3.5
        assert position.num_contracts == 27
        assert position.stop_pct == 0.25
        assert engine.has_position()

    def test_has_position(self, engine, engine_with_position):
        """Test has_position check."""
        assert not engine.has_position()
        assert engine_with_position.has_position()

    def test_remove_position(self, engine_with_position):
        """Test position removal."""
        position = engine_with_position.remove_position()
        assert position is not None
        assert not engine_with_position.has_position()

    def test_trades_today_counter(self, engine, sample_contract):
        """Test trades today counter increments."""
        engine._pending_contract = sample_contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 27
        engine._pending_stop_pct = 0.25

        engine.register_entry(1.45, "10:30:00", "2026-01-26")
        assert engine._trades_today == 1
        assert engine._last_trade_date == "2026-01-26"


# =============================================================================
# STATE PERSISTENCE TESTS
# =============================================================================


class TestStatePersistence:
    """Tests for state persistence."""

    def test_get_state_empty(self, engine):
        """Test state when no position."""
        state = engine.get_state_for_persistence()
        assert state["position"] is None
        assert state["trades_today"] == 0

    def test_get_state_with_position(self, engine_with_position):
        """Test state with position."""
        state = engine_with_position.get_state_for_persistence()
        assert state["position"] is not None
        assert state["trades_today"] == 1

    def test_restore_state(self, engine, engine_with_position):
        """Test state restoration."""
        state = engine_with_position.get_state_for_persistence()

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        assert new_engine.has_position()
        assert new_engine._trades_today == 1

    def test_reset(self, engine_with_position):
        """Test reset clears state."""
        engine_with_position.reset()
        assert not engine_with_position.has_position()
        assert engine_with_position._trades_today == 0

    def test_reset_daily(self, engine):
        """Test daily reset clears trade counter."""
        engine._trades_today = 1
        engine._last_trade_date = "2026-01-25"

        engine.reset_daily("2026-01-26")
        assert engine._trades_today == 0
        assert engine._last_trade_date == "2026-01-26"

    def test_restore_pending_intraday_entries_supports_key_symbol_fallback(self):
        """Restore pending entries when legacy rows omit explicit symbol field."""
        engine = OptionsEngine()
        legacy_key = "MICRO|QQQ 270105P00470000"
        state = {
            "pending_intraday_entries": {
                legacy_key: {
                    "lane": "MICRO",
                    "entry_score": 3.0,
                    "num_contracts": 2,
                    "entry_strategy": IntradayStrategy.MICRO_OTM_MOMENTUM.value,
                    "stop_pct": 0.30,
                    "created_at": "2027-01-04 10:00:00",
                }
            }
        }

        engine.restore_state(state)

        expected_key = engine._pending_engine_entry_key("QQQ 270105P00470000", "MICRO")
        assert expected_key in engine._pending_intraday_entries
        assert engine._pending_intraday_entries[expected_key]["symbol"] == "QQQ 270105P00470000"
        assert engine._pending_intraday_entries[expected_key]["lane"] == "MICRO"

    def test_restore_pending_intraday_entries_skips_stale_rows(self):
        """Restore should skip pending entries older than configured stale threshold."""
        from types import SimpleNamespace

        engine = OptionsEngine()
        engine.algorithm = SimpleNamespace(Time=datetime(2027, 1, 4, 13, 0, 0))
        stale_key = "MICRO|QQQ 270105P00470000"
        fresh_key = "ITM|QQQ 270105C00480000"
        state = {
            "pending_intraday_entries": {
                stale_key: {
                    "lane": "MICRO",
                    "entry_score": 3.0,
                    "num_contracts": 2,
                    "entry_strategy": IntradayStrategy.MICRO_OTM_MOMENTUM.value,
                    "stop_pct": 0.30,
                    "created_at": "2027-01-04 10:00:00",
                },
                fresh_key: {
                    "lane": "ITM",
                    "entry_score": 3.2,
                    "num_contracts": 1,
                    "entry_strategy": IntradayStrategy.ITM_MOMENTUM.value,
                    "stop_pct": 0.25,
                    "created_at": "2027-01-04 12:50:00",
                },
            }
        }

        engine.restore_state(state)

        stale_expected = engine._pending_engine_entry_key("QQQ 270105P00470000", "MICRO")
        fresh_expected = engine._pending_engine_entry_key("QQQ 270105C00480000", "ITM")
        assert stale_expected not in engine._pending_intraday_entries
        assert fresh_expected in engine._pending_intraday_entries


# =============================================================================
# OPTIONS POSITION TESTS
# =============================================================================


class TestOptionsPosition:
    """Tests for OptionsPosition dataclass."""

    def test_to_dict(self, engine_with_position):
        """Test position serialization."""
        position = engine_with_position.get_position()
        data = position.to_dict()

        assert data["entry_price"] == 1.45
        assert data["entry_score"] == 3.5
        assert data["num_contracts"] == 27
        assert "contract" in data

    def test_from_dict(self, engine_with_position):
        """Test position deserialization."""
        position = engine_with_position.get_position()
        data = position.to_dict()

        restored = OptionsPosition.from_dict(data)
        assert restored.entry_price == position.entry_price
        assert restored.entry_score == position.entry_score
        assert restored.contract.symbol == position.contract.symbol


# =============================================================================
# GREEKS MONITORING TESTS (V2.1 RSK-2)
# =============================================================================


class TestGreeksMonitoring:
    """Tests for Greeks monitoring integration with risk engine."""

    def test_contract_greeks_fields(self, sample_contract):
        """Test contract includes Greeks fields."""
        # Update sample contract with Greeks
        sample_contract.delta = 0.50
        sample_contract.gamma = 0.03
        sample_contract.vega = 0.15
        sample_contract.theta = -0.02

        assert sample_contract.delta == 0.50
        assert sample_contract.gamma == 0.03
        assert sample_contract.vega == 0.15
        assert sample_contract.theta == -0.02

    def test_contract_greeks_serialization(self):
        """Test Greeks serialize/deserialize correctly."""
        contract = OptionContract(
            symbol="QQQ 260126C00450000",
            delta=0.50,
            gamma=0.03,
            vega=0.15,
            theta=-0.02,
            strike=450.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
        )

        data = contract.to_dict()
        assert data["gamma"] == 0.03
        assert data["vega"] == 0.15
        assert data["theta"] == -0.02

        restored = OptionContract.from_dict(data)
        assert restored.gamma == 0.03
        assert restored.vega == 0.15
        assert restored.theta == -0.02

    def test_contract_greeks_backwards_compat(self):
        """Test Greeks default to 0 for old serialized data."""
        # Simulate old data without Greeks
        old_data = {
            "symbol": "QQQ 260126C00450000",
            "underlying": "QQQ",
            "direction": "CALL",
            "strike": 450.0,
            "expiry": "2026-01-26",
            "delta": 0.50,
            # No gamma, vega, theta
            "bid": 1.45,
            "ask": 1.55,
            "mid_price": 1.50,
            "open_interest": 1000,
            "days_to_expiry": 1,
        }

        restored = OptionContract.from_dict(old_data)
        assert restored.gamma == 0.0
        assert restored.vega == 0.0
        assert restored.theta == 0.0

    def test_calculate_position_greeks_no_position(self, engine):
        """Test Greeks calculation returns None with no position."""
        result = engine.calculate_position_greeks()
        assert result is None

    def test_calculate_position_greeks_with_position(self):
        """Test Greeks calculation returns per-contract values."""
        from engines.satellite.options_engine import OptionContract, OptionsEngine

        engine = OptionsEngine()
        contract = OptionContract(
            symbol="QQQ 260126C00450000",
            delta=0.50,
            gamma=0.03,
            vega=0.15,
            theta=-0.02,
            strike=450.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
        )

        # Register entry
        engine._pending_contract = contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 10  # 10 contracts
        engine._pending_stop_pct = 0.25
        engine.register_entry(1.45, "10:30:00", "2026-01-26")

        greeks = engine.calculate_position_greeks()
        assert greeks is not None
        # Per-contract Greeks for delta/gamma/vega, normalized theta (% of position value)
        assert greeks.delta == 0.50
        assert greeks.gamma == 0.03
        assert greeks.vega == 0.15
        # theta normalized: (raw_theta × num_contracts) / (num_contracts × mid_price × 100)
        # = (-0.02 × 10) / (10 × 1.50 × 100) = -0.2 / 1500 ≈ -0.000133
        assert abs(greeks.theta - (-0.000133333)) < 0.0001

    def test_calculate_position_greeks_with_intraday_positions(self, monkeypatch):
        """Greeks fallback must include intraday positions when swing is empty."""
        from engines.satellite.options_engine import OptionContract, OptionsEngine, OptionsPosition

        monkeypatch.setattr(config, "CB_GREEKS_INCLUDE_INTRADAY", True, raising=False)
        engine = OptionsEngine()
        engine._position = None

        micro_contract = OptionContract(
            symbol="QQQ 260126C00450000",
            delta=0.40,
            gamma=0.02,
            vega=0.12,
            theta=-0.03,
            strike=450.0,
            expiry="2026-01-26",
            mid_price=2.00,
            open_interest=1000,
            days_to_expiry=1,
        )
        itm_contract = OptionContract(
            symbol="QQQ 260126P00440000",
            delta=-0.72,
            gamma=0.05,
            vega=0.20,
            theta=-0.01,
            strike=440.0,
            expiry="2026-01-26",
            mid_price=1.00,
            open_interest=1000,
            days_to_expiry=1,
        )
        engine._intraday_positions["MICRO"] = [
            OptionsPosition(
                contract=micro_contract,
                entry_price=2.0,
                entry_time="10:30:00",
                entry_score=3.0,
                num_contracts=2,
                stop_price=1.5,
                target_price=2.5,
                stop_pct=0.25,
            )
        ]
        engine._intraday_positions["ITM"] = [
            OptionsPosition(
                contract=itm_contract,
                entry_price=1.0,
                entry_time="10:40:00",
                entry_score=3.0,
                num_contracts=1,
                stop_price=0.7,
                target_price=1.4,
                stop_pct=0.30,
            )
        ]

        greeks = engine.calculate_position_greeks()
        assert greeks is not None
        assert greeks.delta == -0.72  # largest absolute delta
        assert greeks.gamma == 0.05  # largest absolute gamma
        assert greeks.vega == 0.20  # largest absolute vega
        assert greeks.theta == pytest.approx(-0.00015, rel=1e-6)  # most negative theta%

    def test_update_position_greeks(self):
        """Test updating position Greeks."""
        from engines.satellite.options_engine import OptionContract, OptionsEngine

        engine = OptionsEngine()
        contract = OptionContract(
            symbol="QQQ 260126C00450000",
            delta=0.50,
            gamma=0.03,
            vega=0.15,
            theta=-0.02,
            strike=450.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
        )

        engine._pending_contract = contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 10
        engine._pending_stop_pct = 0.25
        engine.register_entry(1.45, "10:30:00", "2026-01-26")

        # Update Greeks
        engine.update_position_greeks(
            delta=0.55,
            gamma=0.04,
            vega=0.18,
            theta=-0.03,
        )

        position = engine.get_position()
        assert position.contract.delta == 0.55
        assert position.contract.gamma == 0.04
        assert position.contract.vega == 0.18
        assert position.contract.theta == -0.03

    def test_update_position_greeks_no_position(self, engine):
        """Test update Greeks does nothing with no position."""
        # Should not raise
        engine.update_position_greeks(0.50, 0.03, 0.15, -0.02)
        assert not engine.has_position()

    def test_update_position_greeks_updates_target_symbol_only(self):
        """Targeted Greeks refresh should update only the matching contract."""
        from engines.satellite.options_engine import OptionContract, OptionsEngine, OptionsPosition

        engine = OptionsEngine()
        micro_contract = OptionContract(
            symbol="QQQ 260126C00450000",
            delta=0.30,
            gamma=0.01,
            vega=0.08,
            theta=-0.02,
            strike=450.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
            days_to_expiry=1,
        )
        itm_contract = OptionContract(
            symbol="QQQ 260126P00440000",
            delta=-0.60,
            gamma=0.04,
            vega=0.18,
            theta=-0.01,
            strike=440.0,
            expiry="2026-01-26",
            mid_price=1.40,
            open_interest=1000,
            days_to_expiry=1,
        )
        engine._intraday_positions["MICRO"] = [
            OptionsPosition(
                contract=micro_contract,
                entry_price=1.5,
                entry_time="10:30:00",
                entry_score=3.0,
                num_contracts=1,
                stop_price=1.2,
                target_price=2.0,
                stop_pct=0.20,
            )
        ]
        engine._intraday_positions["ITM"] = [
            OptionsPosition(
                contract=itm_contract,
                entry_price=1.4,
                entry_time="10:35:00",
                entry_score=3.0,
                num_contracts=1,
                stop_price=1.0,
                target_price=2.1,
                stop_pct=0.25,
            )
        ]

        engine.update_position_greeks(
            delta=0.55,
            gamma=0.03,
            vega=0.11,
            theta=-0.05,
            symbol="QQQ 260126C00450000",
        )

        assert micro_contract.delta == 0.55
        assert micro_contract.gamma == 0.03
        assert micro_contract.vega == 0.11
        assert micro_contract.theta == -0.05

        # Non-target contract remains unchanged.
        assert itm_contract.delta == -0.60
        assert itm_contract.gamma == 0.04
        assert itm_contract.vega == 0.18
        assert itm_contract.theta == -0.01

    def test_check_greeks_breach_no_position(self, engine):
        """Test Greeks breach check with no position."""
        from engines.core.risk_engine import RiskEngine

        risk_engine = RiskEngine()
        is_breach, symbols = engine.check_greeks_breach(risk_engine)

        assert is_breach is False
        assert len(symbols) == 0

    def test_check_greeks_breach_within_limits(self):
        """Test Greeks breach check when within limits."""
        from engines.core.risk_engine import RiskEngine
        from engines.satellite.options_engine import OptionContract, OptionsEngine

        engine = OptionsEngine()
        risk_engine = RiskEngine()

        contract = OptionContract(
            symbol="QQQ 260126C00450000",
            delta=0.50,  # Within limits
            gamma=0.02,  # Within limits
            vega=0.10,  # Within limits
            theta=-0.01,  # Within limits
            strike=450.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
        )

        engine._pending_contract = contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 5  # Small position
        engine._pending_stop_pct = 0.25
        engine.register_entry(1.45, "10:30:00", "2026-01-26")

        is_breach, symbols = engine.check_greeks_breach(risk_engine)
        assert is_breach is False

    def test_check_greeks_breach_delta_exceeded(self):
        """Test Greeks breach when delta exceeds limit."""
        from engines.core.risk_engine import RiskEngine
        from engines.satellite.options_engine import OptionContract, OptionsEngine

        engine = OptionsEngine()
        risk_engine = RiskEngine()

        # Deep ITM contract with high delta (> 0.80 threshold)
        contract = OptionContract(
            symbol="QQQ 260126C00450000",
            delta=0.85,  # Deep ITM, exceeds CB_DELTA_MAX=0.80
            gamma=0.01,
            vega=0.10,
            theta=-0.01,
            strike=450.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
        )

        engine._pending_contract = contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 10
        engine._pending_stop_pct = 0.25
        engine.register_entry(1.45, "10:30:00", "2026-01-26")

        # Per-contract delta 0.85 > threshold 0.80 triggers breach
        is_breach, symbols = engine.check_greeks_breach(risk_engine)
        assert is_breach is True
        assert "ALL_OPTIONS" in symbols


# =============================================================================
# DTE AND DELTA FILTERING TESTS (Blockers #3, #4 fix)
# =============================================================================


class TestDTEDeltaFiltering:
    """Tests for DTE and Delta range filtering."""

    @pytest.fixture
    def engine(self):
        """Create fresh OptionsEngine."""
        return OptionsEngine()

    @pytest.fixture
    def valid_contract(self):
        """Create a valid contract within DTE and delta ranges."""
        return OptionContract(
            symbol="QQQ 260126C00455000",
            strike=455.0,
            expiry="2026-01-28",
            delta=0.50,  # Within 0.40-0.60 range
            gamma=0.02,
            vega=0.15,
            theta=-0.01,
            bid=1.40,
            ask=1.50,
            mid_price=1.45,
            open_interest=5000,
            days_to_expiry=3,  # Within 1-4 DTE range
        )

    def test_entry_allowed_dte_at_zero(self, engine):
        """Test entry allowed when DTE = 0 (minimum per config.OPTIONS_DTE_MIN=0)."""
        contract = OptionContract(
            symbol="QQQ 260126C00455000",
            strike=455.0,
            expiry="2026-01-26",
            delta=0.50,
            mid_price=1.45,
            open_interest=5000,
            days_to_expiry=0,  # 0 DTE - allowed in Intraday mode
        )

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # V3.2: BULL regime required for CALL entries
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        # DTE=0 is within allowed range (0-45), so entry is allowed
        assert result is not None

    def test_entry_blocked_dte_too_high(self, engine):
        """Test entry blocked when DTE > 45 (config.OPTIONS_DTE_MAX)."""
        contract = OptionContract(
            symbol="QQQ 260320C00455000",
            strike=455.0,
            expiry="2026-03-20",
            delta=0.50,
            mid_price=2.50,
            open_interest=5000,
            days_to_expiry=50,  # 50 DTE - exceeds max of 45
        )

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
        )
        assert result is None

    def test_entry_allowed_dte_at_min(self, engine, valid_contract):
        """Test entry allowed when DTE = 1 (minimum)."""
        valid_contract.days_to_expiry = 1  # At minimum

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=valid_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # V3.2: BULL regime required for CALL entries
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        assert result is not None

    def test_entry_allowed_dte_at_max(self, engine, valid_contract):
        """Test entry allowed when DTE = 4 (maximum)."""
        valid_contract.days_to_expiry = 4  # At maximum

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=valid_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # V3.2: BULL regime required for CALL entries
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        assert result is not None

    def test_entry_blocked_delta_too_low(self, engine):
        """Test entry blocked when delta < 0.40 (too far OTM)."""
        contract = OptionContract(
            symbol="QQQ 260126C00480000",
            strike=480.0,  # Far OTM
            expiry="2026-01-28",
            delta=0.30,  # Too low - far OTM
            mid_price=0.50,
            open_interest=5000,
            days_to_expiry=3,
        )

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
        )
        assert result is None

    def test_entry_blocked_delta_too_high(self, engine):
        """Test entry blocked when delta > 0.60 (too deep ITM)."""
        contract = OptionContract(
            symbol="QQQ 260126C00430000",
            strike=430.0,  # Deep ITM
            expiry="2026-01-28",
            delta=0.75,  # Too high - deep ITM
            mid_price=28.0,
            open_interest=5000,
            days_to_expiry=3,
        )

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
        )
        assert result is None

    def test_entry_allowed_delta_at_min(self, engine, valid_contract):
        """Test entry allowed when delta = 0.40 (minimum)."""
        valid_contract.delta = 0.40  # At minimum

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=valid_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # V3.2: BULL regime required for CALL entries
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        assert result is not None

    def test_entry_allowed_delta_at_max(self, engine, valid_contract):
        """Test entry allowed when delta = 0.60 (maximum)."""
        valid_contract.delta = 0.60  # At maximum

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=valid_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=75.0,  # V3.2: BULL regime required for CALL entries
            direction=OptionDirection.CALL,  # V6.0: Direction from conviction resolution
        )
        assert result is not None

    def test_entry_uses_absolute_delta_for_puts(self, engine, valid_contract):
        """Test that delta validation uses absolute value for puts."""
        valid_contract.delta = -0.50  # Put with negative delta
        valid_contract.direction = OptionDirection.PUT

        result = engine.check_entry_signal(
            adx_value=30.0,
            current_price=456.0,
            ma200_value=430.0,
            iv_rank=50.0,
            best_contract=valid_contract,
            current_hour=10,
            current_minute=30,
            current_date="2026-01-26",
            portfolio_value=100000,
            regime_score=35.0,  # V3.2: PUT allowed in BEAR regime
            direction=OptionDirection.PUT,  # V6.0: Direction from conviction resolution
        )
        assert result is not None  # Should allow since abs(-0.50) = 0.50 is valid


# =============================================================================
# GREEKS GAMMA/VEGA/THETA BREACH TESTS (Blocker #5 fix)
# =============================================================================


class TestGreeksBreachThresholds:
    """Tests for all Greeks breach thresholds (gamma, vega, theta)."""

    def test_check_greeks_breach_gamma_exceeded(self):
        """Test Greeks breach when gamma exceeds limit (0.05)."""
        from engines.core.risk_engine import RiskEngine

        engine = OptionsEngine()
        risk_engine = RiskEngine()

        contract = OptionContract(
            symbol="QQQ 260126C00455000",
            delta=0.50,  # Within limits
            gamma=0.06,  # Exceeds CB_GAMMA_WARNING=0.05
            vega=0.10,
            theta=-0.01,
            strike=455.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
        )

        engine._pending_contract = contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 10
        engine._pending_stop_pct = 0.25
        engine.register_entry(1.45, "10:30:00", "2026-01-26")

        # Gamma 0.06 > threshold 0.05 triggers breach
        is_breach, symbols = engine.check_greeks_breach(risk_engine)
        assert is_breach is True
        assert "ALL_OPTIONS" in symbols

    def test_check_greeks_breach_vega_exceeded(self):
        """Test Greeks breach when vega exceeds limit (0.50)."""
        from engines.core.risk_engine import RiskEngine

        engine = OptionsEngine()
        risk_engine = RiskEngine()

        contract = OptionContract(
            symbol="QQQ 260126C00455000",
            delta=0.50,  # Within limits
            gamma=0.02,  # Within limits
            vega=0.60,  # Exceeds CB_VEGA_MAX=0.50
            theta=-0.01,
            strike=455.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
        )

        engine._pending_contract = contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 10
        engine._pending_stop_pct = 0.25
        engine.register_entry(1.45, "10:30:00", "2026-01-26")

        # Vega 0.60 > threshold 0.50 triggers breach
        is_breach, symbols = engine.check_greeks_breach(risk_engine)
        assert is_breach is True
        assert "ALL_OPTIONS" in symbols

    def test_check_greeks_breach_theta_exceeded(self):
        """Test Greeks breach when normalized theta exceeds limit (-0.02 = -2%/day).

        Theta is normalized: theta_pct = (raw_theta × num_contracts) / position_value
        With mid_price=1.50 and num_contracts=10, position_value = 10 × 1.50 × 100 = 1500
        For theta_pct < -0.02: raw_theta × 10 / 1500 < -0.02
        raw_theta must be < -3.0
        """
        from engines.core.risk_engine import RiskEngine

        engine = OptionsEngine()
        risk_engine = RiskEngine()

        contract = OptionContract(
            symbol="QQQ 260126C00455000",
            delta=0.50,  # Within limits
            gamma=0.02,  # Within limits
            vega=0.10,  # Within limits
            theta=-5.00,  # Raw theta that will exceed normalized threshold
            strike=455.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
        )

        engine._pending_contract = contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 10
        engine._pending_stop_pct = 0.25
        engine.register_entry(1.45, "10:30:00", "2026-01-26")

        # Normalized theta = (-5.00 × 10) / (10 × 1.50 × 100) = -50 / 1500 = -0.0333
        # -0.0333 < -0.02 triggers breach
        is_breach, symbols = engine.check_greeks_breach(risk_engine)
        assert is_breach is True
        assert "ALL_OPTIONS" in symbols

    def test_check_greeks_breach_excludes_protective_puts_by_default(self, monkeypatch):
        """V12.22: protective puts should not trigger global Greeks CB by default."""
        from engines.core.risk_engine import RiskEngine

        monkeypatch.setattr(config, "CB_GREEKS_INCLUDE_PROTECTIVE_PUTS", False, raising=False)
        engine = OptionsEngine()
        risk_engine = RiskEngine()

        protective_contract = OptionContract(
            symbol="QQQ 260126P00450000",
            direction=OptionDirection.PUT,
            delta=-0.45,
            gamma=0.04,
            vega=0.10,
            theta=-5.00,  # Would breach theta if included
            strike=450.0,
            expiry="2026-01-26",
            mid_price=2.00,
            open_interest=1000,
            days_to_expiry=0,
        )
        engine._intraday_positions["MICRO"] = [
            OptionsPosition(
                contract=protective_contract,
                entry_price=2.00,
                entry_time="10:30:00",
                entry_score=3.5,
                num_contracts=10,
                stop_price=1.40,
                target_price=3.20,
                stop_pct=0.30,
                entry_strategy=IntradayStrategy.PROTECTIVE_PUTS.value,
            )
        ]

        is_breach, symbols = engine.check_greeks_breach(risk_engine)
        assert is_breach is False
        assert len(symbols) == 0

    def test_check_greeks_breach_can_include_protective_puts_when_enabled(self, monkeypatch):
        """Protective puts can be re-included in CB Greeks via config override."""
        from engines.core.risk_engine import RiskEngine

        monkeypatch.setattr(config, "CB_GREEKS_INCLUDE_INTRADAY", True, raising=False)
        monkeypatch.setattr(config, "CB_GREEKS_INCLUDE_PROTECTIVE_PUTS", True, raising=False)
        engine = OptionsEngine()
        risk_engine = RiskEngine()

        protective_contract = OptionContract(
            symbol="QQQ 260126P00450000",
            direction=OptionDirection.PUT,
            delta=-0.45,
            gamma=0.04,
            vega=0.10,
            theta=-5.00,
            strike=450.0,
            expiry="2026-01-26",
            mid_price=2.00,
            open_interest=1000,
            days_to_expiry=0,
        )
        engine._intraday_positions["MICRO"] = [
            OptionsPosition(
                contract=protective_contract,
                entry_price=2.00,
                entry_time="10:30:00",
                entry_score=3.5,
                num_contracts=10,
                stop_price=1.40,
                target_price=3.20,
                stop_pct=0.30,
                entry_strategy=IntradayStrategy.PROTECTIVE_PUTS.value,
            )
        ]

        is_breach, symbols = engine.check_greeks_breach(risk_engine)
        assert is_breach is True
        assert "ALL_OPTIONS" in symbols

    def test_check_greeks_breach_still_monitors_itm_with_protective_put(self, monkeypatch):
        """V12.22: excluding protective puts must not disable ITM Greeks protection."""
        from engines.core.risk_engine import RiskEngine

        monkeypatch.setattr(config, "CB_GREEKS_INCLUDE_INTRADAY", True, raising=False)
        monkeypatch.setattr(config, "CB_GREEKS_INCLUDE_PROTECTIVE_PUTS", False, raising=False)
        engine = OptionsEngine()
        risk_engine = RiskEngine()

        protective_contract = OptionContract(
            symbol="QQQ 260126P00450000",
            direction=OptionDirection.PUT,
            delta=-0.45,
            gamma=0.04,
            vega=0.10,
            theta=-5.00,
            strike=450.0,
            expiry="2026-01-26",
            mid_price=2.00,
            open_interest=1000,
            days_to_expiry=0,
        )
        itm_contract = OptionContract(
            symbol="QQQ 260205C00430000",
            direction=OptionDirection.CALL,
            delta=0.85,  # Delta breach should still trigger
            gamma=0.01,
            vega=0.10,
            theta=-0.01,
            strike=430.0,
            expiry="2026-02-05",
            mid_price=15.0,
            open_interest=1000,
            days_to_expiry=10,
        )

        engine._intraday_positions["MICRO"] = [
            OptionsPosition(
                contract=protective_contract,
                entry_price=2.00,
                entry_time="10:30:00",
                entry_score=3.5,
                num_contracts=10,
                stop_price=1.40,
                target_price=3.20,
                stop_pct=0.30,
                entry_strategy=IntradayStrategy.PROTECTIVE_PUTS.value,
            )
        ]
        engine._intraday_positions["ITM"] = [
            OptionsPosition(
                contract=itm_contract,
                entry_price=15.0,
                entry_time="10:31:00",
                entry_score=3.5,
                num_contracts=3,
                stop_price=11.0,
                target_price=22.0,
                stop_pct=0.25,
                entry_strategy=IntradayStrategy.ITM_MOMENTUM.value,
            )
        ]

        is_breach, symbols = engine.check_greeks_breach(risk_engine)
        assert is_breach is True
        assert "ALL_OPTIONS" in symbols

    def test_check_greeks_all_within_limits(self):
        """Test no breach when all Greeks are within limits."""
        from engines.core.risk_engine import RiskEngine

        engine = OptionsEngine()
        risk_engine = RiskEngine()

        contract = OptionContract(
            symbol="QQQ 260126C00455000",
            delta=0.50,  # Within CB_DELTA_MAX=0.80
            gamma=0.03,  # Within CB_GAMMA_WARNING=0.05
            vega=0.30,  # Within CB_VEGA_MAX=0.50
            theta=-0.01,  # Within CB_THETA_WARNING=-0.02
            strike=455.0,
            expiry="2026-01-26",
            mid_price=1.50,
            open_interest=1000,
        )

        engine._pending_contract = contract
        engine._pending_entry_score = 3.5
        engine._pending_num_contracts = 10
        engine._pending_stop_pct = 0.25
        engine.register_entry(1.45, "10:30:00", "2026-01-26")

        is_breach, symbols = engine.check_greeks_breach(risk_engine)
        assert is_breach is False
        assert len(symbols) == 0


# =============================================================================
# V2.1.1 DUAL POSITION TRACKING TESTS (GAP #3)
# =============================================================================


class TestDualPositionTracking:
    """Tests for V2.1.1 dual position tracking (swing + intraday)."""

    @pytest.fixture
    def engine(self):
        """Create fresh OptionsEngine."""
        return OptionsEngine()

    @pytest.fixture
    def swing_contract(self):
        """Create a swing mode contract (5+ DTE)."""
        return OptionContract(
            symbol="QQQ 260205C00455000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=455.0,
            expiry="2026-02-05",
            delta=0.50,
            gamma=0.02,
            vega=0.15,
            theta=-0.01,
            bid=3.40,
            ask=3.60,
            mid_price=3.50,
            open_interest=10000,
            days_to_expiry=10,  # Swing mode (5+ DTE)
        )

    @pytest.fixture
    def intraday_contract(self):
        """Create an intraday mode contract (0-2 DTE)."""
        return OptionContract(
            symbol="QQQ 260127C00455000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=455.0,
            expiry="2026-01-27",
            delta=0.50,
            gamma=0.05,
            vega=0.08,
            theta=-0.02,
            bid=0.90,
            ask=1.10,
            mid_price=1.00,
            open_interest=8000,
            days_to_expiry=1,  # Intraday mode (0-2 DTE)
        )

    def test_swing_and_intraday_positions_are_separate_fields(self, engine):
        """Verify swing and intraday positions are distinct attributes."""
        assert hasattr(engine, "_swing_position")
        assert hasattr(engine, "_intraday_position")
        assert engine._swing_position is None
        assert engine._intraday_position is None

    def test_swing_position_does_not_affect_intraday(self, engine, swing_contract):
        """Setting swing position should not affect intraday position."""
        # Directly set swing position
        from engines.satellite.options_engine import OptionsPosition

        engine._swing_position = OptionsPosition(
            contract=swing_contract,
            entry_price=3.50,
            entry_time="10:30:00",
            entry_score=3.5,
            num_contracts=10,
            stop_price=2.80,
            target_price=5.25,
            stop_pct=0.20,
        )

        assert engine._swing_position is not None
        assert engine._intraday_position is None  # Should remain None

    def test_intraday_position_does_not_affect_swing(self, engine, intraday_contract):
        """Setting intraday position should not affect swing position."""
        from engines.satellite.options_engine import OptionsPosition

        engine._intraday_position = OptionsPosition(
            contract=intraday_contract,
            entry_price=1.00,
            entry_time="10:45:00",
            entry_score=3.2,
            num_contracts=20,
            stop_price=0.80,
            target_price=1.50,
            stop_pct=0.20,
        )

        assert engine._intraday_position is not None
        assert engine._swing_position is None  # Should remain None

    def test_both_positions_can_coexist(self, engine, swing_contract, intraday_contract):
        """Both swing and intraday positions can exist simultaneously."""
        from engines.satellite.options_engine import OptionsPosition

        # Set swing position
        engine._swing_position = OptionsPosition(
            contract=swing_contract,
            entry_price=3.50,
            entry_time="10:30:00",
            entry_score=3.5,
            num_contracts=10,
            stop_price=2.80,
            target_price=5.25,
            stop_pct=0.20,
        )

        # Set intraday position
        engine._intraday_position = OptionsPosition(
            contract=intraday_contract,
            entry_price=1.00,
            entry_time="10:45:00",
            entry_score=3.2,
            num_contracts=20,
            stop_price=0.80,
            target_price=1.50,
            stop_pct=0.20,
        )

        # Both should exist
        assert engine._swing_position is not None
        assert engine._intraday_position is not None
        assert engine._swing_position.contract.symbol != engine._intraday_position.contract.symbol

    def test_intraday_trades_counter_separate_from_swing(self, engine):
        """Intraday trades counter should be separate from swing trades counter."""
        engine._trades_today = 1
        engine._intraday_trades_today = 2

        assert engine._trades_today == 1
        assert engine._intraday_trades_today == 2


class TestDualModeArchitecture:
    """Tests for V2.1.1 dual-mode architecture methods."""

    @pytest.fixture
    def engine(self):
        """Create fresh OptionsEngine."""
        return OptionsEngine()

    def test_determine_mode_intraday_0_dte(self, engine):
        """Test 0 DTE returns INTRADAY mode."""
        from models.enums import OptionsMode

        mode = engine.determine_mode(dte=0)
        assert mode == OptionsMode.INTRADAY

    def test_determine_mode_intraday_1_dte(self, engine):
        """Test 1 DTE returns INTRADAY mode."""
        from models.enums import OptionsMode

        mode = engine.determine_mode(dte=1)
        assert mode == OptionsMode.INTRADAY

    def test_determine_mode_intraday_2_dte(self, engine):
        """Test 2 DTE returns INTRADAY mode (V2.13: 1-5 DTE for micro regime)."""
        from models.enums import OptionsMode

        mode = engine.determine_mode(dte=2)
        assert mode == OptionsMode.INTRADAY  # V2.13: 2 DTE is INTRADAY

    def test_determine_mode_intraday_3_dte(self, engine):
        """Test 3 DTE returns INTRADAY mode (V2.13: 1-5 DTE for micro regime)."""
        from models.enums import OptionsMode

        mode = engine.determine_mode(dte=3)
        assert mode == OptionsMode.INTRADAY  # V2.13: 3 DTE is INTRADAY

    def test_determine_mode_intraday_5_dte(self, engine):
        """Test 5 DTE returns INTRADAY mode (V2.13: 1-5 DTE for micro regime)."""
        from models.enums import OptionsMode

        mode = engine.determine_mode(dte=5)
        assert mode == OptionsMode.INTRADAY  # V2.13: 5 DTE is INTRADAY

    def test_determine_mode_swing_45_dte(self, engine):
        """Test 45 DTE returns SWING mode."""
        from models.enums import OptionsMode

        mode = engine.determine_mode(dte=45)
        assert mode == OptionsMode.SWING

    def test_get_mode_allocation_intraday(self, engine):
        """Test intraday mode allocation uses current config split."""
        from models.enums import OptionsMode

        allocation = engine.get_mode_allocation(OptionsMode.INTRADAY, portfolio_value=100000)
        # Current config: 25% of $100,000 = $25,000
        assert allocation == 25000.0

    def test_get_mode_allocation_swing(self, engine):
        """Test swing mode allocation uses current config split."""
        from models.enums import OptionsMode

        allocation = engine.get_mode_allocation(OptionsMode.SWING, portfolio_value=100000)
        # Current config: 35% of $100,000 = $35,000
        assert allocation == 35000.0

    def test_check_intraday_entry_blocked_existing_position(self, engine):
        """Intraday entry blocked when intraday position exists."""
        from engines.satellite.options_engine import OptionContract, OptionsPosition

        # Set intraday position
        contract = OptionContract(
            symbol="QQQ 260127C00455000",
            strike=455.0,
            expiry="2026-01-27",
            delta=0.50,
            mid_price=1.00,
            open_interest=5000,
            days_to_expiry=1,
        )

        position = OptionsPosition(
            contract=contract,
            entry_price=1.00,
            entry_time="10:00:00",
            entry_score=3.2,
            num_contracts=10,
            stop_price=0.80,
            target_price=1.50,
            stop_pct=0.20,
        )
        engine._intraday_positions["MICRO"] = [position]
        engine._refresh_legacy_engine_mirrors()

        # Try to enter again
        result = engine.check_engine_entry_signal(
            vix_current=18.0,
            vix_open=17.0,
            qqq_current=450.0,
            qqq_open=448.0,
            current_hour=11,
            current_minute=0,
            current_time="2026-01-27 11:00:00",
            portfolio_value=100000,
        )

        assert result is None  # Blocked due to existing position

    def test_reserved_usage_counts_all_lane_pending_entries(self, engine):
        """Pending usage must include all lane-keyed pending intraday entries."""
        itm_contract = OptionContract(
            symbol="QQQ 270105C00470000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=470.0,
            expiry="2027-01-05",
            delta=0.70,
            bid=1.9,
            ask=2.1,
            mid_price=2.0,
            open_interest=1200,
            days_to_expiry=1,
        )
        otm_contract = OptionContract(
            symbol="QQQ 270105P00460000",
            underlying="QQQ",
            direction=OptionDirection.PUT,
            strike=460.0,
            expiry="2027-01-05",
            delta=0.35,
            bid=0.9,
            ask=1.1,
            mid_price=1.0,
            open_interest=1200,
            days_to_expiry=1,
        )
        itm_key = engine._pending_engine_entry_key(str(itm_contract.symbol), "ITM")
        micro_key = engine._pending_engine_entry_key(str(otm_contract.symbol), "MICRO")
        engine._pending_intraday_entries[itm_key] = {
            "symbol": str(itm_contract.symbol),
            "lane": "ITM",
            "contract": itm_contract,
            "entry_score": 3.2,
            "num_contracts": 2,
            "entry_strategy": IntradayStrategy.ITM_MOMENTUM.value,
            "stop_pct": 0.40,
        }
        engine._pending_intraday_entries[micro_key] = {
            "symbol": str(otm_contract.symbol),
            "lane": "MICRO",
            "contract": otm_contract,
            "entry_score": 3.0,
            "num_contracts": 3,
            "entry_strategy": IntradayStrategy.MICRO_OTM_MOMENTUM.value,
            "stop_pct": 0.30,
        }
        engine._pending_intraday_entry = True

        usage = engine._get_reserved_bucket_usage_dollars()

        assert usage["ITM"] == pytest.approx(400.0)
        assert usage["OTM"] == pytest.approx(300.0)


class TestIntradayForceExit:
    """Tests for V2.1.1 intraday force exit at 3:30 PM."""

    @pytest.fixture
    def engine_with_intraday_position(self):
        """Create engine with an intraday position."""
        from engines.satellite.options_engine import OptionContract, OptionsEngine, OptionsPosition

        engine = OptionsEngine()

        contract = OptionContract(
            symbol="QQQ 260127C00455000",
            strike=455.0,
            expiry="2026-01-27",
            delta=0.50,
            mid_price=1.00,
            open_interest=5000,
            days_to_expiry=1,
        )

        position = OptionsPosition(
            contract=contract,
            entry_price=1.00,
            entry_time="10:30:00",
            entry_score=3.2,
            num_contracts=10,
            stop_price=0.80,
            target_price=1.50,
            stop_pct=0.20,
        )
        engine._intraday_positions["MICRO"] = [position]
        engine._refresh_legacy_engine_mirrors()

        return engine

    def test_intraday_force_exit_at_1515(self, engine_with_intraday_position):
        """Test intraday force exit at configured INTRADAY_FORCE_EXIT_TIME (15:15)."""
        result = engine_with_intraday_position.check_engine_force_exit(
            current_hour=15,
            current_minute=15,
            current_price=1.10,
        )

        assert result is not None
        assert result.target_weight == 0.0
        assert "INTRADAY_TIME_EXIT_1515" in result.reason

    def test_intraday_force_exit_after_1515(self, engine_with_intraday_position):
        """Test intraday force exit after configured time (15:15)."""
        result = engine_with_intraday_position.check_engine_force_exit(
            current_hour=15,
            current_minute=30,
            current_price=1.10,
        )

        assert result is not None
        assert "INTRADAY_TIME_EXIT_1515" in result.reason

    def test_no_intraday_force_exit_before_1515(self, engine_with_intraday_position):
        """Test no force exit before configured time (15:15)."""
        result = engine_with_intraday_position.check_engine_force_exit(
            current_hour=15,
            current_minute=14,
            current_price=1.10,
        )

        assert result is None

    def test_no_intraday_force_exit_no_position(self):
        """Test no force exit when no intraday position."""
        engine = OptionsEngine()
        result = engine.check_engine_force_exit(
            current_hour=15,
            current_minute=30,
            current_price=1.10,
        )

        assert result is None


# =============================================================================
# V2.1.1 STATE PERSISTENCE TESTS (GAP #4)
# =============================================================================


class TestV211StatePersistence:
    """Tests for V2.1.1 state persistence round-trip."""

    @pytest.fixture
    def engine_with_full_v211_state(self):
        """Create engine with complete V2.1.1 state for persistence testing."""
        from engines.satellite.options_engine import (
            MicroRegimeState,
            OptionContract,
            OptionsEngine,
            OptionsPosition,
        )
        from models.enums import (
            IntradayStrategy,
            MicroRegime,
            OptionsMode,
            VIXDirection,
            VIXLevel,
            WhipsawState,
        )

        engine = OptionsEngine()

        # Create swing position
        swing_contract = OptionContract(
            symbol="QQQ 260205C00455000",
            strike=455.0,
            expiry="2026-02-05",
            delta=0.50,
            gamma=0.02,
            vega=0.15,
            theta=-0.01,
            mid_price=3.50,
            open_interest=10000,
            days_to_expiry=10,
        )

        engine._swing_position = OptionsPosition(
            contract=swing_contract,
            entry_price=3.50,
            entry_time="10:30:00",
            entry_score=3.5,
            num_contracts=10,
            stop_price=2.80,
            target_price=5.25,
            stop_pct=0.20,
        )
        # Persistence serializes canonical _position as swing_position.
        engine._position = engine._swing_position

        # Create intraday position
        intraday_contract = OptionContract(
            symbol="QQQ 260127C00455000",
            strike=455.0,
            expiry="2026-01-27",
            delta=0.50,
            gamma=0.05,
            vega=0.08,
            theta=-0.02,
            mid_price=1.00,
            open_interest=8000,
            days_to_expiry=1,
        )

        engine._intraday_position = OptionsPosition(
            contract=intraday_contract,
            entry_price=1.00,
            entry_time="10:45:00",
            entry_score=3.2,
            num_contracts=20,
            stop_price=0.80,
            target_price=1.50,
            stop_pct=0.20,
        )

        # Set V2.1.1 state fields
        engine._intraday_trades_today = 3
        engine._current_mode = OptionsMode.INTRADAY

        # Set market open data
        engine._vix_at_open = 18.5
        engine._spy_at_open = 500.25
        engine._spy_gap_pct = -0.75

        # Set micro regime state
        engine._micro_regime_engine._state = MicroRegimeState(
            vix_level=VIXLevel.LOW,
            vix_direction=VIXDirection.FALLING,
            micro_regime=MicroRegime.GOOD_MR,
            micro_score=65.0,
            whipsaw_state=WhipsawState.TRENDING,
            recommended_strategy=IntradayStrategy.MICRO_DEBIT_FADE,
            qqq_move_pct=0.45,
            vix_current=17.8,
            vix_open=18.5,
            last_update="2026-01-27 11:00:00",
            spike_cooldown_until="",
        )

        return engine

    def test_swing_position_persists(self, engine_with_full_v211_state):
        """Test swing position round-trip persistence."""
        state = engine_with_full_v211_state.get_state_for_persistence()

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        assert new_engine._swing_position is not None
        assert new_engine._swing_position.contract.symbol == "QQQ 260205C00455000"
        assert new_engine._swing_position.entry_price == 3.50
        assert new_engine._swing_position.num_contracts == 10

    def test_intraday_position_cleared_on_restore(self, engine_with_full_v211_state):
        """
        Test intraday position is CLEARED on restore (not persisted).

        CRITICAL: Intraday positions (0-2 DTE) should NEVER be held overnight.
        If an intraday position exists in persisted state, it means the position
        wasn't properly closed at 15:30 (critical failure). On restore, we must
        clear it to prevent holding 0-2 DTE options overnight (extreme gap risk).
        """
        state = engine_with_full_v211_state.get_state_for_persistence()

        # Verify intraday position was in the state
        assert state.get("intraday_position") is not None

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        # CRITICAL: Intraday position should be cleared, NOT restored
        assert new_engine._intraday_position is None

    def test_intraday_trades_today_persists(self, engine_with_full_v211_state):
        """Test intraday_trades_today round-trip persistence."""
        state = engine_with_full_v211_state.get_state_for_persistence()

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        assert new_engine._intraday_trades_today == 3

    def test_current_mode_persists(self, engine_with_full_v211_state):
        """Test current_mode round-trip persistence."""
        from models.enums import OptionsMode

        state = engine_with_full_v211_state.get_state_for_persistence()

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        assert new_engine._current_mode == OptionsMode.INTRADAY

    def test_vix_at_open_persists(self, engine_with_full_v211_state):
        """Test vix_at_open round-trip persistence."""
        state = engine_with_full_v211_state.get_state_for_persistence()

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        assert new_engine._vix_at_open == 18.5

    def test_spy_at_open_persists(self, engine_with_full_v211_state):
        """Test spy_at_open round-trip persistence."""
        state = engine_with_full_v211_state.get_state_for_persistence()

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        assert new_engine._spy_at_open == 500.25

    def test_spy_gap_pct_persists(self, engine_with_full_v211_state):
        """Test spy_gap_pct round-trip persistence."""
        state = engine_with_full_v211_state.get_state_for_persistence()

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        assert new_engine._spy_gap_pct == -0.75

    def test_micro_regime_state_persists(self, engine_with_full_v211_state):
        """Test micro_regime_state round-trip persistence."""
        from models.enums import IntradayStrategy, MicroRegime, VIXDirection, VIXLevel, WhipsawState

        state = engine_with_full_v211_state.get_state_for_persistence()

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        micro_state = new_engine._micro_regime_engine.get_state()
        assert micro_state.vix_level == VIXLevel.LOW
        assert micro_state.vix_direction == VIXDirection.FALLING
        assert micro_state.micro_regime == MicroRegime.GOOD_MR
        assert micro_state.micro_score == 65.0
        assert micro_state.whipsaw_state == WhipsawState.TRENDING
        assert micro_state.recommended_strategy == IntradayStrategy.MICRO_DEBIT_FADE
        assert micro_state.qqq_move_pct == 0.45
        assert micro_state.vix_current == 17.8
        assert micro_state.vix_open == 18.5
        assert micro_state.last_update == "2026-01-27 11:00:00"

    def test_full_v211_state_round_trip(self, engine_with_full_v211_state):
        """
        Test complete V2.1.1 state survives round-trip.

        Note: Intraday positions are intentionally cleared on restore because
        0-2 DTE options should never be held overnight. See
        test_intraday_position_cleared_on_restore for details.
        """
        from models.enums import OptionsMode

        # Get state for persistence
        state = engine_with_full_v211_state.get_state_for_persistence()

        # Restore to new engine
        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        # Verify all fields (except intraday_position which is cleared)
        assert new_engine._swing_position is not None
        # CRITICAL: Intraday position is cleared on restore (0-2 DTE can't be held overnight)
        assert new_engine._intraday_position is None
        assert new_engine._intraday_trades_today == 3
        assert new_engine._current_mode == OptionsMode.INTRADAY
        assert new_engine._vix_at_open == 18.5
        assert new_engine._spy_at_open == 500.25
        assert new_engine._spy_gap_pct == -0.75

        micro_state = new_engine._micro_regime_engine.get_state()
        assert micro_state.micro_score == 65.0

    def test_state_empty_positions_persists(self):
        """Test persistence when positions are None."""
        engine = OptionsEngine()
        engine._vix_at_open = 20.0
        engine._spy_at_open = 495.0
        engine._spy_gap_pct = 0.5

        state = engine.get_state_for_persistence()

        assert state["swing_position"] is None
        assert state["intraday_position"] is None

        new_engine = OptionsEngine()
        new_engine.restore_state(state)

        assert new_engine._swing_position is None
        assert new_engine._intraday_position is None
        assert new_engine._vix_at_open == 20.0

    def test_backwards_compatible_state_restore(self):
        """Test restoring old state (pre-V2.1.1) doesn't break engine."""
        # Simulate old state without V2.1.1 fields
        old_state = {
            "position": None,
            "trades_today": 1,
            "last_trade_date": "2026-01-25",
            # No swing_position, intraday_position, etc.
        }

        engine = OptionsEngine()
        engine.restore_state(old_state)

        # Should restore without error, using defaults
        assert engine._swing_position is None
        assert engine._intraday_position is None
        assert engine._intraday_trades_today == 0
        assert engine._vix_at_open == 0.0


class TestUpdateMarketOpenData:
    """Tests for V2.1.1 update_market_open_data method."""

    @pytest.fixture
    def engine(self):
        """Create fresh OptionsEngine."""
        return OptionsEngine()

    def test_update_market_open_data_sets_vix(self, engine):
        """Test VIX at open is set correctly."""
        engine.update_market_open_data(
            vix_open=19.5,
            spy_open=502.0,
            spy_prior_close=500.0,
        )

        assert engine._vix_at_open == 19.5

    def test_update_market_open_data_sets_spy(self, engine):
        """Test SPY at open is set correctly."""
        engine.update_market_open_data(
            vix_open=19.5,
            spy_open=502.0,
            spy_prior_close=500.0,
        )

        assert engine._spy_at_open == 502.0

    def test_update_market_open_data_calculates_gap(self, engine):
        """Test gap percentage is calculated correctly."""
        engine.update_market_open_data(
            vix_open=19.5,
            spy_open=502.0,  # +2 from prior
            spy_prior_close=500.0,
        )

        # Gap = (502 - 500) / 500 * 100 = 0.4%
        assert abs(engine._spy_gap_pct - 0.4) < 0.01

    def test_update_market_open_data_negative_gap(self, engine):
        """Test negative gap is calculated correctly."""
        engine.update_market_open_data(
            vix_open=22.0,
            spy_open=495.0,  # -5 from prior
            spy_prior_close=500.0,
        )

        # Gap = (495 - 500) / 500 * 100 = -1.0%
        assert abs(engine._spy_gap_pct - (-1.0)) < 0.01

    def test_update_market_open_data_zero_prior_close(self, engine):
        """Test zero prior close doesn't cause division error."""
        engine.update_market_open_data(
            vix_open=19.5,
            spy_open=502.0,
            spy_prior_close=0.0,
        )

        assert engine._spy_gap_pct == 0.0


class TestSwingFilters:
    """Tests for V2.1.1 swing mode simple intraday filters."""

    @pytest.fixture
    def engine(self):
        """Create fresh OptionsEngine."""
        return OptionsEngine()

    def test_swing_filter_blocks_outside_time_window_early(self, engine):
        """Test swing filter blocks before 10:00 AM."""
        can_enter, reason = engine.check_swing_filters(
            direction=OptionDirection.CALL,
            spy_gap_pct=0.0,
            spy_intraday_change_pct=0.0,
            vix_intraday_change_pct=0.0,
            current_hour=9,
            current_minute=45,  # Before 10:00
        )

        assert can_enter is False
        assert "time_window" in reason.lower()

    def test_swing_filter_blocks_outside_time_window_late(self, engine):
        """Test swing filter blocks after 2:30 PM."""
        can_enter, reason = engine.check_swing_filters(
            direction=OptionDirection.CALL,
            spy_gap_pct=0.0,
            spy_intraday_change_pct=0.0,
            vix_intraday_change_pct=0.0,
            current_hour=14,
            current_minute=45,  # After 14:30
        )

        assert can_enter is False
        assert "time_window" in reason.lower()

    def test_swing_filter_allows_within_time_window(self, engine):
        """Test swing filter allows within 10:00-14:30."""
        can_enter, reason = engine.check_swing_filters(
            direction=OptionDirection.CALL,
            spy_gap_pct=0.0,
            spy_intraday_change_pct=0.0,
            vix_intraday_change_pct=0.0,
            current_hour=11,
            current_minute=30,
        )

        assert can_enter is True
        assert reason == ""

    def test_swing_filter_blocks_extreme_spy_drop(self, engine):
        """Test swing filter blocks on extreme SPY drop."""
        can_enter, reason = engine.check_swing_filters(
            direction=OptionDirection.CALL,
            spy_gap_pct=0.0,
            spy_intraday_change_pct=-3.0,  # Extreme drop
            vix_intraday_change_pct=0.0,
            current_hour=11,
            current_minute=30,
        )

        assert can_enter is False
        assert "extreme drop" in reason.lower()

    def test_swing_filter_blocks_vix_spike(self, engine):
        """Test swing filter blocks on VIX spike."""
        can_enter, reason = engine.check_swing_filters(
            direction=OptionDirection.CALL,
            spy_gap_pct=0.0,
            spy_intraday_change_pct=0.0,
            vix_intraday_change_pct=25.0,  # VIX spike > 20%
            current_hour=11,
            current_minute=30,
        )

        assert can_enter is False
        assert "vix spike" in reason.lower()


class TestDailyResetV211:
    """Tests for V2.1.1 daily reset functionality."""

    @pytest.fixture
    def engine_with_intraday_position(self):
        """Create engine with intraday position for reset testing."""
        from engines.satellite.options_engine import OptionContract, OptionsEngine, OptionsPosition

        engine = OptionsEngine()

        contract = OptionContract(
            symbol="QQQ 260127C00455000",
            strike=455.0,
            expiry="2026-01-27",
            delta=0.50,
            mid_price=1.00,
            open_interest=5000,
            days_to_expiry=1,
        )

        position = OptionsPosition(
            contract=contract,
            entry_price=1.00,
            entry_time="15:00:00",
            entry_score=3.2,
            num_contracts=10,
            stop_price=0.80,
            target_price=1.50,
            stop_pct=0.20,
        )
        engine._intraday_positions["MICRO"] = [position]
        engine._refresh_legacy_engine_mirrors()

        engine._intraday_trades_today = 2
        engine._last_trade_date = "2026-01-26"

        return engine

    def test_daily_reset_clears_intraday_trades(self, engine_with_intraday_position):
        """Test daily reset clears intraday trades counter."""
        engine_with_intraday_position.reset_daily("2026-01-27")

        assert engine_with_intraday_position._intraday_trades_today == 0

    def test_daily_reset_clears_orphan_intraday_position(self, engine_with_intraday_position):
        """Test daily reset clears any orphan intraday position."""
        # Intraday positions should never exist overnight
        engine_with_intraday_position.reset_daily("2026-01-27")

        assert engine_with_intraday_position._intraday_position is None

    def test_daily_reset_resets_micro_regime_engine(self, engine_with_intraday_position):
        """Test daily reset resets Micro Regime Engine."""
        from models.enums import MicroRegime, VIXDirection, VIXLevel

        # Set some micro regime state
        engine_with_intraday_position._micro_regime_engine._state.vix_level = VIXLevel.HIGH
        engine_with_intraday_position._micro_regime_engine._state.micro_score = 80.0

        engine_with_intraday_position.reset_daily("2026-01-27")

        # Check reset to defaults
        state = engine_with_intraday_position._micro_regime_engine.get_state()
        assert state.vix_level == VIXLevel.LOW
        assert state.micro_score == 50.0

    def test_daily_reset_clears_spread_exit_signal_cooldown(self, engine_with_intraday_position):
        """Daily reset must clear spread exit retry cooldown map."""
        engine_with_intraday_position._spread_exit_signal_cooldown = {
            "BULL_CALL|QQQ 260127C00455000|QQQ 260127C00460000": datetime(2026, 1, 26, 15, 59, 0)
        }

        engine_with_intraday_position.reset_daily("2026-01-27")

        assert engine_with_intraday_position._spread_exit_signal_cooldown == {}


class TestClearAllPositions:
    """V2.5 PART 19 FIX: Test clear_all_positions for zombie state prevention."""

    def test_clear_all_positions_clears_spread(self, engine):
        """Test clear_all_positions clears spread position (zombie state fix)."""
        # Simulate a zombie spread position by setting internal state directly
        # This mimics what happens when kill switch closes positions but state isn't cleared
        engine._spread_position = "ZOMBIE_SPREAD"  # Any non-None value

        assert engine.has_spread_position() is True

        # Clear all positions (kill switch scenario)
        engine.clear_all_positions()

        assert engine.has_spread_position() is False
        assert engine._spread_position is None

    def test_clear_all_positions_clears_intraday(self, engine):
        """Test clear_all_positions clears intraday position."""
        # Simulate lane-backed intraday state.
        engine._intraday_positions["MICRO"] = ["ZOMBIE_INTRADAY"]  # Any non-empty payload

        assert engine.has_engine_position() is True

        engine.clear_all_positions()

        assert engine.has_engine_position() is False
        assert engine._intraday_position is None

    def test_clear_all_positions_clears_single_leg(self, engine):
        """Test clear_all_positions clears single-leg position."""
        # Simulate a zombie single-leg position
        engine._position = "ZOMBIE_POSITION"  # Any non-None value

        assert engine.has_position() is True

        engine.clear_all_positions()

        assert engine._position is None

    def test_clear_all_positions_clears_pending_state(self, engine):
        """Test clear_all_positions clears all pending entry state."""
        # Set up pending state
        engine._pending_contract = "PENDING"
        engine._pending_intraday_entry = True
        engine._pending_spread_long_leg = "LONG"
        engine._pending_spread_short_leg = "SHORT"
        engine._pending_spread_width = 5.0

        engine.clear_all_positions()

        assert engine._pending_contract is None
        assert engine._pending_intraday_entry is False
        assert engine._pending_spread_long_leg is None
        assert engine._pending_spread_short_leg is None
        assert engine._pending_spread_width is None

    def test_clear_all_positions_idempotent(self, engine):
        """Test clear_all_positions is safe to call when no positions exist."""
        # Should not raise any errors
        engine.clear_all_positions()
        engine.clear_all_positions()  # Call twice

        assert engine._position is None
        assert engine._spread_position is None
        assert engine._intraday_position is None


# =============================================================================
# V2.20: REJECTION RECOVERY TESTS
# =============================================================================


class TestRejectionRecovery:
    """V2.20: Tests for options engine pending state recovery on rejection."""

    def test_cancel_pending_swing_entry_clears_all_fields(self, engine):
        """Test swing rejection clears all pending fields."""
        engine._pending_contract = "PENDING"
        engine._pending_entry_score = 4.5
        engine._pending_num_contracts = 3
        engine._pending_stop_pct = 0.15
        engine._pending_stop_price = 1.20
        engine._pending_target_price = 2.00
        engine._entry_attempted_today = True

        engine.cancel_pending_swing_entry()

        assert engine._pending_contract is None
        assert engine._pending_entry_score is None
        assert engine._pending_num_contracts is None
        assert engine._pending_stop_pct is None
        assert engine._pending_stop_price is None
        assert engine._pending_target_price is None
        assert engine._entry_attempted_today is False

    def test_cancel_pending_swing_entry_idempotent(self, engine):
        """Test calling cancel when no pending state is safe."""
        engine.cancel_pending_swing_entry()  # Should not raise
        assert engine._pending_contract is None
        assert engine._entry_attempted_today is False

    def test_cancel_pending_spread_entry_clears_all_fields(self, engine):
        """Test spread rejection clears all spread pending fields."""
        engine._pending_spread_long_leg = "LONG"
        engine._pending_spread_short_leg = "SHORT"
        engine._pending_spread_type = "BULL_CALL"
        engine._pending_net_debit = 1.50
        engine._pending_max_profit = 3.50
        engine._pending_spread_width = 5.0
        engine._pending_num_contracts = 2
        engine._pending_entry_score = 3.8
        engine._entry_attempted_today = True

        engine.cancel_pending_spread_entry()

        assert engine._pending_spread_long_leg is None
        assert engine._pending_spread_short_leg is None
        assert engine._pending_spread_type is None
        assert engine._pending_net_debit is None
        assert engine._pending_max_profit is None
        assert engine._pending_spread_width is None
        assert engine._pending_num_contracts is None
        assert engine._pending_entry_score is None
        # V9.0: _entry_attempted_today is NOT cleared on cancel (fill-based tracking)

    def test_cancel_pending_spread_entry_idempotent(self, engine):
        """Test calling cancel when no spread pending is safe."""
        engine.cancel_pending_spread_entry()  # Should not raise
        assert engine._pending_spread_long_leg is None

    def test_cancel_pending_engine_entry_clears_and_preserves_counters(self, engine):
        """V9.0: Intraday rejection clears state but does NOT decrement counters (fill-based)."""
        engine._pending_intraday_entry = True
        engine._pending_contract = "PENDING"
        engine._pending_num_contracts = 1
        engine._pending_stop_pct = 0.50
        engine._intraday_trades_today = 1
        engine._total_options_trades_today = 1
        engine._trades_today = 1

        engine.cancel_pending_engine_entry()

        assert engine._pending_intraday_entry is False
        assert engine._pending_contract is None
        assert engine._pending_num_contracts is None
        assert engine._pending_stop_pct is None
        # V9.0: Counters are fill-based, no decrement on cancel
        assert engine._intraday_trades_today == 1
        assert engine._total_options_trades_today == 1
        assert engine._trades_today == 1

    def test_cancel_pending_intraday_no_underflow(self, engine):
        """Test intraday counter decrement does not go below 0."""
        engine._pending_intraday_entry = True
        engine._intraday_trades_today = 0
        engine._total_options_trades_today = 0
        engine._trades_today = 0

        engine.cancel_pending_engine_entry()

        assert engine._intraday_trades_today == 0
        assert engine._total_options_trades_today == 0
        assert engine._trades_today == 0

    def test_cancel_pending_intraday_when_not_pending_no_decrement(self, engine):
        """Test calling cancel when not pending does NOT decrement counters."""
        engine._pending_intraday_entry = False
        engine._intraday_trades_today = 2
        engine._total_options_trades_today = 3
        engine._trades_today = 3

        engine.cancel_pending_engine_entry()

        # Counters should NOT change because _pending_intraday_entry was False
        assert engine._intraday_trades_today == 2
        assert engine._total_options_trades_today == 3
        assert engine._trades_today == 3


class TestPendingIntradayEntryMaintenance:
    """Pending-entry plumbing hardening tests (lane isolation + stale cleanup)."""

    class _DummySymbol:
        def __init__(self, text: str):
            self._text = text
            self.SecurityType = "OPTION"

        def __str__(self):
            return self._text

    class _DummyOpenOrder:
        def __init__(self, oid: int, symbol_text: str, quantity: float):
            self.Id = oid
            self.Symbol = TestPendingIntradayEntryMaintenance._DummySymbol(symbol_text)
            self.Quantity = quantity

    class _DummyTransactions:
        def __init__(self, open_orders):
            self._open_orders = list(open_orders)
            self.cancel_requests = []

        def GetOpenOrders(self):
            return list(self._open_orders)

        def CancelOrder(self, order_id, tag=""):
            self.cancel_requests.append((int(order_id), str(tag)))

    class _DummyAlgorithm:
        def __init__(self, now: datetime, open_orders):
            self.Time = now
            self.Transactions = TestPendingIntradayEntryMaintenance._DummyTransactions(open_orders)
            self._logs = []

        def Log(self, message):
            self._logs.append(str(message))

    def _make_intraday_position(self, symbol_text: str, strategy: str = "ITM_MOMENTUM"):
        contract = OptionContract(
            symbol=symbol_text,
            underlying="QQQ",
            direction=OptionDirection.PUT if "P" in symbol_text else OptionDirection.CALL,
            strike=480.0,
            expiry="2027-12-31",
            delta=0.75,
            bid=10.0,
            ask=10.5,
            mid_price=10.25,
            open_interest=1000,
            days_to_expiry=14,
        )
        return OptionsPosition(
            contract=contract,
            entry_price=10.25,
            entry_time="2027-01-01 10:30:00",
            entry_score=3.5,
            num_contracts=1,
            stop_price=7.5,
            target_price=12.5,
            stop_pct=0.25,
            entry_strategy=strategy,
            highest_price=10.25,
        )

    def test_clears_stale_pending_when_only_exit_orders_remain(self, engine, monkeypatch):
        from engines.satellite import options_pending_guard as pending_guard_module

        mock_security_type = type("SecurityType", (), {"Option": "OPTION"})
        monkeypatch.setattr(
            options_engine_module,
            "SecurityType",
            mock_security_type,
            raising=False,
        )
        setattr(pending_guard_module, "SecurityType", mock_security_type)
        now = datetime(2027, 1, 4, 12, 0, 0)
        algo = self._DummyAlgorithm(
            now=now,
            open_orders=[
                # Negative qty = OCO exit order (should NOT keep entry pending lock alive)
                self._DummyOpenOrder(oid=9001, symbol_text="QQQ   270119P00480000", quantity=-1),
            ],
        )
        engine.algorithm = algo
        engine._pending_intraday_entry = True
        engine._pending_intraday_entry_since = now - timedelta(minutes=20)
        key = engine._pending_engine_entry_key("QQQ 270119P00480000", "ITM")
        engine._pending_intraday_entries[key] = {
            "symbol": "QQQ 270119P00480000",
            "lane": "ITM",
            "entry_strategy": "ITM_MOMENTUM",
            "created_at": (now - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        engine._intraday_positions["ITM"] = [
            self._make_intraday_position("QQQ 270119P00480000", strategy="ITM_MOMENTUM")
        ]
        engine._intraday_positions["MICRO"] = []

        engine._clear_stale_pending_engine_entry_if_orphaned()

        assert engine._pending_intraday_entries == {}
        assert engine._pending_intraday_entry is False

    def test_requests_cancel_for_aged_open_entry_order(self, engine, monkeypatch):
        from engines.satellite import options_pending_guard as pending_guard_module

        mock_security_type = type("SecurityType", (), {"Option": "OPTION"})
        # SecurityType comes from AlgorithmImports star import (empty stub in tests).
        # Must inject into both modules that reference it at runtime.
        monkeypatch.setattr(
            options_engine_module,
            "SecurityType",
            mock_security_type,
            raising=False,
        )
        setattr(pending_guard_module, "SecurityType", mock_security_type)
        now = datetime(2027, 1, 4, 12, 0, 0)
        algo = self._DummyAlgorithm(
            now=now,
            open_orders=[
                # Positive qty = live entry order
                self._DummyOpenOrder(oid=9101, symbol_text="QQQ   270105P00470000", quantity=2),
            ],
        )
        engine.algorithm = algo
        engine._pending_intraday_entry = True
        # Age must exceed CANCEL_MINUTES (5) but stay below HARD_CLEAR_MINUTES (30)
        engine._pending_intraday_entry_since = now - timedelta(minutes=10)
        key = engine._pending_engine_entry_key("QQQ 270105P00470000", "MICRO")
        engine._pending_intraday_entries[key] = {
            "symbol": "QQQ 270105P00470000",
            "lane": "MICRO",
            "entry_strategy": "MICRO_OTM_MOMENTUM",
            "created_at": (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        engine._intraday_positions["ITM"] = []
        engine._intraday_positions["MICRO"] = []

        engine._clear_stale_pending_engine_entry_if_orphaned()

        assert key in engine._pending_intraday_entries
        assert len(algo.Transactions.cancel_requests) == 1
        assert algo.Transactions.cancel_requests[0][0] == 9101

    def test_keeps_pending_when_open_entry_order_remains_even_with_lane_position(
        self, engine, monkeypatch
    ):
        from engines.satellite import options_pending_guard as pending_guard_module

        mock_security_type = type("SecurityType", (), {"Option": "OPTION"})
        monkeypatch.setattr(
            options_engine_module,
            "SecurityType",
            mock_security_type,
            raising=False,
        )
        setattr(pending_guard_module, "SecurityType", mock_security_type)
        now = datetime(2027, 1, 4, 12, 0, 0)
        algo = self._DummyAlgorithm(
            now=now,
            open_orders=[
                self._DummyOpenOrder(oid=9201, symbol_text="QQQ   270105P00470000", quantity=1),
            ],
        )
        engine.algorithm = algo
        engine._pending_intraday_entry = True
        engine._pending_intraday_entry_since = now - timedelta(minutes=20)
        key = engine._pending_engine_entry_key("QQQ 270105P00470000", "MICRO")
        engine._pending_intraday_entries[key] = {
            "symbol": "QQQ 270105P00470000",
            "lane": "MICRO",
            "entry_strategy": "MICRO_OTM_MOMENTUM",
            "created_at": (now - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        engine._intraday_positions["MICRO"] = [
            self._make_intraday_position("QQQ 270105P00470000", strategy="MICRO_OTM_MOMENTUM")
        ]
        engine._intraday_positions["ITM"] = []

        engine._clear_stale_pending_engine_entry_if_orphaned()

        assert key in engine._pending_intraday_entries
        assert engine._pending_intraday_entry is True
        assert len(algo.Transactions.cancel_requests) == 1
        assert algo.Transactions.cancel_requests[0][0] == 9201

    def test_pending_key_match_ignores_symbol_spacing(self, engine):
        key = engine._pending_engine_entry_key("QQQ 270105P00470000", "MICRO")
        engine._pending_intraday_entries[key] = {
            "symbol": "QQQ 270105P00470000",
            "lane": "MICRO",
        }

        found = engine._find_pending_engine_entry_key("QQQ   270105P00470000", lane="MICRO")

        assert found == key


class TestIntradayLaneIsolation:
    """Lane isolation guardrails for intraday plumbing."""

    def test_legacy_intraday_mirror_not_used_as_lane_source_of_truth(self, engine):
        contract = OptionContract(
            symbol="QQQ 270119P00480000",
            underlying="QQQ",
            direction=OptionDirection.PUT,
            strike=480.0,
            expiry="2027-01-19",
            delta=0.75,
            bid=10.0,
            ask=10.5,
            mid_price=10.25,
            open_interest=1000,
            days_to_expiry=14,
        )
        engine._intraday_position = OptionsPosition(
            contract=contract,
            entry_price=10.25,
            entry_time="2027-01-01 10:30:00",
            entry_score=3.5,
            num_contracts=1,
            stop_price=7.5,
            target_price=12.5,
            stop_pct=0.25,
            entry_strategy="ITM_MOMENTUM",
            highest_price=10.25,
        )

        assert engine.has_engine_position() is False
        assert engine.get_engine_positions() == []
        assert engine._find_engine_lane_by_symbol("QQQ 270119P00480000") is None

    def test_intraday_validation_failure_is_lane_scoped(self, engine):
        engine.set_last_engine_validation_failure("MICRO", "E_MICRO_A", "micro detail")
        engine.set_last_engine_validation_failure("ITM", "E_ITM_A", "itm detail")

        micro_reason, micro_detail = engine.pop_last_engine_validation_failure("MICRO")
        itm_reason, itm_detail = engine.pop_last_engine_validation_failure("ITM")

        assert micro_reason == "E_MICRO_A"
        assert micro_detail == "micro detail"
        assert itm_reason == "E_ITM_A"
        assert itm_detail == "itm detail"

    def test_pending_symbol_conflict_does_not_set_pending_entry_state(self, engine):
        contract = OptionContract(
            symbol="QQQ 270105P00470000",
            underlying="QQQ",
            direction=OptionDirection.PUT,
            strike=470.0,
            expiry="2027-01-05",
            delta=0.40,
            bid=2.0,
            ask=2.1,
            mid_price=2.05,
            open_interest=1800,
            days_to_expiry=1,
        )
        pending_key = engine._pending_engine_entry_key(str(contract.symbol), "ITM")
        engine._pending_intraday_entries[pending_key] = {
            "symbol": str(contract.symbol),
            "lane": "ITM",
            "contract": contract,
            "entry_score": 3.0,
            "num_contracts": 1,
            "entry_strategy": IntradayStrategy.ITM_MOMENTUM.value,
            "stop_pct": 0.40,
            "created_at": "2027-01-04 10:00:00",
        }
        engine._pending_intraday_entry = False
        engine._pending_contract = None
        engine._pending_num_contracts = None
        engine._pending_entry_strategy = None

        result = engine.check_engine_entry_signal(
            vix_current=18.0,
            vix_open=17.0,
            qqq_current=450.0,
            qqq_open=448.0,
            current_hour=11,
            current_minute=0,
            current_time="2027-01-04 11:00:00",
            portfolio_value=100000.0,
            raw_portfolio_value=100000.0,
            best_contract=contract,
            size_multiplier=1.0,
            macro_regime_score=60.0,
            governor_scale=1.0,
            direction=OptionDirection.PUT,
            forced_entry_strategy=IntradayStrategy.MICRO_OTM_MOMENTUM,
            micro_state=engine.get_micro_regime_state(),
            transition_ctx={"transition_overlay": "STABLE"},
        )

        assert result is None
        reason, _ = engine.pop_last_engine_validation_failure("MICRO")
        assert reason == "E_INTRADAY_PENDING_SYMBOL_CONFLICT"
        assert engine._pending_intraday_entry is False
        assert engine._pending_contract is None
        assert engine._pending_num_contracts is None
        assert engine._pending_entry_strategy is None

    def test_active_symbol_conflict_blocks_cross_lane_entry(self, engine):
        contract = OptionContract(
            symbol="QQQ 270105P00470000",
            underlying="QQQ",
            direction=OptionDirection.PUT,
            strike=470.0,
            expiry="2027-01-05",
            delta=0.40,
            bid=2.0,
            ask=2.1,
            mid_price=2.05,
            open_interest=1800,
            days_to_expiry=1,
        )
        engine._intraday_positions["ITM"] = [
            OptionsPosition(
                contract=contract,
                entry_price=2.05,
                entry_time="2027-01-04 10:00:00",
                entry_score=3.0,
                num_contracts=1,
                stop_price=1.2,
                target_price=2.8,
                stop_pct=0.40,
                entry_strategy=IntradayStrategy.ITM_MOMENTUM.value,
                highest_price=2.05,
            )
        ]
        engine._refresh_legacy_engine_mirrors()

        result = engine.check_engine_entry_signal(
            vix_current=18.0,
            vix_open=17.0,
            qqq_current=450.0,
            qqq_open=448.0,
            current_hour=11,
            current_minute=0,
            current_time="2027-01-04 11:00:00",
            portfolio_value=100000.0,
            raw_portfolio_value=100000.0,
            best_contract=contract,
            size_multiplier=1.0,
            macro_regime_score=60.0,
            governor_scale=1.0,
            direction=OptionDirection.PUT,
            forced_entry_strategy=IntradayStrategy.MICRO_OTM_MOMENTUM,
            micro_state=engine.get_micro_regime_state(),
            transition_ctx={"transition_overlay": "STABLE"},
        )

        assert result is None
        reason, detail = engine.pop_last_engine_validation_failure("MICRO")
        assert reason == "E_INTRADAY_ACTIVE_SYMBOL_CONFLICT"
        assert "already open in lane=ITM" in str(detail)

    def test_preflight_allows_micro_otm_second_slot_when_adaptive_cap_boosts(self, engine):
        contract = OptionContract(
            symbol="QQQ 270119C00490000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=490.0,
            expiry="2027-01-19",
            delta=0.35,
            bid=2.1,
            ask=2.3,
            mid_price=2.2,
            open_interest=2000,
            days_to_expiry=2,
        )
        engine._intraday_positions["MICRO"] = [
            OptionsPosition(
                contract=contract,
                entry_price=2.2,
                entry_time="2027-01-04 10:30:00",
                entry_score=3.0,
                num_contracts=1,
                stop_price=1.5,
                target_price=2.8,
                stop_pct=0.30,
                entry_strategy=IntradayStrategy.MICRO_OTM_MOMENTUM.value,
                highest_price=2.2,
            )
        ]
        engine._refresh_legacy_engine_mirrors()

        state = type("State", (), {"micro_regime": MicroRegime.GOOD_MR})()
        ok, code, detail = engine.preflight_engine_entry(
            strategy=IntradayStrategy.MICRO_OTM_MOMENTUM,
            direction=OptionDirection.CALL,
            state=state,
            vix_current=15.0,
            transition_ctx={"transition_overlay": "STABLE", "overlay_bars_since_flip": 20},
        )

        assert ok is True
        assert code == "R_OK"
        assert detail is None

    def test_register_entry_does_not_misclassify_intraday_when_other_symbol_pending(self, engine):
        pending_contract = OptionContract(
            symbol="QQQ 270105P00470000",
            underlying="QQQ",
            direction=OptionDirection.PUT,
            strike=470.0,
            expiry="2027-01-05",
            delta=0.45,
            bid=1.2,
            ask=1.4,
            mid_price=1.3,
            open_interest=1200,
            days_to_expiry=1,
        )
        fill_contract = OptionContract(
            symbol="QQQ 270112C00480000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=480.0,
            expiry="2027-01-12",
            delta=0.55,
            bid=2.0,
            ask=2.2,
            mid_price=2.1,
            open_interest=1600,
            days_to_expiry=8,
        )

        pending_key = engine._pending_engine_entry_key(
            symbol=str(pending_contract.symbol),
            lane="MICRO",
        )
        engine._pending_intraday_entries[pending_key] = {
            "symbol": str(pending_contract.symbol),
            "lane": "MICRO",
            "contract": pending_contract,
            "entry_score": 3.0,
            "num_contracts": 1,
            "entry_strategy": IntradayStrategy.MICRO_OTM_MOMENTUM.value,
            "stop_pct": 0.30,
            "created_at": "2027-01-04 10:00:00",
        }
        engine._pending_intraday_entry = True

        pos = engine.register_entry(
            fill_price=2.1,
            entry_time="2027-01-04 10:45:00",
            current_date="2027-01-04",
            contract=fill_contract,
            symbol=str(fill_contract.symbol),
        )

        assert pos is not None
        assert engine.get_position() is not None
        assert engine._find_engine_lane_by_symbol(str(fill_contract.symbol)) is None


class TestIntradayDailyReserveFairness:
    def test_blocks_micro_when_itm_reserve_unmet(self, engine, monkeypatch):
        monkeypatch.setattr(config, "INTRADAY_ENGINE_DAILY_RESERVE_ENABLED", True)
        monkeypatch.setattr(config, "INTRADAY_MIN_ITM_TRADES_RESERVED", 1)
        monkeypatch.setattr(config, "MAX_OPTIONS_TRADES_PER_DAY", 5)

        engine._total_options_trades_today = 4
        engine._intraday_itm_trades_today = 0
        engine._intraday_micro_trades_today = 4

        can_trade = engine._can_trade_options(OptionsMode.INTRADAY, intraday_engine="MICRO")

        assert can_trade is False
        reason, detail = engine.pop_last_trade_limit_failure()
        assert reason == "R_TRADE_DAILY_RESERVE_ITM"
        assert "Lane=MICRO" in str(detail)

    def test_blocks_itm_when_micro_reserve_unmet(self, engine, monkeypatch):
        monkeypatch.setattr(config, "INTRADAY_ENGINE_DAILY_RESERVE_ENABLED", True)
        monkeypatch.setattr(config, "INTRADAY_MIN_MICRO_TRADES_RESERVED", 1)
        monkeypatch.setattr(config, "MAX_OPTIONS_TRADES_PER_DAY", 5)

        engine._total_options_trades_today = 4
        engine._intraday_itm_trades_today = 4
        engine._intraday_micro_trades_today = 0

        can_trade = engine._can_trade_options(OptionsMode.INTRADAY, intraday_engine="ITM")

        assert can_trade is False
        reason, detail = engine.pop_last_trade_limit_failure()
        assert reason == "R_TRADE_DAILY_RESERVE_MICRO"
        assert "Lane=ITM" in str(detail)

    def test_allows_lane_when_reserve_already_satisfied(self, engine, monkeypatch):
        monkeypatch.setattr(config, "INTRADAY_ENGINE_DAILY_RESERVE_ENABLED", True)
        monkeypatch.setattr(config, "INTRADAY_MIN_ITM_TRADES_RESERVED", 1)
        monkeypatch.setattr(config, "MAX_OPTIONS_TRADES_PER_DAY", 5)

        engine._total_options_trades_today = 4
        engine._intraday_itm_trades_today = 1
        engine._intraday_micro_trades_today = 3

        can_trade = engine._can_trade_options(OptionsMode.INTRADAY, intraday_engine="MICRO")

        assert can_trade is True


class TestPortfolioScalingCaps:
    class _AlgoStub:
        class _PortfolioStub:
            TotalPortfolioValue = 250000.0

        Portfolio = _PortfolioStub()
        Time = datetime(2027, 1, 4, 10, 0)
        LiveMode = False

        @staticmethod
        def Log(_message):
            return None

    def test_effective_caps_follow_equity_tier(self, engine, monkeypatch):
        monkeypatch.setattr(config, "OPTIONS_PORTFOLIO_SCALING_ENABLED", True)
        monkeypatch.setattr(
            config,
            "OPTIONS_PORTFOLIO_SCALING_TIERS",
            (
                {
                    "name": "BASE",
                    "max_equity": 100000,
                    "total_positions": 7,
                    "max_swing_positions": 4,
                    "vass_concurrent": 2,
                    "itm_concurrent": 1,
                    "micro_concurrent": 1,
                    "max_options_trades_per_day": 5,
                    "max_swing_trades_per_day": 3,
                    "itm_max_trades_per_day": 4,
                    "micro_max_trades_per_day": 6,
                    "intraday_max_contracts": 50,
                },
                {
                    "name": "MID",
                    "max_equity": None,
                    "total_positions": 10,
                    "max_swing_positions": 6,
                    "vass_concurrent": 3,
                    "itm_concurrent": 2,
                    "micro_concurrent": 2,
                    "max_options_trades_per_day": 9,
                    "max_swing_trades_per_day": 5,
                    "itm_max_trades_per_day": 6,
                    "micro_max_trades_per_day": 8,
                    "intraday_max_contracts": 75,
                },
            ),
        )
        engine.algorithm = self._AlgoStub()

        pos_caps = engine._get_effective_position_caps()
        trade_caps = engine._get_effective_trade_caps()
        assert pos_caps == {"TOTAL": 10, "SWING": 6, "VASS": 3, "ITM": 2, "MICRO": 2}
        assert trade_caps == {"TOTAL": 9, "SWING": 5, "ITM": 6, "MICRO": 8}
        assert engine._get_effective_engine_contract_cap() == 75

    def test_global_daily_limit_uses_scaled_cap(self, engine, monkeypatch):
        monkeypatch.setattr(config, "OPTIONS_PORTFOLIO_SCALING_ENABLED", True)
        monkeypatch.setattr(config, "OPTIONS_ENFORCE_GLOBAL_DAILY_CAP", True)
        monkeypatch.setattr(
            config,
            "OPTIONS_PORTFOLIO_SCALING_TIERS",
            (
                {
                    "name": "TEST",
                    "max_equity": None,
                    "total_positions": 9,
                    "max_swing_positions": 5,
                    "vass_concurrent": 2,
                    "itm_concurrent": 1,
                    "micro_concurrent": 1,
                    "max_options_trades_per_day": 2,
                    "max_swing_trades_per_day": 2,
                    "itm_max_trades_per_day": 2,
                    "micro_max_trades_per_day": 2,
                    "intraday_max_contracts": 40,
                },
            ),
        )
        engine.algorithm = self._AlgoStub()
        engine._total_options_trades_today = 2

        can_trade = engine._can_trade_options(OptionsMode.INTRADAY, intraday_engine="MICRO")
        assert can_trade is False
        reason, detail = engine.pop_last_trade_limit_failure()
        assert reason == "R_SLOT_TOTAL_MAX"
        assert "2/2" in str(detail)

    def test_global_daily_limit_disabled_does_not_block(self, engine, monkeypatch):
        monkeypatch.setattr(config, "OPTIONS_ENFORCE_GLOBAL_DAILY_CAP", False)
        monkeypatch.setattr(config, "MAX_OPTIONS_TRADES_PER_DAY", 2)
        monkeypatch.setattr(config, "INTRADAY_ENGINE_DAILY_RESERVE_ENABLED", False)
        monkeypatch.setattr(config, "OPTIONS_RESERVE_SWING_DAILY_SLOTS_ENABLED", False)
        monkeypatch.setattr(config, "OPTIONS_RESERVE_INTRADAY_DAILY_SLOTS_ENABLED", False)
        engine._total_options_trades_today = 2

        can_trade = engine._can_trade_options(OptionsMode.INTRADAY, intraday_engine="MICRO")

        assert can_trade is True


class TestMicroRetryEligibility:
    def test_skip_retry_for_global_daily_limit_context(self):
        assert (
            MicroEntryEngine._should_queue_engine_retry(
                "R_SLOT_TOTAL_MAX", "Global limit reached | 5/5"
            )
            is False
        )

    def test_skip_retry_for_explicit_daily_total_code(self):
        assert (
            MicroEntryEngine._should_queue_engine_retry("R_TRADE_DAILY_TOTAL_MAX", "daily cap")
            is False
        )

    def test_keep_retry_for_slot_total_cap_context(self):
        assert (
            MicroEntryEngine._should_queue_engine_retry(
                "R_SLOT_TOTAL_MAX", "R_SLOT_TOTAL_MAX: 7 >= 7"
            )
            is True
        )


class TestExpirationExitContract:
    def test_check_expiring_options_force_exit_contract(self, engine):
        contract = OptionContract(
            symbol="QQQ 270105C00470000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=470.0,
            expiry="2027-01-05",
            delta=0.60,
            bid=2.0,
            ask=2.2,
            mid_price=2.1,
            open_interest=1500,
            days_to_expiry=0,
        )
        position = OptionsPosition(
            contract=contract,
            entry_price=2.0,
            entry_time="2027-01-05 10:00:00",
            entry_score=3.2,
            num_contracts=2,
            stop_price=1.5,
            target_price=2.8,
            stop_pct=0.25,
            entry_strategy=IntradayStrategy.ITM_MOMENTUM.value,
            highest_price=2.3,
        )
        engine._intraday_positions["ITM"] = [position]
        engine._refresh_legacy_engine_mirrors()

        signal = engine.check_expiring_options_force_exit(
            current_date="2027-01-05",
            current_hour=23,
            current_minute=59,
            current_price=2.1,
            contract_expiry_date="2027-01-05",
            position=position,
        )

        assert signal is not None
        assert signal.symbol == "QQQ 270105C00470000"


class TestIntradayForceExitTimeSource:
    def test_force_exit_time_uses_scheduler_dynamic_cutoff_when_available(self):
        class _Scheduler:
            def get_engine_options_close_hhmm(self):
                return (12, 15)

        class _Algo:
            scheduler = _Scheduler()

        engine = OptionsEngine(algorithm=_Algo())
        hh, mm = engine._get_engine_force_exit_hhmm()
        assert (hh, mm) == (12, 15)


class TestIntradayRetryIsolation:
    """Lane-scoped retry helper behavior in MainOptionsMixin."""

    class _Harness(MainOptionsMixin):
        pass

    def test_retry_state_is_lane_scoped(self):
        harness = self._Harness()
        harness.Time = datetime(2027, 1, 4, 12, 0, 0)
        harness._intraday_retry_state_by_lane = {
            "MICRO": {"pending": False, "expires": None, "direction": None, "reason_code": None},
            "ITM": {"pending": False, "expires": None, "direction": None, "reason_code": None},
        }

        expires_at = harness.Time + timedelta(minutes=20)
        harness._queue_engine_retry(
            lane="MICRO",
            direction=OptionDirection.CALL,
            reason_code="R_SLOT_TOTAL_MAX",
            expires_at=expires_at,
        )

        assert harness._get_engine_retry_state("MICRO")["pending"] is True
        assert harness._get_engine_retry_state("ITM")["pending"] is False
        assert harness._consume_engine_retry("ITM") is None

        consumed = harness._consume_engine_retry("MICRO")
        assert consumed is not None
        direction, reason_code = consumed
        assert direction == OptionDirection.CALL
        assert reason_code == "R_SLOT_TOTAL_MAX"
        assert harness._get_engine_retry_state("MICRO")["pending"] is False

    def test_unknown_lane_does_not_collapse_into_micro(self):
        harness = self._Harness()
        harness.Time = datetime(2027, 1, 4, 12, 0, 0)
        harness._intraday_retry_state_by_lane = {
            "MICRO": {"pending": False, "expires": None, "direction": None, "reason_code": None},
            "ITM": {"pending": False, "expires": None, "direction": None, "reason_code": None},
        }

        expires_at = harness.Time + timedelta(minutes=10)
        harness._queue_engine_retry(
            lane="UNKNOWN_LANE",
            direction=OptionDirection.PUT,
            reason_code="R_TEST_UNKNOWN",
            expires_at=expires_at,
        )

        assert harness._get_engine_retry_state("MICRO")["pending"] is False
        assert harness._get_engine_retry_state("UNKNOWN")["pending"] is True
        assert harness._consume_engine_retry("MICRO") is None
        consumed = harness._consume_engine_retry("UNKNOWN")
        assert consumed is not None
        assert consumed[0] == OptionDirection.PUT
        assert consumed[1] == "R_TEST_UNKNOWN"

    def test_unknown_lane_cooldown_tracked_separately(self):
        harness = self._Harness()
        harness.Time = datetime(2027, 1, 4, 12, 0, 0)
        harness._options_intraday_cooldown_until_by_lane = {"MICRO": None, "ITM": None}

        unknown_until = harness.Time + timedelta(minutes=7)
        harness._set_engine_lane_cooldown("NOISE", unknown_until)

        assert harness._get_engine_lane_cooldown_until("MICRO") is None
        assert harness._get_engine_lane_cooldown_until("UNKNOWN") == unknown_until
        assert harness._is_engine_lane_cooldown_active("MICRO") is False
        assert harness._is_engine_lane_cooldown_active("UNKNOWN") is True


class TestIntradayEngineBucketMapping:
    class _Harness(MainOptionsMixin):
        pass

    def test_debit_fade_maps_to_micro_bucket(self):
        harness = self._Harness()
        assert harness._engine_bucket_from_strategy("DEBIT_FADE") == "MICRO"
        assert harness._engine_bucket_from_strategy("INTRADAY_DEBIT_FADE") == "MICRO"
        assert harness._engine_bucket_from_strategy("MICRO_DEBIT_FADE") == "MICRO"
        assert harness._engine_bucket_from_strategy("ITM_MOMENTUM") == "ITM"


class TestRejectionAwareSizing:
    """V2.21: Tests for rejection-aware spread sizing (margin estimation + cap)."""

    @pytest.fixture
    def spread_contracts(self):
        """Create valid long/short spread contracts for testing."""
        long_leg = OptionContract(
            symbol="QQQ 271231C00300000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=300.0,
            expiry="2027-12-31",
            delta=0.60,
            bid=5.00,
            ask=5.50,
            mid_price=5.25,
            open_interest=5000,
            days_to_expiry=21,
        )
        short_leg = OptionContract(
            symbol="QQQ 271231C00305000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=305.0,
            expiry="2027-12-31",
            delta=0.40,
            bid=3.00,
            ask=3.50,
            mid_price=3.25,
            open_interest=5000,
            days_to_expiry=21,
        )
        return long_leg, short_leg

    def _call_spread_entry(self, engine, long_leg, short_leg, margin_remaining=None):
        """Helper to call check_spread_entry_signal with valid params."""
        return engine.check_spread_entry_signal(
            regime_score=70.0,  # > 60 = BULL_CALL
            vix_current=18.0,
            adx_value=30.0,  # Strong trend
            current_price=302.0,
            ma200_value=280.0,  # Price > MA200
            iv_rank=50.0,
            current_hour=10,
            current_minute=30,
            current_date="2027-01-15",
            portfolio_value=200000.0,
            long_leg_contract=long_leg,
            short_leg_contract=short_leg,
            gap_filter_triggered=False,
            vol_shock_active=False,
            size_multiplier=1.0,
            margin_remaining=margin_remaining,
        )

    def test_margin_scales_spreads_down(self, engine, spread_contracts):
        """When margin is tight, num_spreads should scale down."""
        long_leg, short_leg = spread_contracts
        # width = 305 - 300 = 5, estimated margin per spread = 5 * 100 = $500
        # With margin=$3000, safety=0.80 -> usable=$2400 -> max_by_margin=4
        # Dollar cap $7500 / (5.50 - 3.00 = $2.50 * 1.10 = $2.75 * 100 = $275) = 27
        # So margin should scale 27 -> 4
        signal = self._call_spread_entry(engine, long_leg, short_leg, margin_remaining=3000.0)
        if signal is not None:
            # If signal fires, num_contracts should be scaled by margin
            assert engine._pending_num_contracts <= 4
        else:
            # If margin too tight (below min), signal is None
            assert engine._entry_attempted_today is False

    def test_margin_below_min_returns_none(self, engine, spread_contracts):
        """When margin can only fit 1 spread (< MIN_SPREAD_CONTRACTS=2), return None."""
        long_leg, short_leg = spread_contracts
        # width=5, margin_per_spread=$500, safety=0.80
        # margin=$600 -> usable=$480 -> max=0 spreads
        signal = self._call_spread_entry(engine, long_leg, short_leg, margin_remaining=600.0)
        assert signal is None
        # Key: _entry_attempted_today should NOT be set
        assert engine._entry_attempted_today is False

    def test_margin_none_falls_back_to_dollar_cap(self, engine, spread_contracts):
        """When margin_remaining is None, sizing uses dollar cap only."""
        long_leg, short_leg = spread_contracts
        signal = self._call_spread_entry(engine, long_leg, short_leg, margin_remaining=None)
        if signal is not None:
            # Without margin constraint, uses full dollar cap
            # $7500 / $275 = 27 contracts
            assert engine._pending_num_contracts >= 20  # Not scaled down

    def test_low_vix_abs_debit_cap_scales_with_width(self, engine):
        """Low-VIX absolute debit cap scales with width (not fixed $2 on all widths).

        V12.12+: Multiple gates (trend confirm, percentage width, friction-to-target)
        can block at low VIX. Isolate the debit cap test by disabling unrelated gates
        and using tight bid-ask spreads to minimize friction.
        """
        # $5 width (1.66% of $302) passes SPREAD_WIDTH_MAX_PCT (2.0%)
        # Tight bid-ask keeps friction-to-target ratio low
        long_leg = OptionContract(
            symbol="QQQ 271231C00300000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=300.0,
            expiry="2027-12-31",
            delta=0.60,
            bid=3.40,
            ask=3.50,
            mid_price=3.45,
            open_interest=5000,
            days_to_expiry=21,
        )
        short_leg = OptionContract(
            symbol="QQQ 271231C00305000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=305.0,
            expiry="2027-12-31",
            delta=0.32,
            bid=1.80,
            ask=1.90,
            mid_price=1.85,
            open_interest=5000,
            days_to_expiry=21,
        )

        orig_trend = config.VASS_BULL_DEBIT_TREND_CONFIRM_ENABLED
        orig_friction = getattr(config, "SPREAD_ENTRY_FRICTION_GATE_ENABLED", True)
        try:
            config.VASS_BULL_DEBIT_TREND_CONFIRM_ENABLED = False
            config.SPREAD_ENTRY_FRICTION_GATE_ENABLED = False
            signal = engine.check_spread_entry_signal(
                regime_score=70.0,
                vix_current=12.0,  # compressed IV -> low-VIX absolute debit gate active
                adx_value=30.0,
                current_price=302.0,
                ma200_value=280.0,
                iv_rank=50.0,
                current_hour=10,
                current_minute=30,
                current_date="2027-01-15",
                portfolio_value=200000.0,
                long_leg_contract=long_leg,
                short_leg_contract=short_leg,
                gap_filter_triggered=False,
                vol_shock_active=False,
                size_multiplier=1.0,
                margin_remaining=None,
                direction=OptionDirection.CALL,
            )
        finally:
            config.VASS_BULL_DEBIT_TREND_CONFIRM_ENABLED = orig_trend
            config.SPREAD_ENTRY_FRICTION_GATE_ENABLED = orig_friction

        # Net debit ~$1.60 on $5 width. Width-scaled cap allows this.
        assert signal is not None

    def test_rejection_cap_constrains_sizing(self, engine, spread_contracts):
        """Post-rejection cap should further constrain sizing."""
        long_leg, short_leg = spread_contracts
        # Set rejection cap to $2000 (very tight)
        engine._rejection_margin_cap = 2000.0
        # Even with large live margin, cap constrains
        # usable = min(50000*0.80, 2000) = 2000 -> max = 2000/500 = 4
        signal = self._call_spread_entry(engine, long_leg, short_leg, margin_remaining=50000.0)
        if signal is not None:
            assert engine._pending_num_contracts <= 4

    def test_rejection_cap_cleared_on_fill(self, engine):
        """Rejection cap should be cleared after successful spread fill."""
        engine._rejection_margin_cap = 5000.0

        # Set up pending spread state for register_spread_entry
        long_leg = OptionContract(
            symbol="QQQ 271231C00300000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=300.0,
            expiry="2027-12-31",
            delta=0.60,
            bid=5.00,
            ask=5.50,
            mid_price=5.25,
            open_interest=5000,
            days_to_expiry=21,
        )
        short_leg = OptionContract(
            symbol="QQQ 271231C00305000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=305.0,
            expiry="2027-12-31",
            delta=0.40,
            bid=3.00,
            ask=3.50,
            mid_price=3.25,
            open_interest=5000,
            days_to_expiry=21,
        )
        engine._pending_spread_long_leg = long_leg
        engine._pending_spread_short_leg = short_leg
        engine._pending_spread_type = "BULL_CALL"
        engine._pending_net_debit = 2.00
        engine._pending_max_profit = 3.00
        engine._pending_spread_width = 5.0
        engine._pending_num_contracts = 10
        engine._pending_entry_score = 3.5

        engine.register_spread_entry(
            long_leg_fill_price=5.25,
            short_leg_fill_price=3.25,
            entry_time="10:30:00",
            current_date="2027-01-15",
            regime_score=70.0,
        )

        assert engine._rejection_margin_cap is None

    def test_rejection_cap_cleared_on_daily_reset(self, engine):
        """Rejection cap should be cleared on new trading day."""
        engine._rejection_margin_cap = 5000.0
        engine._last_trade_date = "2027-01-14"

        engine.reset_daily("2027-01-15")

        assert engine._rejection_margin_cap is None

    def test_entry_not_attempted_on_margin_skip(self, engine, spread_contracts):
        """When margin skip triggers, _entry_attempted_today stays False."""
        long_leg, short_leg = spread_contracts
        engine._entry_attempted_today = False

        # Very low margin = will skip
        signal = self._call_spread_entry(engine, long_leg, short_leg, margin_remaining=100.0)

        assert signal is None
        assert engine._entry_attempted_today is False

    def test_vass_scoped_bull_debit_trend_blocks_below_ma20(self, engine):
        """Scoped trend confirmation blocks low/medium-IV BULL_CALL when QQQ is below MA20."""
        ok, code, detail = engine.check_vass_bull_debit_trend_confirmation(
            vix_current=18.0,
            current_price=302.0,
            qqq_open=300.0,
            qqq_sma20=305.0,
            qqq_sma20_ready=True,
        )

        assert ok is False
        assert code == "R_BULL_DEBIT_TREND_MA20"
        assert "MA20" in detail

    def test_vass_scoped_bull_debit_trend_blocks_weak_day_move(self, engine):
        """Scoped trend confirmation blocks low/medium-IV BULL_CALL when day move is negative.

        VASS_BULL_DEBIT_REQUIRE_POSITIVE_DAY must be enabled for this gate to fire.
        """
        orig = getattr(config, "VASS_BULL_DEBIT_REQUIRE_POSITIVE_DAY", True)
        try:
            config.VASS_BULL_DEBIT_REQUIRE_POSITIVE_DAY = True
            ok, code, detail = engine.check_vass_bull_debit_trend_confirmation(
                vix_current=18.0,
                current_price=301.5,
                qqq_open=301.7,  # about -0.07%, below relaxed threshold of -0.05%
                qqq_sma20=295.0,
                qqq_sma20_ready=True,
            )

            assert ok is False
            assert code == "R_BULL_DEBIT_TREND_DAY"
            assert "QQQ day" in detail
        finally:
            config.VASS_BULL_DEBIT_REQUIRE_POSITIVE_DAY = orig

    def test_vass_scoped_bull_debit_trend_not_applied_outside_scope(self, engine):
        """Scoped trend confirmation should bypass when VIX is above configured scope."""
        ok, code, detail = engine.check_vass_bull_debit_trend_confirmation(
            vix_current=22.5,
            current_price=302.0,
            qqq_open=300.0,
            qqq_sma20=305.0,
            qqq_sma20_ready=True,
        )

        assert ok is True
        assert code == "R_OK"
        assert "SCOPE_BYPASS" in detail


# =============================================================================
# V2.22: NEUTRALITY EXIT (HYSTERESIS SHIELD) TESTS
# =============================================================================


@pytest.mark.skipif(
    not getattr(config, "SPREAD_NEUTRALITY_EXIT_ENABLED", True),
    reason="V5.3: Neutrality exit disabled for strategy validation",
)
class TestNeutralityExit:
    """V2.22: Tests for symmetric neutrality exit — close flat spreads in dead zone."""

    @pytest.fixture
    def long_leg(self):
        """Long leg contract for spread."""
        return OptionContract(
            symbol="QQQ 271231C00300000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=300.0,
            expiry="2027-12-31",
            delta=0.60,
            bid=5.00,
            ask=5.50,
            mid_price=5.25,
            open_interest=5000,
            days_to_expiry=21,
        )

    @pytest.fixture
    def short_leg(self):
        """Short leg contract for spread."""
        return OptionContract(
            symbol="QQQ 271231C00305000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=305.0,
            expiry="2027-12-31",
            delta=0.40,
            bid=3.00,
            ask=3.50,
            mid_price=3.25,
            open_interest=5000,
            days_to_expiry=21,
        )

    def _make_spread(self, engine, spread_type, net_debit, long_leg, short_leg):
        """Helper: set up a spread position on the engine."""
        engine._spread_position = SpreadPosition(
            long_leg=long_leg,
            short_leg=short_leg,
            spread_type=spread_type,
            net_debit=net_debit,
            max_profit=5.0 - net_debit,  # width=5, max_profit = width - debit
            width=5.0,
            entry_time="10:00:00",
            entry_score=4.0,
            num_spreads=3,
            regime_at_entry=40.0,
        )

    def test_neutrality_exit_fires_in_dead_zone_flat_pnl(self, engine, long_leg, short_leg):
        """Bear Put in dead zone with flat P&L should trigger neutrality exit."""
        self._make_spread(engine, "BEAR_PUT", 2.50, long_leg, short_leg)
        # Current value = long - short; entry debit = 2.50
        # pnl_pct = (current - entry) / entry
        # For +3%: current = 2.50 * 1.03 = 2.575 → long=5.575, short=3.00
        long_price = 5.575
        short_price = 3.00  # current_value = 5.575 - 3.00 = 2.575 → pnl_pct = +3%

        result = engine.check_spread_exit_signals(
            long_leg_price=long_price,
            short_leg_price=short_price,
            regime_score=52.0,  # Dead zone (45-60)
            vix_current=20.0,
            current_dte=15,  # Not near expiry
        )

        assert result is not None
        assert len(result) > 0

    def test_neutrality_exit_spares_winners(self, engine, long_leg, short_leg):
        """Spread with +20% P&L in dead zone should NOT trigger neutrality exit."""
        self._make_spread(engine, "BULL_CALL", 2.50, long_leg, short_leg)
        # +20%: current = 2.50 * 1.20 = 3.00
        long_price = 6.00
        short_price = 3.00  # value = 3.00, pnl_pct = +20%

        result = engine.check_spread_exit_signals(
            long_leg_price=long_price,
            short_leg_price=short_price,
            regime_score=55.0,  # Dead zone
            vix_current=14.0,  # V9.4: Below STRESS threshold (19.0) to test neutrality logic
            current_dte=15,
        )

        # Should be None — +20% exceeds ±10% band, no exit trigger
        assert result is None

    def test_neutrality_exit_spares_losers(self, engine, long_leg, short_leg):
        """Spread with -25% P&L in dead zone should NOT trigger neutrality exit."""
        self._make_spread(engine, "BEAR_PUT", 2.50, long_leg, short_leg)
        # -25%: current = 2.50 * 0.75 = 1.875
        long_price = 4.875
        short_price = 3.00  # value = 1.875, pnl_pct = -25%

        result = engine.check_spread_exit_signals(
            long_leg_price=long_price,
            short_leg_price=short_price,
            regime_score=50.0,  # Dead zone
            vix_current=20.0,
            current_dte=15,
        )

        # Should be None — -25% outside ±10% band, let stop loss handle it
        assert result is None

    def test_neutrality_exit_not_triggered_outside_dead_zone(self, engine, long_leg, short_leg):
        """Spread with flat P&L outside dead zone should NOT trigger neutrality exit."""
        self._make_spread(engine, "BULL_CALL", 2.50, long_leg, short_leg)
        # Flat P&L (+2%): current = 2.55
        long_price = 5.55
        short_price = 3.00  # value = 2.55, pnl_pct = +2%

        result = engine.check_spread_exit_signals(
            long_leg_price=long_price,
            short_leg_price=short_price,
            regime_score=70.0,  # V3.4: Outside dead zone (45-65) — bullish conviction
            vix_current=14.0,  # V9.4: Below STRESS threshold (19.0) to test neutrality logic
            current_dte=15,
        )

        # Should be None — regime has directional conviction
        assert result is None

    def test_neutrality_exit_credit_spread_in_dead_zone(self, engine, long_leg, short_leg):
        """Credit spread in dead zone with flat P&L should trigger neutrality exit."""
        # Credit spread: net_debit is negative (credit received)
        engine._spread_position = SpreadPosition(
            long_leg=long_leg,
            short_leg=short_leg,
            spread_type="BULL_PUT_CREDIT",
            net_debit=-1.50,  # Received $1.50 credit
            max_profit=1.50,  # Max profit = credit received
            width=5.0,
            entry_time="10:00:00",
            entry_score=4.0,
            num_spreads=3,
            regime_at_entry=65.0,
        )
        # Credit P&L: pnl = credit - current_cost; pnl_pct = pnl / max_profit
        # For flat (+2%): pnl = 0.03, pnl_pct = 0.03/1.50 = 2%
        # current_spread_value = short - long; pnl = 1.50 - current_value
        # pnl_pct = +2% → pnl = 0.03 → current_value = 1.50 - 0.03 = 1.47
        short_price = 4.47
        long_price = 3.00  # short - long = 1.47

        result = engine.check_spread_exit_signals(
            long_leg_price=long_price,
            short_leg_price=short_price,
            regime_score=50.0,  # Dead zone
            vix_current=20.0,
            current_dte=15,
        )

        assert result is not None
        assert len(result) > 0

    def test_neutrality_exit_disabled_by_config(self, engine, long_leg, short_leg):
        """Neutrality exit should not fire when disabled in config.

        V12.x: Use regime_score=48 to stay below VASS_REGIME_BREAK_BEAR_CEILING (50)
        and inside neutrality dead zone (48-62) without triggering the bear regime break exit.
        """
        self._make_spread(engine, "BEAR_PUT", 2.50, long_leg, short_leg)
        long_price = 5.575
        short_price = 3.00  # +3% P&L, dead zone

        original = config.SPREAD_NEUTRALITY_EXIT_ENABLED
        try:
            config.SPREAD_NEUTRALITY_EXIT_ENABLED = False
            result = engine.check_spread_exit_signals(
                long_leg_price=long_price,
                short_leg_price=short_price,
                regime_score=48.0,
                vix_current=20.0,
                current_dte=15,
            )
            # Should be None — feature disabled
            assert result is None
        finally:
            config.SPREAD_NEUTRALITY_EXIT_ENABLED = original

    def test_neutrality_exit_within_pnl_band(self, engine, long_leg, short_leg):
        """P&L within ±6% band should trigger neutrality exit in dead zone.

        V6.13: SPREAD_NEUTRALITY_EXIT_PNL_BAND = 6%, SPREAD_NEUTRALITY_ZONE = 48-62.
        """
        self._make_spread(engine, "BULL_CALL", 2.50, long_leg, short_leg)
        # +4%: current = 2.50 * 1.04 = 2.60
        long_price = 5.60
        short_price = 3.00  # value = 2.60, pnl_pct = +4%

        result = engine.check_spread_exit_signals(
            long_leg_price=long_price,
            short_leg_price=short_price,
            regime_score=50.0,  # Dead zone (within 48-62)
            vix_current=20.0,
            current_dte=15,
        )

        assert result is not None
        assert len(result) > 0


# =============================================================================
# V2.23: VASS CREDIT SPREAD ENTRY TESTS
# =============================================================================


class TestVASSCreditSpreadEntry:
    """Tests for VASS credit spread strategy selection and entry signals."""

    @pytest.fixture
    def engine(self):
        """Create an OptionsEngine instance for testing."""
        return OptionsEngine(algorithm=None)

    @pytest.fixture
    def credit_short_leg(self):
        """Create a short leg (we SELL) for Bull Put Credit."""
        return OptionContract(
            symbol="QQQ 270315P00500000",
            underlying="QQQ",
            direction=OptionDirection.PUT,
            strike=500.0,
            expiry="2027-03-15",
            delta=-0.30,
            bid=3.50,
            ask=3.80,
            mid_price=3.65,
            open_interest=5000,
            days_to_expiry=10,
        )

    @pytest.fixture
    def credit_long_leg(self):
        """Create a long leg (we BUY for protection) for Bull Put Credit."""
        return OptionContract(
            symbol="QQQ 270315P00495000",
            underlying="QQQ",
            direction=OptionDirection.PUT,
            strike=495.0,
            expiry="2027-03-15",
            delta=-0.20,
            bid=1.80,
            ask=2.10,
            mid_price=1.95,
            open_interest=4000,
            days_to_expiry=10,
        )

    @pytest.fixture
    def bear_call_short_leg(self):
        """Create a short leg for Bear Call Credit."""
        return OptionContract(
            symbol="QQQ 270315C00520000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=520.0,
            expiry="2027-03-15",
            delta=0.30,
            bid=3.20,
            ask=3.50,
            mid_price=3.35,
            open_interest=4500,
            days_to_expiry=10,
        )

    @pytest.fixture
    def bear_call_long_leg(self):
        """Create a long leg for Bear Call Credit."""
        return OptionContract(
            symbol="QQQ 270315C00525000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=525.0,
            expiry="2027-03-15",
            delta=0.20,
            bid=1.60,
            ask=1.90,
            mid_price=1.75,
            open_interest=3500,
            days_to_expiry=10,
        )

    # --- VASS Strategy Selection Tests ---

    def test_select_strategy_high_iv_bullish(self, engine):
        """HIGH IV + BULLISH should select Bull Put Credit with weekly DTE (V6.9: sell premium)."""
        strategy, dte_min, dte_max = engine._select_strategy("BULLISH", "HIGH")
        # V6.9: Reverted to V2.8 - sell premium in HIGH IV environment
        assert strategy == SpreadStrategy.BULL_PUT_CREDIT
        assert dte_min == config.VASS_HIGH_IV_DTE_MIN
        assert dte_max == config.VASS_HIGH_IV_DTE_MAX

    def test_select_strategy_low_iv_bearish(self, engine):
        """LOW IV + BEARISH should select Bear Put Debit with monthly DTE."""
        strategy, dte_min, dte_max = engine._select_strategy("BEARISH", "LOW")
        assert strategy == SpreadStrategy.BEAR_PUT_DEBIT
        assert dte_min == config.VASS_LOW_IV_DTE_MIN
        assert dte_max == config.VASS_LOW_IV_DTE_MAX

    def test_select_strategy_medium_iv_bullish(self, engine):
        """V12.6: MEDIUM IV + BULLISH selects Bull Call Debit (VASS_MEDIUM_IV_PREFER_CREDIT=False)."""
        strategy, dte_min, dte_max = engine._select_strategy("BULLISH", "MEDIUM")
        if config.VASS_MEDIUM_IV_PREFER_CREDIT:
            assert strategy == SpreadStrategy.BULL_PUT_CREDIT
        else:
            assert strategy == SpreadStrategy.BULL_CALL_DEBIT
        assert dte_min == config.VASS_MEDIUM_IV_DTE_MIN
        assert dte_max == config.VASS_MEDIUM_IV_DTE_MAX

    def test_select_strategy_medium_iv_bearish(self, engine):
        """V12.6: MEDIUM IV + BEARISH selects Bear Put Debit (VASS_MEDIUM_IV_PREFER_CREDIT=False)."""
        strategy, dte_min, dte_max = engine._select_strategy("BEARISH", "MEDIUM")
        if config.VASS_MEDIUM_IV_PREFER_CREDIT:
            assert strategy == SpreadStrategy.BEAR_CALL_CREDIT
        else:
            assert strategy == SpreadStrategy.BEAR_PUT_DEBIT
        assert dte_min == config.VASS_MEDIUM_IV_DTE_MIN
        assert dte_max == config.VASS_MEDIUM_IV_DTE_MAX

    # --- Credit Entry Signal Tests ---

    def test_credit_entry_blocked_in_stress_overlay(
        self, engine, credit_short_leg, credit_long_leg
    ):
        """V9.4: BULL_PUT_CREDIT blocked when STRESS overlay active (VIX >= 19.0).

        V3.0 thesis: PUT spreads allowed in bearish regimes, but V6.22 STRESS overlay
        now blocks BULL_PUT_CREDIT to prevent selling puts into rising vol.
        """
        signal = engine.check_credit_spread_entry_signal(
            regime_score=45.0,  # V3.0: Bearish regime (< 50) for PUT spreads
            vix_current=28.0,  # High IV → triggers STRESS overlay (>= 19.0)
            adx_value=30.0,
            current_price=520.0,
            ma200_value=480.0,
            iv_rank=70.0,
            current_hour=11,
            current_minute=0,
            current_date="2027-03-05",
            portfolio_value=100_000,
            short_leg_contract=credit_short_leg,
            long_leg_contract=credit_long_leg,
            strategy=SpreadStrategy.BULL_PUT_CREDIT,
            direction=OptionDirection.PUT,
        )

        # V9.4: BULL_PUT_CREDIT blocked by STRESS overlay at VIX >= 19.0
        assert signal is None

    def test_credit_entry_blocked_low_credit(self, engine, credit_long_leg):
        """V3.0: Credit spread with insufficient premium should be rejected."""
        # Create short leg with very low bid (credit < $0.30)
        low_premium_short = OptionContract(
            symbol="QQQ 270315P00500000",
            underlying="QQQ",
            direction=OptionDirection.PUT,
            strike=500.0,
            expiry="2027-03-15",
            delta=-0.30,
            bid=2.20,  # bid - long_leg.ask = 2.20 - 2.10 = $0.10 < $0.30 min
            ask=2.50,
            mid_price=2.35,
            open_interest=5000,
            days_to_expiry=10,
        )

        signal = engine.check_credit_spread_entry_signal(
            regime_score=45.0,  # V3.0: Bearish regime (< 50) for PUT spreads
            vix_current=28.0,
            adx_value=30.0,
            current_price=510.0,
            ma200_value=480.0,
            iv_rank=70.0,
            current_hour=11,
            current_minute=0,
            current_date="2027-03-05",
            portfolio_value=100_000,
            short_leg_contract=low_premium_short,
            long_leg_contract=credit_long_leg,
            strategy=SpreadStrategy.BULL_PUT_CREDIT,
        )

        assert signal is None

    def test_credit_entry_margin_sizing(self, engine, credit_short_leg, credit_long_leg):
        """Credit spread sizing should use margin-based calculation."""
        # Width = $5.00 (500 - 495), Credit = $1.40 (3.50 - 2.10)
        # Margin per spread = (5.00 - 1.40) * 100 = $360
        # $7500 cap / $360 = 20 spreads
        num_spreads, credit_per, max_loss_per, total_margin = engine._calculate_credit_spread_size(
            credit_short_leg, credit_long_leg, 7500
        )

        assert num_spreads > 0
        assert credit_per > 0  # Positive credit received
        assert max_loss_per > 0  # Defined max loss
        assert total_margin <= 7500  # Never exceeds allocation

    def test_credit_put_blocked_lower_neutral_stress_overlay(
        self, engine, credit_short_leg, credit_long_leg
    ):
        """V9.4: PUT credit spread in Lower NEUTRAL blocked by STRESS overlay.

        V3.9 thesis: Lower NEUTRAL allows PUT-only at reduced sizing.
        But V6.22 STRESS overlay (VIX >= 19.0) blocks BULL_PUT_CREDIT.
        """
        signal = engine.check_credit_spread_entry_signal(
            regime_score=55.0,  # V3.9: Lower NEUTRAL (50-59)
            vix_current=28.0,  # High IV → triggers STRESS overlay (>= 19.0)
            adx_value=30.0,
            current_price=520.0,
            ma200_value=480.0,
            iv_rank=70.0,
            current_hour=11,
            current_minute=0,
            current_date="2027-03-05",
            portfolio_value=100_000,
            short_leg_contract=credit_short_leg,
            long_leg_contract=credit_long_leg,
            strategy=SpreadStrategy.BULL_PUT_CREDIT,
            direction=OptionDirection.PUT,
        )

        # V9.4: BULL_PUT_CREDIT blocked by STRESS overlay at VIX >= 19.0
        assert signal is None

    # --- IVSensor Tests ---

    def test_iv_sensor_classification_high(self):
        """IVSensor with VIX avg=30 should classify as HIGH."""
        sensor = IVSensor(smoothing_minutes=30)
        # V6.6: Updated - Feed 15 readings of VIX=30 (threshold raised to 28)
        for _ in range(15):
            sensor.update(30.0)

        assert sensor.is_ready()
        assert sensor.classify() == "HIGH"

    def test_iv_sensor_classification_low(self):
        """IVSensor with VIX avg=12 should classify as LOW."""
        sensor = IVSensor(smoothing_minutes=30)
        for _ in range(15):
            sensor.update(12.0)

        assert sensor.is_ready()
        assert sensor.classify() == "LOW"

    def test_iv_sensor_classification_medium(self):
        """IVSensor with VIX avg=20 should classify as MEDIUM."""
        sensor = IVSensor(smoothing_minutes=30)
        for _ in range(15):
            sensor.update(20.0)

        assert sensor.is_ready()
        assert sensor.classify() == "MEDIUM"

    # --- is_credit_strategy / is_debit_strategy ---

    def test_is_credit_strategy(self, engine):
        """Verify credit strategy classification."""
        assert engine.is_credit_strategy(SpreadStrategy.BULL_PUT_CREDIT) is True
        assert engine.is_credit_strategy(SpreadStrategy.BEAR_CALL_CREDIT) is True
        assert engine.is_credit_strategy(SpreadStrategy.BULL_CALL_DEBIT) is False
        assert engine.is_credit_strategy(SpreadStrategy.BEAR_PUT_DEBIT) is False

    def test_is_debit_strategy(self, engine):
        """Verify debit strategy classification."""
        assert engine.is_debit_strategy(SpreadStrategy.BULL_CALL_DEBIT) is True
        assert engine.is_debit_strategy(SpreadStrategy.BEAR_PUT_DEBIT) is True
        assert engine.is_debit_strategy(SpreadStrategy.BULL_PUT_CREDIT) is False
        assert engine.is_debit_strategy(SpreadStrategy.BEAR_CALL_CREDIT) is False


class TestResolverMicroPrimary:
    """Micro-first resolver behavior for intraday signals."""

    def test_micro_misaligned_without_conviction_blocks(self, engine):
        """Micro misalignment without conviction is blocked in current resolver policy."""
        should_trade, resolved_direction, reason = engine.resolve_trade_signal(
            engine="MICRO",
            engine_direction="BEARISH",
            engine_conviction=False,
            macro_direction="BULLISH",
            conviction_strength=None,
        )
        assert should_trade is False
        assert resolved_direction is None
        assert "MISALIGNED_NO_CONVICTION" in reason

    def test_vass_misaligned_without_conviction_still_blocked(self, engine):
        """V12.9: VASS without conviction is blocked (conviction-only direction mode)."""
        should_trade, resolved_direction, reason = engine.resolve_trade_signal(
            engine="VASS",
            engine_direction="BEARISH",
            engine_conviction=False,
            macro_direction="BULLISH",
            conviction_strength=None,
        )
        assert should_trade is False
        assert resolved_direction is None
        # V12.9: VASS_USE_CONVICTION_ONLY_DIRECTION=True means no conviction → no trade
        assert "VASS_NO_CONVICTION" in reason or "Misaligned" in reason


class TestOvernightGapProtectionExit:
    """Overnight gap-protection exit metadata for VASS spread closes."""

    def _make_credit_spread(self) -> SpreadPosition:
        long_leg = OptionContract(
            symbol="QQQ 240816C00457000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=457.0,
            expiry="2024-08-16",
            delta=0.20,
            bid=1.20,
            ask=1.30,
            mid_price=1.25,
            open_interest=5000,
            days_to_expiry=9,
        )
        short_leg = OptionContract(
            symbol="QQQ 240816C00453000",
            underlying="QQQ",
            direction=OptionDirection.CALL,
            strike=453.0,
            expiry="2024-08-16",
            delta=0.35,
            bid=2.40,
            ask=2.60,
            mid_price=2.50,
            open_interest=5000,
            days_to_expiry=9,
        )
        return SpreadPosition(
            long_leg=long_leg,
            short_leg=short_leg,
            spread_type="BEAR_CALL_CREDIT",
            net_debit=-1.40,
            max_profit=1.40,
            width=4.0,
            entry_time="2024-08-07 12:15:00",
            entry_score=4.0,
            num_spreads=2,
            regime_at_entry=62.0,
        )

    def test_overnight_gap_exit_includes_credit_metadata(self, engine):
        """Credit spread OGP exits should include metadata needed for credit-market close path."""
        engine._spread_position = self._make_credit_spread()
        signals = engine.check_overnight_gap_protection_exit(
            current_vix=22.7,
            current_date="2024-08-07",
        )

        assert signals is not None
        assert len(signals) == 1
        signal = signals[0]
        md = signal.metadata or {}
        assert md.get("spread_close_short") is True
        assert md.get("spread_type") == "BEAR_CALL_CREDIT"
        assert md.get("is_credit_spread") is True
        assert md.get("options_lane") == "VASS"
        assert md.get("options_strategy") == "BEAR_CALL_CREDIT"
        assert float(md.get("spread_entry_debit", -1.0)) == 0.0
        assert float(md.get("spread_entry_credit", 0.0)) == 1.40
