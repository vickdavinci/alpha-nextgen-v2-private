"""Partial-fill OCO seed helpers for OptionsEngine."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from models.enums import IntradayStrategy


def _infer_direction_hint(symbol_text: Optional[str]) -> Optional[str]:
    text = str(symbol_text or "")
    if re.search(r"\d{6}C\d{8}", text):
        return "CALL"
    if re.search(r"\d{6}P\d{8}", text):
        return "PUT"
    return None


def get_engine_partial_fill_oco_seed_impl(
    self, symbol: str, fill_price: float
) -> Optional[Dict[str, Any]]:
    """Return OCO seed for single-leg engine/pending partial entry fill, if applicable."""
    symbol_norm = self._symbol_str(symbol)
    if not symbol_norm:
        return None

    lane = self._find_engine_lane_by_symbol(symbol_norm)
    pos = self._get_engine_lane_position(lane) if lane else None
    if (
        pos is not None
        and pos.contract is not None
        and float(getattr(pos, "stop_price", 0.0) or 0.0) > 0
        and float(getattr(pos, "target_price", 0.0) or 0.0) > 0
    ):
        return {
            "entry_price": float(getattr(pos, "entry_price", 0.0) or 0.0),
            "stop_price": float(getattr(pos, "stop_price", 0.0) or 0.0),
            "target_price": float(getattr(pos, "target_price", 0.0) or 0.0),
            "entry_strategy": str(getattr(pos, "entry_strategy", "UNKNOWN") or "UNKNOWN"),
        }

    return self.get_pending_engine_partial_oco_seed(symbol=symbol_norm, fill_price=fill_price)


def get_intraday_partial_fill_oco_seed_impl(
    self, symbol: str, fill_price: float
) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias for engine partial-fill OCO seed."""
    return get_engine_partial_fill_oco_seed_impl(self, symbol=symbol, fill_price=fill_price)


def get_partial_fill_oco_seed_impl(
    self, symbol: str, fill_price: float, order_tag: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return OCO seed for partial fills across intraday and swing single-legs."""
    symbol_norm = self._symbol_str(symbol)
    if not symbol_norm:
        return None

    intraday_seed = self.get_engine_partial_fill_oco_seed(
        symbol=symbol_norm,
        fill_price=fill_price,
    )
    if intraday_seed is not None:
        return intraday_seed

    if (
        self._pending_contract is None
        or self._pending_intraday_entry
        or self._symbol_str(self._pending_contract.symbol) != symbol_norm
    ):
        inferred_strategy = self._infer_engine_strategy_from_order_tag(order_tag)
        if inferred_strategy == IntradayStrategy.NO_TRADE.value:
            return None
        entry_px = float(fill_price or 0.0)
        if entry_px <= 0:
            return None
        direction_hint = _infer_direction_hint(symbol_norm)
        target_pct, stop_pct = self._get_intraday_exit_profile(
            inferred_strategy,
            direction=direction_hint,
        )
        if stop_pct is None or float(stop_pct) <= 0 or float(target_pct) <= 0:
            return None
        self.log(
            f"OCO_PARTIAL_FALLBACK: Strategy={inferred_strategy} | Symbol={symbol_norm} | "
            f"Entry=${entry_px:.2f}",
            trades_only=True,
        )
        return {
            "entry_price": entry_px,
            "stop_price": entry_px * (1 - float(stop_pct)),
            "target_price": entry_px * (1 + float(target_pct)),
            "entry_strategy": inferred_strategy,
        }

    stop_price = float(getattr(self, "_pending_stop_price", 0.0) or 0.0)
    target_price = float(getattr(self, "_pending_target_price", 0.0) or 0.0)
    if stop_price <= 0 or target_price <= 0:
        return None

    entry_px = float(fill_price or 0.0)
    if entry_px <= 0:
        entry_px = float(getattr(self._pending_contract, "mid_price", 0.0) or 0.0)
    if entry_px <= 0:
        return None

    return {
        "entry_price": entry_px,
        "stop_price": stop_price,
        "target_price": target_price,
        "entry_strategy": str(self._pending_entry_strategy or "SWING_SINGLE"),
    }


def get_pending_engine_partial_oco_seed_impl(
    self, symbol: str, fill_price: float
) -> Optional[Dict[str, Any]]:
    """
    Build temporary OCO pricing for a pending intraday entry partial fill.

    Used when an entry order partially fills before full fill registration.
    """
    if fill_price is None or float(fill_price) <= 0:
        return None

    symbol_norm = self._symbol_str(symbol)
    if not symbol_norm:
        return None

    payload = self._get_pending_engine_entry_payload(symbol=symbol_norm)
    if payload is not None:
        entry_strategy = str(payload.get("entry_strategy") or "SWING_SINGLE")
        stop_pct = float(payload.get("stop_pct") if payload.get("stop_pct") is not None else 0.20)
        current_dte = 0
    else:
        if not self._pending_intraday_entry or self._pending_contract is None:
            return None
        pending_symbol = self._symbol_str(self._pending_contract.symbol)
        if symbol_norm != pending_symbol:
            return None
        entry_strategy = self._pending_entry_strategy or "SWING_SINGLE"
        stop_pct = self._pending_stop_pct if self._pending_stop_pct is not None else 0.20
        # current_dte set from pending payload fallback above.
    direction_hint = None
    if payload is not None:
        direction_hint = _infer_direction_hint(symbol_norm)
    elif self._pending_contract is not None:
        right = str(getattr(self._pending_contract, "right", "") or "").upper()
        direction_hint = "CALL" if right == "CALL" else "PUT" if right == "PUT" else None
        if direction_hint is None:
            direction_hint = _infer_direction_hint(symbol_norm)

    target_pct, strategy_floor = self._get_intraday_exit_profile(
        entry_strategy,
        direction=direction_hint,
    )
    if payload is not None:
        current_dte = 0
    else:
        current_dte = int(getattr(self._pending_contract, "days_to_expiry", 0))
    target_pct = self._apply_intraday_target_overrides(
        entry_strategy=entry_strategy,
        target_pct=float(target_pct),
        current_dte=current_dte,
    )
    stop_pct = self._apply_intraday_stop_overrides(
        entry_strategy=entry_strategy,
        stop_pct=float(stop_pct),
        current_dte=current_dte,
    )
    if strategy_floor is not None and strategy_floor > 0:
        stop_pct = max(float(stop_pct), float(strategy_floor))

    entry_px = float(fill_price)
    return {
        "entry_price": entry_px,
        "stop_price": entry_px * (1 - float(stop_pct)),
        "target_price": entry_px * (1 + float(target_pct)),
        "entry_strategy": entry_strategy,
    }


def get_pending_intraday_partial_oco_seed_impl(
    self, symbol: str, fill_price: float
) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias for pending engine partial OCO seed."""
    return get_pending_engine_partial_oco_seed_impl(self, symbol=symbol, fill_price=fill_price)
