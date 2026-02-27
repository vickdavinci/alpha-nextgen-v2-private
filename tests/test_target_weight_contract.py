"""
Golden payload tests for TargetWeight contract.

These tests ensure the TargetWeight schema remains stable.
If these tests fail after a change, you've broken the contract
and must update all engines before merging.

Contract Rules:
1. SCHEMA_VERSION must match golden payloads
2. to_dict() must produce expected structure
3. from_dict() must round-trip correctly
4. All required fields must be present
"""

import pytest

from models.enums import Urgency
from models.target_weight import TargetWeight


class TestTargetWeightContract:
    """
    Golden payload tests - ensure TargetWeight structure is stable.

    IMPORTANT: If you need to change these tests, you are breaking the contract.
    All strategy engines depend on this structure.
    """

    # Golden payloads - these represent the "expected" format
    # Each payload represents a real-world signal from a strategy engine
    # V2.3.2: Updated to schema 1.1 with requested_quantity field
    GOLDEN_PAYLOADS = [
        {
            "schema_version": "1.1",
            "symbol": "QLD",
            "target_weight": 0.30,
            "source": "TREND",
            "urgency": "EOD",
            "reason": "BB Breakout detected",
            "timestamp": None,
            "metadata": {},
            "requested_quantity": None,
        },
        {
            "schema_version": "1.1",
            "symbol": "TQQQ",
            "target_weight": 0.15,
            "source": "MR",
            "urgency": "IMMEDIATE",
            "reason": "RSI oversold bounce",
            "timestamp": None,
            "metadata": {},
            "requested_quantity": None,
        },
        {
            "schema_version": "1.1",
            "symbol": "TMF",
            "target_weight": 0.10,
            "source": "HEDGE",
            "urgency": "EOD",
            "reason": "Regime CAUTIOUS, adding hedge",
            "timestamp": None,
            "metadata": {},
            "requested_quantity": None,
        },
        {
            "schema_version": "1.1",
            "symbol": "SHV",
            "target_weight": 0.40,
            "source": "YIELD",
            "urgency": "EOD",
            "reason": "Park idle cash",
            "timestamp": None,
            "metadata": {},
            "requested_quantity": None,
        },
        {
            "schema_version": "1.1",
            "symbol": "TQQQ",
            "target_weight": 0.0,
            "source": "MR",
            "urgency": "IMMEDIATE",
            "reason": "TIME_EXIT_15:45",
            "timestamp": None,
            "metadata": {},
            "requested_quantity": None,
        },
    ]

    def test_schema_version_matches(self):
        """Ensure current schema version matches golden payloads."""
        # V2.3.2: Updated to 1.1 for requested_quantity field
        assert TargetWeight.SCHEMA_VERSION == "1.1", (
            f"Schema version mismatch! Expected 1.1, got {TargetWeight.SCHEMA_VERSION}. "
            "If intentional, update all GOLDEN_PAYLOADS and notify all engine maintainers."
        )

    @pytest.mark.parametrize("payload", GOLDEN_PAYLOADS)
    def test_golden_payload_structure(self, payload):
        """Ensure TargetWeight can produce golden payload format."""
        # Create TargetWeight from golden payload data
        tw = TargetWeight(
            symbol=payload["symbol"],
            target_weight=payload["target_weight"],
            source=payload["source"],
            urgency=Urgency(payload["urgency"]),
            reason=payload["reason"],
        )

        # Serialize and compare
        result = tw.to_dict()

        assert result["schema_version"] == payload["schema_version"]
        assert result["symbol"] == payload["symbol"]
        assert result["target_weight"] == payload["target_weight"]
        assert result["source"] == payload["source"]
        assert result["urgency"] == payload["urgency"]
        assert result["reason"] == payload["reason"]
        assert result["requested_quantity"] == payload["requested_quantity"]

    @pytest.mark.parametrize("payload", GOLDEN_PAYLOADS)
    def test_round_trip_serialization(self, payload):
        """Ensure to_dict() -> from_dict() preserves all data."""
        # Create original
        original = TargetWeight(
            symbol=payload["symbol"],
            target_weight=payload["target_weight"],
            source=payload["source"],
            urgency=Urgency(payload["urgency"]),
            reason=payload["reason"],
        )

        # Round-trip
        serialized = original.to_dict()
        restored = TargetWeight.from_dict(serialized)

        # Compare
        assert restored.symbol == original.symbol
        assert restored.target_weight == original.target_weight
        assert restored.source == original.source
        assert restored.urgency == original.urgency
        assert restored.reason == original.reason

    def test_required_fields_present(self):
        """Ensure all required fields are present in to_dict()."""
        tw = TargetWeight(
            symbol="QLD",
            target_weight=0.30,
            source="TREND",
            urgency=Urgency.EOD,
            reason="Test",
        )

        result = tw.to_dict()

        required_fields = [
            "schema_version",
            "symbol",
            "target_weight",
            "source",
            "urgency",
            "reason",
            "requested_quantity",  # V2.3.2: Added for options sizing
        ]
        for field in required_fields:
            assert field in result, f"Missing required field: {field}"

    def test_exit_signal_detection(self):
        """Ensure exit signals are correctly identified."""
        exit_signal = TargetWeight(
            symbol="TQQQ",
            target_weight=0.0,
            source="MR",
            urgency=Urgency.IMMEDIATE,
            reason="TIME_EXIT_15:45",
        )

        assert exit_signal.is_exit_signal() is True
        assert exit_signal.is_entry_signal() is False

    def test_entry_signal_detection(self):
        """Ensure entry signals are correctly identified."""
        entry_signal = TargetWeight(
            symbol="QLD",
            target_weight=0.30,
            source="TREND",
            urgency=Urgency.EOD,
            reason="BB Breakout",
        )

        assert entry_signal.is_entry_signal() is True
        assert entry_signal.is_exit_signal() is False


class TestTargetWeightValidation:
    """Test TargetWeight field validation."""

    def test_invalid_symbol_empty(self):
        """Reject empty symbol."""
        with pytest.raises(ValueError, match="symbol must be a non-empty string"):
            TargetWeight(
                symbol="",
                target_weight=0.30,
                source="TREND",
                urgency=Urgency.EOD,
                reason="Test",
            )

    def test_invalid_target_weight_negative(self):
        """Reject negative target_weight."""
        with pytest.raises(ValueError, match="target_weight must be between"):
            TargetWeight(
                symbol="QLD",
                target_weight=-0.1,
                source="TREND",
                urgency=Urgency.EOD,
                reason="Test",
            )

    def test_invalid_target_weight_over_one(self):
        """Reject target_weight > 1.0."""
        with pytest.raises(ValueError, match="target_weight must be between"):
            TargetWeight(
                symbol="QLD",
                target_weight=1.5,
                source="TREND",
                urgency=Urgency.EOD,
                reason="Test",
            )

    def test_invalid_source(self):
        """Reject unknown source."""
        with pytest.raises(ValueError, match="source must be one of"):
            TargetWeight(
                symbol="QLD",
                target_weight=0.30,
                source="UNKNOWN",
                urgency=Urgency.EOD,
                reason="Test",
            )

    def test_valid_sources(self):
        """Accept all valid sources."""
        # V2.3.2: Added OPT and OPT_INTRADAY sources
        valid_sources = [
            "TREND",
            "MR",
            "HEDGE",
            "YIELD",
            "COLD_START",
            "RISK",
            "ROUTER",
            "OPT",
            "OPT_INTRADAY",
        ]

        for source in valid_sources:
            tw = TargetWeight(
                symbol="QLD",
                target_weight=0.30,
                source=source,
                urgency=Urgency.EOD,
                reason="Test",
            )
            assert tw.source == source

    def test_schema_version_mismatch_on_load(self):
        """Reject loading incompatible schema version."""
        bad_payload = {
            "schema_version": "2.0",  # Future version
            "symbol": "QLD",
            "target_weight": 0.30,
            "source": "TREND",
            "urgency": "EOD",
            "reason": "Test",
        }

        with pytest.raises(ValueError, match="Incompatible schema version"):
            TargetWeight.from_dict(bad_payload)

    def test_opt_intraday_metadata_promotes_legacy_strategy(self):
        """OPT_INTRADAY should normalize legacy intraday_strategy metadata."""
        tw = TargetWeight(
            symbol="QQQ 260130C00500000",
            target_weight=0.10,
            source="OPT_INTRADAY",
            urgency=Urgency.IMMEDIATE,
            reason="ENTRY",
            requested_quantity=2,
            metadata={"intraday_strategy": "micro_debit_fade"},
        )
        assert tw.metadata.get("options_strategy") == "MICRO_DEBIT_FADE"
        assert tw.metadata.get("options_lane") == "MICRO"

    def test_opt_intraday_metadata_infers_itm_lane(self):
        """ITM strategy metadata should infer ITM lane automatically."""
        tw = TargetWeight(
            symbol="QQQ 260130C00500000",
            target_weight=0.10,
            source="OPT_INTRADAY",
            urgency=Urgency.IMMEDIATE,
            reason="ENTRY",
            requested_quantity=1,
            metadata={"options_strategy": "ITM_MOMENTUM"},
        )
        assert tw.metadata.get("options_strategy") == "ITM_MOMENTUM"
        assert tw.metadata.get("options_lane") == "ITM"
