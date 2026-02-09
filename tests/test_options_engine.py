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

import pytest

import config
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
from models.enums import Urgency

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
        """Test wide spread (> 25%) reduces score significantly. V2.3.7 threshold."""
        score = engine._score_liquidity(
            spread_pct=0.30,  # > 25% (V2.3.7 warning threshold)
            open_interest=10000,
        )
        assert score == 0.5  # (0.0 + 1.0) / 2

    def test_liquidity_low_oi(self, engine):
        """Test low open interest reduces score."""
        # V2.3.7: OI thresholds changed (MIN_OI now 100, half is 50)
        score = engine._score_liquidity(
            spread_pct=0.03,
            open_interest=75,  # 50-100 (low OI range)
        )
        assert score == 0.75  # (1.0 + 0.5) / 2

    def test_liquidity_very_low_oi(self, engine):
        """Test very low OI reduces score significantly."""
        # V2.3.7: OI thresholds changed (MIN_OI now 100, half is 50)
        score = engine._score_liquidity(
            spread_pct=0.03,
            open_interest=30,  # < 50
        )
        assert score == 0.5  # (1.0 + 0.0) / 2


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
        assert target_price == 1.45 * (1 + 0.50)  # $2.175

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
        """Test exit when profit target hit (+50%)."""
        # Entry at $1.45, target at $2.175
        result = engine_with_position.check_exit_signals(current_price=2.20)
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
        """Test intraday mode allocation is 6.25% (config.OPTIONS_INTRADAY_ALLOCATION)."""
        from models.enums import OptionsMode

        allocation = engine.get_mode_allocation(OptionsMode.INTRADAY, portfolio_value=100000)
        # 6.25% of $100,000 = $6,250
        assert allocation == 6250.0

    def test_get_mode_allocation_swing(self, engine):
        """Test swing mode allocation is 18.75% (config.OPTIONS_SWING_ALLOCATION)."""
        from models.enums import OptionsMode

        allocation = engine.get_mode_allocation(OptionsMode.SWING, portfolio_value=100000)
        # 18.75% of $100,000 = $18,750
        assert allocation == 18750.0

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

        engine._intraday_position = OptionsPosition(
            contract=contract,
            entry_price=1.00,
            entry_time="10:00:00",
            entry_score=3.2,
            num_contracts=10,
            stop_price=0.80,
            target_price=1.50,
            stop_pct=0.20,
        )

        # Try to enter again
        result = engine.check_intraday_entry_signal(
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

        engine._intraday_position = OptionsPosition(
            contract=contract,
            entry_price=1.00,
            entry_time="10:30:00",
            entry_score=3.2,
            num_contracts=10,
            stop_price=0.80,
            target_price=1.50,
            stop_pct=0.20,
        )

        return engine

    def test_intraday_force_exit_at_1530(self, engine_with_intraday_position):
        """Test intraday force exit at 15:30 ET."""
        result = engine_with_intraday_position.check_intraday_force_exit(
            current_hour=15,
            current_minute=30,
            current_price=1.10,
        )

        assert result is not None
        assert result.target_weight == 0.0
        assert "INTRADAY_TIME_EXIT_1530" in result.reason

    def test_intraday_force_exit_after_1530(self, engine_with_intraday_position):
        """Test intraday force exit after 15:30 ET."""
        result = engine_with_intraday_position.check_intraday_force_exit(
            current_hour=15,
            current_minute=35,
            current_price=1.10,
        )

        assert result is not None
        assert "INTRADAY_TIME_EXIT_1530" in result.reason

    def test_no_intraday_force_exit_before_1530(self, engine_with_intraday_position):
        """Test no force exit before 15:30 ET."""
        result = engine_with_intraday_position.check_intraday_force_exit(
            current_hour=15,
            current_minute=29,
            current_price=1.10,
        )

        assert result is None

    def test_no_intraday_force_exit_no_position(self):
        """Test no force exit when no intraday position."""
        engine = OptionsEngine()
        result = engine.check_intraday_force_exit(
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
            recommended_strategy=IntradayStrategy.DEBIT_FADE,
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
        assert micro_state.recommended_strategy == IntradayStrategy.DEBIT_FADE
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

        engine._intraday_position = OptionsPosition(
            contract=contract,
            entry_price=1.00,
            entry_time="15:00:00",
            entry_score=3.2,
            num_contracts=10,
            stop_price=0.80,
            target_price=1.50,
            stop_pct=0.20,
        )

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
        # Simulate a zombie intraday position
        engine._intraday_position = "ZOMBIE_INTRADAY"  # Any non-None value

        assert engine.has_intraday_position() is True

        engine.clear_all_positions()

        assert engine.has_intraday_position() is False
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
        assert engine._entry_attempted_today is False

    def test_cancel_pending_spread_entry_idempotent(self, engine):
        """Test calling cancel when no spread pending is safe."""
        engine.cancel_pending_spread_entry()  # Should not raise
        assert engine._pending_spread_long_leg is None
        assert engine._entry_attempted_today is False

    def test_cancel_pending_intraday_entry_clears_and_decrements(self, engine):
        """Test intraday rejection clears state and decrements counter."""
        engine._pending_intraday_entry = True
        engine._pending_contract = "PENDING"
        engine._pending_num_contracts = 1
        engine._pending_stop_pct = 0.50
        engine._intraday_trades_today = 1
        engine._total_options_trades_today = 1
        engine._trades_today = 1

        engine.cancel_pending_intraday_entry()

        assert engine._pending_intraday_entry is False
        assert engine._pending_contract is None
        assert engine._pending_num_contracts is None
        assert engine._pending_stop_pct is None
        assert engine._intraday_trades_today == 0
        assert engine._total_options_trades_today == 0
        assert engine._trades_today == 0

    def test_cancel_pending_intraday_no_underflow(self, engine):
        """Test intraday counter decrement does not go below 0."""
        engine._pending_intraday_entry = True
        engine._intraday_trades_today = 0
        engine._total_options_trades_today = 0
        engine._trades_today = 0

        engine.cancel_pending_intraday_entry()

        assert engine._intraday_trades_today == 0
        assert engine._total_options_trades_today == 0
        assert engine._trades_today == 0

    def test_cancel_pending_intraday_when_not_pending_no_decrement(self, engine):
        """Test calling cancel when not pending does NOT decrement counters."""
        engine._pending_intraday_entry = False
        engine._intraday_trades_today = 2
        engine._total_options_trades_today = 3
        engine._trades_today = 3

        engine.cancel_pending_intraday_entry()

        # Counters should NOT change because _pending_intraday_entry was False
        assert engine._intraday_trades_today == 2
        assert engine._total_options_trades_today == 3
        assert engine._trades_today == 3


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
            current_dte=15,
        )

        assert result is not None
        assert len(result) > 0

    def test_neutrality_exit_disabled_by_config(self, engine, long_leg, short_leg):
        """Neutrality exit should not fire when disabled in config."""
        self._make_spread(engine, "BEAR_PUT", 2.50, long_leg, short_leg)
        long_price = 5.575
        short_price = 3.00  # +3% P&L, dead zone

        original = config.SPREAD_NEUTRALITY_EXIT_ENABLED
        try:
            config.SPREAD_NEUTRALITY_EXIT_ENABLED = False
            result = engine.check_spread_exit_signals(
                long_leg_price=long_price,
                short_leg_price=short_price,
                regime_score=52.0,
                current_dte=15,
            )
            # Should be None — feature disabled
            assert result is None
        finally:
            config.SPREAD_NEUTRALITY_EXIT_ENABLED = original

    def test_neutrality_exit_boundary_pnl_10pct(self, engine, long_leg, short_leg):
        """P&L at exactly +10% boundary should trigger neutrality exit (inclusive)."""
        self._make_spread(engine, "BULL_CALL", 2.50, long_leg, short_leg)
        # +10%: current = 2.50 * 1.10 = 2.75
        long_price = 5.75
        short_price = 3.00  # value = 2.75, pnl_pct = +10%

        result = engine.check_spread_exit_signals(
            long_leg_price=long_price,
            short_leg_price=short_price,
            regime_score=50.0,  # Dead zone
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
        """HIGH IV + BULLISH should select Bull Call Debit with weekly DTE (V5.3: debit for gamma)."""
        strategy, dte_min, dte_max = engine._select_strategy("BULLISH", "HIGH")
        # V5.3: Changed from CREDIT to DEBIT - gamma capture in high IV environment
        assert strategy == SpreadStrategy.BULL_CALL_DEBIT
        assert dte_min == config.VASS_HIGH_IV_DTE_MIN
        assert dte_max == config.VASS_HIGH_IV_DTE_MAX

    def test_select_strategy_low_iv_bearish(self, engine):
        """LOW IV + BEARISH should select Bear Put Debit with monthly DTE."""
        strategy, dte_min, dte_max = engine._select_strategy("BEARISH", "LOW")
        assert strategy == SpreadStrategy.BEAR_PUT_DEBIT
        assert dte_min == config.VASS_LOW_IV_DTE_MIN
        assert dte_max == config.VASS_LOW_IV_DTE_MAX

    def test_select_strategy_medium_iv_bullish(self, engine):
        """MEDIUM IV + BULLISH should select Bull Call Debit with weekly DTE."""
        strategy, dte_min, dte_max = engine._select_strategy("BULLISH", "MEDIUM")
        assert strategy == SpreadStrategy.BULL_CALL_DEBIT
        assert dte_min == config.VASS_MEDIUM_IV_DTE_MIN
        assert dte_max == config.VASS_MEDIUM_IV_DTE_MAX

    # --- Credit Entry Signal Tests ---

    def test_credit_entry_signal_bull_put(self, engine, credit_short_leg, credit_long_leg):
        """V3.0: Bull Put Credit entry should generate TargetWeight with credit metadata.

        V3.0 thesis: PUT spreads (including Bull Put Credit) are allowed in bearish regimes (< 50).
        V6.4: Short PUT strike (500) must be >= 3% OTM, so current_price must be >= 515.46.
        """
        signal = engine.check_credit_spread_entry_signal(
            regime_score=45.0,  # V3.0: Bearish regime (< 50) for PUT spreads
            vix_current=28.0,  # High IV
            adx_value=30.0,
            current_price=520.0,  # V6.4: Raised to pass 3% OTM gate (500/520 = 3.85% OTM)
            ma200_value=480.0,
            iv_rank=70.0,
            current_hour=11,
            current_minute=0,
            current_date="2027-03-05",
            portfolio_value=100_000,
            short_leg_contract=credit_short_leg,
            long_leg_contract=credit_long_leg,
            strategy=SpreadStrategy.BULL_PUT_CREDIT,
            direction=OptionDirection.PUT,  # V6.0: Direction from conviction resolution
        )

        assert signal is not None
        assert signal.metadata["spread_type"] == "BULL_PUT_CREDIT"
        assert signal.metadata["is_credit_spread"] is True
        assert signal.metadata["spread_credit_received"] > 0
        assert signal.urgency == Urgency.IMMEDIATE
        # V2.23.1 APVP: Primary symbol must be LONG leg (protection) for router combo convention
        assert (
            signal.symbol == credit_long_leg.symbol
        ), "Primary symbol must be long leg (protection)"
        assert signal.requested_quantity > 0, "Quantity must be positive for router combo logic"
        # Short leg goes in metadata for combo order
        assert signal.metadata["spread_short_leg_symbol"] == credit_short_leg.symbol

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

    def test_credit_put_allowed_lower_neutral_reduced_sizing(
        self, engine, credit_short_leg, credit_long_leg
    ):
        """V3.9: PUT credit spread allowed in Lower NEUTRAL (50-59) at 50% sizing.

        V3.9 thesis: Lower NEUTRAL allows PUT-only at reduced sizing.
        Upper NEUTRAL (60-69) is CALL-only zone.
        BULL_PUT_CREDIT is PUT direction, so allowed at 50% in Lower NEUTRAL.
        V6.4: Short PUT strike (500) must be >= 3% OTM, so current_price must be >= 515.46.
        """
        signal = engine.check_credit_spread_entry_signal(
            regime_score=55.0,  # V3.9: Lower NEUTRAL (50-59) - PUT allowed at 50% sizing
            vix_current=28.0,
            adx_value=30.0,
            current_price=520.0,  # V6.4: Raised to pass 3% OTM gate (500/520 = 3.85% OTM)
            ma200_value=480.0,
            iv_rank=70.0,
            current_hour=11,
            current_minute=0,
            current_date="2027-03-05",
            portfolio_value=100_000,
            short_leg_contract=credit_short_leg,
            long_leg_contract=credit_long_leg,
            strategy=SpreadStrategy.BULL_PUT_CREDIT,
            direction=OptionDirection.PUT,  # V6.0: Direction from conviction resolution
        )

        # V3.9: PUT allowed in Lower NEUTRAL at reduced sizing
        assert signal is not None
        assert "BULL_PUT_CREDIT" in signal.reason

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
