"""
V2.30: StartupGate — All-Weather startup arming sequence.

Separate from ColdStartEngine (which handles 5-day warmup after kill switch).
This gate ensures the bot ramps into the market safely while allowing defensive
engines (hedges, bearish options) from day 1.

Once fully armed, it stays armed permanently. Kill switch does NOT reset it.

Phases:
    INDICATOR_WARMUP: 5 days. Hedges + yield active. No directional trades.
    OBSERVATION: 5 days. Add bearish options (PUT spreads at 50% size).
    REDUCED: 5 days. All engines at 50% sizing.
    FULLY_ARMED: Permanent. No restrictions.

Design principle: The gate controls HOW MUCH capital to deploy.
The regime engine controls WHAT to deploy it in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import config

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm


class StartupGate:
    """V2.30: All-Weather startup arming sequence.

    Separate from ColdStartEngine (which handles 5-day warmup after kill switch).
    This gate ramps capital deployment over 15 days while allowing defensive
    engines from day 1.

    Phases:
        INDICATOR_WARMUP: 5 days. Hedges + yield only. No directional trades.
        OBSERVATION: 5 days. Add bearish options (PUT spreads at 50% size).
        REDUCED: 5 days. All engines at 50% sizing.
        FULLY_ARMED: Permanent. No restrictions.
    """

    # Valid phase names (ordered)
    PHASES = ("INDICATOR_WARMUP", "OBSERVATION", "REDUCED", "FULLY_ARMED")

    def __init__(self, algorithm: Optional[QCAlgorithm] = None):
        self.algorithm = algorithm

        # Phase tracking
        self._phase: str = "INDICATOR_WARMUP"
        self._days_in_phase: int = 0

    def log(self, message: str) -> None:
        if self.algorithm:
            self.algorithm.Log(message)

    # --- Core API (granular permission checks) ---

    def is_fully_armed(self) -> bool:
        """Check if all startup gates have been passed."""
        return self._phase == "FULLY_ARMED" or not config.STARTUP_GATE_ENABLED

    def allows_hedges(self) -> bool:
        """Hedges (TMF/PSQ) are ALWAYS allowed — defensive by nature."""
        return True

    def allows_yield(self) -> bool:
        """Yield sleeve (SHV) is ALWAYS allowed — cash management."""
        return True

    def allows_bearish_options(self) -> bool:
        """PUT spreads allowed from OBSERVATION onward (day 6+)."""
        if not config.STARTUP_GATE_ENABLED:
            return True
        return self._phase in ("OBSERVATION", "REDUCED", "FULLY_ARMED")

    def allows_directional_longs(self) -> bool:
        """Trend, MR, bullish options allowed from REDUCED onward (day 11+)."""
        if not config.STARTUP_GATE_ENABLED:
            return True
        return self._phase in ("REDUCED", "FULLY_ARMED")

    def get_size_multiplier(self) -> float:
        """Get position size multiplier for current phase.

        Returns:
            1.0 for FULLY_ARMED or gate disabled.
            STARTUP_GATE_REDUCED_SIZE_MULT (0.50) for REDUCED and OBSERVATION.
            0.0 for INDICATOR_WARMUP (no directional trades).
        """
        if not config.STARTUP_GATE_ENABLED or self._phase == "FULLY_ARMED":
            return 1.0
        if self._phase in ("REDUCED", "OBSERVATION"):
            return config.STARTUP_GATE_REDUCED_SIZE_MULT
        return 0.0  # INDICATOR_WARMUP

    def get_phase(self) -> str:
        return self._phase

    # --- Daily update (called from _on_eod_processing) ---

    def end_of_day_update(self) -> str:
        """Advance phase based on calendar days. No regime dependency.

        Called once per day at EOD. Phase progression is purely time-based:
        the regime engine handles all market-condition decisions.

        Returns:
            Current phase name after update.
        """
        if not config.STARTUP_GATE_ENABLED or self._phase == "FULLY_ARMED":
            return self._phase

        self._days_in_phase += 1

        # Phase 0: INDICATOR_WARMUP
        if self._phase == "INDICATOR_WARMUP":
            self.log(
                f"STARTUP_GATE: Warmup Day {self._days_in_phase}/"
                f"{config.STARTUP_GATE_WARMUP_DAYS} | Hedges active, no longs"
            )
            if self._days_in_phase >= config.STARTUP_GATE_WARMUP_DAYS:
                self._phase = "OBSERVATION"
                self._days_in_phase = 0
                self.log(
                    "STARTUP_GATE: Warmup complete → OBSERVATION "
                    "(hedges + bearish options at 50%)"
                )

        # Phase 1: OBSERVATION
        elif self._phase == "OBSERVATION":
            self.log(
                f"STARTUP_GATE: Observation Day {self._days_in_phase}/"
                f"{config.STARTUP_GATE_OBSERVATION_DAYS} | "
                f"Hedges + bearish options active"
            )
            if self._days_in_phase >= config.STARTUP_GATE_OBSERVATION_DAYS:
                self._phase = "REDUCED"
                self._days_in_phase = 0
                self.log(
                    "STARTUP_GATE: Observation complete → REDUCED " "(all engines at 50% sizing)"
                )

        # Phase 2: REDUCED
        elif self._phase == "REDUCED":
            self.log(
                f"STARTUP_GATE: Reduced Day {self._days_in_phase}/"
                f"{config.STARTUP_GATE_REDUCED_DAYS} | All engines at 50%"
            )
            if self._days_in_phase >= config.STARTUP_GATE_REDUCED_DAYS:
                self._phase = "FULLY_ARMED"
                self._days_in_phase = 0
                self.log("STARTUP_GATE: FULLY ARMED — all restrictions lifted")

        return self._phase

    # --- Persistence ---

    def get_state_for_persistence(self) -> dict:
        return {
            "phase": self._phase,
            "days_in_phase": self._days_in_phase,
        }

    def restore_state(self, state: dict) -> None:
        phase = state.get("phase", "INDICATOR_WARMUP")
        # V2.30: Handle old V2.29 state keys gracefully
        if phase == "REGIME_GATE":
            phase = "INDICATOR_WARMUP"
        if phase not in self.PHASES:
            self.log(
                f"STARTUP_GATE: Invalid phase '{phase}' in saved state, "
                f"resetting to INDICATOR_WARMUP"
            )
            phase = "INDICATOR_WARMUP"
        self._phase = phase
        # V2.30: Support both old 'arming_days' and new 'days_in_phase' keys
        self._days_in_phase = state.get("days_in_phase", state.get("arming_days", 0))
        self.log(
            f"STARTUP_GATE: Restored | Phase={self._phase} | " f"DaysInPhase={self._days_in_phase}"
        )
