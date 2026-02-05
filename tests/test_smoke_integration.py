"""
Smoke integration tests for Alpha NextGen.

These tests verify that components wire together correctly WITHOUT
requiring QuantConnect or real market data.

PURPOSE:
- Catch import errors
- Catch interface mismatches (TargetWeight structure)
- Verify basic wiring between components
- Run fast (<1 second total)

These are NOT strategy tests. They don't test if strategies are profitable.
They test if components can communicate.
"""

from unittest.mock import MagicMock

import pytest

from models.enums import ExposureGroup, RegimeLevel, Urgency
from models.target_weight import TargetWeight


class TestSmokeTargetWeightFlow:
    """
    Smoke test: Verify TargetWeight can flow from engine to router.

    This catches:
    - Import errors in the TargetWeight module
    - Interface changes that break the contract
    - Serialization issues
    """

    def test_smoke_create_target_weight(self):
        """
        SMOKE: TargetWeight can be instantiated.
        """
        tw = TargetWeight(
            symbol="QLD",
            target_weight=0.30,
            source="TREND",
            urgency=Urgency.EOD,
            reason="Smoke test signal",
        )

        assert tw.symbol == "QLD"
        assert tw.target_weight == 0.30
        assert tw.source == "TREND"
        assert tw.urgency == Urgency.EOD

    def test_smoke_target_weight_to_dict(self):
        """
        SMOKE: TargetWeight can be serialized.
        """
        tw = TargetWeight(
            symbol="TQQQ",
            target_weight=0.15,
            source="MR",
            urgency=Urgency.IMMEDIATE,
            reason="RSI oversold",
        )

        data = tw.to_dict()

        assert "schema_version" in data
        assert data["symbol"] == "TQQQ"
        assert data["urgency"] == "IMMEDIATE"

    def test_smoke_target_weight_from_dict(self):
        """
        SMOKE: TargetWeight can be deserialized.
        """
        data = {
            "schema_version": "1.0",
            "symbol": "TMF",
            "target_weight": 0.10,
            "source": "HEDGE",
            "urgency": "EOD",
            "reason": "Add hedge",
        }

        tw = TargetWeight.from_dict(data)

        assert tw.symbol == "TMF"
        assert tw.urgency == Urgency.EOD


class TestSmokeEnums:
    """
    Smoke test: Verify all enums are importable and have expected values.
    """

    def test_smoke_urgency_enum(self):
        """SMOKE: Urgency enum has required values."""
        assert Urgency.IMMEDIATE.value == "IMMEDIATE"
        assert Urgency.EOD.value == "EOD"

    def test_smoke_regime_level_enum(self):
        """SMOKE: RegimeLevel enum has required values."""
        assert RegimeLevel.RISK_ON.value == "RISK_ON"
        assert RegimeLevel.NEUTRAL.value == "NEUTRAL"
        assert RegimeLevel.CAUTIOUS.value == "CAUTIOUS"
        assert RegimeLevel.DEFENSIVE.value == "DEFENSIVE"

    def test_smoke_exposure_group_enum(self):
        """SMOKE: ExposureGroup enum has required values."""
        assert ExposureGroup.NASDAQ_BETA.value == "NASDAQ_BETA"
        assert ExposureGroup.SPY_BETA.value == "SPY_BETA"
        assert ExposureGroup.RATES.value == "RATES"


class TestSmokeToyEngine:
    """
    Smoke test: Toy engine producing TargetWeight.

    This simulates what a real strategy engine does
    without requiring the actual engine implementation.
    """

    @pytest.fixture
    def toy_trend_engine(self, mock_algorithm):
        """Toy trend engine that emits fixed signals."""

        class ToyTrendEngine:
            def __init__(self, algorithm):
                self.algorithm = algorithm

            def generate_signals(self):
                """Emit a fixed signal for testing."""
                return [
                    TargetWeight(
                        symbol="QLD",
                        target_weight=0.30,
                        source="TREND",
                        urgency=Urgency.EOD,
                        reason="Toy BB breakout signal",
                    )
                ]

        return ToyTrendEngine(mock_algorithm)

    @pytest.fixture
    def toy_mr_engine(self, mock_algorithm):
        """Toy mean reversion engine that emits fixed signals."""

        class ToyMREngine:
            def __init__(self, algorithm):
                self.algorithm = algorithm

            def generate_signals(self):
                """Emit a fixed signal for testing."""
                return [
                    TargetWeight(
                        symbol="TQQQ",
                        target_weight=0.15,
                        source="MR",
                        urgency=Urgency.IMMEDIATE,
                        reason="Toy RSI oversold signal",
                    )
                ]

        return ToyMREngine(mock_algorithm)

    def test_smoke_engine_generates_signals(self, toy_trend_engine):
        """SMOKE: Engine can generate TargetWeight signals."""
        signals = toy_trend_engine.generate_signals()

        assert len(signals) == 1
        assert isinstance(signals[0], TargetWeight)
        assert signals[0].symbol == "QLD"

    def test_smoke_multiple_engines_coexist(self, toy_trend_engine, toy_mr_engine):
        """SMOKE: Multiple engines can generate signals independently."""
        trend_signals = toy_trend_engine.generate_signals()
        mr_signals = toy_mr_engine.generate_signals()

        assert len(trend_signals) == 1
        assert len(mr_signals) == 1
        assert trend_signals[0].source == "TREND"
        assert mr_signals[0].source == "MR"

    def test_smoke_signals_can_be_aggregated(self, toy_trend_engine, toy_mr_engine):
        """SMOKE: Signals from multiple engines can be combined."""
        all_signals = []
        all_signals.extend(toy_trend_engine.generate_signals())
        all_signals.extend(toy_mr_engine.generate_signals())

        assert len(all_signals) == 2
        symbols = {s.symbol for s in all_signals}
        assert symbols == {"QLD", "TQQQ"}


class TestSmokeToyRouter:
    """
    Smoke test: Toy router receiving TargetWeight signals.

    This simulates the Portfolio Router's core function:
    receive signals and determine orders to place.
    """

    @pytest.fixture
    def toy_router(self, mock_algorithm):
        """Toy router that receives signals and tracks them."""

        class ToyRouter:
            def __init__(self, algorithm):
                self.algorithm = algorithm
                self.received_signals = []
                self.orders_to_place = []

            def receive_signals(self, signals):
                """Receive signals from engines."""
                self.received_signals.extend(signals)

            def process_signals(self):
                """Process signals and determine orders."""
                for signal in self.received_signals:
                    if signal.is_entry_signal():
                        self.orders_to_place.append(
                            {
                                "symbol": signal.symbol,
                                "action": "BUY",
                                "weight": signal.target_weight,
                                "urgency": signal.urgency.value,
                            }
                        )
                    elif signal.is_exit_signal():
                        self.orders_to_place.append(
                            {
                                "symbol": signal.symbol,
                                "action": "SELL",
                                "weight": 0.0,
                                "urgency": signal.urgency.value,
                            }
                        )

        return ToyRouter(mock_algorithm)

    def test_smoke_router_receives_signals(self, toy_router):
        """SMOKE: Router can receive TargetWeight signals."""
        signal = TargetWeight(
            symbol="QLD",
            target_weight=0.30,
            source="TREND",
            urgency=Urgency.EOD,
            reason="Test",
        )

        toy_router.receive_signals([signal])

        assert len(toy_router.received_signals) == 1
        assert toy_router.received_signals[0].symbol == "QLD"

    def test_smoke_router_processes_entry_signal(self, toy_router):
        """SMOKE: Router correctly processes entry signal."""
        signal = TargetWeight(
            symbol="QLD",
            target_weight=0.30,
            source="TREND",
            urgency=Urgency.EOD,
            reason="Entry test",
        )

        toy_router.receive_signals([signal])
        toy_router.process_signals()

        assert len(toy_router.orders_to_place) == 1
        assert toy_router.orders_to_place[0]["action"] == "BUY"
        assert toy_router.orders_to_place[0]["weight"] == 0.30

    def test_smoke_router_processes_exit_signal(self, toy_router):
        """SMOKE: Router correctly processes exit signal."""
        signal = TargetWeight(
            symbol="TQQQ",
            target_weight=0.0,
            source="MR",
            urgency=Urgency.IMMEDIATE,
            reason="Exit test",
        )

        toy_router.receive_signals([signal])
        toy_router.process_signals()

        assert len(toy_router.orders_to_place) == 1
        assert toy_router.orders_to_place[0]["action"] == "SELL"


class TestSmokeEndToEnd:
    """
    Smoke test: Complete flow from engine to router.

    This is the minimal integration test that proves
    the TargetWeight contract works end-to-end.
    """

    def test_smoke_full_flow(self, mock_algorithm):
        """
        SMOKE: Complete flow - Engine -> Signal -> Router -> Order intent.

        This test proves:
        1. Engine can create TargetWeight
        2. Signal can be serialized/deserialized
        3. Router can receive and process signal
        4. No import or interface errors
        """
        # Step 1: Engine creates signal
        signal = TargetWeight(
            symbol="QLD",
            target_weight=0.30,
            source="TREND",
            urgency=Urgency.EOD,
            reason="Full flow test",
        )

        # Step 2: Signal is serialized (simulating transport)
        serialized = signal.to_dict()

        # Step 3: Signal is deserialized (simulating receipt)
        restored = TargetWeight.from_dict(serialized)

        # Step 4: Router processes signal
        is_entry = restored.is_entry_signal()
        is_eod = restored.urgency == Urgency.EOD

        # Assertions
        assert restored.symbol == "QLD"
        assert restored.target_weight == 0.30
        assert is_entry is True
        assert is_eod is True

        # Step 5: Verify logging works (doesn't throw)
        mock_algorithm.Log(str(restored))
        mock_algorithm.Log.assert_called_once()
