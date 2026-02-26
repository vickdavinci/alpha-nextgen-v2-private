"""Pending-entry stale guard helpers for OptionsEngine."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from AlgorithmImports import *

import config


def clear_stale_pending_spread_entry_if_orphaned_impl(self) -> None:
    """Clear stale pending spread lock when no matching open leg orders exist."""
    if self._pending_spread_long_leg is None or self._pending_spread_short_leg is None:
        return
    if self.algorithm is None or not hasattr(self.algorithm, "Time"):
        return

    if self._pending_spread_entry_since is None:
        self._pending_spread_entry_since = self.algorithm.Time
        return

    stale_minutes = int(getattr(config, "SPREAD_PENDING_ENTRY_STALE_MINUTES", 7))
    if stale_minutes <= 0:
        return

    age_minutes = (self.algorithm.Time - self._pending_spread_entry_since).total_seconds() / 60.0
    if age_minutes < stale_minutes:
        return

    pending_symbols = {
        self._symbol_str(self._pending_spread_long_leg.symbol),
        self._symbol_str(self._pending_spread_short_leg.symbol),
    }
    pending_symbols = {s for s in pending_symbols if s}

    has_open_orders = False
    try:
        for open_order in self.algorithm.Transactions.GetOpenOrders():
            if getattr(open_order.Symbol, "SecurityType", None) != SecurityType.Option:
                continue
            open_sym = self._symbol_str(open_order.Symbol)
            if open_sym in pending_symbols:
                has_open_orders = True
                break
    except Exception:
        # Do not clear state when broker/order query fails.
        return

    if has_open_orders:
        return

    self.cancel_pending_spread_entry()
    self.log(
        f"OPT_MACRO_RECOVERY: Cleared stale pending spread entry | "
        f"AgeMin={age_minutes:.1f} | Pending={','.join(sorted(pending_symbols)) or 'NONE'}",
        trades_only=True,
    )


def clear_stale_pending_intraday_entry_if_orphaned_impl(self) -> None:
    """
    Clear stale pending intraday entry locks.

    Prevents long-lived E_INTRADAY_PENDING_ENTRY lock after missed/implicit
    broker cancel events while preserving normal in-flight order behavior.
    """
    if not self._pending_intraday_entries and not self._pending_intraday_entry:
        return
    if self.algorithm is None or not hasattr(self.algorithm, "Time"):
        return
    now = self.algorithm.Time

    if self._pending_intraday_entry_since is None:
        self._pending_intraday_entry_since = now
        return

    stale_minutes = int(getattr(config, "INTRADAY_PENDING_ENTRY_STALE_MINUTES", 5))
    if stale_minutes <= 0:
        return

    fast_clear_seconds = int(getattr(config, "INTRADAY_PENDING_ENTRY_FAST_CLEAR_SECONDS", 90))
    cancel_after_minutes = int(getattr(config, "INTRADAY_PENDING_ENTRY_CANCEL_MINUTES", 20))
    cancel_after_seconds = max(0, cancel_after_minutes * 60)
    hard_clear_minutes = int(getattr(config, "INTRADAY_PENDING_ENTRY_HARD_CLEAR_MINUTES", 60))

    # Normalize legacy single-pending fields into lane-keyed payloads.
    if not self._pending_intraday_entries and self._pending_intraday_entry:
        legacy_symbol = (
            self._symbol_key(self._pending_contract.symbol)
            if self._pending_contract is not None
            else ""
        )
        legacy_lane = str(self._pending_intraday_entry_engine or "MICRO").upper()
        if legacy_symbol:
            legacy_key = self._pending_intraday_entry_key(symbol=legacy_symbol, lane=legacy_lane)
            self._pending_intraday_entries[legacy_key] = {
                "symbol": legacy_symbol,
                "lane": legacy_lane,
                "entry_score": self._pending_entry_score,
                "num_contracts": self._pending_num_contracts,
                "entry_strategy": self._pending_entry_strategy,
                "stop_pct": self._pending_stop_pct,
                "created_at": self._pending_intraday_entry_since.strftime("%Y-%m-%d %H:%M:%S"),
            }

    open_entry_order_ids_by_symbol = {}
    scan_errors = 0
    open_orders = []
    try:
        open_orders = list(self.algorithm.Transactions.GetOpenOrders())
    except Exception:
        open_orders = []

    for open_order in open_orders:
        try:
            open_symbol = getattr(open_order, "Symbol", None)
            if open_symbol is None:
                continue
            if getattr(open_symbol, "SecurityType", None) != SecurityType.Option:
                continue
            # Ignore obvious close-path tags so they don't hold entry locks open.
            order_tag = str(getattr(open_order, "Tag", "") or "").upper()
            if (
                "OCO_" in order_tag
                or "FORCE_CLOSE" in order_tag
                or "INTRADAY_TIME_EXIT" in order_tag
                or "SPREAD_CLOSE" in order_tag
                or "RECON_ORPHAN" in order_tag
            ):
                continue
            order_qty = float(getattr(open_order, "Quantity", 0) or 0)
            if order_qty <= 0:
                # Entry-pending logic should ignore OCO stop/profit exits (negative qty).
                continue
            symbol_key = self._symbol_key(open_symbol)
            if not symbol_key:
                continue
            oid = getattr(open_order, "Id", None)
            if oid is None:
                oid = getattr(open_order, "OrderId", None)
            if oid is None:
                continue
            open_entry_order_ids_by_symbol.setdefault(symbol_key, []).append(int(oid))
        except Exception:
            scan_errors += 1
            continue

    def _parse_created_at(payload: Optional[Dict[str, Any]]) -> datetime:
        if isinstance(payload, dict):
            created_raw = payload.get("created_at")
            if isinstance(created_raw, datetime):
                return created_raw
            if isinstance(created_raw, str) and created_raw.strip():
                text = created_raw.strip()
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        return datetime.strptime(text[:19], fmt)
                    except Exception:
                        continue
        return self._pending_intraday_entry_since or now

    cleared_keys = []
    cancel_requests = 0
    max_age_minutes = 0.0
    for key, payload in list(self._pending_intraday_entries.items()):
        symbol_norm = (
            self._symbol_key(payload.get("symbol", "")) if isinstance(payload, dict) else ""
        )
        if not symbol_norm:
            symbol_norm = self._pending_intraday_symbol_from_key(key)
        if not symbol_norm:
            continue

        lane = str((payload or {}).get("lane", "")).upper() if isinstance(payload, dict) else ""
        if lane not in ("MICRO", "ITM") and "|" in str(key):
            lane = str(key).split("|", 1)[0].upper()
        lane_has_position = bool(self._intraday_positions.get(lane) or [])
        created_at = _parse_created_at(payload if isinstance(payload, dict) else None)
        age_seconds = max(0.0, (now - created_at).total_seconds())
        age_minutes = age_seconds / 60.0
        max_age_minutes = max(max_age_minutes, age_minutes)

        open_entry_order_ids = open_entry_order_ids_by_symbol.get(symbol_norm, [])
        if open_entry_order_ids:
            # Active entry order still live. Optionally cancel if it overstays.
            if (
                (not lane_has_position)
                and cancel_after_seconds > 0
                and age_seconds >= cancel_after_seconds
            ):
                for oid in open_entry_order_ids:
                    try:
                        self.algorithm.Transactions.CancelOrder(
                            oid,
                            f"INTRADAY_PENDING_TIMEOUT {age_minutes:.1f}m",
                        )
                        self.log(
                            f"INTRADAY_PENDING_TIMEOUT_CANCEL: Lane={lane or 'UNKNOWN'} | "
                            f"Symbol={symbol_norm} | OrderId={oid} | AgeMin={age_minutes:.1f}",
                            trades_only=True,
                        )
                        cancel_requests += 1
                    except Exception:
                        continue
            # Do not hard-clear while broker still reports open entry orders.
            # Clearing here can permit duplicate entries and mis-accounting on late fills.
            continue

        # If lane already has a live position, pending-entry lock is stale.
        if lane_has_position:
            self._pending_intraday_entries.pop(key, None)
            cleared_keys.append(key)
            continue

        # Orphan pending (no open entry order + no position): clear on fast/stale thresholds.
        should_clear = age_minutes >= stale_minutes
        if not should_clear and fast_clear_seconds > 0:
            should_clear = age_seconds >= fast_clear_seconds
        if not should_clear and hard_clear_minutes > 0:
            should_clear = age_minutes >= hard_clear_minutes
        if not should_clear:
            continue
        self._pending_intraday_entries.pop(key, None)
        cleared_keys.append(key)

    if not cleared_keys and cancel_requests <= 0:
        if scan_errors > 0:
            self.log(
                f"OPT_MICRO_RECOVERY: Pending scan errors | Count={scan_errors}",
                trades_only=True,
            )
        return

    self._pending_intraday_entry = bool(self._pending_intraday_entries)
    self._pending_intraday_entry_since = (
        None if not self._pending_intraday_entries else self._pending_intraday_entry_since
    )
    self._pending_intraday_entry_engine = (
        None if not self._pending_intraday_entries else self._pending_intraday_entry_engine
    )
    if not self._pending_intraday_entries:
        self._pending_contract = None
        self._pending_entry_score = None
        self._pending_num_contracts = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None
        self._pending_entry_strategy = None
    self.log(
        f"OPT_MICRO_RECOVERY: Pending entry maintenance | Cleared={len(cleared_keys)} | "
        f"CancelReq={cancel_requests} | MaxAgeMin={max_age_minutes:.1f} | ScanErr={scan_errors}",
        trades_only=True,
    )


def cancel_pending_intraday_entry_impl(
    self, engine: Optional[str] = None, symbol: Optional[str] = None
) -> Optional[str]:
    """
    Clear pending intraday entry state after broker rejection.
    """
    cleared_lane: Optional[str] = None
    if symbol is not None:
        key = self._find_pending_intraday_entry_key(symbol=symbol, lane=engine)
        if key is not None:
            payload = self._pending_intraday_entries.pop(key, None)
            if isinstance(payload, dict):
                lane = str(payload.get("lane", "")).upper()
                cleared_lane = lane or None
    elif engine is None:
        if self._pending_intraday_entries:
            lanes = {
                str(v.get("lane", "")).upper()
                for v in self._pending_intraday_entries.values()
                if isinstance(v, dict) and v.get("lane")
            }
            if len(lanes) == 1:
                cleared_lane = next(iter(lanes))
        self._pending_intraday_entries = {}
    else:
        eng = str(engine).upper()
        before = len(self._pending_intraday_entries)
        self._pending_intraday_entries = {
            k: v
            for k, v in self._pending_intraday_entries.items()
            if str(v.get("lane", "")).upper() != eng
        }
        if len(self._pending_intraday_entries) < before:
            cleared_lane = eng

    self._pending_intraday_entry = bool(self._pending_intraday_entries)
    self._pending_intraday_entry_since = (
        None if not self._pending_intraday_entries else self._pending_intraday_entry_since
    )
    self._pending_intraday_entry_engine = (
        None if not self._pending_intraday_entries else self._pending_intraday_entry_engine
    )
    if not self._pending_intraday_entries:
        self._pending_contract = None
        self._pending_entry_score = None
        self._pending_num_contracts = None
        self._pending_stop_pct = None
        self._pending_stop_price = None
        self._pending_target_price = None
        self._pending_entry_strategy = None
    self.log(
        "OPT_MICRO_RECOVERY: Pending intraday entry cancelled | Retry allowed",
        trades_only=True,
    )
    return cleared_lane


def has_pending_intraday_entry_impl(self, engine: Optional[str] = None) -> bool:
    """True when an intraday entry is currently pending."""
    if self._pending_intraday_entry or self._pending_intraday_entries:
        self._clear_stale_pending_intraday_entry_if_orphaned()
    if engine is None:
        return bool(self._pending_intraday_entries) or self._pending_intraday_entry
    eng = str(engine).upper()
    for payload in self._pending_intraday_entries.values():
        if str(payload.get("lane", "")).upper() == eng:
            return True
    return (
        self._pending_intraday_entry and (self._pending_intraday_entry_engine or "").upper() == eng
    )


def get_pending_intraday_entry_lane_impl(self, symbol: Optional[str] = None) -> Optional[str]:
    """Best-effort lane lookup for a pending intraday entry."""
    if symbol is not None:
        key = self._find_pending_intraday_entry_key(symbol=symbol)
        if key is None:
            return None
        payload = self._pending_intraday_entries.get(key) or {}
        lane = str(payload.get("lane", "")).upper()
        if lane:
            return lane
        return None
    if self._pending_intraday_entries:
        try:
            payload = next(iter(self._pending_intraday_entries.values()))
            lane = str(payload.get("lane", "")).upper() if isinstance(payload, dict) else ""
            if lane:
                return lane
        except Exception:
            pass
    if self._pending_intraday_entry_engine:
        lane = str(self._pending_intraday_entry_engine).upper()
        return lane or None
    return None


def get_pending_entry_contract_symbol_impl(self) -> str:
    """Best-effort symbol for current pending single-leg entry contract."""
    if self._pending_intraday_entries:
        try:
            payload = next(iter(self._pending_intraday_entries.values()))
            if isinstance(payload, dict):
                sym = self._symbol_str(payload.get("symbol", ""))
                if sym:
                    return sym
            key = next(iter(self._pending_intraday_entries.keys()))
            return self._pending_intraday_symbol_from_key(key)
        except Exception:
            return ""
    if self._pending_contract is None:
        return ""
    try:
        return self._symbol_str(self._pending_contract.symbol)
    except Exception:
        return ""


def normalize_symbol_key_impl(self, symbol: Optional[str]) -> Optional[str]:
    sym = self._symbol_str(symbol) if symbol else ""
    return sym or None


def sync_pending_intraday_exit_flags_impl(self) -> None:
    active = bool(self._pending_intraday_exit_lanes) or bool(self._pending_intraday_exit_symbols)
    self._pending_intraday_exit = active
    if not active:
        self._pending_intraday_exit_engine = None


def has_pending_intraday_exit_impl(
    self, engine: Optional[str] = None, symbol: Optional[str] = None
) -> bool:
    """True when an intraday close signal has already been emitted and is in-flight."""
    symbol_key = self._normalize_symbol_key(symbol)
    if symbol_key is not None:
        return symbol_key in self._pending_intraday_exit_symbols

    if engine is None:
        return (
            bool(self._pending_intraday_exit_symbols)
            or bool(self._pending_intraday_exit_lanes)
            or self._pending_intraday_exit
        )
    eng = str(engine).upper()
    return eng in self._pending_intraday_exit_lanes or (
        self._pending_intraday_exit and (self._pending_intraday_exit_engine or "").upper() == eng
    )


def mark_pending_intraday_exit_impl(self, symbol: Optional[str] = None) -> bool:
    """
    Mark intraday close as pending to block duplicate software/force exits.
    """
    symbol_key = self._normalize_symbol_key(symbol)
    if symbol_key is not None:
        if self._find_intraday_lane_by_symbol(symbol_key) is None:
            return False
        if symbol_key in self._pending_intraday_exit_symbols:
            return False
        self._pending_intraday_exit_symbols.add(symbol_key)
        self._sync_pending_intraday_exit_flags()
        return True

    target_lane = None
    if self._pending_intraday_exit_engine:
        target_lane = str(self._pending_intraday_exit_engine).upper()
    else:
        target_lane = self.get_intraday_position_engine()
        if target_lane is None:
            return False

    lane_key = str(target_lane).upper()
    if lane_key in self._pending_intraday_exit_lanes:
        return False
    self._pending_intraday_exit_engine = target_lane
    self._pending_intraday_exit_lanes.add(lane_key)
    self._sync_pending_intraday_exit_flags()
    return True


def cancel_pending_intraday_exit_impl(self, symbol: Optional[str] = None) -> bool:
    """
    Clear pending intraday exit lock after a rejected/canceled close order.
    """
    symbol_key = self._normalize_symbol_key(symbol)
    if symbol_key is not None:
        if symbol_key not in self._pending_intraday_exit_symbols:
            return False
        self._pending_intraday_exit_symbols.discard(symbol_key)
        self._sync_pending_intraday_exit_flags()
        self.log(
            f"OPT_MICRO_RECOVERY: Pending intraday exit lock cleared | Symbol={symbol_key}",
            trades_only=True,
        )
        return True

    if (
        not self._pending_intraday_exit_lanes
        and not self._pending_intraday_exit_symbols
        and not self._pending_intraday_exit
    ):
        return False

    self._pending_intraday_exit_lanes.clear()
    self._pending_intraday_exit_symbols.clear()
    self._sync_pending_intraday_exit_flags()
    self.log("OPT_MICRO_RECOVERY: Pending intraday exit lock cleared", trades_only=True)
    return True
