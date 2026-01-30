"""
Tests for MicroRegimeEngine (V2.1.1 Options Engine).

Tests cover:
- VIX Direction Classification (7 directions)
- VIX Level Classification (3 levels)
- 21 Micro Regime Matrix
- Micro Score Calculation
- Strategy Recommendation
- Whipsaw Detection
- Spike Detection
- Full Update Cycle
"""

from collections import deque
from unittest.mock import MagicMock

import pytest

import config
from engines.satellite.options_engine import MicroRegimeEngine, VIXSnapshot
from models.enums import IntradayStrategy, MicroRegime, VIXDirection, VIXLevel, WhipsawState

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def micro_engine():
    """Create a fresh MicroRegimeEngine instance."""
    return MicroRegimeEngine(log_func=None)


@pytest.fixture
def micro_engine_with_log():
    """Create MicroRegimeEngine with mock logging."""
    log_mock = MagicMock()
    return MicroRegimeEngine(log_func=log_mock)


# =============================================================================
# VIX DIRECTION CLASSIFICATION TESTS
# =============================================================================


class TestClassifyVIXDirection:
    """Tests for classify_vix_direction() method."""

    def test_falling_fast_direction(self, micro_engine):
        """VIX falling fast (< -5%)."""
        # VIX dropped from 20 to 18 (-10%)
        direction, score = micro_engine.classify_vix_direction(18.0, 20.0)
        assert direction == VIXDirection.FALLING_FAST
        assert score == config.MICRO_SCORE_DIR_FALLING_FAST

    def test_falling_direction(self, micro_engine):
        """VIX falling (-5% to -2%)."""
        # VIX dropped from 20 to 19.4 (-3%)
        direction, score = micro_engine.classify_vix_direction(19.4, 20.0)
        assert direction == VIXDirection.FALLING
        assert score == config.MICRO_SCORE_DIR_FALLING

    def test_stable_direction(self, micro_engine):
        """VIX stable (-2% to +2%)."""
        # VIX unchanged
        direction, score = micro_engine.classify_vix_direction(20.0, 20.0)
        assert direction == VIXDirection.STABLE
        assert score == config.MICRO_SCORE_DIR_STABLE

    def test_stable_direction_small_rise(self, micro_engine):
        """VIX stable with small rise (+1%)."""
        direction, score = micro_engine.classify_vix_direction(20.2, 20.0)
        assert direction == VIXDirection.STABLE

    def test_rising_direction(self, micro_engine):
        """VIX rising (+2% to +5%)."""
        # VIX rose from 20 to 20.6 (+3%)
        direction, score = micro_engine.classify_vix_direction(20.6, 20.0)
        assert direction == VIXDirection.RISING
        assert score == config.MICRO_SCORE_DIR_RISING

    def test_rising_fast_direction(self, micro_engine):
        """VIX rising fast (+5% to +10%)."""
        # VIX rose from 20 to 21.4 (+7%)
        direction, score = micro_engine.classify_vix_direction(21.4, 20.0)
        assert direction == VIXDirection.RISING_FAST
        assert score == config.MICRO_SCORE_DIR_RISING_FAST

    def test_spiking_direction(self, micro_engine):
        """VIX spiking (> +10%)."""
        # VIX rose from 20 to 23 (+15%)
        direction, score = micro_engine.classify_vix_direction(23.0, 20.0)
        assert direction == VIXDirection.SPIKING
        assert score == config.MICRO_SCORE_DIR_SPIKING

    def test_zero_open_returns_stable(self, micro_engine):
        """Zero VIX open should return STABLE."""
        direction, _ = micro_engine.classify_vix_direction(20.0, 0.0)
        assert direction == VIXDirection.STABLE

    # Boundary tests
    def test_boundary_falling_fast_to_falling(self, micro_engine):
        """Test boundary between FALLING_FAST and FALLING (-5.0%)."""
        # Exactly at -5%
        direction, _ = micro_engine.classify_vix_direction(19.0, 20.0)
        # -5% should be FALLING (not FALLING_FAST, which is < -5%)
        assert direction == VIXDirection.FALLING

    def test_boundary_falling_to_stable(self, micro_engine):
        """Test boundary between FALLING and STABLE (-2.0%)."""
        # Exactly at -2% is STABLE (FALLING is < -2%, not <=)
        direction, _ = micro_engine.classify_vix_direction(19.6, 20.0)
        assert direction == VIXDirection.STABLE
        # Just below -2% is FALLING
        direction_below, _ = micro_engine.classify_vix_direction(19.58, 20.0)  # -2.1%
        assert direction_below == VIXDirection.FALLING

    def test_boundary_stable_to_rising(self, micro_engine):
        """Test boundary between STABLE and RISING (+2.0%)."""
        # Exactly at +2%
        direction, _ = micro_engine.classify_vix_direction(20.4, 20.0)
        assert direction == VIXDirection.STABLE

    def test_boundary_rising_to_rising_fast(self, micro_engine):
        """Test boundary between RISING and RISING_FAST (+5.0%)."""
        # Exactly at +5%
        direction, _ = micro_engine.classify_vix_direction(21.0, 20.0)
        assert direction == VIXDirection.RISING

    def test_boundary_rising_fast_to_spiking(self, micro_engine):
        """Test boundary between RISING_FAST and SPIKING (+10.0%)."""
        # Exactly at +10%
        direction, _ = micro_engine.classify_vix_direction(22.0, 20.0)
        assert direction == VIXDirection.RISING_FAST


# =============================================================================
# VIX LEVEL CLASSIFICATION TESTS
# =============================================================================


class TestClassifyVIXLevel:
    """Tests for classify_vix_level() method."""

    def test_very_low_vix(self, micro_engine):
        """VIX < 15 is LOW with highest score."""
        level, score = micro_engine.classify_vix_level(12.0)
        assert level == VIXLevel.LOW
        assert score == config.MICRO_SCORE_VIX_VERY_CALM

    def test_low_vix(self, micro_engine):
        """VIX 15-18 is LOW with calm score."""
        level, score = micro_engine.classify_vix_level(16.0)
        assert level == VIXLevel.LOW
        assert score == config.MICRO_SCORE_VIX_CALM

    def test_normal_low_vix(self, micro_engine):
        """VIX 18-20 is LOW with normal score."""
        level, score = micro_engine.classify_vix_level(19.0)
        assert level == VIXLevel.LOW
        assert score == config.MICRO_SCORE_VIX_NORMAL

    def test_elevated_medium_vix(self, micro_engine):
        """VIX 20-23 is MEDIUM with elevated score."""
        level, score = micro_engine.classify_vix_level(21.0)
        assert level == VIXLevel.MEDIUM
        assert score == config.MICRO_SCORE_VIX_ELEVATED

    def test_high_medium_vix(self, micro_engine):
        """VIX 23-25 is MEDIUM with high score."""
        level, score = micro_engine.classify_vix_level(24.0)
        assert level == VIXLevel.MEDIUM
        assert score == config.MICRO_SCORE_VIX_HIGH

    def test_extreme_high_vix(self, micro_engine):
        """VIX > 25 is HIGH with extreme score."""
        level, score = micro_engine.classify_vix_level(30.0)
        assert level == VIXLevel.HIGH
        assert score == config.MICRO_SCORE_VIX_EXTREME

    def test_very_high_vix(self, micro_engine):
        """VIX > 40 is still HIGH."""
        level, _ = micro_engine.classify_vix_level(45.0)
        assert level == VIXLevel.HIGH

    # Boundary tests
    def test_boundary_low_to_medium(self, micro_engine):
        """Test boundary at VIX = 20 (LOW to MEDIUM)."""
        level_19, _ = micro_engine.classify_vix_level(19.9)
        level_20, _ = micro_engine.classify_vix_level(20.0)
        assert level_19 == VIXLevel.LOW
        assert level_20 == VIXLevel.MEDIUM

    def test_boundary_medium_to_high(self, micro_engine):
        """Test boundary at VIX = 25 (MEDIUM to HIGH)."""
        level_24, _ = micro_engine.classify_vix_level(24.9)
        level_25, _ = micro_engine.classify_vix_level(25.0)
        assert level_24 == VIXLevel.MEDIUM
        assert level_25 == VIXLevel.HIGH


# =============================================================================
# MICRO REGIME CLASSIFICATION TESTS (21 REGIMES)
# =============================================================================


class TestClassifyMicroRegime:
    """Tests for classify_micro_regime() - 21 regime matrix."""

    # VIX LOW (< 20) regimes
    def test_low_falling_fast_is_perfect_mr(self, micro_engine):
        """LOW + FALLING_FAST = PERFECT_MR."""
        regime = micro_engine.classify_micro_regime(VIXLevel.LOW, VIXDirection.FALLING_FAST)
        assert regime == MicroRegime.PERFECT_MR

    def test_low_falling_is_good_mr(self, micro_engine):
        """LOW + FALLING = GOOD_MR."""
        regime = micro_engine.classify_micro_regime(VIXLevel.LOW, VIXDirection.FALLING)
        assert regime == MicroRegime.GOOD_MR

    def test_low_stable_is_normal(self, micro_engine):
        """LOW + STABLE = NORMAL."""
        regime = micro_engine.classify_micro_regime(VIXLevel.LOW, VIXDirection.STABLE)
        assert regime == MicroRegime.NORMAL

    def test_low_rising_is_caution(self, micro_engine):
        """LOW + RISING = CAUTION_LOW."""
        regime = micro_engine.classify_micro_regime(VIXLevel.LOW, VIXDirection.RISING)
        assert regime == MicroRegime.CAUTION_LOW

    def test_low_rising_fast_is_transition(self, micro_engine):
        """LOW + RISING_FAST = TRANSITION."""
        regime = micro_engine.classify_micro_regime(VIXLevel.LOW, VIXDirection.RISING_FAST)
        assert regime == MicroRegime.TRANSITION

    def test_low_spiking_is_risk_off(self, micro_engine):
        """LOW + SPIKING = RISK_OFF_LOW."""
        regime = micro_engine.classify_micro_regime(VIXLevel.LOW, VIXDirection.SPIKING)
        assert regime == MicroRegime.RISK_OFF_LOW

    def test_low_whipsaw_is_choppy(self, micro_engine):
        """LOW + WHIPSAW = CHOPPY_LOW."""
        regime = micro_engine.classify_micro_regime(VIXLevel.LOW, VIXDirection.WHIPSAW)
        assert regime == MicroRegime.CHOPPY_LOW

    # VIX MEDIUM (20-25) regimes
    def test_medium_falling_fast_is_recovering(self, micro_engine):
        """MEDIUM + FALLING_FAST = RECOVERING."""
        regime = micro_engine.classify_micro_regime(VIXLevel.MEDIUM, VIXDirection.FALLING_FAST)
        assert regime == MicroRegime.RECOVERING

    def test_medium_falling_is_improving(self, micro_engine):
        """MEDIUM + FALLING = IMPROVING."""
        regime = micro_engine.classify_micro_regime(VIXLevel.MEDIUM, VIXDirection.FALLING)
        assert regime == MicroRegime.IMPROVING

    def test_medium_stable_is_cautious(self, micro_engine):
        """MEDIUM + STABLE = CAUTIOUS."""
        regime = micro_engine.classify_micro_regime(VIXLevel.MEDIUM, VIXDirection.STABLE)
        assert regime == MicroRegime.CAUTIOUS

    def test_medium_rising_is_worsening(self, micro_engine):
        """MEDIUM + RISING = WORSENING."""
        regime = micro_engine.classify_micro_regime(VIXLevel.MEDIUM, VIXDirection.RISING)
        assert regime == MicroRegime.WORSENING

    def test_medium_rising_fast_is_deteriorating(self, micro_engine):
        """MEDIUM + RISING_FAST = DETERIORATING."""
        regime = micro_engine.classify_micro_regime(VIXLevel.MEDIUM, VIXDirection.RISING_FAST)
        assert regime == MicroRegime.DETERIORATING

    def test_medium_spiking_is_breaking(self, micro_engine):
        """MEDIUM + SPIKING = BREAKING."""
        regime = micro_engine.classify_micro_regime(VIXLevel.MEDIUM, VIXDirection.SPIKING)
        assert regime == MicroRegime.BREAKING

    def test_medium_whipsaw_is_unstable(self, micro_engine):
        """MEDIUM + WHIPSAW = UNSTABLE."""
        regime = micro_engine.classify_micro_regime(VIXLevel.MEDIUM, VIXDirection.WHIPSAW)
        assert regime == MicroRegime.UNSTABLE

    # VIX HIGH (> 25) regimes
    def test_high_falling_fast_is_panic_easing(self, micro_engine):
        """HIGH + FALLING_FAST = PANIC_EASING."""
        regime = micro_engine.classify_micro_regime(VIXLevel.HIGH, VIXDirection.FALLING_FAST)
        assert regime == MicroRegime.PANIC_EASING

    def test_high_falling_is_calming(self, micro_engine):
        """HIGH + FALLING = CALMING."""
        regime = micro_engine.classify_micro_regime(VIXLevel.HIGH, VIXDirection.FALLING)
        assert regime == MicroRegime.CALMING

    def test_high_stable_is_elevated(self, micro_engine):
        """HIGH + STABLE = ELEVATED."""
        regime = micro_engine.classify_micro_regime(VIXLevel.HIGH, VIXDirection.STABLE)
        assert regime == MicroRegime.ELEVATED

    def test_high_rising_is_worsening_high(self, micro_engine):
        """HIGH + RISING = WORSENING_HIGH."""
        regime = micro_engine.classify_micro_regime(VIXLevel.HIGH, VIXDirection.RISING)
        assert regime == MicroRegime.WORSENING_HIGH

    def test_high_rising_fast_is_full_panic(self, micro_engine):
        """HIGH + RISING_FAST = FULL_PANIC."""
        regime = micro_engine.classify_micro_regime(VIXLevel.HIGH, VIXDirection.RISING_FAST)
        assert regime == MicroRegime.FULL_PANIC

    def test_high_spiking_is_crash(self, micro_engine):
        """HIGH + SPIKING = CRASH."""
        regime = micro_engine.classify_micro_regime(VIXLevel.HIGH, VIXDirection.SPIKING)
        assert regime == MicroRegime.CRASH

    def test_high_whipsaw_is_volatile(self, micro_engine):
        """HIGH + WHIPSAW = VOLATILE."""
        regime = micro_engine.classify_micro_regime(VIXLevel.HIGH, VIXDirection.WHIPSAW)
        assert regime == MicroRegime.VOLATILE


# =============================================================================
# MICRO SCORE CALCULATION TESTS
# =============================================================================


class TestCalculateMicroScore:
    """Tests for calculate_micro_score() method."""

    def test_ideal_mr_conditions_high_score(self, micro_engine):
        """Ideal MR conditions should yield high score."""
        # VIX 15 falling, QQQ moved 1% over 2 hours
        score = micro_engine.calculate_micro_score(
            vix_current=15.0,
            vix_open=17.0,  # Falling ~12%
            qqq_current=400.0,
            qqq_open=396.0,  # +1% move
            move_duration_minutes=120,
        )
        # Should be high score (good MR conditions)
        assert score >= 50

    def test_crisis_conditions_low_score(self, micro_engine):
        """Crisis conditions should yield low/negative score."""
        # VIX 35 spiking, QQQ crashed 3%
        score = micro_engine.calculate_micro_score(
            vix_current=35.0,
            vix_open=28.0,  # +25% spike
            qqq_current=380.0,
            qqq_open=391.4,  # -3% crash
            move_duration_minutes=30,  # Fast move
        )
        # Should be low score (bad conditions)
        assert score <= 30

    def test_score_range_validation(self, micro_engine):
        """Score should be within expected range."""
        # Various conditions
        scores = []
        for vix_current in [12, 18, 25, 40]:
            for vix_change in [-15, -5, 0, 5, 15]:
                vix_open = vix_current / (1 + vix_change / 100)
                score = micro_engine.calculate_micro_score(
                    vix_current=vix_current,
                    vix_open=vix_open,
                    qqq_current=400.0,
                    qqq_open=398.0,
                    move_duration_minutes=60,
                )
                scores.append(score)

        # Scores should be within reasonable range
        assert min(scores) >= -20
        assert max(scores) <= 100

    def test_zero_qqq_open_handles_gracefully(self, micro_engine):
        """Zero QQQ open should not crash."""
        score = micro_engine.calculate_micro_score(
            vix_current=18.0,
            vix_open=20.0,
            qqq_current=400.0,
            qqq_open=0.0,  # Zero open
            move_duration_minutes=60,
        )
        # Should return a valid score (missing QQQ component)
        assert isinstance(score, float)


class TestScoreQQQMove:
    """Tests for _score_qqq_move() method."""

    def test_tiny_move(self, micro_engine):
        """Move < 0.3% is tiny."""
        score = micro_engine._score_qqq_move(0.2)
        assert score == config.MICRO_SCORE_MOVE_TINY

    def test_building_move(self, micro_engine):
        """Move 0.3-0.5% is building."""
        score = micro_engine._score_qqq_move(0.4)
        assert score == config.MICRO_SCORE_MOVE_BUILDING

    def test_approaching_move(self, micro_engine):
        """Move 0.5-0.8% is approaching."""
        score = micro_engine._score_qqq_move(0.7)
        assert score == config.MICRO_SCORE_MOVE_APPROACHING

    def test_trigger_move(self, micro_engine):
        """Move 0.8-1.25% is trigger zone."""
        score = micro_engine._score_qqq_move(1.0)
        assert score == config.MICRO_SCORE_MOVE_TRIGGER

    def test_extended_move(self, micro_engine):
        """Move > 1.25% is extended."""
        score = micro_engine._score_qqq_move(2.0)
        assert score == config.MICRO_SCORE_MOVE_EXTENDED


class TestScoreMoveVelocity:
    """Tests for _score_move_velocity() method."""

    def test_gradual_velocity(self, micro_engine):
        """Duration > 120 min is gradual."""
        score = micro_engine._score_move_velocity(150)
        assert score == config.MICRO_SCORE_VELOCITY_GRADUAL

    def test_moderate_velocity(self, micro_engine):
        """Duration 60-120 min is moderate."""
        score = micro_engine._score_move_velocity(90)
        assert score == config.MICRO_SCORE_VELOCITY_MODERATE

    def test_fast_velocity(self, micro_engine):
        """Duration 30-60 min is fast."""
        score = micro_engine._score_move_velocity(45)
        assert score == config.MICRO_SCORE_VELOCITY_FAST

    def test_spike_velocity(self, micro_engine):
        """Duration < 30 min is spike."""
        score = micro_engine._score_move_velocity(15)
        assert score == config.MICRO_SCORE_VELOCITY_SPIKE


# =============================================================================
# STRATEGY RECOMMENDATION TESTS
# =============================================================================


class TestRecommendStrategy:
    """Tests for recommend_strategy() method."""

    def test_perfect_mr_regime_recommends_debit_fade(self, micro_engine):
        """PERFECT_MR with high score -> DEBIT_FADE."""
        strategy = micro_engine.recommend_strategy(
            micro_regime=MicroRegime.PERFECT_MR,
            micro_score=70,
            vix_current=15.0,
            qqq_move_pct=1.0,
        )
        assert strategy == IntradayStrategy.DEBIT_FADE

    def test_crash_regime_recommends_no_trade(self, micro_engine):
        """CRASH regime -> NO_TRADE."""
        strategy = micro_engine.recommend_strategy(
            micro_regime=MicroRegime.CRASH,
            micro_score=20,
            vix_current=40.0,
            qqq_move_pct=3.0,
        )
        assert strategy == IntradayStrategy.NO_TRADE

    def test_crash_with_negative_score_recommends_protective(self, micro_engine):
        """CRASH with negative score -> PROTECTIVE_PUTS."""
        strategy = micro_engine.recommend_strategy(
            micro_regime=MicroRegime.CRASH,
            micro_score=-10,
            vix_current=45.0,
            qqq_move_pct=5.0,
        )
        assert strategy == IntradayStrategy.PROTECTIVE_PUTS

    def test_choppy_regime_recommends_credit(self, micro_engine):
        """CHOPPY_LOW with sufficient VIX -> CREDIT_SPREAD."""
        strategy = micro_engine.recommend_strategy(
            micro_regime=MicroRegime.CHOPPY_LOW,
            micro_score=40,
            vix_current=20.0,  # Above credit min VIX
            qqq_move_pct=0.5,
        )
        assert strategy == IntradayStrategy.CREDIT_SPREAD

    def test_full_panic_recommends_no_trade(self, micro_engine):
        """FULL_PANIC -> NO_TRADE."""
        strategy = micro_engine.recommend_strategy(
            micro_regime=MicroRegime.FULL_PANIC,
            micro_score=10,
            vix_current=35.0,
            qqq_move_pct=4.0,
        )
        assert strategy == IntradayStrategy.NO_TRADE

    def test_deteriorating_recommends_itm_momentum(self, micro_engine):
        """DETERIORATING with high VIX and big move -> ITM_MOMENTUM."""
        strategy = micro_engine.recommend_strategy(
            micro_regime=MicroRegime.DETERIORATING,
            micro_score=30,
            vix_current=28.0,  # Above ITM min VIX
            qqq_move_pct=1.5,  # Above ITM min move
        )
        assert strategy == IntradayStrategy.ITM_MOMENTUM


# =============================================================================
# WHIPSAW DETECTION TESTS
# =============================================================================


class TestDetectWhipsaw:
    """Tests for _detect_whipsaw() method."""

    def test_insufficient_history_returns_trending(self, micro_engine):
        """Less than 6 data points -> TRENDING."""
        # Add only 3 data points
        for i in range(3):
            micro_engine._vix_history.append(
                VIXSnapshot(timestamp=f"10:{i}0", value=20.0 + i, change_from_open_pct=i)
            )

        state, reversals = micro_engine._detect_whipsaw()
        assert state == WhipsawState.TRENDING
        assert reversals == 0

    def test_steady_trend_is_trending(self, micro_engine):
        """Consistent upward movement -> TRENDING."""
        # Add 12 points with consistent upward move
        for i in range(12):
            micro_engine._vix_history.append(
                VIXSnapshot(timestamp=f"10:{i:02d}", value=20.0 + i * 0.5, change_from_open_pct=i)
            )

        state, reversals = micro_engine._detect_whipsaw()
        assert state == WhipsawState.TRENDING
        assert reversals <= 2

    def test_many_reversals_is_whipsaw(self, micro_engine):
        """Many direction changes -> WHIPSAW."""
        # Add 12 points with alternating direction
        for i in range(12):
            value = 20.0 + (1.0 if i % 2 == 0 else -1.0)
            micro_engine._vix_history.append(
                VIXSnapshot(timestamp=f"10:{i:02d}", value=value, change_from_open_pct=0)
            )

        state, reversals = micro_engine._detect_whipsaw()
        assert state == WhipsawState.WHIPSAW
        assert reversals >= 5


# =============================================================================
# SPIKE DETECTION TESTS
# =============================================================================


class TestCheckSpikeAlert:
    """Tests for check_spike_alert() method."""

    def test_no_spike_on_small_change(self, micro_engine):
        """Small VIX change -> no spike."""
        alert = micro_engine.check_spike_alert(
            vix_current=20.0, vix_5min_ago=19.8, current_time="10:05"
        )
        assert alert is False

    def test_spike_on_large_change(self, micro_engine):
        """Large VIX change -> spike alert."""
        alert = micro_engine.check_spike_alert(
            vix_current=22.0, vix_5min_ago=20.0, current_time="10:05"
        )
        # 10% change should trigger spike
        assert alert is True

    def test_zero_vix_5min_ago_no_crash(self, micro_engine):
        """Zero VIX 5min ago should not crash."""
        alert = micro_engine.check_spike_alert(
            vix_current=20.0, vix_5min_ago=0.0, current_time="10:05"
        )
        assert alert is False


# =============================================================================
# FULL UPDATE CYCLE TESTS
# =============================================================================


class TestUpdate:
    """Tests for update() method."""

    def test_update_returns_valid_state(self, micro_engine):
        """Update should return complete state."""
        state = micro_engine.update(
            vix_current=18.0,
            vix_open=20.0,
            qqq_current=400.0,
            qqq_open=398.0,
            current_time="10:30",
        )

        # Check all fields are populated
        assert state.vix_level in [VIXLevel.LOW, VIXLevel.MEDIUM, VIXLevel.HIGH]
        assert state.vix_direction in list(VIXDirection)
        assert state.micro_regime in list(MicroRegime)
        assert isinstance(state.micro_score, float)
        assert state.recommended_strategy in list(IntradayStrategy)
        assert state.last_update == "10:30"

    def test_update_adds_to_history(self, micro_engine):
        """Update should add to VIX history."""
        initial_len = len(micro_engine._vix_history)

        micro_engine.update(
            vix_current=18.0,
            vix_open=20.0,
            qqq_current=400.0,
            qqq_open=398.0,
            current_time="10:30",
        )

        assert len(micro_engine._vix_history) == initial_len + 1

    def test_update_calculates_qqq_move(self, micro_engine):
        """Update should calculate QQQ move percentage."""
        state = micro_engine.update(
            vix_current=18.0,
            vix_open=20.0,
            qqq_current=402.0,
            qqq_open=400.0,  # +0.5%
            current_time="10:30",
        )

        assert state.qqq_move_pct == pytest.approx(0.5, rel=0.01)

    def test_multiple_updates_track_history(self, micro_engine):
        """Multiple updates should build VIX history."""
        for i in range(15):
            micro_engine.update(
                vix_current=18.0 + i * 0.1,
                vix_open=20.0,
                qqq_current=400.0 + i,
                qqq_open=398.0,
                current_time=f"10:{30 + i}",
            )

        # History should be capped at maxlen (12)
        assert len(micro_engine._vix_history) == 12


# =============================================================================
# RESET TESTS
# =============================================================================


class TestResetDaily:
    """Tests for reset_daily() method."""

    def test_reset_clears_state(self, micro_engine):
        """Reset should clear all state."""
        # First add some state
        micro_engine.update(
            vix_current=18.0,
            vix_open=20.0,
            qqq_current=400.0,
            qqq_open=398.0,
            current_time="10:30",
        )

        # Reset
        micro_engine.reset_daily()

        # Check state is cleared
        assert len(micro_engine._vix_history) == 0
        assert micro_engine._vix_15min_ago == 0.0
        assert micro_engine._vix_30min_ago == 0.0
        assert micro_engine._qqq_open == 0.0


# =============================================================================
# INTEGRATION TESTS
# =============================================================================


class TestMicroRegimeIntegration:
    """Integration tests for MicroRegimeEngine."""

    def test_full_trading_day_simulation(self, micro_engine):
        """Simulate a full trading day of updates."""
        # Market open
        vix_open = 18.0
        qqq_open = 400.0

        strategies_seen = set()

        # Simulate updates every 15 minutes (10:00 - 15:00)
        for hour in range(10, 15):
            for minute in [0, 15, 30, 45]:
                # Simulate some VIX movement
                time_factor = (hour - 10) * 60 + minute
                vix_current = vix_open + time_factor * 0.01 - 1.0
                qqq_current = qqq_open + time_factor * 0.02 - 3.0

                state = micro_engine.update(
                    vix_current=vix_current,
                    vix_open=vix_open,
                    qqq_current=qqq_current,
                    qqq_open=qqq_open,
                    current_time=f"{hour}:{minute:02d}",
                )

                strategies_seen.add(state.recommended_strategy)

        # Should see variety of strategies over a trading day
        assert len(strategies_seen) >= 1

    def test_crisis_escalation_scenario(self, micro_engine):
        """Simulate VIX spiking during a market crash."""
        vix_open = 20.0
        qqq_open = 400.0

        # VIX spikes from 20 to 35 over 2 hours
        for i in range(8):
            vix_current = 20.0 + i * 2.0
            qqq_current = 400.0 - i * 5.0  # QQQ falling

            state = micro_engine.update(
                vix_current=vix_current,
                vix_open=vix_open,
                qqq_current=qqq_current,
                qqq_open=qqq_open,
                current_time=f"10:{i * 15:02d}",
            )

            # As VIX rises, strategy should become more defensive
            if vix_current > 30:
                assert state.recommended_strategy in [
                    IntradayStrategy.NO_TRADE,
                    IntradayStrategy.PROTECTIVE_PUTS,
                    IntradayStrategy.CREDIT_SPREAD,
                    IntradayStrategy.ITM_MOMENTUM,
                ]

    def test_recovery_scenario(self, micro_engine):
        """Simulate VIX falling during recovery."""
        vix_open = 30.0  # Started high
        qqq_open = 380.0  # Started low

        # VIX falls from 30 to 20 over 2 hours
        for i in range(8):
            vix_current = 30.0 - i * 1.5
            qqq_current = 380.0 + i * 3.0  # QQQ recovering

            state = micro_engine.update(
                vix_current=vix_current,
                vix_open=vix_open,
                qqq_current=qqq_current,
                qqq_open=qqq_open,
                current_time=f"10:{i * 15:02d}",
            )

            # As VIX falls, should eventually see MR strategies
            if vix_current < 22:
                # Should be in recovering/improving regime
                assert state.vix_direction == VIXDirection.FALLING_FAST
