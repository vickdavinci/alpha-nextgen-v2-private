from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple

import config


@dataclass
class VIXSnapshot:
    """VIX data point for monitoring."""

    timestamp: str
    value: float
    change_from_open_pct: float = 0.0


class IVSensor:
    """
    V5.3: VASS Conviction Engine - VIX Direction + Level Tracking.

    Tracks VIX on multiple timeframes:
    - Intraday: 30-min SMA for strategy flickering prevention
    - Weekly: 5-day SMA for short-term trend
    - Monthly: 20-day SMA for sustained trend

    Conviction triggers when VIX shows clear directional movement:
    - 5d change > +20% → BEARISH conviction
    - 5d change < -15% → BULLISH conviction
    - 20d change > +30% → STRONG BEARISH
    - 20d change < -20% → STRONG BULLISH
    - VIX crosses above 25 → BEARISH
    - VIX crosses below 15 → BULLISH
    """

    def __init__(self, smoothing_minutes: int = 30, log_func=None):
        """
        Initialize VASS Conviction Engine.

        Args:
            smoothing_minutes: Intraday SMA window (default 30 minutes)
            log_func: Logging function (optional)
        """
        # Intraday smoothing (existing)
        self._vix_history: Deque[float] = deque(
            maxlen=smoothing_minutes or config.VASS_IV_SMOOTHING_MINUTES
        )
        self._last_classification: Optional[str] = None
        self._log = log_func or (lambda x: None)

        # V5.3: Daily VIX tracking for conviction
        self._vix_daily_history: Deque[float] = deque(
            maxlen=config.VASS_VIX_20D_PERIOD + 5  # 25 days buffer
        )
        self._last_vix_daily: Optional[float] = None
        self._last_update_date: Optional[str] = None

        # V5.3: Level crossing tracking
        self._prev_vix_level: Optional[float] = None
        self._crossed_above_fear: bool = False
        self._crossed_below_complacent: bool = False

    def update(self, vix_value: float, current_date: str = None) -> None:
        """
        Add VIX reading (called every minute from OnData).

        Args:
            vix_value: Current VIX value
            current_date: Date string (YYYY-MM-DD) for daily tracking
        """
        if vix_value <= 0:
            return

        # Intraday update
        self._vix_history.append(vix_value)

        # Daily update (once per day)
        if current_date and current_date != self._last_update_date:
            self._vix_daily_history.append(vix_value)
            self._last_update_date = current_date

            # Track level crossings
            if self._prev_vix_level is not None:
                fear_cross_level = float(getattr(config, "VASS_VIX_FEAR_CROSS_LEVEL", 22.0))
                complacent_cross_level = float(
                    getattr(config, "VASS_VIX_COMPLACENT_CROSS_LEVEL", 14.0)
                )

                # V10.8: low-VIX conviction broadening via relaxed crossing levels.
                if bool(getattr(config, "VASS_LOW_VIX_CONVICTION_RELAX_ENABLED", False)):
                    low_vix_max = float(getattr(config, "VASS_LOW_VIX_CONVICTION_MAX_VIX", 16.0))
                    if self._prev_vix_level <= low_vix_max and vix_value <= low_vix_max:
                        crossing_delta = float(getattr(config, "VASS_LOW_VIX_CROSSING_DELTA", 0.80))
                        fear_cross_level = max(0.1, fear_cross_level - crossing_delta)
                        complacent_cross_level = complacent_cross_level + crossing_delta

                # Crossed above fear threshold
                if self._prev_vix_level < fear_cross_level <= vix_value:
                    self._crossed_above_fear = True
                    self._log(f"VASS: VIX crossed ABOVE {fear_cross_level:.1f} (fear threshold)")
                else:
                    self._crossed_above_fear = False

                # Crossed below complacent threshold
                if self._prev_vix_level > complacent_cross_level >= vix_value:
                    self._crossed_below_complacent = True
                    self._log(
                        f"VASS: VIX crossed BELOW {complacent_cross_level:.1f} (complacent threshold)"
                    )
                else:
                    self._crossed_below_complacent = False

            self._prev_vix_level = vix_value
            self._last_vix_daily = vix_value

    def get_smoothed_vix(self) -> float:
        """Return 30-min SMA of VIX (intraday smoothing)."""
        if not self._vix_history:
            return 20.0  # Default to medium IV
        return sum(self._vix_history) / len(self._vix_history)

    def get_vix_5d_sma(self) -> Optional[float]:
        """Return 5-day SMA of VIX (weekly trend)."""
        period = config.VASS_VIX_5D_PERIOD
        if len(self._vix_daily_history) < period:
            return None
        recent = list(self._vix_daily_history)[-period:]
        return sum(recent) / len(recent)

    def get_vix_20d_sma(self) -> Optional[float]:
        """Return 20-day SMA of VIX (monthly trend)."""
        period = config.VASS_VIX_20D_PERIOD
        if len(self._vix_daily_history) < period:
            return None
        recent = list(self._vix_daily_history)[-period:]
        return sum(recent) / len(recent)

    def get_vix_5d_change(self) -> Optional[float]:
        """
        Return 5-day VIX change as percentage.

        Returns:
            Percentage change (0.20 = +20%), or None if insufficient data
        """
        period = config.VASS_VIX_5D_PERIOD
        if len(self._vix_daily_history) < period:
            return None
        history = list(self._vix_daily_history)
        vix_now = history[-1]
        vix_5d_ago = history[-period]
        if vix_5d_ago <= 0:
            return None
        return (vix_now - vix_5d_ago) / vix_5d_ago

    def get_vix_20d_change(self) -> Optional[float]:
        """
        Return 20-day VIX change as percentage.

        Returns:
            Percentage change (0.30 = +30%), or None if insufficient data
        """
        period = config.VASS_VIX_20D_PERIOD
        if len(self._vix_daily_history) < period:
            return None
        history = list(self._vix_daily_history)
        vix_now = history[-1]
        vix_20d_ago = history[-period]
        if vix_20d_ago <= 0:
            return None
        return (vix_now - vix_20d_ago) / vix_20d_ago

    def classify(self) -> str:
        """Classify IV environment: LOW, MEDIUM, HIGH."""
        if not self._vix_history or len(self._vix_history) < 5:
            self._log("VASS: Insufficient VIX data, defaulting to MEDIUM")
            return "MEDIUM"

        smoothed = self.get_smoothed_vix()

        if smoothed < config.VASS_IV_LOW_THRESHOLD:
            classification = "LOW"
        elif smoothed > config.VASS_IV_HIGH_THRESHOLD:
            classification = "HIGH"
        else:
            classification = "MEDIUM"

        if self._last_classification != classification:
            self._log(
                f"VASS: IV environment changed {self._last_classification} → {classification} "
                f"(VIX SMA={smoothed:.1f})"
            )
            self._last_classification = classification

        return classification

    def has_conviction(self) -> Tuple[bool, Optional[str], str]:
        """
        V5.3: Check if VASS has conviction to override Macro.

        Returns:
            Tuple of (has_conviction, direction, reason)
            - has_conviction: True if clear signal
            - direction: "BULLISH" or "BEARISH" or None
            - reason: Human-readable explanation
        """
        vix_5d_change = self.get_vix_5d_change()
        vix_20d_change = self.get_vix_20d_change()

        fear_cross_level = float(getattr(config, "VASS_VIX_FEAR_CROSS_LEVEL", 22.0))
        complacent_cross_level = float(getattr(config, "VASS_VIX_COMPLACENT_CROSS_LEVEL", 14.0))
        bearish_5d_threshold = float(getattr(config, "VASS_VIX_5D_BEARISH_THRESHOLD", 0.16))
        bullish_5d_threshold = float(getattr(config, "VASS_VIX_5D_BULLISH_THRESHOLD", -0.20))
        bearish_veto_min_vix = float(getattr(config, "VASS_VIX_BEARISH_VETO_MIN_LEVEL", 18.0))
        bearish_veto_5d_min_change = float(
            getattr(config, "VASS_VIX_BEARISH_VETO_5D_MIN_CHANGE", 0.25)
        )
        current_vix_level = self.get_smoothed_vix()

        # V10.8: low-VIX conviction broadening (threshold-first, no new indicators).
        if bool(getattr(config, "VASS_LOW_VIX_CONVICTION_RELAX_ENABLED", False)):
            smoothed_vix = self.get_smoothed_vix()
            low_vix_max = float(getattr(config, "VASS_LOW_VIX_CONVICTION_MAX_VIX", 16.0))
            if smoothed_vix <= low_vix_max:
                crossing_delta = float(getattr(config, "VASS_LOW_VIX_CROSSING_DELTA", 0.80))
                fear_cross_level = max(0.1, fear_cross_level - crossing_delta)
                complacent_cross_level = complacent_cross_level + crossing_delta

                low_vix_5d_threshold = float(
                    getattr(config, "VASS_LOW_VIX_5D_CHANGE_THRESHOLD", 0.12)
                )
                bearish_5d_threshold = min(bearish_5d_threshold, low_vix_5d_threshold)
                bullish_5d_threshold = max(bullish_5d_threshold, -low_vix_5d_threshold)

        # Level crossing conviction (immediate)
        if self._crossed_above_fear:
            return True, "BEARISH", f"VIX crossed above {fear_cross_level:.1f}"

        if self._crossed_below_complacent:
            return True, "BULLISH", f"VIX crossed below {complacent_cross_level:.1f}"

        # 5-day change conviction (fast-moving fear)
        if vix_5d_change is not None:
            effective_bearish_5d_threshold = max(bearish_5d_threshold, bearish_veto_5d_min_change)
            if (
                vix_5d_change > effective_bearish_5d_threshold
                and current_vix_level >= bearish_veto_min_vix
            ):
                return (
                    True,
                    "BEARISH",
                    f"VIX 5d change +{vix_5d_change:.0%} > +{effective_bearish_5d_threshold:.0%} "
                    f"and VIX {current_vix_level:.1f} >= {bearish_veto_min_vix:.1f}",
                )

            if vix_5d_change < bullish_5d_threshold:
                return (
                    True,
                    "BULLISH",
                    f"VIX 5d change {vix_5d_change:.0%} < {bullish_5d_threshold:.0%}",
                )

        # 20-day change conviction (sustained direction)
        if vix_20d_change is not None:
            if (
                vix_20d_change > config.VASS_VIX_20D_STRONG_BEARISH
                and current_vix_level >= bearish_veto_min_vix
            ):
                return (
                    True,
                    "BEARISH",
                    f"VIX 20d change +{vix_20d_change:.0%} > "
                    f"+{config.VASS_VIX_20D_STRONG_BEARISH:.0%} (STRONG) and "
                    f"VIX {current_vix_level:.1f} >= {bearish_veto_min_vix:.1f}",
                )

            if vix_20d_change < config.VASS_VIX_20D_STRONG_BULLISH:
                return (
                    True,
                    "BULLISH",
                    f"VIX 20d change {vix_20d_change:.0%} < {config.VASS_VIX_20D_STRONG_BULLISH:.0%} (STRONG)",
                )

        # No conviction
        return False, None, "No clear VIX direction signal"

    def is_ready(self) -> bool:
        """True if enough history for reliable classification."""
        return len(self._vix_history) >= 10

    def is_conviction_ready(self) -> bool:
        """True if enough daily history for conviction signals (5 days min)."""
        return len(self._vix_daily_history) >= config.VASS_VIX_5D_PERIOD

    def get_history_length(self) -> int:
        """Return current intraday history length."""
        return len(self._vix_history)

    def get_daily_history_length(self) -> int:
        """Return current daily history length."""
        return len(self._vix_daily_history)

    def reset(self) -> None:
        """Clear all history (for testing or session reset)."""
        self._vix_history.clear()
        self._vix_daily_history.clear()
        self._last_classification = None
        self._last_vix_daily = None
        self._last_update_date = None
        self._prev_vix_level = None
        self._crossed_above_fear = False
        self._crossed_below_complacent = False

    def get_state_summary(self) -> Dict[str, object]:
        """Return current state for logging/debugging."""
        return {
            "vix_intraday_sma": self.get_smoothed_vix(),
            "vix_5d_sma": self.get_vix_5d_sma(),
            "vix_20d_sma": self.get_vix_20d_sma(),
            "vix_5d_change": self.get_vix_5d_change(),
            "vix_20d_change": self.get_vix_20d_change(),
            "iv_classification": self.classify() if self.is_ready() else "NOT_READY",
            "conviction": self.has_conviction(),
            "daily_history_len": len(self._vix_daily_history),
        }
