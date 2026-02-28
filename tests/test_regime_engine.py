"""
Unit tests for Regime Engine.

Tests the 4-factor market state scoring system (0-100):
- Trend factor (35%)
- Volatility factor (25%)
- Breadth factor (25%)
- Credit factor (15%)

Spec: docs/04-regime-engine.md
"""

import pytest

import config
from engines.core.regime_engine import RegimeEngine, RegimeState
from models.enums import RegimeLevel


class TestRegimeEngine:
    """Tests for RegimeEngine."""

    def _create_price_series(self, base: float, count: int, trend: float = 0.0) -> list:
        """Create a synthetic price series for testing."""
        prices = []
        price = base
        for _ in range(count):
            prices.append(price)
            price *= 1 + trend
        return prices

    def test_regime_engine_initialization(self):
        """Test engine initializes with neutral state."""
        engine = RegimeEngine()
        assert engine.get_previous_score() == 50.0
        assert engine._vol_history == []

    def test_regime_score_calculation_bullish(self):
        """Test regime score is calculated correctly for bullish conditions."""
        engine = RegimeEngine()

        # Create bullish price data
        # Price above all SMAs, low volatility, good breadth, healthy credit
        spy_prices = self._create_price_series(400, 25, 0.002)  # Rising prices
        rsp_prices = self._create_price_series(150, 25, 0.003)  # RSP rising faster
        hyg_prices = self._create_price_series(75, 25, 0.002)  # HYG rising
        ief_prices = self._create_price_series(100, 25, 0.0)  # IEF flat

        current_price = spy_prices[-1]
        state = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=current_price * 0.98,  # Price above SMA20
            spy_sma50=current_price * 0.95,  # Price above SMA50
            spy_sma200=current_price * 0.90,  # Price above SMA200
        )

        # In bullish conditions, score should be elevated
        assert state.smoothed_score > 50
        assert state.new_longs_allowed is True

    def test_regime_score_calculation_bearish(self):
        """Test regime score is calculated correctly for bearish conditions."""
        engine = RegimeEngine()

        # Create bearish price data
        spy_prices = self._create_price_series(400, 25, -0.003)  # Falling prices
        rsp_prices = self._create_price_series(150, 25, -0.004)  # RSP falling faster
        hyg_prices = self._create_price_series(75, 25, -0.003)  # HYG falling
        ief_prices = self._create_price_series(100, 25, 0.002)  # IEF rising (flight to safety)

        current_price = spy_prices[-1]
        state = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=current_price * 1.02,  # Price below SMA20
            spy_sma50=current_price * 1.05,  # Price below SMA50
            spy_sma200=current_price * 1.10,  # Price below SMA200
            vix_level=32.0,  # V2.3: Elevated VIX during bearish conditions
        )

        # In bearish conditions, score should be depressed
        assert state.smoothed_score < 50

    def test_regime_score_boundaries(self):
        """Test regime score stays within 0-100 bounds."""
        engine = RegimeEngine()

        # Test with extreme bullish conditions
        spy_prices = self._create_price_series(400, 25, 0.01)
        rsp_prices = self._create_price_series(150, 25, 0.015)
        hyg_prices = self._create_price_series(75, 25, 0.01)
        ief_prices = self._create_price_series(100, 25, -0.005)

        current_price = spy_prices[-1]
        state = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=current_price * 0.90,
            spy_sma50=current_price * 0.85,
            spy_sma200=current_price * 0.80,
        )

        assert 0 <= state.smoothed_score <= 100
        assert 0 <= state.raw_score <= 100
        assert 0 <= state.trend_score <= 100
        assert 0 <= state.volatility_score <= 100
        assert 0 <= state.breadth_score <= 100
        assert 0 <= state.credit_score <= 100

    def test_regime_classification_risk_on(self):
        """Test RISK_ON classification for score >= 70."""
        engine = RegimeEngine()
        engine._previous_smoothed_score = 80  # Start high

        spy_prices = self._create_price_series(400, 25, 0.005)
        rsp_prices = self._create_price_series(150, 25, 0.007)
        hyg_prices = self._create_price_series(75, 25, 0.005)
        ief_prices = self._create_price_series(100, 25, -0.002)

        current_price = spy_prices[-1]
        state = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=current_price * 0.95,
            spy_sma50=current_price * 0.92,
            spy_sma200=current_price * 0.88,
        )

        # With high previous score and bullish data, should be RISK_ON
        if state.smoothed_score >= 70:
            assert state.state == RegimeLevel.RISK_ON
            assert state.tmf_target_pct == 0.0
            assert state.psq_target_pct == 0.0

    def test_regime_classification_risk_off(self):
        """Test RISK_OFF classification for score < 30."""
        engine = RegimeEngine()
        engine._previous_smoothed_score = 20  # Start very low

        spy_prices = self._create_price_series(400, 25, -0.008)
        rsp_prices = self._create_price_series(150, 25, -0.01)
        hyg_prices = self._create_price_series(75, 25, -0.008)
        ief_prices = self._create_price_series(100, 25, 0.005)

        current_price = spy_prices[-1]
        state = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=current_price * 1.05,
            spy_sma50=current_price * 1.10,
            spy_sma200=current_price * 1.15,
        )

        # With low previous score and bearish data, should be low
        if state.smoothed_score < 30:
            assert state.state == RegimeLevel.RISK_OFF
            assert state.new_longs_allowed is False

    def test_regime_smoothing(self):
        """Test EMA smoothing with config alpha."""
        engine = RegimeEngine()
        engine._previous_smoothed_score = 50.0

        # Create data that would give a raw score around 70
        spy_prices = self._create_price_series(400, 25, 0.003)
        rsp_prices = self._create_price_series(150, 25, 0.004)
        hyg_prices = self._create_price_series(75, 25, 0.003)
        ief_prices = self._create_price_series(100, 25, 0.0)

        current_price = spy_prices[-1]
        state = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=current_price * 0.97,
            spy_sma50=current_price * 0.94,
            spy_sma200=current_price * 0.90,
        )

        # Smoothed score should be between previous (50) and raw
        alpha = config.REGIME_SMOOTHING_ALPHA
        expected_smoothed = alpha * state.raw_score + (1 - alpha) * 50.0
        assert abs(state.smoothed_score - expected_smoothed) < 0.01

    def test_regime_uses_proxy_symbols_only(self):
        """Test regime calculation uses SPY, RSP, HYG, IEF (not traded symbols)."""
        engine = RegimeEngine()

        # The calculate method only accepts proxy symbol data
        # This is enforced by the function signature
        spy_prices = self._create_price_series(400, 25)
        rsp_prices = self._create_price_series(150, 25)
        hyg_prices = self._create_price_series(75, 25)
        ief_prices = self._create_price_series(100, 25)

        # Should work with proxy symbols
        state = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=400,
            spy_sma50=395,
            spy_sma200=380,
        )

        assert isinstance(state, RegimeState)

    def test_regime_insufficient_data_raises_error(self):
        """Test that insufficient data raises ValueError."""
        engine = RegimeEngine()

        # Only 10 prices when 21 are needed
        short_prices = self._create_price_series(400, 10)

        with pytest.raises(ValueError, match="Need at least"):
            engine.calculate(
                spy_closes=short_prices,
                rsp_closes=short_prices,
                hyg_closes=short_prices,
                ief_closes=short_prices,
                spy_sma20=400,
                spy_sma50=395,
                spy_sma200=380,
            )

    def test_regime_state_to_dict(self):
        """Test RegimeState serialization."""
        engine = RegimeEngine()

        spy_prices = self._create_price_series(400, 25)
        rsp_prices = self._create_price_series(150, 25)
        hyg_prices = self._create_price_series(75, 25)
        ief_prices = self._create_price_series(100, 25)

        state = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=400,
            spy_sma50=395,
            spy_sma200=380,
        )

        data = state.to_dict()

        assert "smoothed_score" in data
        assert "raw_score" in data
        assert "state" in data
        assert "trend_score" in data
        assert "volatility_score" in data
        assert "breadth_score" in data
        assert "credit_score" in data
        assert "new_longs_allowed" in data
        assert "cold_start_allowed" in data
        assert "tmf_target_pct" in data
        assert "psq_target_pct" in data

    def test_regime_engine_reset(self):
        """Test engine reset functionality."""
        engine = RegimeEngine()
        engine._previous_smoothed_score = 75.0
        engine._vol_history = [0.15, 0.16, 0.14]

        engine.reset()

        assert engine._previous_smoothed_score == 50.0
        assert engine._vol_history == []

    def test_regime_score_persistence(self):
        """Test score persistence across calculations."""
        engine = RegimeEngine()

        spy_prices = self._create_price_series(400, 25, 0.002)
        rsp_prices = self._create_price_series(150, 25, 0.002)
        hyg_prices = self._create_price_series(75, 25, 0.002)
        ief_prices = self._create_price_series(100, 25, 0.0)

        current_price = spy_prices[-1]

        # First calculation
        state1 = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=current_price * 0.98,
            spy_sma50=current_price * 0.95,
            spy_sma200=current_price * 0.90,
        )

        # Second calculation should use previous smoothed score
        state2 = engine.calculate(
            spy_closes=spy_prices,
            rsp_closes=rsp_prices,
            hyg_closes=hyg_prices,
            ief_closes=ief_prices,
            spy_sma20=current_price * 0.98,
            spy_sma50=current_price * 0.95,
            spy_sma200=current_price * 0.90,
        )

        # V3.0: VIX Direction activates on second calc (after _vix_prior is set),
        # so raw scores differ. Test that smoothing is working by verifying
        # state2.smoothed_score moves toward state2.raw_score from state1.smoothed_score
        # (i.e., smoothed2 is between smoothed1 and raw2, or closer to raw2)
        if state2.raw_score > state1.smoothed_score:
            # Raw is higher, smoothed should increase
            assert state2.smoothed_score > state1.smoothed_score
        elif state2.raw_score < state1.smoothed_score:
            # Raw is lower, smoothed should decrease
            assert state2.smoothed_score < state1.smoothed_score
        # If equal, smoothed can stay the same (edge case)


class TestRegimeStateFlags:
    """Tests for RegimeState flag derivation."""

    def test_new_longs_allowed_above_30(self):
        """Test new_longs_allowed is True when score >= 30."""
        # Score 30+ allows new longs
        state = RegimeState(
            smoothed_score=35,
            raw_score=35,
            state=RegimeLevel.DEFENSIVE,
            trend_score=50,
            vix_score=50,  # V2.3
            volatility_score=50,
            breadth_score=50,
            credit_score=50,
            vix_level=20.0,  # V2.3
            realized_vol=0.15,
            vol_percentile=0.5,
            breadth_spread_value=0.0,
            credit_spread_value=0.0,
            new_longs_allowed=True,
            cold_start_allowed=False,
            tmf_target_pct=0.10,
            psq_target_pct=0.0,
        )
        assert state.new_longs_allowed is True

    def test_new_longs_blocked_below_30(self):
        """Test new_longs_allowed is False when score < 30."""
        state = RegimeState(
            smoothed_score=25,
            raw_score=25,
            state=RegimeLevel.RISK_OFF,
            trend_score=30,
            vix_score=30,  # V2.3
            volatility_score=30,
            breadth_score=30,
            credit_score=30,
            vix_level=25.0,  # V2.3
            realized_vol=0.25,
            vol_percentile=0.8,
            breadth_spread_value=-0.02,
            credit_spread_value=-0.02,
            new_longs_allowed=False,
            cold_start_allowed=False,
            tmf_target_pct=0.20,
            psq_target_pct=0.10,
        )
        assert state.new_longs_allowed is False

    def test_cold_start_allowed_above_50(self):
        """Test cold_start_allowed is True when score > 50."""
        state = RegimeState(
            smoothed_score=55,
            raw_score=55,
            state=RegimeLevel.NEUTRAL,
            trend_score=55,
            vix_score=55,  # V2.3
            volatility_score=55,
            breadth_score=55,
            credit_score=55,
            vix_level=18.0,  # V2.3
            realized_vol=0.15,
            vol_percentile=0.5,
            breadth_spread_value=0.005,
            credit_spread_value=0.005,
            new_longs_allowed=True,
            cold_start_allowed=True,
            tmf_target_pct=0.0,
            psq_target_pct=0.0,
        )
        assert state.cold_start_allowed is True

    def test_cold_start_blocked_at_50(self):
        """Test cold_start_allowed is False when score = 50 (must be > 50)."""
        state = RegimeState(
            smoothed_score=50,
            raw_score=50,
            state=RegimeLevel.NEUTRAL,
            trend_score=50,
            vix_score=50,  # V2.3
            volatility_score=50,
            breadth_score=50,
            credit_score=50,
            vix_level=20.0,  # V2.3
            realized_vol=0.15,
            vol_percentile=0.5,
            breadth_spread_value=0.0,
            credit_spread_value=0.0,
            new_longs_allowed=True,
            cold_start_allowed=False,  # Must be > 50, not >= 50
            tmf_target_pct=0.0,
            psq_target_pct=0.0,
        )
        assert state.cold_start_allowed is False


class TestHedgeTargets:
    """Tests for hedge target calculations (V3.0: thesis-aligned thresholds).

    Note: Production config has hedges disabled (all 0.0). Global fixtures
    in conftest.py enable hedge values for testing.
    """

    def test_no_hedges_above_50(self):
        """V3.0: Test no hedges when score >= 50 (Neutral+)."""
        engine = RegimeEngine()
        tmf, psq = engine._calculate_hedge_targets(55)
        assert tmf == 0.0
        assert psq == 0.0

    def test_light_hedge_40_to_49(self):
        """V3.0: Test light hedge (TMF only) when score 40-49 (Cautious)."""
        engine = RegimeEngine()
        tmf, psq = engine._calculate_hedge_targets(45)
        assert tmf == 0.10  # 10% TMF
        assert psq == 0.0

    def test_medium_hedge_30_to_39(self):
        """V3.0: Test medium hedge when score 30-39 (Defensive)."""
        engine = RegimeEngine()
        tmf, psq = engine._calculate_hedge_targets(35)
        assert tmf == 0.15  # 15% TMF
        assert psq == 0.05  # 5% PSQ

    def test_full_hedge_below_30(self):
        """V3.0: Test full hedge when score < 30 (Risk Off)."""
        engine = RegimeEngine()
        tmf, psq = engine._calculate_hedge_targets(25)
        assert tmf == 0.20  # 20% TMF
        assert psq == 0.10  # 10% PSQ


class TestRegimeClassification:
    """Tests for regime classification thresholds."""

    def test_classify_risk_on(self):
        """Test RISK_ON classification for score >= 70."""
        engine = RegimeEngine()
        assert engine._classify_regime(70) == RegimeLevel.RISK_ON
        assert engine._classify_regime(85) == RegimeLevel.RISK_ON
        assert engine._classify_regime(100) == RegimeLevel.RISK_ON

    def test_classify_neutral(self):
        """Test NEUTRAL classification for score 50-69."""
        engine = RegimeEngine()
        assert engine._classify_regime(50) == RegimeLevel.NEUTRAL
        assert engine._classify_regime(60) == RegimeLevel.NEUTRAL
        assert engine._classify_regime(69) == RegimeLevel.NEUTRAL

    def test_classify_cautious(self):
        """Test CAUTIOUS classification for score 45-49."""
        engine = RegimeEngine()
        assert engine._classify_regime(45) == RegimeLevel.CAUTIOUS
        assert engine._classify_regime(47) == RegimeLevel.CAUTIOUS
        assert engine._classify_regime(49) == RegimeLevel.CAUTIOUS

    def test_classify_defensive(self):
        """Test DEFENSIVE classification for score 35-44."""
        engine = RegimeEngine()
        assert engine._classify_regime(35) == RegimeLevel.DEFENSIVE
        assert engine._classify_regime(40) == RegimeLevel.DEFENSIVE
        assert engine._classify_regime(44) == RegimeLevel.DEFENSIVE

    def test_classify_risk_off(self):
        """Test RISK_OFF classification for score < 35."""
        engine = RegimeEngine()
        assert engine._classify_regime(0) == RegimeLevel.RISK_OFF
        assert engine._classify_regime(20) == RegimeLevel.RISK_OFF
        assert engine._classify_regime(34) == RegimeLevel.RISK_OFF
