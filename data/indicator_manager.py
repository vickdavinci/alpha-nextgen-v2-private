"""Indicator readiness/value helpers used by signal pipelines."""

from typing import Any


def is_indicator_ready(indicator: Any) -> bool:
    """Best-effort readiness check across QC indicators and test doubles."""
    try:
        return bool(getattr(indicator, "IsReady", False))
    except Exception:
        return False


def safe_indicator_value(indicator: Any, default: float = 0.0) -> float:
    """Return current indicator value, or default when unavailable."""
    try:
        if is_indicator_ready(indicator):
            current = getattr(indicator, "Current", None)
            value = getattr(current, "Value", None)
            if value is not None:
                return float(value)
        return float(default)
    except Exception:
        return float(default)


def rolling_window_ready(window: Any, min_size: int = 1) -> bool:
    """Best-effort readiness check for QC RollingWindow-like objects."""
    try:
        size = int(getattr(window, "Count", 0) or 0)
    except Exception:
        return False
    return size >= max(1, int(min_size))
