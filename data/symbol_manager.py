"""Symbol-normalization and comparison helpers."""

from typing import Any

from data.data_validator import normalize_symbol_key


def symbols_match(left: Any, right: Any) -> bool:
    """Return True when two symbol-like values resolve to the same key."""
    lhs = normalize_symbol_key(left)
    rhs = normalize_symbol_key(right)
    return bool(lhs) and lhs == rhs


def build_spread_runtime_key(long_symbol: Any, short_symbol: Any) -> str:
    """Build stable spread runtime key from normalized leg symbols."""
    long_key = normalize_symbol_key(long_symbol)
    short_key = normalize_symbol_key(short_symbol)
    if not long_key or not short_key:
        return ""
    return f"{long_key}|{short_key}"
