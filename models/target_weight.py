"""
TargetWeight dataclass - The ONLY interface between Strategy Engines and Portfolio Router.

This is the "contract" that all strategy engines must use to communicate their
trading intentions. The Portfolio Router is the sole consumer of these signals
and the only component authorized to place orders.

Schema Version History:
- 1.0: Initial version (symbol, target_weight, source, urgency, reason)
"""

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Optional

from models.enums import Urgency


@dataclass
class TargetWeight:
    """
    Represents a strategy engine's desired position weight.

    This is the ONLY interface between Strategy Engines and Portfolio Router.
    Strategy engines emit TargetWeight objects; they NEVER place orders directly.

    Attributes:
        symbol: Ticker symbol (e.g., "QLD", "TQQQ", "TMF")
        target_weight: Desired portfolio weight as decimal (0.0 to 1.0)
                      0.0 means exit/close position
                      0.30 means 30% of portfolio
        source: Engine that generated this signal ("TREND", "MR", "HEDGE", "YIELD", "COLD_START")
        urgency: When to execute (IMMEDIATE for intraday, EOD for next open)
        reason: Human-readable explanation for logging

    Example:
        >>> from models.target_weight import TargetWeight
        >>> from models.enums import Urgency
        >>> signal = TargetWeight(
        ...     symbol="QLD",
        ...     target_weight=0.30,
        ...     source="TREND",
        ...     urgency=Urgency.EOD,
        ...     reason="BB Breakout detected"
        ... )
    """

    # Class constant for schema version (ClassVar excludes it from dataclass fields)
    # Increment when structure changes
    SCHEMA_VERSION: ClassVar[str] = "1.0"

    # Required fields
    symbol: str
    target_weight: float
    source: str
    urgency: Urgency
    reason: str

    # Optional fields
    timestamp: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate fields after initialization."""
        # Validate symbol
        if not self.symbol or not isinstance(self.symbol, str):
            raise ValueError(f"symbol must be a non-empty string, got: {self.symbol}")

        # Validate target_weight range
        if not 0.0 <= self.target_weight <= 1.0:
            raise ValueError(
                f"target_weight must be between 0.0 and 1.0, got: {self.target_weight}"
            )

        # Validate source
        valid_sources = {
            "TREND",
            "MR",
            "HEDGE",
            "YIELD",
            "COLD_START",
            "RISK",
            "ROUTER",
            "OPT",
            "OPT_INTRADAY",
        }
        if self.source not in valid_sources:
            raise ValueError(f"source must be one of {valid_sources}, got: {self.source}")

        # Validate urgency type
        if not isinstance(self.urgency, Urgency):
            raise ValueError(f"urgency must be Urgency enum, got: {type(self.urgency)}")

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize to dictionary for logging, persistence, or transmission.

        Returns:
            Dictionary with schema version and all fields.
        """
        return {
            "schema_version": self.SCHEMA_VERSION,
            "symbol": self.symbol,
            "target_weight": self.target_weight,
            "source": self.source,
            "urgency": self.urgency.value,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TargetWeight":
        """
        Deserialize from dictionary.

        Args:
            data: Dictionary with TargetWeight fields

        Returns:
            TargetWeight instance

        Raises:
            ValueError: If schema version is incompatible or fields are invalid
        """
        schema_version = data.get("schema_version", "1.0")
        if schema_version != cls.SCHEMA_VERSION:
            raise ValueError(
                f"Incompatible schema version: {schema_version}, expected: {cls.SCHEMA_VERSION}"
            )

        return cls(
            symbol=data["symbol"],
            target_weight=data["target_weight"],
            source=data["source"],
            urgency=Urgency(data["urgency"]),
            reason=data["reason"],
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {}),
        )

    def is_exit_signal(self) -> bool:
        """Check if this signal requests position exit."""
        return self.target_weight == 0.0

    def is_entry_signal(self) -> bool:
        """Check if this signal requests position entry."""
        return self.target_weight > 0.0

    def __str__(self) -> str:
        """Human-readable representation for logging."""
        action = "EXIT" if self.is_exit_signal() else f"{self.target_weight:.1%}"
        return f"TargetWeight({self.symbol} -> {action} | {self.source} | {self.urgency.value} | {self.reason})"
