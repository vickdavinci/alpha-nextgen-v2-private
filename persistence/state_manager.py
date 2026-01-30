"""
State Manager - Orchestrates persistence of all system state via ObjectStore.

Ensures the algorithm survives restarts without losing critical information:
- Capital phase and lockbox
- Cold start progress
- Position tracking (entry prices, stops, highest highs)
- Regime smoothing
- Risk state (kill dates, weekly breaker)

Key Responsibilities:
1. Save state at EOD and on critical events
2. Load state on algorithm startup
3. Validate loaded state for consistency
4. Reconcile with actual broker positions
5. Handle missing/corrupted state gracefully

Spec: docs/15-state-persistence.md
"""

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

import config


# ObjectStore key constants
class StateKeys:
    """ObjectStore key names for state persistence."""

    PREFIX = "ALPHA_NEXTGEN_"
    CAPITAL = "ALPHA_NEXTGEN_CAPITAL"
    COLDSTART = "ALPHA_NEXTGEN_COLDSTART"
    POSITIONS = "ALPHA_NEXTGEN_POSITIONS"
    REGIME = "ALPHA_NEXTGEN_REGIME"
    RISK = "ALPHA_NEXTGEN_RISK"
    WEEKLY = "ALPHA_NEXTGEN_WEEKLY"
    EXECUTION = "ALPHA_NEXTGEN_EXECUTION"
    ROUTER = "ALPHA_NEXTGEN_ROUTER"

    ALL_KEYS = [CAPITAL, COLDSTART, POSITIONS, REGIME, RISK, WEEKLY, EXECUTION, ROUTER]


# Current schema version for migration support
SCHEMA_VERSION = "1.0"


@dataclass
class PositionState:
    """
    Persisted state for a single position.

    Attributes:
        symbol: Ticker symbol.
        entry_price: Fill price at entry.
        entry_date: Date of entry (YYYY-MM-DD).
        highest_high: Maximum price since entry.
        current_stop: Current stop level.
        strategy_tag: Source strategy (TREND, MR, COLD_START, etc.).
        quantity: Number of shares.
    """

    symbol: str
    entry_price: float
    entry_date: str
    highest_high: float
    current_stop: Optional[float]
    strategy_tag: str
    quantity: int

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "entry_date": self.entry_date,
            "highest_high": self.highest_high,
            "current_stop": self.current_stop,
            "strategy_tag": self.strategy_tag,
            "quantity": self.quantity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PositionState":
        """Deserialize from dictionary."""
        return cls(
            symbol=data["symbol"],
            entry_price=data["entry_price"],
            entry_date=data["entry_date"],
            highest_high=data["highest_high"],
            current_stop=data.get("current_stop"),
            strategy_tag=data["strategy_tag"],
            quantity=data["quantity"],
        )


@dataclass
class ReconciliationResult:
    """Result of position reconciliation."""

    matched: List[str] = field(default_factory=list)
    quantity_mismatches: List[str] = field(default_factory=list)
    closed_positions: List[str] = field(default_factory=list)
    unexpected_positions: List[str] = field(default_factory=list)

    def has_issues(self) -> bool:
        """Check if reconciliation found any issues."""
        return bool(self.quantity_mismatches or self.closed_positions or self.unexpected_positions)


class StateManager:
    """
    Orchestrates persistence of all system state.

    Coordinates saving and loading state for all engines and components
    via QuantConnect's ObjectStore.

    Key Features:
    - Separate keys for each state category
    - Atomic updates per category
    - Schema versioning for migration
    - Validation on load
    - Graceful fallback to defaults
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """
        Initialize StateManager.

        Args:
            algorithm: QuantConnect algorithm instance (None for testing).
        """
        self.algorithm = algorithm
        self._positions: Dict[str, PositionState] = {}
        self._save_count = 0
        self._load_count = 0

        # Mock ObjectStore for testing
        self._mock_store: Dict[str, str] = {}

    def log(self, message: str) -> None:
        """Log via algorithm or skip for testing."""
        if self.algorithm:
            self.algorithm.Log(message)  # type: ignore[attr-defined]

    # =========================================================================
    # ObjectStore Abstraction
    # =========================================================================

    def _object_store_contains(self, key: str) -> bool:
        """Check if key exists in ObjectStore."""
        if self.algorithm:
            return self.algorithm.ObjectStore.ContainsKey(key)  # type: ignore[attr-defined]
        return key in self._mock_store

    def _object_store_read(self, key: str) -> str:
        """Read value from ObjectStore."""
        if self.algorithm:
            return self.algorithm.ObjectStore.Read(key)  # type: ignore[attr-defined]
        return self._mock_store.get(key, "")

    def _object_store_save(self, key: str, value: str) -> None:
        """Save value to ObjectStore."""
        if self.algorithm:
            self.algorithm.ObjectStore.Save(key, value)  # type: ignore[attr-defined]
        else:
            self._mock_store[key] = value

    def _object_store_delete(self, key: str) -> None:
        """Delete key from ObjectStore."""
        if self.algorithm:
            if self.algorithm.ObjectStore.ContainsKey(key):  # type: ignore[attr-defined]
                self.algorithm.ObjectStore.Delete(key)  # type: ignore[attr-defined]
        else:
            self._mock_store.pop(key, None)

    # =========================================================================
    # Generic Save/Load
    # =========================================================================

    def _save_state(self, key: str, data: Dict[str, Any]) -> bool:
        """
        Save state dict to ObjectStore.

        Args:
            key: ObjectStore key.
            data: State dictionary to save.

        Returns:
            True if save succeeded.
        """
        try:
            # Add version and metadata
            wrapped = {
                "version": SCHEMA_VERSION,
                "data": data,
            }
            self._object_store_save(key, json.dumps(wrapped))
            return True
        except Exception as e:
            self.log(f"STATE: SAVE_ERROR | {key} | {e}")
            return False

    def _load_state(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Load state dict from ObjectStore.

        Args:
            key: ObjectStore key.

        Returns:
            State dictionary, or None if not found/error.
        """
        if not self._object_store_contains(key):
            self.log(f"STATE: NOT_FOUND | {key} | Using defaults")
            return None

        try:
            raw = self._object_store_read(key)
            wrapped = json.loads(raw)

            # Check version
            version = wrapped.get("version", "1.0")
            if version != SCHEMA_VERSION:
                self.log(f"STATE: VERSION_MISMATCH | {key} | {version} vs {SCHEMA_VERSION}")
                # Could trigger migration here

            return wrapped.get("data", {})

        except json.JSONDecodeError as e:
            self.log(f"STATE: CORRUPT | {key} | JSON error: {e} | Deleting corrupted file")
            self._object_store_delete(key)  # Delete corrupted state to prevent zombie state
            return None
        except Exception as e:
            self.log(f"STATE: LOAD_ERROR | {key} | {e}")
            return None

    # =========================================================================
    # Capital State
    # =========================================================================

    def save_capital_state(self, capital_engine: Any) -> bool:
        """Save capital engine state."""
        data = capital_engine.get_state_for_persistence()
        success = self._save_state(StateKeys.CAPITAL, data)
        if success:
            self.log(f"STATE: SAVED | Capital | Phase={data.get('current_phase')}")
        return success

    def load_capital_state(self, capital_engine: Any) -> bool:
        """Load capital engine state."""
        data = self._load_state(StateKeys.CAPITAL)
        if data:
            capital_engine.restore_state(data)
            self.log(f"STATE: LOADED | Capital | Phase={data.get('current_phase')}")
            return True
        return False

    # =========================================================================
    # Cold Start State
    # =========================================================================

    def save_coldstart_state(self, cold_start_engine: Any) -> bool:
        """Save cold start engine state."""
        data = cold_start_engine.get_state_for_persistence()
        success = self._save_state(StateKeys.COLDSTART, data)
        if success:
            self.log(f"STATE: SAVED | ColdStart | Days={data.get('days_running')}")
        return success

    def load_coldstart_state(self, cold_start_engine: Any) -> bool:
        """Load cold start engine state."""
        data = self._load_state(StateKeys.COLDSTART)
        if data:
            cold_start_engine.restore_state(data)
            self.log(f"STATE: LOADED | ColdStart | Days={data.get('days_running')}")
            return True
        return False

    # =========================================================================
    # Position State
    # =========================================================================

    def save_positions(self, positions: Dict[str, PositionState]) -> bool:
        """
        Save position tracking state.

        Args:
            positions: Dict of symbol -> PositionState.

        Returns:
            True if save succeeded.
        """
        data = {symbol: pos.to_dict() for symbol, pos in positions.items()}
        success = self._save_state(StateKeys.POSITIONS, {"positions": data})
        if success:
            self.log(f"STATE: SAVED | Positions | Count={len(positions)}")
            self._positions = positions.copy()
        return success

    def load_positions(self) -> Dict[str, PositionState]:
        """
        Load position tracking state.

        Returns:
            Dict of symbol -> PositionState.
        """
        data = self._load_state(StateKeys.POSITIONS)
        if not data:
            return {}

        positions = {}
        raw_positions = data.get("positions", {})
        for symbol, pos_data in raw_positions.items():
            try:
                positions[symbol] = PositionState.from_dict(pos_data)
            except (KeyError, TypeError) as e:
                self.log(f"STATE: POSITION_ERROR | {symbol} | {e}")

        self.log(f"STATE: LOADED | Positions | Count={len(positions)}")
        self._positions = positions
        return positions

    def add_position(
        self,
        symbol: str,
        entry_price: float,
        entry_date: str,
        strategy_tag: str,
        quantity: int,
    ) -> None:
        """
        Add or update a position.

        Args:
            symbol: Ticker symbol.
            entry_price: Entry fill price.
            entry_date: Entry date (YYYY-MM-DD).
            strategy_tag: Source strategy.
            quantity: Number of shares.
        """
        self._positions[symbol] = PositionState(
            symbol=symbol,
            entry_price=entry_price,
            entry_date=entry_date,
            highest_high=entry_price,
            current_stop=None,
            strategy_tag=strategy_tag,
            quantity=quantity,
        )
        self.log(
            f"STATE: POSITION_ADD | {symbol} | "
            f"Entry={entry_price:.2f} | Qty={quantity} | Tag={strategy_tag}"
        )

    def update_position(
        self,
        symbol: str,
        highest_high: Optional[float] = None,
        current_stop: Optional[float] = None,
        quantity: Optional[int] = None,
    ) -> None:
        """
        Update an existing position.

        Args:
            symbol: Ticker symbol.
            highest_high: New highest high (if changed).
            current_stop: New stop level (if changed).
            quantity: New quantity (if changed).
        """
        if symbol not in self._positions:
            self.log(f"STATE: POSITION_NOT_FOUND | {symbol}")
            return

        pos = self._positions[symbol]
        if highest_high is not None:
            pos.highest_high = highest_high
        if current_stop is not None:
            pos.current_stop = current_stop
        if quantity is not None:
            pos.quantity = quantity

    def remove_position(self, symbol: str) -> None:
        """Remove a position (on exit)."""
        if symbol in self._positions:
            del self._positions[symbol]
            self.log(f"STATE: POSITION_REMOVE | {symbol}")

    def get_position(self, symbol: str) -> Optional[PositionState]:
        """Get position state for a symbol."""
        return self._positions.get(symbol)

    def get_all_positions(self) -> Dict[str, PositionState]:
        """Get all tracked positions."""
        return self._positions.copy()

    # =========================================================================
    # Regime State
    # =========================================================================

    def save_regime_state(self, regime_engine: Any) -> bool:
        """Save regime engine state."""
        data = regime_engine.get_state_for_persistence()
        success = self._save_state(StateKeys.REGIME, data)
        if success:
            self.log(f"STATE: SAVED | Regime | Score={data.get('smoothed_score', 'N/A')}")
        return success

    def load_regime_state(self, regime_engine: Any) -> bool:
        """Load regime engine state."""
        data = self._load_state(StateKeys.REGIME)
        if data:
            if hasattr(regime_engine, "restore_state"):
                regime_engine.restore_state(data)
            elif hasattr(regime_engine, "load_state"):
                regime_engine.load_state(data)
            self.log(f"STATE: LOADED | Regime | Score={data.get('smoothed_score', 'N/A')}")
            return True
        return False

    # =========================================================================
    # Risk State
    # =========================================================================

    def save_risk_state(self, risk_engine: Any) -> bool:
        """Save risk engine state."""
        data = risk_engine.get_state_for_persistence()
        success = self._save_state(StateKeys.RISK, data)
        if success:
            self.log(f"STATE: SAVED | Risk")
        return success

    def load_risk_state(self, risk_engine: Any) -> bool:
        """Load risk engine state."""
        data = self._load_state(StateKeys.RISK)
        if data:
            risk_engine.load_state(data)
            self.log(f"STATE: LOADED | Risk")
            return True
        return False

    # =========================================================================
    # Weekly State
    # =========================================================================

    def save_weekly_state(
        self,
        week_start_equity: float,
        week_start_date: str,
        weekly_breaker_triggered: bool,
    ) -> bool:
        """Save weekly breaker state."""
        data = {
            "week_start_equity": week_start_equity,
            "week_start_date": week_start_date,
            "weekly_breaker_triggered": weekly_breaker_triggered,
        }
        success = self._save_state(StateKeys.WEEKLY, data)
        if success:
            self.log(f"STATE: SAVED | Weekly | Equity=${week_start_equity:,.2f}")
        return success

    def load_weekly_state(self) -> Optional[Dict[str, Any]]:
        """Load weekly breaker state."""
        data = self._load_state(StateKeys.WEEKLY)
        if data:
            self.log(
                f"STATE: LOADED | Weekly | " f"Equity=${data.get('week_start_equity', 0):,.2f}"
            )
        return data

    # =========================================================================
    # Execution Engine State
    # =========================================================================

    def save_execution_state(self, execution_engine: Any) -> bool:
        """Save execution engine state."""
        data = execution_engine.get_state_for_persistence()
        success = self._save_state(StateKeys.EXECUTION, data)
        if success:
            self.log(f"STATE: SAVED | Execution")
        return success

    def load_execution_state(self) -> Optional[Dict[str, Any]]:
        """Load execution engine state."""
        return self._load_state(StateKeys.EXECUTION)

    # =========================================================================
    # Portfolio Router State
    # =========================================================================

    def save_router_state(self, router: Any) -> bool:
        """Save portfolio router state."""
        data = router.get_state_for_persistence()
        success = self._save_state(StateKeys.ROUTER, data)
        if success:
            self.log(f"STATE: SAVED | Router")
        return success

    def load_router_state(self) -> Optional[Dict[str, Any]]:
        """Load portfolio router state."""
        return self._load_state(StateKeys.ROUTER)

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def save_all(
        self,
        capital_engine: Optional[Any] = None,
        cold_start_engine: Optional[Any] = None,
        regime_engine: Optional[Any] = None,
        risk_engine: Optional[Any] = None,
        execution_engine: Optional[Any] = None,
        router: Optional[Any] = None,
    ) -> int:
        """
        Save all state to ObjectStore.

        Args:
            capital_engine: Capital engine instance.
            cold_start_engine: Cold start engine instance.
            regime_engine: Regime engine instance.
            risk_engine: Risk engine instance.
            execution_engine: Execution engine instance.
            router: Portfolio router instance.

        Returns:
            Number of categories successfully saved.
        """
        saved = 0

        if capital_engine:
            if self.save_capital_state(capital_engine):
                saved += 1

        if cold_start_engine:
            if self.save_coldstart_state(cold_start_engine):
                saved += 1

        if self._positions:
            if self.save_positions(self._positions):
                saved += 1

        if regime_engine:
            if self.save_regime_state(regime_engine):
                saved += 1

        if risk_engine:
            if self.save_risk_state(risk_engine):
                saved += 1

        if execution_engine:
            if self.save_execution_state(execution_engine):
                saved += 1

        if router:
            if self.save_router_state(router):
                saved += 1

        self._save_count += 1
        self.log(f"STATE: SAVE_ALL | Saved {saved} categories | Save #{self._save_count}")
        return saved

    def load_all(
        self,
        capital_engine: Optional[Any] = None,
        cold_start_engine: Optional[Any] = None,
        regime_engine: Optional[Any] = None,
        risk_engine: Optional[Any] = None,
    ) -> int:
        """
        Load all state from ObjectStore.

        Args:
            capital_engine: Capital engine instance.
            cold_start_engine: Cold start engine instance.
            regime_engine: Regime engine instance.
            risk_engine: Risk engine instance.

        Returns:
            Number of categories successfully loaded.
        """
        loaded = 0

        if capital_engine:
            if self.load_capital_state(capital_engine):
                loaded += 1

        if cold_start_engine:
            if self.load_coldstart_state(cold_start_engine):
                loaded += 1

        # Always load positions
        positions = self.load_positions()
        if positions:
            loaded += 1

        if regime_engine:
            if self.load_regime_state(regime_engine):
                loaded += 1

        if risk_engine:
            if self.load_risk_state(risk_engine):
                loaded += 1

        self._load_count += 1
        self.log(f"STATE: LOAD_ALL | Loaded {loaded} categories | Load #{self._load_count}")
        return loaded

    # =========================================================================
    # Position Reconciliation
    # =========================================================================

    def reconcile_positions(
        self,
        broker_positions: Dict[str, int],
    ) -> ReconciliationResult:
        """
        Reconcile persisted state with actual broker positions.

        Args:
            broker_positions: Dict of symbol -> quantity from broker.

        Returns:
            ReconciliationResult with details of any mismatches.
        """
        result = ReconciliationResult()
        persisted_symbols = set(self._positions.keys())
        broker_symbols = set(broker_positions.keys())

        # Check each persisted position
        for symbol in persisted_symbols:
            if symbol not in broker_symbols:
                # Position was closed
                result.closed_positions.append(symbol)
                self.log(f"STATE: RECONCILE | {symbol} | Position closed")
                self.remove_position(symbol)
            elif broker_positions[symbol] != self._positions[symbol].quantity:
                # Quantity mismatch
                result.quantity_mismatches.append(symbol)
                old_qty = self._positions[symbol].quantity
                new_qty = broker_positions[symbol]
                self.log(
                    f"STATE: RECONCILE | {symbol} | "
                    f"Qty mismatch: persisted={old_qty}, broker={new_qty}"
                )
                self._positions[symbol].quantity = new_qty
            else:
                # Match
                result.matched.append(symbol)

        # Check for unexpected positions
        for symbol in broker_symbols - persisted_symbols:
            result.unexpected_positions.append(symbol)
            self.log(
                f"STATE: RECONCILE | {symbol} | " f"Unexpected position (not in persisted state)"
            )
            # Add minimal tracking
            self._positions[symbol] = PositionState(
                symbol=symbol,
                entry_price=0.0,  # Unknown
                entry_date="",  # Unknown
                highest_high=0.0,
                current_stop=None,
                strategy_tag="UNKNOWN",
                quantity=broker_positions[symbol],
            )

        self.log(
            f"STATE: RECONCILE | Complete | "
            f"Matched={len(result.matched)}, "
            f"Mismatches={len(result.quantity_mismatches)}, "
            f"Closed={len(result.closed_positions)}, "
            f"Unexpected={len(result.unexpected_positions)}"
        )

        return result

    # =========================================================================
    # Reset Operations
    # =========================================================================

    def reset_all(self) -> None:
        """Delete all persisted state (testing/debugging only)."""
        for key in StateKeys.ALL_KEYS:
            self._object_store_delete(key)
        self._positions.clear()
        self.log("STATE: RESET_ALL | All state deleted")

    def reset_category(self, key: str) -> None:
        """Delete a specific state category."""
        if key in StateKeys.ALL_KEYS:
            self._object_store_delete(key)
            if key == StateKeys.POSITIONS:
                self._positions.clear()
            self.log(f"STATE: RESET | {key} deleted")

    # =========================================================================
    # Validation
    # =========================================================================

    def validate_capital_state(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and fix capital state.

        Args:
            data: Raw loaded state.

        Returns:
            Validated state with invalid values corrected.
        """
        # Validate phase
        phase = data.get("current_phase", "SEED")
        if phase not in ("SEED", "GROWTH"):
            self.log(f"STATE: VALIDATE | Invalid phase '{phase}', using SEED")
            data["current_phase"] = "SEED"

        # Validate days_above_threshold
        days = data.get("days_above_threshold", 0)
        if not isinstance(days, int) or days < 0:
            self.log(f"STATE: VALIDATE | Invalid days_above_threshold {days}, using 0")
            data["days_above_threshold"] = 0

        # Validate lockbox
        lockbox = data.get("locked_amount", 0.0)
        if not isinstance(lockbox, (int, float)) or lockbox < 0:
            self.log(f"STATE: VALIDATE | Invalid locked_amount {lockbox}, using 0")
            data["locked_amount"] = 0.0

        return data

    def validate_regime_state(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and fix regime state.

        Args:
            data: Raw loaded state.

        Returns:
            Validated state with invalid values clamped.
        """
        # Validate smoothed_score (0-100)
        score = data.get("smoothed_score", 50.0)
        if not isinstance(score, (int, float)):
            self.log(f"STATE: VALIDATE | Invalid smoothed_score type, using 50")
            data["smoothed_score"] = 50.0
        elif score < 0 or score > 100:
            clamped = max(0, min(100, score))
            self.log(f"STATE: VALIDATE | Clamped smoothed_score {score} -> {clamped}")
            data["smoothed_score"] = clamped

        return data

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_statistics(self) -> Dict[str, Any]:
        """Get state manager statistics."""
        return {
            "position_count": len(self._positions),
            "save_count": self._save_count,
            "load_count": self._load_count,
            "positions": list(self._positions.keys()),
        }
