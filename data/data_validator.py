"""Shared data-validation helpers for option/equity payloads."""

from typing import Any, Tuple


def normalize_symbol_key(symbol: Any) -> str:
    """Normalize symbol-like values to stable uppercase keys."""
    if symbol is None:
        return ""
    text = str(symbol).strip().upper()
    if not text:
        return ""
    return " ".join(text.split())


def is_option_symbol(symbol: Any) -> bool:
    """Return True when the symbol resembles a QC option contract string."""
    sym = normalize_symbol_key(symbol)
    return bool(sym) and len(sym) > 10 and ("C0" in sym or "P0" in sym)


def has_valid_price(price: Any) -> bool:
    """Best-effort positive numeric price check."""
    try:
        return float(price) > 0.0
    except Exception:
        return False


def validate_option_order_payload(
    symbol: Any,
    contract_price: Any,
    requested_quantity: Any,
) -> Tuple[bool, str]:
    """Validate basic option payload invariants used by router/engine paths."""
    symbol_key = normalize_symbol_key(symbol)
    if not is_option_symbol(symbol_key):
        return False, "E_INVALID_OPTION_SYMBOL"
    if not has_valid_price(contract_price):
        return False, "E_INVALID_CONTRACT_PRICE"
    try:
        qty = int(requested_quantity or 0)
    except Exception:
        return False, "E_INVALID_REQUESTED_QUANTITY"
    if qty <= 0:
        return False, "E_INVALID_REQUESTED_QUANTITY"
    return True, "OK"
