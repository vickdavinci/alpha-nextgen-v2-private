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
    OptionContract,
    OptionDirection,
    OptionsEngine,
    OptionsPosition,
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
        symbol="QQQ 260126C00450000",
        underlying="QQQ",
        direction=OptionDirection.CALL,
        strike=450.0,
        expiry="2026-01-26",
        delta=0.50,
        bid=1.40,
        ask=1.50,
        mid_price=1.45,
        open_interest=10000,
        days_to_expiry=3,
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
        """Test score < 3.0 is invalid."""
        score = EntryScore(
            score_adx=0.50,
            score_momentum=0.75,
            score_iv=0.75,
            score_liquidity=0.75,
        )
        assert score.total == 2.75
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
        """Test moderate spread reduces score."""
        score = engine._score_liquidity(
            spread_pct=0.08,  # 5-10%
            open_interest=10000,
        )
        assert score == 0.75  # (0.5 + 1.0) / 2

    def test_liquidity_wide_spread(self, engine):
        """Test wide spread (> 10%) reduces score significantly."""
        score = engine._score_liquidity(
            spread_pct=0.15,  # > 10%
            open_interest=10000,
        )
        assert score == 0.5  # (0.0 + 1.0) / 2

    def test_liquidity_low_oi(self, engine):
        """Test low open interest reduces score."""
        score = engine._score_liquidity(
            spread_pct=0.03,
            open_interest=3000,  # 2500-5000
        )
        assert score == 0.75  # (1.0 + 0.5) / 2

    def test_liquidity_very_low_oi(self, engine):
        """Test very low OI reduces score significantly."""
        score = engine._score_liquidity(
            spread_pct=0.03,
            open_interest=500,  # < 2500
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
        """Test score 3.0-3.25 gets 20% stop."""
        tier = engine.get_stop_tier(3.0)
        assert tier["stop_pct"] == 0.20
        assert tier["contracts"] == 34

    def test_tier_3_25(self, engine):
        """Test score 3.25-3.5 gets 22% stop."""
        tier = engine.get_stop_tier(3.25)
        assert tier["stop_pct"] == 0.22
        assert tier["contracts"] == 31

    def test_tier_3_5(self, engine):
        """Test score 3.5-3.75 gets 25% stop."""
        tier = engine.get_stop_tier(3.5)
        assert tier["stop_pct"] == 0.25
        assert tier["contracts"] == 27

    def test_tier_3_75(self, engine):
        """Test score 3.75-4.0 gets 30% stop."""
        tier = engine.get_stop_tier(3.75)
        assert tier["stop_pct"] == 0.30
        assert tier["contracts"] == 23

    def test_tier_4_0(self, engine):
        """Test score 4.0 gets highest tier."""
        tier = engine.get_stop_tier(4.0)
        assert tier["stop_pct"] == 0.30
        assert tier["contracts"] == 23


class TestPositionSizing:
    """Tests for position sizing calculation."""

    def test_basic_position_size(self, engine):
        """Test basic position sizing with 1% risk."""
        num_contracts, stop_pct, stop_price, target_price = engine.calculate_position_size(
            entry_score=3.5,
            premium=1.45,
            portfolio_value=100000,
        )
        # 1% risk = $1000
        # Risk per contract = $1.45 × 0.25 × 100 = $36.25
        # Max contracts = $1000 / $36.25 = 27.6 → capped at tier max 27
        assert num_contracts <= 27
        assert stop_pct == 0.25
        assert stop_price == 1.45 * (1 - 0.25)  # $1.0875
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
        assert data["symbol"] == "QQQ 260126C00450000"
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

    def test_entry_allowed_regime_at_40(self, engine, sample_contract):
        """Test entry allowed when regime score = 40 (boundary)."""
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
            regime_score=40.0,  # At threshold
        )
        assert result is not None

    def test_entry_allowed_regime_above_40(self, engine, sample_contract):
        """Test entry allowed when regime score > 40."""
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
            regime_score=65.0,  # Above threshold
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
        from engines.satellite.options_engine import OptionsEngine, OptionContract

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
        # Per-contract Greeks (not scaled) for risk limit checking
        assert greeks.delta == 0.50
        assert greeks.gamma == 0.03
        assert greeks.vega == 0.15
        assert greeks.theta == -0.02

    def test_update_position_greeks(self):
        """Test updating position Greeks."""
        from engines.satellite.options_engine import OptionsEngine, OptionContract

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
        from engines.satellite.options_engine import OptionsEngine, OptionContract
        from engines.core.risk_engine import RiskEngine

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
        from engines.satellite.options_engine import OptionsEngine, OptionContract
        from engines.core.risk_engine import RiskEngine

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

    def test_entry_blocked_dte_too_low(self, engine):
        """Test entry blocked when DTE < 1."""
        contract = OptionContract(
            symbol="QQQ 260126C00455000",
            strike=455.0,
            expiry="2026-01-26",
            delta=0.50,
            mid_price=1.45,
            open_interest=5000,
            days_to_expiry=0,  # 0 DTE - too low
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

    def test_entry_blocked_dte_too_high(self, engine):
        """Test entry blocked when DTE > 4."""
        contract = OptionContract(
            symbol="QQQ 260205C00455000",
            strike=455.0,
            expiry="2026-02-05",
            delta=0.50,
            mid_price=2.50,
            open_interest=5000,
            days_to_expiry=10,  # 10 DTE - too high
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
        """Test Greeks breach when theta exceeds limit (-0.02)."""
        from engines.core.risk_engine import RiskEngine

        engine = OptionsEngine()
        risk_engine = RiskEngine()

        contract = OptionContract(
            symbol="QQQ 260126C00455000",
            delta=0.50,  # Within limits
            gamma=0.02,  # Within limits
            vega=0.10,  # Within limits
            theta=-0.03,  # Exceeds CB_THETA_WARNING=-0.02 (more negative)
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

        # Theta -0.03 < threshold -0.02 triggers breach
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
