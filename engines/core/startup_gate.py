"""
V6.0: StartupGate — Simplified startup arming sequence.

Options are independent with their own conviction system (VASS/MICRO).
This gate only controls TREND and MR engines during initial deployment.

Phases:
    INDICATOR_WARMUP: 3 days. Nothing allowed. Indicators warming up.
    REDUCED: 3 days. TREND & MR at 50%, OPTIONS at 100%.
    FULLY_ARMED: Permanent. Everything at 100%.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import config

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm


class StartupGate:
    """V6.0: Simplified startup arming sequence.

    Options are independent — they have their own conviction system (VASS/MICRO).
    This gate only controls TREND and MR engines during initial deployment.

    Phases:
        INDICATOR_WARMUP: 3 days. Nothing allowed. Indicators warming up.
        REDUCED: 3 days. TREND & MR at 50%, OPTIONS at 100%.
        FULLY_ARMED: Permanent. Everything at 100%.
    """

    # Valid phase names (ordered)
    PHASES = ("INDICATOR_WARMUP", "REDUCED", "FULLY_ARMED")

    def __init__(self, algorithm: Optional[QCAlgorithm] = None):
        self.algorithm = algorithm

        # Phase tracking
        self._phase: str = "INDICATOR_WARMUP"
        self._days_in_phase: int = 0

    def log(self, message: str) -> None:
        if self.algorithm:
            self.algorithm.Log(message)

    # --- Core API ---

    def is_fully_armed(self) -> bool:
        """Check if all startup gates have been passed."""
        # V6.4: Bypass startup gate in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_STARTUP_GATE_ENABLED:
            return True
        return self._phase == "FULLY_ARMED" or not config.STARTUP_GATE_ENABLED

    def allows_hedges(self) -> bool:
        """Hedges (TMF/PSQ) are ALWAYS allowed — defensive by nature."""
        return True

    def allows_yield(self) -> bool:
        """Yield sleeve (SHV) is ALWAYS allowed — cash management."""
        return True

    def allows_options(self) -> bool:
        """V6.0: Options allowed from REDUCED onward (day 4+).

        Options have their own conviction system (VASS/MICRO) that handles
        direction decisions. No startup gate direction restrictions.
        """
        # V6.4: Bypass startup gate in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_STARTUP_GATE_ENABLED:
            return True
        if not config.STARTUP_GATE_ENABLED:
            return True
        return self._phase in ("REDUCED", "FULLY_ARMED")

    def allows_trend_mr(self) -> bool:
        """TREND and MR engines allowed from REDUCED onward (day 4+)."""
        # V6.4: Bypass startup gate in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_STARTUP_GATE_ENABLED:
            return True
        if not config.STARTUP_GATE_ENABLED:
            return True
        return self._phase in ("REDUCED", "FULLY_ARMED")

    def get_trend_mr_size_multiplier(self) -> float:
        """Get TREND/MR size multiplier for current phase.

        Returns:
            1.0 for FULLY_ARMED or gate disabled.
            0.5 for REDUCED phase.
            0.0 for INDICATOR_WARMUP (no trades).
        """
        # V6.4: Bypass startup gate in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_STARTUP_GATE_ENABLED:
            return 1.0
        if not config.STARTUP_GATE_ENABLED or self._phase == "FULLY_ARMED":
            return 1.0
        if self._phase == "REDUCED":
            return config.STARTUP_GATE_REDUCED_SIZE_MULT  # 0.5
        return 0.0  # INDICATOR_WARMUP

    def get_options_size_multiplier(self) -> float:
        """V6.0: Options always at 100% when allowed.

        Options have their own conviction system for direction/sizing.
        """
        # V6.4: Bypass startup gate in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_STARTUP_GATE_ENABLED:
            return 1.0
        if not config.STARTUP_GATE_ENABLED or self._phase == "FULLY_ARMED":
            return 1.0
        if self._phase == "REDUCED":
            return 1.0  # Options at 100% from day 4
        return 0.0  # INDICATOR_WARMUP - not allowed

    def get_phase(self) -> str:
        return self._phase

    # --- Daily update (called from _on_eod_processing) ---

    def end_of_day_update(self) -> str:
        """Advance phase based on calendar days.

        Called once per day at EOD. Phase progression is purely time-based.

        Returns:
            Current phase name after update.
        """
        if not config.STARTUP_GATE_ENABLED or self._phase == "FULLY_ARMED":
            return self._phase

        self._days_in_phase += 1

        # Phase 0: INDICATOR_WARMUP (3 days)
        if self._phase == "INDICATOR_WARMUP":
            self.log(
                f"STARTUP_GATE: Warmup Day {self._days_in_phase}/"
                f"{config.STARTUP_GATE_WARMUP_DAYS} | No trading"
            )
            if self._days_in_phase >= config.STARTUP_GATE_WARMUP_DAYS:
                self._phase = "REDUCED"
                self._days_in_phase = 0
                self.log(
                    "STARTUP_GATE: Warmup complete -> REDUCED " "(TREND/MR at 50%, OPTIONS at 100%)"
                )

        # Phase 1: REDUCED (3 days)
        elif self._phase == "REDUCED":
            self.log(
                f"STARTUP_GATE: Reduced Day {self._days_in_phase}/"
                f"{config.STARTUP_GATE_REDUCED_DAYS} | TREND/MR at 50%, OPTIONS at 100%"
            )
            if self._days_in_phase >= config.STARTUP_GATE_REDUCED_DAYS:
                self._phase = "FULLY_ARMED"
                self._days_in_phase = 0
                self.log("STARTUP_GATE: FULLY ARMED - all restrictions lifted")

        return self._phase

    # --- Persistence ---

    def get_state_for_persistence(self) -> dict:
        return {
            "phase": self._phase,
            "days_in_phase": self._days_in_phase,
        }

    def restore_state(self, state: dict) -> None:
        phase = state.get("phase", "INDICATOR_WARMUP")
        # V6.0: Handle old phase names gracefully
        if phase in ("REGIME_GATE", "OBSERVATION"):
            phase = "INDICATOR_WARMUP"
        if phase not in self.PHASES:
            self.log(
                f"STARTUP_GATE: Invalid phase '{phase}' in saved state, "
                f"resetting to INDICATOR_WARMUP"
            )
            phase = "INDICATOR_WARMUP"
        self._phase = phase
        self._days_in_phase = state.get("days_in_phase", state.get("arming_days", 0))
        self.log(
            f"STARTUP_GATE: Restored | Phase={self._phase} | DaysInPhase={self._days_in_phase}"
        )
