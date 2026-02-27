from typing import Any

from AlgorithmImports import *

import config
from engines.satellite.options_engine import ExitOrderTracker, SpreadFillTracker
from models.enums import Urgency
from models.target_weight import TargetWeight


class MainOrdersMixin:
    """Large order/fill lifecycle methods extracted from main.py (move-only)."""

    def _is_terminal_exit_retry_tag(self, tag_text: str) -> bool:
        """
        Identify close-order tags that should never enter retry->emergency recursion.

        These tags already represent forced-risk actions (assignment/emergency/orphan).
        If they fail, we log and defer to reconciliation instead of recursively
        resubmitting from inside OnOrderEvent.
        """
        tag_u = str(tag_text or "").upper()
        if not tag_u:
            return False
        terminal_markers = (
            "EMERG_OPTION_RETRY_EXHAUSTED",
            "EMERG_",
            "PARTIAL_ASSIGNMENT",
            "ORPHAN_",
            "RECON_ORPHAN_OPTION",
            "KILL_SWITCH",
            "KS_",
            "MARGIN_CB",
            "EXERCISE_",
        )
        return any(marker in tag_u for marker in terminal_markers)

    def _cancel_residual_option_orders(self, symbol: Symbol, reason: str = "") -> int:
        """Cancel residual same-symbol non-OCO open orders once position is flat."""
        try:
            symbol_norm = self._normalize_symbol_str(symbol)
            canceled = 0
            for order in list(self.Transactions.GetOpenOrders()):
                try:
                    order_norm = self._normalize_symbol_str(order.Symbol)
                    if order_norm != symbol_norm:
                        continue
                    tag = str(getattr(order, "Tag", "") or "")
                    tag_u = tag.upper()
                    # Keep OCO rails and spread-managed combo legs untouched here.
                    if tag_u.startswith("OCO_STOP:") or tag_u.startswith("OCO_PROFIT:"):
                        continue
                    if "SPREAD_CLOSE" in tag_u or "SPREAD_CLOSE_RETRY" in tag_u:
                        continue
                    self.Transactions.CancelOrder(order.Id)
                    canceled += 1
                except Exception:
                    continue
            if canceled > 0:
                self.Log(
                    f"RESIDUAL_ORDER_CANCEL: {symbol_norm} | Canceled={canceled} | Reason={reason or 'NA'}"
                )
            return canceled
        except Exception as e:
            self.Log(f"RESIDUAL_ORDER_CANCEL_ERROR: {symbol} | {e}")
            return 0

    def _on_moo_fallback(self) -> None:
        """
        MOO fallback check at 09:31 ET.

        Checks if MOO orders failed to execute and converts them to market orders.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return

        results = self.execution_engine.check_moo_fallbacks()
        for result in results:
            if result.get("success"):
                self.Log(f"MOO_FALLBACK: Order {result.get('order_id')} fallback submitted")
            else:
                self.Log(
                    f"MOO_FALLBACK: Order {result.get('order_id')} fallback failed - "
                    f"{result.get('error')}"
                )

    def _cleanup_stale_orders(self) -> None:
        """
        V3.0 P2: Clean up stale orders at start of logic cycle.

        Cancels orders that have been pending for more than 5 minutes to prevent
        orphaned orders from previous failed cycles from interfering with new logic.
        Runs every 5 minutes to avoid excessive API calls.
        """
        # Rate limit: only run every 5 minutes
        if not hasattr(self, "_last_stale_order_check"):
            self._last_stale_order_check = None

        if self._last_stale_order_check is not None:
            minutes_since_check = (self.Time - self._last_stale_order_check).total_seconds() / 60
            if minutes_since_check < 5:
                return  # Too soon, skip

        self._last_stale_order_check = self.Time

        # Get all open orders and cancel those older than 5 minutes
        try:
            open_orders = list(self.Transactions.GetOpenOrders())
            if not open_orders:
                return

            stale_count = 0
            for order in open_orders:
                # V3.0 FIX: Handle timezone-aware vs naive datetime comparison
                try:
                    order_age_minutes = (self.Time - order.Time).total_seconds() / 60
                except TypeError:
                    # If timezone mismatch, use naive comparison
                    order_age_minutes = (
                        self.Time.replace(tzinfo=None) - order.Time.replace(tzinfo=None)
                    ).total_seconds() / 60
                if order_age_minutes > 5:
                    order_tag = str(getattr(order, "Tag", "") or "")
                    # Keep protective OCO orders alive; they are long-lived by design.
                    if "OCO_STOP:" in order_tag or "OCO_PROFIT:" in order_tag:
                        continue
                    try:
                        self.Transactions.CancelOrder(order.Id)
                        stale_count += 1
                    except Exception as e:
                        self.Log(f"STALE_CLEANUP: Failed to cancel order {order.Id} | {e}")

            if stale_count > 0:
                self.Log(f"STALE_CLEANUP: Cancelled {stale_count} orders older than 5 minutes")

        except Exception as e:
            self.Log(f"STALE_CLEANUP: Error checking orders | {e}")

    def _oco_engine_prefix_for_strategy(self, entry_strategy: str) -> str:
        """Map strategy tag to stable engine prefix for OCO attribution."""
        strategy = str(entry_strategy or "UNKNOWN").upper()
        if strategy == "ITM_MOMENTUM":
            return "ITM"
        if strategy == "PROTECTIVE_PUTS":
            return "MICRO"
        if strategy.startswith("MICRO_") or strategy in (
            "DEBIT_FADE",
            "OTM_MOMENTUM",
            "INTRADAY_DEBIT_FADE",
        ):
            return "MICRO"
        return "OPT"

    def _record_order_tag_map(self, order_id: int, symbol: str, tag: str, source: str) -> None:
        """Emit deterministic order-id -> tag mapping for RCA even when broker CSV drops tags."""
        try:
            oid = int(order_id or 0)
        except Exception:
            oid = 0
        clean_tag = str(tag or "").strip()
        if oid <= 0 or not clean_tag:
            return
        if oid in self._order_tag_map_logged_ids:
            return
        self._order_tag_map_logged_ids.add(oid)
        if len(self._order_tag_map_logged_ids) > 50000:
            self._order_tag_map_logged_ids.clear()
        trace_id = self._extract_trace_id_from_tag(clean_tag) or "NONE"
        sym = self._normalize_symbol_str(symbol)
        self.Log(
            f"ORDER_TAG_MAP: OrderId={oid} | Symbol={sym or symbol} | Source={source} | "
            f"Tag={self._compact_tag_for_log(clean_tag)} | Trace={trace_id}"
        )

    def _record_order_tag_resolve(
        self,
        order_id: int,
        symbol: str,
        resolved_tag: str,
        source: str,
    ) -> None:
        """Log how lifecycle events resolve tags (event/order/oco/cache/symbol-cache)."""
        try:
            oid = int(order_id or 0)
        except Exception:
            oid = 0
        clean_tag = str(resolved_tag or "").strip()
        if oid <= 0 or not clean_tag:
            return
        if oid in self._order_tag_resolve_logged_ids:
            return
        self._order_tag_resolve_logged_ids.add(oid)
        if len(self._order_tag_resolve_logged_ids) > 50000:
            self._order_tag_resolve_logged_ids.clear()
        trace_id = self._extract_trace_id_from_tag(clean_tag) or "NONE"
        sym = self._normalize_symbol_str(symbol)
        self.Log(
            f"ORDER_TAG_RESOLVE: OrderId={oid} | Symbol={sym or symbol} | Source={source} | "
            f"Tag={self._compact_tag_for_log(clean_tag)} | Trace={trace_id}"
        )

    def _record_order_lifecycle_event(
        self,
        status: str,
        order_id: int,
        symbol: str,
        quantity: int = 0,
        fill_price: float = 0.0,
        order_type: str = "",
        order_tag: str = "",
        trace_id: str = "",
        message: str = "",
        source: str = "",
    ) -> None:
        """Persist order lifecycle records independent of console log budget."""
        if not bool(getattr(config, "ORDER_LIFECYCLE_OBSERVABILITY_ENABLED", True)):
            return
        max_rows = int(getattr(config, "ORDER_LIFECYCLE_OBSERVABILITY_MAX_ROWS", 50000))
        self._append_observability_record(
            records=self._order_lifecycle_records,
            overflow_attr="_order_lifecycle_overflow_logged",
            max_rows=max_rows,
            overflow_log_prefix="ORDER_LIFECYCLE_OBSERVABILITY",
            row={
                "time": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": str(status or "").upper(),
                "order_id": str(int(order_id or 0)),
                "symbol": str(symbol or ""),
                "quantity": str(int(quantity or 0)),
                "fill_price": f"{float(fill_price or 0.0):.6f}",
                "order_type": str(order_type or ""),
                "order_tag": str(order_tag or ""),
                "trace_id": str(trace_id or ""),
                "message": str(message or ""),
                "source": str(source or ""),
            },
        )

    def _get_order_tag(self, order_event: OrderEvent) -> str:
        """Best-effort extraction of original order tag for fill classification."""
        order_id = int(getattr(order_event, "OrderId", 0) or 0)
        symbol = str(getattr(order_event, "Symbol", "") or "")

        event_tag = str(getattr(order_event, "Tag", "") or "").strip()
        if event_tag:
            self._cache_order_tag_hint(order_id, event_tag)
            self._record_order_tag_resolve(order_id, symbol, event_tag, "event")
            return event_tag

        try:
            order = self.Transactions.GetOrderById(order_event.OrderId)
            if order and getattr(order, "Tag", None):
                order_tag = str(order.Tag).strip()
                if order_tag:
                    self._cache_order_tag_hint(order_id, order_tag)
                    self._record_order_tag_resolve(order_id, symbol, order_tag, "order")
                    return order_tag
        except Exception:
            pass

        try:
            if self.oco_manager is not None:
                hinted = self.oco_manager.get_order_tag_hint(order_id)
                if hinted:
                    self._cache_order_tag_hint(order_id, hinted)
                    self._record_order_tag_resolve(order_id, symbol, hinted, "oco")
                    return hinted
        except Exception:
            pass

        cached = self._order_tag_hint_cache.get(order_id, "")
        if cached:
            self._record_order_tag_resolve(order_id, symbol, cached, "cache")
            return cached

        # Last-resort fallback for broker events that drop order tags on cancel/fill.
        symbol_hint = self._get_recent_symbol_fill_tag(symbol, max_age_minutes=480)
        if symbol_hint:
            self._record_order_tag_resolve(order_id, symbol, symbol_hint, "symbol_cache")
            return symbol_hint
        return ""

    def _cache_order_tag_hint(self, order_id: int, tag: str) -> None:
        """Cache order tag hints for lifecycle attribution when broker tags are blank."""
        if order_id <= 0:
            return
        clean = str(tag or "").strip()
        if not clean:
            return
        self._order_tag_hint_cache[order_id] = clean
        if len(self._order_tag_hint_cache) > 25000:
            self._order_tag_hint_cache.clear()

    def _cache_symbol_fill_tag(self, symbol: str, tag: str) -> None:
        """Cache last non-empty fill tag per option symbol for telemetry fallback/reconcile guards."""
        sym = self._normalize_symbol_str(symbol)
        clean = str(tag or "").strip()
        if not sym or not clean:
            return
        self._last_option_fill_tag_by_symbol[sym] = clean
        self._last_option_fill_time_by_symbol[sym] = self.Time
        if len(self._last_option_fill_tag_by_symbol) > 5000:
            self._last_option_fill_tag_by_symbol.clear()
            self._last_option_fill_time_by_symbol.clear()

    def _get_recent_symbol_fill_tag(self, symbol: str, max_age_minutes: int = 240) -> str:
        """Return last cached fill tag for symbol when fresh enough."""
        sym = self._normalize_symbol_str(symbol)
        if not sym:
            return ""
        tag = str(self._last_option_fill_tag_by_symbol.get(sym, "") or "").strip()
        ts = self._last_option_fill_time_by_symbol.get(sym)
        if not tag or ts is None:
            return ""
        try:
            age = (self.Time - ts).total_seconds() / 60.0
            if age > float(max_age_minutes):
                return ""
        except Exception:
            return ""
        return tag

    def _extract_trace_id_from_tag(self, order_tag: str) -> str:
        """Extract trace id from order tag (best-effort) for RCA joins."""
        if not order_tag:
            return ""
        tag = str(order_tag)
        markers = ("trace=", "trace_id=", "trace:")
        lowered = tag.lower()
        for marker in markers:
            idx = lowered.find(marker)
            if idx < 0:
                continue
            value = tag[idx + len(marker) :].strip()
            if not value:
                return ""
            for sep in ("|", ";", ",", " "):
                cut = value.find(sep)
                if cut >= 0:
                    value = value[:cut]
                    break
            return value.strip()
        return ""

    def _compact_tag_for_log(self, order_tag: str, max_chars: int = 64) -> str:
        """Trim noisy broker tags to keep logs under budget while preserving correlation."""
        tag = str(order_tag or "").strip()
        if not tag:
            return "NO_TAG"
        if len(tag) <= max_chars:
            return tag
        return f"{tag[:max_chars]}..."

    def _sync_engine_oco(
        self,
        symbol: str,
        position: Any,
        quantity: int,
        reason: str,
    ) -> None:
        """
        Ensure exactly one active OCO pair is live for an intraday MICRO symbol.

        Called on entry and partial-close so remaining contracts stay protected.
        """
        try:
            symbol_norm = self._normalize_symbol_str(symbol)
            qty = int(max(0, quantity))
            if not symbol_norm:
                self.Log(f"OCO_SYNC_SKIP: Invalid symbol | Raw={symbol} | Reason={reason}")
                return
            if qty <= 0:
                self.Log(
                    f"OCO_SYNC_SKIP: Non-positive quantity | Symbol={symbol_norm} | Qty={qty} | Reason={reason}"
                )
                return
            if position is None:
                self.Log(
                    f"OCO_SYNC_SKIP: Missing position seed | Symbol={symbol_norm} | Reason={reason}"
                )
                return
            entry_price = float(getattr(position, "entry_price", 0.0) or 0.0)
            stop_price = float(getattr(position, "stop_price", 0.0) or 0.0)
            target_price = float(getattr(position, "target_price", 0.0) or 0.0)
            entry_strategy = str(getattr(position, "entry_strategy", "UNKNOWN") or "UNKNOWN")
            if isinstance(position, dict):
                entry_price = float(position.get("entry_price", entry_price) or 0.0)
                stop_price = float(position.get("stop_price", stop_price) or 0.0)
                target_price = float(position.get("target_price", target_price) or 0.0)
                entry_strategy = str(position.get("entry_strategy", entry_strategy) or "UNKNOWN")
            if entry_price <= 0 or stop_price <= 0 or target_price <= 0:
                self.Log(
                    f"OCO_SYNC_SKIP: Invalid OCO prices | Symbol={symbol_norm} | "
                    f"Entry={entry_price:.2f} Stop={stop_price:.2f} Target={target_price:.2f} | "
                    f"Reason={reason}"
                )
                return

            active_pair = self.oco_manager.get_active_pair(symbol_norm)
            if active_pair is not None:
                active_qty = abs(int(getattr(active_pair.stop_leg, "quantity", 0) or 0))
                active_stop = float(getattr(active_pair.stop_leg, "trigger_price", 0.0) or 0.0)
                active_target = float(getattr(active_pair.profit_leg, "trigger_price", 0.0) or 0.0)
                price_eps = float(getattr(config, "OCO_RESYNC_PRICE_EPS", 0.01))
                qty_same = active_qty == qty
                stop_same = abs(active_stop - stop_price) <= price_eps
                target_same = abs(active_target - target_price) <= price_eps
                if qty_same and stop_same and target_same:
                    return
                self.oco_manager.cancel_by_symbol(symbol_norm, reason=f"OCO_RESYNC_{reason}")
                self.Log(
                    f"OCO_RESYNC: Cancelled stale OCO | {symbol_norm} | "
                    f"OldQty={active_qty} NewQty={qty} | "
                    f"OldStop=${active_stop:.2f} NewStop=${stop_price:.2f} | "
                    f"OldTarget=${active_target:.2f} NewTarget=${target_price:.2f} | "
                    f"Reason={reason}"
                )

            entry_tag_hint = ""
            if isinstance(position, dict):
                entry_tag_hint = str(position.get("entry_tag", "") or "")
            if not entry_tag_hint:
                entry_tag_hint = self._get_recent_symbol_fill_tag(symbol_norm, max_age_minutes=240)
            trace_id = self._extract_trace_id_from_tag(entry_tag_hint)
            engine_prefix = self._oco_engine_prefix_for_strategy(entry_strategy)
            tag_context = f"{engine_prefix}:{entry_strategy}"
            if trace_id:
                tag_context = f"{tag_context}|trace={trace_id}"

            oco_pair = self.oco_manager.create_oco_pair(
                symbol=symbol_norm,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                quantity=qty,
                current_date=str(self.Time.date()),
                tag_context=tag_context,
            )
            if oco_pair and self.oco_manager.submit_oco_pair(oco_pair, current_time=str(self.Time)):
                self.Log(
                    f"OCO_SYNC: {reason} | {symbol_norm} | Qty={qty} | "
                    f"Stop=${stop_price:.2f} | "
                    f"Target=${target_price:.2f}"
                )
        except Exception as e:
            self.Log(f"OCO_SYNC_ERROR: {symbol} | Reason={reason} | {e}")

    def _sync_intraday_oco(
        self,
        symbol: str,
        position: Any,
        quantity: int,
        reason: str,
    ) -> None:
        """Backward-compatible alias for engine OCO sync helper."""
        self._sync_engine_oco(
            symbol=symbol,
            position=position,
            quantity=quantity,
            reason=reason,
        )

    def OnOrderEvent(self, orderEvent: OrderEvent) -> None:
        """
        Handle order status changes.

        Processes filled orders:
            - Logs trade details
            - Tracks trade for daily summary
            - Updates position tracking
            - Forwards to execution engine

        Handles rejected orders:
            - Logs rejection reason
            - Notifies execution engine

        Args:
            orderEvent: Order event from QuantConnect.
        """
        # V2.6 Bug #2: Handle partial fills for spread orders
        if orderEvent.Status == OrderStatus.PartiallyFilled:
            symbol = str(orderEvent.Symbol)
            fill_price = orderEvent.FillPrice
            fill_qty = orderEvent.FillQuantity

            order_tag = self._get_order_tag(orderEvent)
            if not order_tag and fill_qty < 0:
                # Backfill blank broker exit tags from recent symbol tag cache so exit attribution remains analyzable.
                order_tag = self._get_recent_symbol_fill_tag(symbol)
                if order_tag:
                    self.Log(
                        f"EXIT_TAG_BACKFILL: {symbol[-20:]} | OrderId={orderEvent.OrderId} | Tag={self._compact_tag_for_log(order_tag)}"
                    )
            order_type = "UNKNOWN"
            try:
                order = self.Transactions.GetOrderById(orderEvent.OrderId)
                if order is not None:
                    order_type = str(getattr(order, "Type", "UNKNOWN"))
            except Exception:
                pass
            trace_id = self._extract_trace_id_from_tag(order_tag) or "NONE"
            compact_tag = self._compact_tag_for_log(order_tag)
            if order_tag:
                self._cache_symbol_fill_tag(symbol, order_tag)

            self.Log(
                f"PARTIAL_FILL: {symbol[-20:]} | Qty={fill_qty} @ ${fill_price:.2f} | "
                f"Remaining={orderEvent.Quantity - orderEvent.FillQuantity} | "
                f"OrderId={orderEvent.OrderId} | Type={order_type} | "
                f"Tag={compact_tag} | Trace={trace_id}"
            )
            self._record_order_lifecycle_event(
                status="PARTIALLY_FILLED",
                order_id=orderEvent.OrderId,
                symbol=symbol,
                quantity=int(fill_qty),
                fill_price=float(fill_price),
                order_type=order_type,
                order_tag=order_tag,
                trace_id=trace_id,
                message=str(getattr(orderEvent, "Message", "") or ""),
                source="ON_ORDER_EVENT",
            )

            # Route partial fills to spread handler if applicable
            if "QQQ" in symbol and ("C" in symbol or "P" in symbol):
                symbol_norm = self._normalize_symbol_str(symbol)
                if self._is_spread_fill_symbol(symbol):
                    # This is a spread fill - accumulate in tracker
                    self._handle_spread_leg_fill(symbol, fill_price, abs(fill_qty))
                else:
                    live_qty = abs(self._get_option_holding_quantity(symbol_norm))
                    sync_qty = live_qty if live_qty > 0 else abs(int(fill_qty))
                    if sync_qty > 0:
                        oco_seed = self.options_engine.get_partial_fill_oco_seed(
                            symbol=symbol_norm,
                            fill_price=float(fill_price),
                            order_tag=order_tag,
                        )
                        if oco_seed is not None:
                            self._sync_engine_oco(
                                symbol=symbol_norm,
                                position=oco_seed,
                                quantity=sync_qty,
                                reason="PARTIAL_FILL",
                            )

            # Notify execution engine only if the order originated there.
            self._forward_execution_event(
                order_event=orderEvent,
                status="PartiallyFilled",
                fill_price=fill_price,
                fill_quantity=fill_qty,
            )
            self._save_state_throttled("ORDER_PARTIAL_FILL", min_minutes=3)
            return  # Don't fall through to other handlers

        if orderEvent.Status == OrderStatus.Filled:
            symbol = str(orderEvent.Symbol)
            fill_price = orderEvent.FillPrice
            fill_qty = orderEvent.FillQuantity
            direction = "BUY" if fill_qty > 0 else "SELL"

            order_tag = self._get_order_tag(orderEvent)
            if not order_tag and fill_qty < 0:
                # Backfill blank broker exit tags from recent symbol tag cache.
                order_tag = self._get_recent_symbol_fill_tag(symbol)
                if order_tag:
                    self.Log(
                        f"EXIT_TAG_BACKFILL: {symbol[-20:]} | OrderId={orderEvent.OrderId} | Tag={self._compact_tag_for_log(order_tag)}"
                    )
            order_type = "UNKNOWN"
            try:
                order = self.Transactions.GetOrderById(orderEvent.OrderId)
                if order is not None:
                    order_type = str(getattr(order, "Type", "UNKNOWN"))
            except Exception:
                pass
            trace_id = self._extract_trace_id_from_tag(order_tag) or "NONE"
            compact_tag = self._compact_tag_for_log(order_tag)
            if order_tag:
                self._cache_symbol_fill_tag(symbol, order_tag)
            self.Log(
                f"FILL: {direction} {abs(fill_qty)} {symbol} @ ${fill_price:.2f} | "
                f"OrderId={orderEvent.OrderId} | Type={order_type} | "
                f"Tag={compact_tag} | Trace={trace_id}"
            )
            self._record_order_lifecycle_event(
                status="FILLED",
                order_id=orderEvent.OrderId,
                symbol=symbol,
                quantity=int(fill_qty),
                fill_price=float(fill_price),
                order_type=order_type,
                order_tag=order_tag,
                trace_id=trace_id,
                message=str(getattr(orderEvent, "Message", "") or ""),
                source="ON_ORDER_EVENT",
            )

            # V2.14 Fix #12: SLIPPAGE_EXCEEDED check
            # Compare fill price to expected market price (bid for sells, ask for buys)
            try:
                security = self.Securities[orderEvent.Symbol]
                if fill_qty > 0:  # BUY - compare to ask
                    expected_price = security.AskPrice
                else:  # SELL - compare to bid
                    expected_price = security.BidPrice

                if expected_price > 0:
                    slippage_pct = abs(fill_price - expected_price) / expected_price
                    if slippage_pct > config.SLIPPAGE_BUFFER_PCT:
                        self.Log(
                            f"SLIPPAGE_EXCEEDED: {symbol} | "
                            f"Expected=${expected_price:.2f} Actual=${fill_price:.2f} | "
                            f"Slippage={slippage_pct:.2%} > {config.SLIPPAGE_BUFFER_PCT:.0%}"
                        )
            except Exception:
                pass  # Skip slippage check if price lookup fails

            # Track trade for daily summary
            trade_desc = f"{direction} {abs(fill_qty)} {symbol} @ ${fill_price:.2f}"
            self.today_trades.append(trade_desc)

            # Update position tracking
            self._on_fill(symbol, fill_price, fill_qty, orderEvent)

            # V2.1: Check if this is an OCO order fill
            if self.oco_manager.has_order(orderEvent.OrderId):
                self.oco_manager.on_order_fill(
                    broker_order_id=orderEvent.OrderId,
                    fill_price=fill_price,
                    fill_quantity=fill_qty,
                    fill_time=str(self.Time),
                )

            # Forward to execution engine only for mapped orders.
            self._forward_execution_event(
                order_event=orderEvent,
                status="Filled",
                fill_price=fill_price,
                fill_quantity=fill_qty,
            )

            # V2.3.6: Clean up spread tracking when short leg fills successfully
            if symbol in self._pending_spread_orders:
                long_leg = self._pending_spread_orders.pop(symbol)
                self._pending_spread_orders_reverse.pop(long_leg, None)
                self.Log(f"SPREAD: Both legs filled successfully | Short={symbol} Long={long_leg}")
                # Entry-leg tracking cleanup only.
                # Do NOT clear live spread state here; spread was just opened and must remain
                # tracked for exits/diagnostics. Clearing it here causes ghost/orphan churn.
                self.Log("SPREAD: Entry tracking map cleared after paired fill")

            # V2.4.1 FIX #8: Kill switch check on options fills
            # Kill switch may trip between signal generation and fill.
            # If active, immediately liquidate the new options position.
            # V2.4.2 FIX: Only for OPENING buys, not closing buys (buying back shorts)
            is_option = orderEvent.Symbol.SecurityType == SecurityType.Option
            if is_option and fill_qty > 0:  # BUY fills
                # Check current position AFTER fill to determine if this was opening or closing
                current_position = self.Portfolio[orderEvent.Symbol].Quantity
                # If position is now positive, this was an opening buy (new long)
                # If position is 0 or negative, this was a closing buy (covering short)
                is_opening_trade = current_position > 0

                if is_opening_trade and self.risk_engine.is_kill_switch_active():
                    self.Log(
                        f"KILL_SWITCH_ON_FILL: Options position opened while kill switch active | "
                        f"{symbol} x{fill_qty} @ ${fill_price:.2f} | LIQUIDATING IMMEDIATELY"
                    )
                    # Immediately liquidate the options position
                    self._submit_option_close_market_order(
                        symbol=orderEvent.Symbol,
                        quantity=-fill_qty,
                        reason="KILL_SWITCH_ON_FILL",
                        tag_hint=order_tag,
                    )

            # V2.25 Fix #1: Exercise/Assignment Detection
            # Was unreachable dead code (elif after if Filled:). Moved inside.
            # QC backtester uses "Simulated option assignment" not "Exercise".
            try:
                order = self.Transactions.GetOrderById(orderEvent.OrderId)
                is_exercise = order.Type == OrderType.OptionExercise
            except Exception:
                is_exercise = False
            if not is_exercise:
                msg_lower = str(orderEvent.Message).lower()
                is_exercise = "exercise" in msg_lower or "assignment" in msg_lower
            if is_exercise:
                partial_signals = self.options_engine.handle_partial_assignment(
                    symbol, abs(fill_qty)
                )
                if partial_signals:
                    sanitized_signals = []
                    for signal in partial_signals:
                        try:
                            signal_symbol = self._normalize_symbol_str(signal.symbol)
                            live_qty = abs(int(self._get_option_holding_quantity(signal_symbol)))
                        except Exception:
                            live_qty = 0
                        if live_qty <= 0:
                            self.Log(
                                f"PARTIAL_ASSIGNMENT_SKIP_NO_HOLDING: {signal.symbol} | "
                                "Reason=No live orphan leg quantity"
                            )
                            continue
                        signal.requested_quantity = live_qty
                        sanitized_signals.append(signal)
                    if sanitized_signals:
                        self.portfolio_router.receive_signals(sanitized_signals)
                        self.Log(
                            f"PARTIAL_ASSIGNMENT_SUBMITTED: {symbol} | "
                            f"Signals={len(sanitized_signals)}"
                        )
                    else:
                        self.Log(
                            f"PARTIAL_ASSIGNMENT_SKIP: {symbol} | "
                            "Reason=No tradable orphan leg after live-qty check"
                        )

                self.Log(
                    f"EXERCISE_DETECTED: {symbol} | Qty={fill_qty} | "
                    f"Msg='{orderEvent.Message}' | "
                    f"CRITICAL: Option exercise/assignment detected"
                )
                qqq_holding = self.Portfolio[self.qqq]
                if qqq_holding.Invested:
                    # V6.4 Fix: Check for protective long PUT before liquidating
                    # If we have a spread with a long PUT that's ITM, exercise it instead
                    # of blindly liquidating QQQ at market price
                    spread = self.options_engine.get_spread_position()
                    exercised_long_put = False

                    if spread is not None and "PUT" in spread.spread_type.upper():
                        # We have a PUT spread - check if long PUT can offset
                        long_leg = spread.long_leg
                        qqq_price = self.Securities[self.qqq].Price
                        long_strike = long_leg.strike

                        # Long PUT is ITM if QQQ price < strike
                        if qqq_price < long_strike:
                            # Exercise the long PUT to sell QQQ at strike price
                            # This is better than liquidating at market when long PUT is ITM
                            try:
                                long_symbol = self.Symbol(long_leg.symbol)
                                long_holding = self.Portfolio.get(long_symbol)
                                if (
                                    long_holding
                                    and long_holding.Invested
                                    and long_holding.Quantity > 0
                                ):
                                    exercise_qty = min(
                                        int(long_holding.Quantity),
                                        abs(int(qqq_holding.Quantity / 100)),
                                    )
                                    if exercise_qty > 0:
                                        self.Log(
                                            f"EXERCISE_LONG_PUT: Exercising protective PUT instead of market liquidation | "
                                            f"Strike=${long_strike:.2f} vs Market=${qqq_price:.2f} | "
                                            f"Benefit=${(long_strike - qqq_price) * exercise_qty * 100:,.2f} | "
                                            f"Qty={exercise_qty}"
                                        )
                                        self.ExerciseOption(long_symbol, exercise_qty)
                                        exercised_long_put = True
                            except Exception as e:
                                self.Log(
                                    f"EXERCISE_LONG_PUT_ERROR: Failed to exercise long PUT: {e}"
                                )

                    if not exercised_long_put:
                        # No protective long PUT or exercise failed - liquidate at market
                        self.Log(
                            f"EXERCISE_LIQUIDATE: QQQ position from exercise | "
                            f"Qty={qqq_holding.Quantity} | Value=${qqq_holding.HoldingsValue:,.2f}"
                        )
                        self.Liquidate(self.qqq, tag="EXERCISE_LIQUIDATE")

                    # Clear spread tracking for this option
                    if symbol in self._pending_spread_orders:
                        self._pending_spread_orders.pop(symbol)

            # Exit retry lifecycle cleanup: once symbol is flat after a fill, clear retry tracker(s).
            if abs(self._get_option_holding_quantity(symbol)) <= 0:
                symbol_norm = self._normalize_symbol_str(symbol)
                stale_retry_keys = [
                    key
                    for key in list(self._pending_exit_orders.keys())
                    if self._normalize_symbol_str(key) == symbol_norm
                ]
                for key in stale_retry_keys:
                    self._pending_exit_orders.pop(key, None)
                    self._exit_retry_scheduled_at.pop(key, None)
                if stale_retry_keys:
                    self.Log(
                        f"EXIT_RETRY_CLEANUP: Cleared {len(stale_retry_keys)} tracker(s) after flat fill | "
                        f"{symbol[-20:]}"
                    )

            self._save_state_throttled("ORDER_FILLED", min_minutes=3)

        elif orderEvent.Status == OrderStatus.Invalid:
            self.Log(f"INVALID: {orderEvent.Symbol} - {orderEvent.Message}")
            self._log_order_lifecycle_issue(orderEvent, "Invalid")
            failed_symbol = str(orderEvent.Symbol)
            failed_symbol_norm = self._normalize_symbol_str(failed_symbol)
            invalid_order = self.Transactions.GetOrderById(orderEvent.OrderId)
            invalid_tag = (
                str(getattr(invalid_order, "Tag", "") or "") if invalid_order is not None else ""
            )
            if not invalid_tag:
                invalid_tag = self._get_recent_symbol_fill_tag(failed_symbol) or ""
            invalid_trace_id = self._extract_trace_id_from_tag(invalid_tag) or "NONE"
            invalid_type = (
                str(getattr(invalid_order, "Type", "UNKNOWN"))
                if invalid_order is not None
                else "UNKNOWN"
            )
            self._record_order_lifecycle_event(
                status="INVALID",
                order_id=orderEvent.OrderId,
                symbol=failed_symbol,
                quantity=int(getattr(orderEvent, "FillQuantity", 0) or 0),
                fill_price=float(getattr(orderEvent, "FillPrice", 0.0) or 0.0),
                order_type=invalid_type,
                order_tag=invalid_tag,
                trace_id=invalid_trace_id,
                message=str(getattr(orderEvent, "Message", "") or ""),
                source="ON_ORDER_EVENT",
            )
            if "RECON_ORPHAN_OPTION" in invalid_tag:
                self._recon_orphan_close_submitted.pop(failed_symbol_norm, None)
            if "Margin" in str(orderEvent.Message) or "buying power" in str(orderEvent.Message):
                self._diag_margin_reject_count += 1
            invalid_tag_upper = str(invalid_tag or "").upper()
            invalid_terminal_retry = self._is_terminal_exit_retry_tag(invalid_tag_upper)
            self._forward_execution_event(
                order_event=orderEvent,
                status="Invalid",
                rejection_reason=orderEvent.Message,
            )
            is_oco_invalid = self.oco_manager.has_order(orderEvent.OrderId)
            if is_oco_invalid:
                self.oco_manager.on_order_inactive(
                    broker_order_id=orderEvent.OrderId,
                    status="Invalid",
                    detail=str(orderEvent.Message),
                    event_time=str(self.Time),
                )
                # OCO invalid/cancel is operational close-order churn, not entry rejection.
                # Do not route through margin/rejection recovery flows.
                self._save_state_throttled("ORDER_INVALID_OCO", min_minutes=3)
                return

            # V2.4.4 P0 Fix #4: Margin Call Circuit Breaker
            # Track consecutive margin calls and enter cooldown after hitting limit
            if "Margin" in str(orderEvent.Message):
                # V6.6.1: Only count margin rejections for OPENING orders
                # Closing/liquidation rejects should NOT trigger the circuit breaker
                order = self.Transactions.GetOrderById(orderEvent.OrderId)
                is_opening = True
                counted = False
                try:
                    if order is not None:
                        current_qty = self.Portfolio[order.Symbol].Quantity
                        # If order direction is opposite current position, it's a closing order
                        if current_qty != 0 and (order.Quantity * current_qty) < 0:
                            is_opening = False
                        # Explicit liquidation/forced tags should never count
                        if order.Tag and any(
                            k in order.Tag
                            for k in (
                                "LIQUIDATE",
                                "KILL_SWITCH",
                                "KS_",
                                "GOVERNOR",
                                "MARGIN_CB",
                                "FORCE_",
                                "ORPHAN_",
                                "EMERG_",
                                "ASSIGNMENT",
                                "EXERCISE",
                            )
                        ):
                            is_opening = False
                except Exception:
                    # If we can't classify, default to counting (safer than ignoring)
                    is_opening = True

                # V6.6.1: Guard with margin utilization (avoid false positives)
                margin_stressed = True
                if self.portfolio_router and config.MARGIN_UTILIZATION_ENABLED:
                    try:
                        utilization = self.portfolio_router.get_current_margin_usage()
                        margin_stressed = utilization >= (config.MAX_MARGIN_UTILIZATION + 0.05)
                    except Exception:
                        margin_stressed = True

                if not is_opening or not margin_stressed:
                    reason = "closing/forced order" if not is_opening else "low utilization"
                    self.Log(
                        f"MARGIN_CB_SKIP: Not counting margin reject ({reason}) | "
                        f"OrderId={orderEvent.OrderId}"
                    )
                else:
                    self._margin_call_consecutive_count += 1
                    counted = True
                if (
                    counted
                    and self._margin_call_consecutive_count >= config.MARGIN_CALL_MAX_CONSECUTIVE
                    and not self._margin_cb_in_progress
                ):
                    # V2.27: Re-entry guard — MarketOrder inside OnOrderEvent can recurse
                    self._margin_cb_in_progress = True
                    # V2.12 Fix #5: LIQUIDATE positions, not just cooldown
                    # Evidence: V2.11 showed positions held overnight into gap → kill switch
                    self.Log(
                        f"MARGIN_CB_LIQUIDATE: {self._margin_call_consecutive_count} consecutive "
                        f"margin calls | Force closing all options positions"
                    )

                    # Cancel all pending options orders
                    try:
                        for order in self.Transactions.GetOpenOrders():
                            order_symbol = str(order.Symbol)
                            if "QQQ" in order_symbol and (
                                "C0" in order_symbol or "P0" in order_symbol
                            ):
                                self.Transactions.CancelOrder(order.Id)
                                self.Log(f"MARGIN_CB_LIQUIDATE: Cancelled order {order.Id}")
                    except Exception as e:
                        self.Log(f"MARGIN_CB_LIQUIDATE: Error cancelling orders | {e}")

                    # V2.28: Use spread-aware liquidation instead of blind iteration
                    # Previous code iterated Portfolio.Values in unpredictable order,
                    # which could sell long leg first → naked short → margin rejection
                    self._liquidate_all_spread_aware("MARGIN_CB_LIQUIDATE")

                    self._margin_cb_in_progress = False

                    # Clear spread tracking
                    if self.options_engine:
                        self.options_engine.clear_spread_position()

                    # V2.18.2: Clear ghost margin reservations in router
                    # Bug fix: clear_spread_position() only clears OptionsEngine state,
                    # but leaves margin reservation in router causing permanent lockout
                    if self.portfolio_router:
                        self.portfolio_router.clear_all_spread_margins()

                    # Enter cooldown
                    cooldown_hours = config.MARGIN_CALL_COOLDOWN_HOURS
                    cooldown_until = self.Time + timedelta(hours=cooldown_hours)
                    self._margin_call_cooldown_until = str(cooldown_until)
                    self.Log(f"MARGIN_CB_COOLDOWN: Until {self._margin_call_cooldown_until}")
            else:
                # Reset counter on non-margin invalid
                self._margin_call_consecutive_count = 0

            # V2.3.6 FIX: Handle spread leg failure - liquidate orphaned leg

            # Case 1: Short leg failed - liquidate orphaned long leg
            if failed_symbol in self._pending_spread_orders:
                long_leg_symbol = self._pending_spread_orders.pop(failed_symbol)
                self._pending_spread_orders_reverse.pop(
                    long_leg_symbol, None
                )  # V2.6: Clean reverse
                self.Log(
                    f"SPREAD: Short leg FAILED - checking long leg | "
                    f"Short={failed_symbol[-15:]} | Long={long_leg_symbol[-15:]}"
                )

                # Check if we have a position in the long leg that needs liquidating
                # V2.19 FIX: Don't iterate Securities.Keys (20K+ loop)
                # Just try to access Portfolio directly - returns default if not found
                try:
                    holding, broker_symbol = self._find_portfolio_holding(
                        long_leg_symbol, security_type=SecurityType.Option
                    )
                    if holding and holding.Invested:
                        qty = holding.Quantity
                        orphan_key = f"{self._normalize_symbol_str(long_leg_symbol)}|{self._normalize_symbol_str(failed_symbol)}"
                        self.Log(
                            f"SPREAD: LIQUIDATING orphaned long leg | "
                            f"OrderId={orderEvent.OrderId} | SpreadKey={orphan_key} | "
                            f"{long_leg_symbol[-20:]} x{qty}"
                        )
                        self._submit_option_close_market_order(
                            symbol=broker_symbol,
                            quantity=-qty,
                            reason="ORPHAN_LONG",
                            engine_hint="VASS",
                        )
                    else:
                        self.Log(
                            f"SPREAD: No position in long leg - no cleanup needed | "
                            f"{long_leg_symbol[-20:]}"
                        )
                except Exception as e:
                    self.Log(f"SPREAD: ERROR liquidating orphaned long leg | {e}")

            # V2.6 Bug #5: Case 2: Long leg failed - liquidate orphaned short leg
            elif failed_symbol in self._pending_spread_orders_reverse:
                short_leg_symbol = self._pending_spread_orders_reverse.pop(failed_symbol)
                self._pending_spread_orders.pop(short_leg_symbol, None)  # Clean forward mapping
                self.Log(
                    f"SPREAD: Long leg FAILED - checking short leg | "
                    f"Long={failed_symbol[-15:]} | Short={short_leg_symbol[-15:]}"
                )

                # Check if we have a position in the short leg that needs closing
                # V2.19 FIX: Don't iterate Securities.Keys (20K+ loop)
                # Just try to access Portfolio directly - returns default if not found
                try:
                    holding, broker_symbol = self._find_portfolio_holding(
                        short_leg_symbol, security_type=SecurityType.Option
                    )
                    if holding and holding.Invested:
                        qty = holding.Quantity
                        orphan_key = f"{self._normalize_symbol_str(failed_symbol)}|{self._normalize_symbol_str(short_leg_symbol)}"
                        self.Log(
                            f"SPREAD: BUYING BACK orphaned short leg | "
                            f"OrderId={orderEvent.OrderId} | SpreadKey={orphan_key} | "
                            f"{short_leg_symbol[-20:]} x{abs(qty)}"
                        )
                        # Short leg is negative qty, buy back means positive order
                        self._submit_option_close_market_order(
                            symbol=broker_symbol,
                            quantity=-qty,
                            reason="ORPHAN_SHORT",
                            engine_hint="VASS",
                        )
                    else:
                        self.Log(
                            f"SPREAD: No position in short leg - no cleanup needed | "
                            f"{short_leg_symbol[-20:]}"
                        )
                except Exception as e:
                    self.Log(f"SPREAD: ERROR buying back orphaned short leg | {e}")

            # V2.6 Bug #14: Check if this is a failed exit order that needs retry
            if (
                failed_symbol not in self._pending_exit_orders
                and invalid_order is not None
                and not invalid_terminal_retry
            ):
                try:
                    holdings_qty = self.Portfolio[invalid_order.Symbol].Quantity
                    is_close_side = (invalid_order.Quantity < 0 < holdings_qty) or (
                        invalid_order.Quantity > 0 > holdings_qty
                    )
                except Exception:
                    is_close_side = False
                if is_close_side:
                    _eb = self._infer_option_engine_bucket_for_symbol(
                        symbol=failed_symbol,
                        tag_hint=(invalid_tag or ""),
                    )
                    self._pending_exit_orders[failed_symbol] = ExitOrderTracker(
                        symbol=failed_symbol,
                        order_id=int(orderEvent.OrderId),
                        reason=(invalid_tag or "INVALID_CLOSE"),
                        engine_bucket=_eb,
                    )

            if failed_symbol in self._pending_exit_orders:
                exit_tracker = self._pending_exit_orders[failed_symbol]
                if exit_tracker.should_retry(config.EXIT_ORDER_RETRY_COUNT):
                    exit_tracker.record_attempt(str(self.Time))
                    self.Log(
                        f"EXIT_RETRY: {failed_symbol[-20:]} attempt "
                        f"{exit_tracker.retry_count}/{config.EXIT_ORDER_RETRY_COUNT}"
                    )
                    # Schedule retry after delay
                    self._schedule_exit_retry(failed_symbol)
                else:
                    failed_key = self._resolve_pending_exit_tracker_key(failed_symbol)
                    if failed_key is not None:
                        self._pending_exit_orders.pop(failed_key, None)
                        self._exit_retry_scheduled_at.pop(failed_key, None)
                    # All retries exhausted - emergency close
                    if invalid_terminal_retry:
                        self.Log(
                            f"EXIT_EMERGENCY_SKIP: {failed_symbol[-20:]} retries exhausted for "
                            "terminal forced-close tag; deferring to reconciliation"
                        )
                    else:
                        self.Log(
                            f"EXIT_EMERGENCY: {failed_symbol[-20:]} all retries failed - "
                            f"forcing market close"
                        )
                        self._force_market_close(failed_symbol)

            # V2.20: Event-driven state recovery — notify source engine
            # Runs AFTER existing handlers (margin CB, orphan legs, exit retry)
            self._handle_order_rejection(failed_symbol, orderEvent)
            self._save_state_throttled("ORDER_INVALID", min_minutes=3)

        elif orderEvent.Status == OrderStatus.Canceled:
            self._log_order_lifecycle_issue(orderEvent, "Canceled")
            canceled_symbol = str(orderEvent.Symbol)
            canceled_symbol_norm = self._normalize_symbol_str(canceled_symbol)
            canceled_order = self.Transactions.GetOrderById(orderEvent.OrderId)
            canceled_tag = (
                str(getattr(canceled_order, "Tag", "") or "") if canceled_order is not None else ""
            )
            canceled_tag_upper = canceled_tag.upper()
            canceled_terminal_retry = self._is_terminal_exit_retry_tag(canceled_tag_upper)
            if not canceled_tag:
                canceled_tag = self._get_recent_symbol_fill_tag(canceled_symbol) or ""
                canceled_tag_upper = canceled_tag.upper()
                canceled_terminal_retry = self._is_terminal_exit_retry_tag(canceled_tag_upper)
            canceled_trace_id = self._extract_trace_id_from_tag(canceled_tag) or "NONE"
            canceled_type = (
                str(getattr(canceled_order, "Type", "UNKNOWN"))
                if canceled_order is not None
                else "UNKNOWN"
            )
            self._record_order_lifecycle_event(
                status="CANCELED",
                order_id=orderEvent.OrderId,
                symbol=canceled_symbol,
                quantity=int(getattr(orderEvent, "FillQuantity", 0) or 0),
                fill_price=float(getattr(orderEvent, "FillPrice", 0.0) or 0.0),
                order_type=canceled_type,
                order_tag=canceled_tag,
                trace_id=canceled_trace_id,
                message=str(getattr(orderEvent, "Message", "") or ""),
                source="ON_ORDER_EVENT",
            )
            if "RECON_ORPHAN_OPTION" in canceled_tag:
                self._recon_orphan_close_submitted.pop(canceled_symbol_norm, None)
            self._forward_execution_event(
                order_event=orderEvent,
                status="Canceled",
            )
            is_oco_cancel = self.oco_manager.has_order(orderEvent.OrderId)
            if not is_oco_cancel:
                tag_upper = canceled_tag.upper()
                if tag_upper.startswith("OCO_STOP:") or tag_upper.startswith("OCO_PROFIT:"):
                    is_oco_cancel = True
            if is_oco_cancel:
                self.oco_manager.on_order_inactive(
                    broker_order_id=orderEvent.OrderId,
                    status="Canceled",
                    detail=str(getattr(orderEvent, "Message", "")),
                    event_time=str(self.Time),
                )
            # Do not escalate expected OCO sibling cancels into spread-close retry churn.
            if not is_oco_cancel:
                self._queue_spread_close_retry_on_cancel(canceled_symbol, orderEvent)

            # V10.5: Route single-leg close cancels into retry pipeline.
            is_spread_leg = False
            for spread in self.options_engine.get_spread_positions():
                if canceled_symbol_norm in {
                    self._normalize_symbol_str(spread.long_leg.symbol),
                    self._normalize_symbol_str(spread.short_leg.symbol),
                }:
                    is_spread_leg = True
                    break
            if not is_oco_cancel and canceled_order is not None and not is_spread_leg:
                try:
                    holdings_qty = self.Portfolio[canceled_order.Symbol].Quantity
                    is_close_side = (canceled_order.Quantity < 0 < holdings_qty) or (
                        canceled_order.Quantity > 0 > holdings_qty
                    )
                except Exception:
                    is_close_side = False
                if is_close_side and not canceled_terminal_retry:
                    tracker = self._pending_exit_orders.get(canceled_symbol)
                    if tracker is None:
                        _eb = self._infer_option_engine_bucket_for_symbol(
                            symbol=canceled_symbol,
                            tag_hint=(canceled_tag or ""),
                        )
                        tracker = ExitOrderTracker(
                            symbol=canceled_symbol,
                            order_id=int(orderEvent.OrderId),
                            reason=(canceled_tag or "CANCELED_CLOSE"),
                            engine_bucket=_eb,
                        )
                        self._pending_exit_orders[canceled_symbol] = tracker
                    if tracker.should_retry(config.EXIT_ORDER_RETRY_COUNT):
                        tracker.record_attempt(str(self.Time))
                        self.Log(
                            f"EXIT_RETRY: {canceled_symbol[-20:]} attempt "
                            f"{tracker.retry_count}/{config.EXIT_ORDER_RETRY_COUNT} (Canceled)"
                        )
                        self._schedule_exit_retry(canceled_symbol)
                    else:
                        canceled_key = self._resolve_pending_exit_tracker_key(canceled_symbol)
                        if canceled_key is not None:
                            self._pending_exit_orders.pop(canceled_key, None)
                            self._exit_retry_scheduled_at.pop(canceled_key, None)
                        self.Log(
                            f"EXIT_EMERGENCY: {canceled_symbol[-20:]} all retries failed - "
                            f"forcing market close"
                        )
                        self._force_market_close(canceled_symbol)

            # V9.1 FIX: Skip rejection handler for OCO cancels.
            # OCO cancels occur when one leg fills (e.g., profit target hit cancels
            # the paired stop). These are normal operational events, not order failures.
            # Routing them through _handle_order_rejection incorrectly clears pending
            # state for NEW entries submitted at the same timestamp (Bug: Aug 8 2017
            # orphan position cascade — 669 contracts registered as SWING instead of
            # INTRADAY because OCO cancel wiped _pending_intraday_entry flag).
            if not is_oco_cancel:
                self._handle_order_rejection(canceled_symbol, orderEvent)
            self._save_state_throttled("ORDER_CANCELED", min_minutes=3)

    def _log_order_lifecycle_issue(self, order_event: OrderEvent, status: str) -> None:
        """Compact attribution log for canceled/invalid orders."""
        if not self._should_log_backtest_category("LOG_ORDER_LIFECYCLE_BACKTEST_ENABLED", True):
            return
        max_per_day = int(getattr(config, "LOG_ORDER_LIFECYCLE_MAX_PER_DAY", 200))
        if self._order_lifecycle_log_count >= max_per_day:
            self._order_lifecycle_suppressed_count += 1
            return
        order = self.Transactions.GetOrderById(order_event.OrderId)
        order_type = str(getattr(order, "Type", "UNKNOWN")) if order is not None else "UNKNOWN"
        raw_tag = self._get_order_tag(order_event)
        tag = raw_tag if raw_tag else f"NO_TAG:{order_type}"
        msg = str(getattr(order_event, "Message", "") or "")
        self.Log(
            f"ORDER_LIFECYCLE: Status={status} | OrderId={order_event.OrderId} | "
            f"Symbol={order_event.Symbol} | Type={order_type} | Tag={tag} | Msg={msg}"
        )
        self._order_lifecycle_log_count += 1

    def _forward_execution_event(
        self,
        order_event: OrderEvent,
        status: str,
        fill_price: float = 0.0,
        fill_quantity: int = 0,
        rejection_reason: str = "",
    ) -> None:
        """
        V6.22: Forward broker events to ExecutionEngine only for mapped orders.

        OCO/manual/spread-atomic orders are external to ExecutionEngine and should
        not be logged as EXEC: UNKNOWN_ORDER repeatedly.
        """
        broker_id = int(order_event.OrderId)
        if self.execution_engine.get_order_by_broker_id(broker_id) is None:
            if broker_id not in self._external_exec_event_logged:
                order = self.Transactions.GetOrderById(broker_id)
                order_type = (
                    str(getattr(order, "Type", "UNKNOWN")) if order is not None else "UNKNOWN"
                )
                tag = self._get_order_tag(order_event)
                if not tag:
                    tag = f"NO_TAG:{order_type}"
                self.Log(
                    f"EXEC_EXTERNAL: BrokerID={broker_id} | Status={status} | "
                    f"Symbol={order_event.Symbol} | Tag={tag}"
                )
                self._external_exec_event_logged.add(broker_id)
                max_external_cache = 20000
                if len(self._external_exec_event_logged) > max_external_cache:
                    self._external_exec_event_logged.clear()
                    self.Log(
                        f"EXEC_EXTERNAL_CACHE_RESET: Cleared external event cache at "
                        f"{max_external_cache}+ entries"
                    )
            return

        self.execution_engine.on_order_event(
            broker_order_id=broker_id,
            status=status,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            rejection_reason=str(rejection_reason or ""),
        )

    def _is_micro_entry_fill(self, symbol: str, fill_qty: float, order_tag: str) -> bool:
        """Classify fill as MICRO entry for recovery and EOD safety sweeps."""
        if fill_qty <= 0:
            return False
        symbol_norm = self._normalize_symbol_str(symbol)
        if "QQQ" not in symbol_norm or ("C" not in symbol_norm and "P" not in symbol_norm):
            return False
        tag_upper = str(order_tag or "").upper()
        if tag_upper.startswith("MICRO:") or tag_upper == "MICRO":
            return True
        if tag_upper.startswith("HEDGE:") or tag_upper == "HEDGE":
            return True
        # Some broker fills resolve tag from trace fallback. Treat MICRO traces as intraday.
        if "|TRACE=MICRO_" in tag_upper and "VASS" not in tag_upper and "ITM:" not in tag_upper:
            return True
        return False

    def _is_spread_fill_symbol(self, symbol: str) -> bool:
        """
        Return True only when symbol matches currently tracked spread-entry legs.

        Prevents MICRO single-leg fills from being misclassified as spread fills when
        stale spread pending state is present.
        """
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return False

        tracker = self._spread_fill_tracker
        if tracker is not None:
            tracker_symbols = {
                self._normalize_symbol_str(tracker.long_leg_symbol),
                self._normalize_symbol_str(tracker.short_leg_symbol),
            }
            if symbol_norm in tracker_symbols:
                return True

        pending_long, pending_short = self.options_engine.get_pending_spread_legs()
        pending_symbols = set()
        if pending_long is not None:
            pending_symbols.add(self._normalize_symbol_str(pending_long.symbol))
        if pending_short is not None:
            pending_symbols.add(self._normalize_symbol_str(pending_short.symbol))
        return symbol_norm in pending_symbols

    def _cancel_spread_linked_oco(self, long_symbol: str, short_symbol: str, reason: str) -> None:
        """Cancel any OCO siblings tied to spread legs before forced-close retries."""
        if not hasattr(self, "oco_manager"):
            return
        for leg_symbol in (long_symbol, short_symbol):
            try:
                self.oco_manager.cancel_by_symbol(leg_symbol, reason=reason)
            except Exception:
                pass

    def _schedule_spread_safe_lock_retry(
        self,
        spread_key: str,
        long_symbol: str,
        short_symbol: str,
        retry_reason: str,
        detail: str,
    ) -> None:
        """Schedule non-fatal safe-lock retry when spread close submission fails."""
        safe_retry_min = int(getattr(config, "SPREAD_CLOSE_SAFE_LOCK_RETRY_MIN", 10))
        max_retry_cycles = int(getattr(config, "SPREAD_CLOSE_MAX_RETRY_CYCLES", 12))
        self.Log(
            f"SPREAD_CLOSE_FALSE_NONFATAL: Long={long_symbol} Short={short_symbol} | "
            f"Reason={retry_reason} | Detail={detail}"
        )
        self.Log(
            f"SPREAD_SAFE_LOCK_RETRY_SCHEDULED: Long={long_symbol} Short={short_symbol} | "
            f"RetryIn={safe_retry_min}m | Reason={retry_reason}"
        )
        self._record_order_lifecycle_event(
            status="SPREAD_EXIT_RETRY_EXHAUSTED",
            order_id=0,
            symbol=str(long_symbol or ""),
            quantity=0,
            fill_price=0.0,
            order_type="SPREAD_SAFE_LOCK",
            order_tag="SPREAD_CLOSE",
            trace_id="",
            message=(
                f"Key={spread_key} | Long={long_symbol} | Short={short_symbol} | "
                f"Reason={retry_reason} | Detail={detail} | RetryInMin={safe_retry_min}"
            ),
            source="SPREAD_RETRY",
        )
        self._spread_forced_close_reason[spread_key] = f"SAFE_LOCK_RETRY:{retry_reason}"
        self._spread_forced_close_retry[spread_key] = self.Time + timedelta(minutes=safe_retry_min)
        # Keep retrying at emergency cadence after initial escalation.
        self._spread_forced_close_retry_cycles[spread_key] = max(max_retry_cycles - 1, 0)
        self._spread_last_close_submit_at[spread_key] = self.Time

    def _cleanup_stale_spread_state(self) -> None:
        """
        V2.6: Clean up stale spread tracking state (Bug #7 fix).

        Called when fill tracker times out or needs reset.
        """
        self.Log("SPREAD: Cleaning up stale tracking state")

        # Clear fill tracker
        self._spread_fill_tracker = None

        # Clear pending orders mappings
        self._pending_spread_orders.clear()
        self._pending_spread_orders_reverse.clear()
        self._spread_close_trackers.clear()
        self._spread_forced_close_retry.clear()
        self._spread_forced_close_reason.clear()
        self._spread_forced_close_cancel_counts.clear()
        self._spread_forced_close_retry_cycles.clear()
        self._spread_last_close_submit_at.clear()

        # Clear options engine pending spread state
        self.options_engine.clear_pending_spread_state_hard()

    def _emergency_close_spread_legs(self) -> None:
        """
        V2.6: Emergency close spread legs when quantity mismatch detected.

        Liquidates whatever is in the portfolio to prevent orphaned positions.
        """
        self.Log("SPREAD: EMERGENCY - Closing mismatched legs")

        if self._spread_fill_tracker is None:
            return

        tracker = self._spread_fill_tracker

        # Try to close any filled legs
        try:
            # Check long leg
            if tracker.long_leg_symbol:
                long_holding, long_symbol = self._find_portfolio_holding(
                    tracker.long_leg_symbol, security_type=SecurityType.Option
                )
                if long_holding and long_holding.Invested:
                    qty = long_holding.Quantity
                    self._submit_option_close_market_order(
                        symbol=long_symbol,
                        quantity=-qty,
                        reason="EMERG_QTY_MISMATCH",
                        engine_hint="VASS",
                    )
                    self.Log(
                        f"SPREAD: Emergency closed long leg | {self._normalize_symbol_str(long_symbol)} x{qty}"
                    )

            # Check short leg
            if tracker.short_leg_symbol:
                short_holding, short_symbol = self._find_portfolio_holding(
                    tracker.short_leg_symbol, security_type=SecurityType.Option
                )
                if short_holding and short_holding.Invested:
                    qty = short_holding.Quantity
                    self._submit_option_close_market_order(
                        symbol=short_symbol,
                        quantity=-qty,
                        reason="EMERG_QTY_MISMATCH",
                        engine_hint="VASS",
                    )
                    self.Log(
                        f"SPREAD: Emergency closed short leg | {self._normalize_symbol_str(short_symbol)} x{qty}"
                    )

        except Exception as e:
            self.Log(f"SPREAD_ERROR: Emergency close failed | {e}")

    def _schedule_exit_retry(self, symbol: str) -> None:
        """
        V2.6 Bug #14: Schedule a retry for a failed exit order.

        Args:
            symbol: Symbol that failed to exit.
        """
        tracker_key = self._resolve_pending_exit_tracker_key(symbol)
        if tracker_key is None:
            return
        try:
            # Schedule retry after delay using QC's scheduling
            retry_seconds = config.EXIT_ORDER_RETRY_DELAY_SECONDS
            retry_dt = self.Time + timedelta(seconds=retry_seconds)
            existing_retry = self._exit_retry_scheduled_at.get(tracker_key)
            if existing_retry is not None and existing_retry > self.Time:
                self.Log(
                    f"EXIT_RETRY: Schedule already pending for {tracker_key[-20:]} | "
                    f"At={existing_retry.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                return

            # Use Schedule.On for proper scheduling
            def retry_action():
                self._exit_retry_scheduled_at.pop(tracker_key, None)
                self._retry_exit_order(tracker_key)

            # Schedule for N seconds from now (handles minute/second rollover safely).
            date_rule = (
                self.DateRules.Today
                if retry_dt.date() == self.Time.date()
                else self.DateRules.On(retry_dt.year, retry_dt.month, retry_dt.day)
            )
            self.Schedule.On(
                date_rule,
                self.TimeRules.At(
                    retry_dt.hour,
                    retry_dt.minute,
                    retry_dt.second,
                ),
                retry_action,
            )
            self._exit_retry_scheduled_at[tracker_key] = retry_dt
            self.Log(
                f"EXIT_RETRY: Scheduled retry for {tracker_key[-20:]} at "
                f"{retry_dt.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as e:
            self.Log(f"EXIT_RETRY_ERROR: Failed to schedule retry | {e}")
            self._exit_retry_scheduled_at.pop(tracker_key, None)
            # Fallback: immediate retry
            self._retry_exit_order(tracker_key)

    def _retry_exit_order(self, symbol: str) -> None:
        """
        V2.6 Bug #14: Retry a failed exit order.

        Args:
            symbol: Symbol to retry exit for.
        """
        tracker_key = self._resolve_pending_exit_tracker_key(symbol)
        if tracker_key is None:
            return
        self._exit_retry_scheduled_at.pop(tracker_key, None)

        tracker = self._pending_exit_orders.get(tracker_key)
        if tracker is None:
            return
        self.Log(
            f"EXIT_RETRY: Retrying exit for {tracker_key[-20:]} (attempt {tracker.retry_count})"
        )
        if self._has_open_non_oco_order_for_symbol(tracker_key):
            self.Log(
                f"EXIT_RETRY_DEFER: Non-OCO open order already exists for {tracker_key[-20:]} | "
                "Skipping duplicate retry submit"
            )
            self._schedule_exit_retry(tracker_key)
            return

        try:
            # Ensure OCO legs are canceled before submitting a retry close.
            try:
                self.oco_manager.cancel_by_symbol(tracker_key, reason="EXIT_RETRY")
            except Exception:
                pass
            if self._has_open_order_for_symbol(tracker_key):
                self.Log(
                    f"EXIT_RETRY_DEFER: Waiting for prior order cancel/settle | {tracker_key[-20:]}"
                )
                self._schedule_exit_retry(tracker_key)
                return

            holding, broker_symbol = self._find_portfolio_holding(tracker_key)
            if holding and holding.Invested:
                qty = holding.Quantity
                ticket = self._submit_option_close_market_order(
                    symbol=broker_symbol,
                    quantity=-qty,
                    reason=f"RETRY_{tracker.reason}",
                    engine_hint=str(getattr(tracker, "engine_bucket", "") or ""),
                    tag_hint=str(getattr(tracker, "reason", "") or ""),
                )
                try:
                    if ticket is not None and hasattr(ticket, "OrderId"):
                        tracker.order_id = int(ticket.OrderId)
                except Exception:
                    pass
                self.Log(f"EXIT_RETRY: Submitted market order | {tracker_key[-20:]} x{qty}")
            else:
                # Position already closed
                self._pending_exit_orders.pop(tracker_key, None)
                self._exit_retry_scheduled_at.pop(tracker_key, None)
                self.Log(f"EXIT_RETRY: Position already closed | {tracker_key[-20:]}")
        except Exception as e:
            self.Log(f"EXIT_RETRY_ERROR: Retry failed | {tracker_key[-20:]} | {e}")

    def _force_market_close(self, symbol: str) -> None:
        """
        V2.6 Bug #14: Emergency market close when all retries exhausted.

        V2.33: If symbol is an option, use atomic close to ensure shorts close first.

        Args:
            symbol: Symbol to force close.
        """
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return
        if not hasattr(self, "_force_market_close_inflight"):
            self._force_market_close_inflight = set()
        if symbol_norm in self._force_market_close_inflight:
            self.Log(
                f"EXIT_EMERGENCY_REENTRY_SKIP: {symbol_norm[-20:]} | "
                "Reason=Already processing forced close for symbol"
            )
            return
        self._force_market_close_inflight.add(symbol_norm)
        self.Log(f"EXIT_EMERGENCY: Force market close | {symbol[-20:]}")
        try:
            holding, broker_symbol = self._find_portfolio_holding(symbol)
            if holding and holding.Invested:
                qty = holding.Quantity

                # Single-symbol forced close path: submit direct close and avoid
                # re-entering atomic close loops from inside OnOrderEvent callbacks.
                if holding.Symbol.SecurityType == SecurityType.Option:
                    engine_bucket = self._infer_option_engine_bucket_for_symbol(
                        symbol=broker_symbol
                    )
                    self.Log(
                        f"EXIT_EMERGENCY: Direct option close submit | {symbol[-20:]} | "
                        f"Engine={engine_bucket}"
                    )
                    self._submit_option_close_market_order(
                        symbol=broker_symbol,
                        quantity=-qty,
                        reason="EMERG_OPTION_RETRY_EXHAUSTED",
                        engine_hint=engine_bucket,
                    )
                else:
                    # Equity: Use Liquidate for absolute closure
                    self.Liquidate(broker_symbol, tag="EMERG_ALL_RETRIES_FAILED")
                    self.Log(f"EXIT_EMERGENCY: Liquidated | {symbol[-20:]} x{qty}")
            else:
                self.Log(f"EXIT_EMERGENCY: No position to close | {symbol[-20:]}")
        except Exception as e:
            self.Log(f"EXIT_EMERGENCY_ERROR: Liquidate failed | {e}")
        finally:
            self._force_market_close_inflight.discard(symbol_norm)

    def _has_open_order_for_symbol(self, symbol: str, tag_contains: str = "") -> bool:
        """Return True when an open order exists for symbol, optionally filtered by tag."""
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return False
        tag_filter = str(tag_contains or "").upper().strip()
        for open_order in self.Transactions.GetOpenOrders():
            if self._normalize_symbol_str(open_order.Symbol) != symbol_norm:
                continue
            if not tag_filter:
                return True
            open_tag = str(getattr(open_order, "Tag", "") or "").upper()
            if tag_filter in open_tag:
                return True
        return False

    def _has_open_non_oco_order_for_symbol(self, symbol: str) -> bool:
        """Return True when symbol has an open non-OCO order (entry/exit in flight)."""
        symbol_norm = self._normalize_symbol_str(symbol)
        if not symbol_norm:
            return False
        for open_order in self.Transactions.GetOpenOrders():
            if self._normalize_symbol_str(open_order.Symbol) != symbol_norm:
                continue
            open_tag = str(getattr(open_order, "Tag", "") or "").upper()
            if "OCO_STOP:" in open_tag or "OCO_PROFIT:" in open_tag:
                continue
            return True
        return False

    def _parse_and_store_rejection_margin(self, order_event) -> None:
        """
        V2.21: Parse broker Free Margin from rejection message for adaptive retry.

        Broker message format:
        "Insufficient buying power... Free Margin: 48927, Maintenance Margin Delta: 56172"

        Stores safety_factor * Free Margin as options-engine rejection margin cap.
        """
        import re

        try:
            msg = str(order_event.Message) if hasattr(order_event, "Message") else ""
            patterns = [
                r"Free Margin:\s*\$?([0-9][0-9,]*\.?[0-9]*)",
                r"Available(?:\s+Margin)?[:=]\s*\$?([0-9][0-9,]*\.?[0-9]*)",
                r"Margin Remaining[:=]\s*\$?([0-9][0-9,]*\.?[0-9]*)",
            ]
            free_margin = None
            for pattern in patterns:
                match = re.search(pattern, msg, flags=re.IGNORECASE)
                if not match:
                    continue
                raw = match.group(1).replace(",", "").rstrip(".")
                try:
                    free_margin = float(raw)
                    break
                except ValueError:
                    continue

            if free_margin is not None:
                safety = getattr(config, "SPREAD_REJECTION_MARGIN_SAFETY", 0.80)
                cap = free_margin * safety
                self.options_engine.set_rejection_margin_cap(cap)
                self.Log(
                    f"REJECTION_MARGIN: Free=${free_margin:,.0f} | "
                    f"Cap=${cap:,.0f} (x{safety:.0%})"
                )
            elif "insufficient buying power" in msg.lower() or "margin" in msg.lower():
                self.Log(
                    "REJECTION_MARGIN_PARSE_FAIL: Could not parse free margin from rejection message"
                )
        except Exception as e:
            self.Log(f"REJECTION_MARGIN: Parse error: {e}")

    def _on_fill(
        self,
        symbol: str,
        fill_price: float,
        fill_qty: float,
        order_event: OrderEvent,
    ) -> None:
        """
        Handle order fill event.

        Updates position tracking in relevant engines.

        Args:
            symbol: Symbol that was filled.
            fill_price: Fill price.
            fill_qty: Fill quantity (positive=buy, negative=sell).
            order_event: Original order event.
        """
        # Update trend engine - V6.11: Updated for diversified universe
        if symbol in config.TREND_SYMBOLS:
            if fill_qty > 0:
                # Get ATR for initial stop calculation - V6.11: Added UGL, UCO
                atr_value = 0.0
                if symbol == "QLD" and self.qld_atr.IsReady:
                    atr_value = self.qld_atr.Current.Value
                elif symbol == "SSO" and self.sso_atr.IsReady:
                    atr_value = self.sso_atr.Current.Value
                elif symbol == "UGL" and self.ugl_atr.IsReady:
                    atr_value = self.ugl_atr.Current.Value
                elif symbol == "UCO" and self.uco_atr.IsReady:
                    atr_value = self.uco_atr.Current.Value
                self.trend_engine.register_entry(
                    symbol=symbol,
                    entry_price=fill_price,
                    entry_date=str(self.Time.date()),
                    atr=atr_value,
                    strategy_tag="TREND",
                )
            else:
                # V6.12: Record trade P&L before removing position
                position = self.trend_engine.get_position(symbol)
                if position and hasattr(self, "pnl_tracker"):
                    self.pnl_tracker.record_trade(
                        symbol=symbol,
                        engine="TREND",
                        entry_date=position.entry_date,
                        exit_date=str(self.Time.date()),
                        entry_price=position.entry_price,
                        exit_price=fill_price,
                        quantity=abs(int(fill_qty)),
                    )
                self.trend_engine.remove_position(symbol)

        # Update MR engine - V6.11: Updated to include SPXL
        if symbol in config.MR_SYMBOLS:
            try:
                if fill_qty > 0:
                    # Use current price as VWAP approximation
                    vwap = (
                        self.Securities[symbol].Price
                        if hasattr(self, symbol.lower())
                        else fill_price
                    )
                    self.mr_engine.register_entry(
                        symbol=symbol,
                        entry_price=fill_price,
                        entry_time=str(self.Time),
                        vwap=vwap,
                    )
                else:
                    # V6.12: Record trade P&L before removing position
                    mr_position = self.mr_engine.get_position()
                    if mr_position and hasattr(self, "pnl_tracker"):
                        entry_date = mr_position.get("entry_time", str(self.Time))[:10]
                        self.pnl_tracker.record_trade(
                            symbol=symbol,
                            engine="MR",
                            entry_date=entry_date,
                            exit_date=str(self.Time.date()),
                            entry_price=mr_position.get("entry_price", fill_price),
                            exit_price=fill_price,
                            quantity=abs(int(fill_qty)),
                        )
                    self.mr_engine.remove_position()
            except Exception as e:
                self.Log(f"MR_TRACK_ERROR: {symbol}: {e}")

        # V2.1/V2.3: Update Options engine if QQQ option
        symbol_norm = self._normalize_symbol_str(symbol)
        if "QQQ" in symbol_norm and ("C" in symbol_norm or "P" in symbol_norm):
            try:
                order_tag = self._get_order_tag(order_event)
                # V2.5 FIX: Check spread mode FIRST (before fill_qty sign check)
                # Bull Call Spread: Long leg = BUY (qty > 0), Short leg = SELL (qty < 0)
                # The bug was: "if fill_qty > 0" excluded short leg fills entirely!
                if self._is_spread_fill_symbol(symbol_norm):
                    # Spread mode: track ANY leg fill (long=positive, short=negative)
                    # Use abs(fill_qty) because _handle_spread_leg_fill expects positive qty
                    self._handle_spread_leg_fill(symbol, fill_price, abs(fill_qty))
                elif fill_qty > 0:
                    # V6.12 FIX: Check if this is a BUY to close a spread short leg
                    # Short leg close = BUY (fill_qty > 0), but it's an EXIT not an entry
                    is_spread_short_close = False
                    for spread in self.options_engine.get_spread_positions():
                        if (
                            spread.short_leg
                            and self._normalize_symbol_str(spread.short_leg.symbol) == symbol_norm
                        ):
                            is_spread_short_close = True
                            break
                    if is_spread_short_close:
                        # This is a short leg close (BUY to close)
                        self._handle_spread_leg_close(symbol, fill_price, fill_qty)
                    else:
                        # Single-leg entry (legacy or intraday)
                        force_intraday_recovery = (
                            self._is_micro_entry_fill(
                                symbol=symbol,
                                fill_qty=fill_qty,
                                order_tag=order_tag,
                            )
                            and not self.options_engine.has_pending_engine_entry()
                        )
                        if force_intraday_recovery:
                            self._diag_micro_tag_recovery_count += 1
                            self.Log(
                                f"MICRO_TAG_RECOVERY: Fill classified intraday from tag | "
                                f"Symbol={symbol_norm} | Tag={order_tag or 'NO_TAG'}"
                            )

                        recovery_contract = None
                        if force_intraday_recovery:
                            recovery_contract = self._build_option_contract_from_fill(
                                symbol=symbol,
                                fill_price=fill_price,
                            )
                        position = self.options_engine.register_entry(
                            fill_price=fill_price,
                            entry_time=str(self.Time),
                            current_date=str(self.Time.date()),
                            force_intraday=force_intraday_recovery,
                            contract=recovery_contract,
                            symbol=symbol_norm,
                            order_tag=order_tag,
                        )

                        if position:
                            entry_is_intraday = (
                                self.options_engine.find_engine_lane_by_symbol(symbol_norm)
                                is not None
                            )
                            # Create/refresh OCO pair for stop and profit exits.
                            if entry_is_intraday:
                                live_qty = abs(self._get_option_holding_quantity(symbol))
                                if live_qty <= 0:
                                    live_qty = int(abs(fill_qty))
                                self._sync_engine_oco(
                                    symbol=symbol_norm,
                                    position=position,
                                    quantity=live_qty,
                                    reason="ENTRY_FILL",
                                )
                            else:
                                oco_pair = self.oco_manager.create_oco_pair(
                                    symbol=symbol,
                                    entry_price=fill_price,
                                    stop_price=position.stop_price,
                                    target_price=position.target_price,
                                    quantity=abs(int(fill_qty)),
                                    current_date=str(self.Time.date()),
                                    tag_context="SWING_SINGLE",
                                )
                                if oco_pair:
                                    submitted = self.oco_manager.submit_oco_pair(
                                        oco_pair, current_time=str(self.Time)
                                    )
                                    if submitted:
                                        self.Log(
                                            f"OPT: OCO pair created | "
                                            f"Stop=${position.stop_price:.2f} | "
                                            f"Target=${position.target_price:.2f}"
                                        )
                                    else:
                                        self.Log(
                                            f"OCO_SYNC_SKIP: Submit failed | Symbol={symbol_norm} | "
                                            f"Qty={abs(int(fill_qty))} | Stop=${position.stop_price:.2f} | "
                                            f"Target=${position.target_price:.2f} | Reason=SWING_ENTRY_FILL"
                                        )
                                else:
                                    self.Log(
                                        f"OCO_SYNC_SKIP: Create failed | Symbol={symbol_norm} | "
                                        f"Qty={abs(int(fill_qty))} | Stop=${position.stop_price:.2f} | "
                                        f"Target=${position.target_price:.2f} | Reason=SWING_ENTRY_FILL"
                                    )
                            # Record intraday entry snapshot for robust exit accounting.
                            if entry_is_intraday:
                                self._intraday_entry_snapshot[symbol_norm] = {
                                    "entry_price": position.entry_price,
                                    "entry_time": position.entry_time,
                                    "quantity": abs(int(fill_qty)),
                                    "entry_strategy": getattr(
                                        position, "entry_strategy", "UNKNOWN"
                                    ),
                                    "entry_tag": order_tag or "",
                                    "entry_dte": int(
                                        getattr(position.contract, "days_to_expiry", -1)
                                    )
                                    if position and position.contract
                                    else -1,
                                }
                                entry_bucket = self._engine_bucket_from_strategy(
                                    getattr(position, "entry_strategy", "")
                                )
                                if entry_bucket == "ITM":
                                    self._itm_open_symbols.add(symbol_norm)
                                else:
                                    self._micro_open_symbols.add(symbol_norm)
                elif fill_qty < 0:
                    # Exit routing must be symbol-aware because spread + intraday can coexist.
                    is_spread_leg = False
                    for spread in self.options_engine.get_spread_positions():
                        spread_long_norm = (
                            self._normalize_symbol_str(spread.long_leg.symbol)
                            if spread.long_leg
                            else ""
                        )
                        spread_short_norm = (
                            self._normalize_symbol_str(spread.short_leg.symbol)
                            if spread.short_leg
                            else ""
                        )
                        if symbol_norm in {spread_long_norm, spread_short_norm}:
                            is_spread_leg = True
                            break
                    if is_spread_leg:
                        # Spread exit - track leg closes
                        self._handle_spread_leg_close(symbol, fill_price, fill_qty)
                    elif self.options_engine.has_engine_position():
                        # Resolve by symbol to avoid same-lane mismatches when multiple intraday positions coexist.
                        intraday_pos = None
                        for candidate in self.options_engine.get_engine_positions():
                            candidate_symbol_norm = (
                                self._normalize_symbol_str(candidate.contract.symbol)
                                if candidate is not None and candidate.contract is not None
                                else ""
                            )
                            if candidate_symbol_norm == symbol_norm:
                                intraday_pos = candidate
                                break
                        live_qty_after_fill = abs(self._get_option_holding_quantity(symbol))
                        if intraday_pos is not None:
                            if live_qty_after_fill > 0:
                                intraday_pos.num_contracts = int(live_qty_after_fill)
                                # Re-arm OCO immediately so remaining contracts stay protected.
                                self._sync_engine_oco(
                                    symbol=symbol_norm,
                                    position=intraday_pos,
                                    quantity=int(live_qty_after_fill),
                                    reason="PARTIAL_CLOSE",
                                )
                                self.Log(
                                    f"INTRADAY_PARTIAL_CLOSE: {symbol_norm} | RemainingQty={live_qty_after_fill} | State retained"
                                )
                                self._greeks_breach_logged = False
                                return

                            removed_position = self.options_engine.remove_engine_position(
                                symbol=symbol_norm
                            )
                            if removed_position:
                                # Cancel any lingering OCO pair after explicit close fill.
                                try:
                                    self.oco_manager.cancel_by_symbol(
                                        removed_position.contract.symbol,
                                        reason="INTRADAY_POSITION_CLOSED",
                                    )
                                except Exception as e:
                                    self.Log(
                                        f"OCO_CLEANUP_ERROR: {removed_position.contract.symbol} | {e}"
                                    )
                                # P0 plumbing: when position is now flat, cancel any residual
                                # same-symbol non-OCO orders to prevent accidental short opens.
                                self._cancel_residual_option_orders(
                                    removed_position.contract.symbol,
                                    reason="INTRADAY_FLAT_FILLED",
                                )
                            if removed_position and removed_position.entry_price > 0:
                                snapshot = self._intraday_entry_snapshot.get(symbol_norm) or {}
                                closed_qty = abs(int(fill_qty))
                                try:
                                    snapshot_qty = int(
                                        snapshot.get("quantity", closed_qty) or closed_qty
                                    )
                                    if live_qty_after_fill <= 0 and snapshot_qty > 0:
                                        closed_qty = snapshot_qty
                                except Exception:
                                    closed_qty = abs(int(fill_qty))
                                is_win = fill_price > removed_position.entry_price
                                # V10.8: Do NOT feed MICRO outcomes into VASS spread win-rate / breaker state.
                                self.options_engine.record_engine_result(
                                    symbol=symbol,
                                    is_win=is_win,
                                    current_time=str(self.Time),
                                    strategy=getattr(removed_position, "entry_strategy", ""),
                                )
                                result_str = "WIN" if is_win else "LOSS"
                                self.Log(
                                    f"INTRADAY_RESULT: {result_str} | "
                                    f"Entry=${removed_position.entry_price:.2f} | Exit=${fill_price:.2f} | "
                                    f"P&L={((fill_price - removed_position.entry_price) / removed_position.entry_price):.1%}"
                                )
                                cached_reason = self._single_leg_last_exit_reason.pop(
                                    symbol_norm, ""
                                )
                                if not cached_reason:
                                    cached_reason = f"UNATTRIBUTED_EXIT:{self._compact_tag_for_log(order_tag, max_chars=48)}"
                                entry_strategy = getattr(removed_position, "entry_strategy", "")
                                engine_bucket = self._engine_bucket_from_strategy(entry_strategy)
                                self._record_exit_path_pnl(
                                    reason=cached_reason,
                                    order_tag=order_tag,
                                    pnl_dollars=(fill_price - removed_position.entry_price)
                                    * 100
                                    * closed_qty,
                                    engine_tag=engine_bucket,
                                )
                                self._diag_intraday_result_count += 1
                                self._diag_intraday_results_by_engine[engine_bucket] = (
                                    int(self._diag_intraday_results_by_engine.get(engine_bucket, 0))
                                    + 1
                                )
                                dte_for_result = (
                                    getattr(removed_position.contract, "days_to_expiry", None)
                                    if removed_position is not None and removed_position.contract
                                    else None
                                )
                                self._inc_micro_dte_counter(
                                    self._diag_micro_dte_win
                                    if is_win
                                    else self._diag_micro_dte_loss,
                                    dte_for_result,
                                )
                                # V6.12: Record trade in monthly P&L tracker
                                if hasattr(self, "pnl_tracker"):
                                    self.pnl_tracker.record_trade(
                                        symbol=symbol,
                                        engine="OPT_INTRADAY",
                                        entry_date=removed_position.entry_time[:10]
                                        if removed_position.entry_time
                                        else str(self.Time.date()),
                                        exit_date=str(self.Time.date()),
                                        entry_price=removed_position.entry_price,
                                        exit_price=fill_price,
                                        quantity=closed_qty,
                                    )
                                self._clear_micro_symbol_tracking(symbol_norm)
                            self._greeks_breach_logged = False  # Reset for next position
                        else:
                            # Symbol not found in intraday lane map.
                            # If intraday traces/snapshots still exist for this symbol, avoid touching
                            # swing state and let snapshot fallback accounting handle it below.
                            likely_intraday_orphan = (
                                symbol_norm in self._intraday_entry_snapshot
                                or symbol_norm in self._micro_open_symbols
                                or symbol_norm in self._itm_open_symbols
                            )
                            if likely_intraday_orphan:
                                self.Log(
                                    f"INTRADAY_EXIT_STATE_MISS: {symbol_norm} | "
                                    f"Skip swing fallback, awaiting snapshot reconciliation"
                                )
                            else:
                                removed_position = self.options_engine.remove_position(symbol)
                                if removed_position:
                                    if abs(self._get_option_holding_quantity(symbol)) <= 0:
                                        self._clear_micro_symbol_tracking(symbol_norm)
                                    try:
                                        self.oco_manager.cancel_by_symbol(
                                            removed_position.contract.symbol,
                                            reason="SINGLE_LEG_POSITION_CLOSED",
                                        )
                                    except Exception as e:
                                        self.Log(
                                            f"OCO_CLEANUP_ERROR: {removed_position.contract.symbol} | {e}"
                                        )
                            self._greeks_breach_logged = False
                    else:
                        # Single-leg exit (legacy swing)
                        removed_position = self.options_engine.remove_position(symbol)
                        if removed_position:
                            try:
                                self.oco_manager.cancel_by_symbol(
                                    removed_position.contract.symbol,
                                    reason="SINGLE_LEG_POSITION_CLOSED",
                                )
                            except Exception as e:
                                self.Log(
                                    f"OCO_CLEANUP_ERROR: {removed_position.contract.symbol} | {e}"
                                )
                            if (
                                float(getattr(removed_position, "entry_price", 0.0) or 0.0) > 0
                                and self._intraday_entry_snapshot.get(symbol_norm) is None
                            ):
                                cached_reason = self._single_leg_last_exit_reason.pop(
                                    symbol_norm, ""
                                )
                                if not cached_reason:
                                    cached_reason = f"UNATTRIBUTED_EXIT:{self._compact_tag_for_log(order_tag, max_chars=48)}"
                                self._record_exit_path_pnl(
                                    reason=cached_reason,
                                    order_tag=order_tag,
                                    pnl_dollars=(fill_price - removed_position.entry_price)
                                    * 100
                                    * abs(int(fill_qty)),
                                    engine_tag=self._engine_bucket_from_strategy(
                                        getattr(removed_position, "entry_strategy", "")
                                    ),
                                )
                        # V6.15: Fallback intraday result accounting for orphan/implicit exits.
                        snapshot = self._intraday_entry_snapshot.get(symbol_norm)
                        live_qty_after_fill = abs(self._get_option_holding_quantity(symbol))
                        if (
                            snapshot
                            and snapshot.get("entry_price", 0) > 0
                            and live_qty_after_fill <= 0
                        ):
                            self._intraday_entry_snapshot.pop(symbol_norm, None)
                            entry_price = float(snapshot["entry_price"])
                            is_win = fill_price > entry_price
                            self.options_engine.record_engine_result(
                                symbol=symbol,
                                is_win=is_win,
                                current_time=str(self.Time),
                                strategy=str(snapshot.get("entry_strategy", "")),
                            )
                            result_str = "WIN" if is_win else "LOSS"
                            fallback_strategy = str(snapshot.get("entry_strategy", "UNKNOWN"))
                            self.Log(
                                f"INTRADAY_RESULT: {result_str} | "
                                f"Entry=${entry_price:.2f} | Exit=${fill_price:.2f} | "
                                f"P&L={((fill_price - entry_price) / entry_price):.1%} | "
                                f"Strategy={fallback_strategy} | Path=FALLBACK"
                            )
                            cached_reason = self._single_leg_last_exit_reason.pop(symbol_norm, "")
                            if not cached_reason:
                                cached_reason = f"UNATTRIBUTED_EXIT:{self._compact_tag_for_log(order_tag, max_chars=48)}"
                            fallback_engine_bucket = self._engine_bucket_from_strategy(
                                str(snapshot.get("entry_strategy", ""))
                            )
                            closed_qty = abs(int(fill_qty))
                            try:
                                snapshot_qty = int(
                                    snapshot.get("quantity", closed_qty) or closed_qty
                                )
                                if snapshot_qty > 0:
                                    closed_qty = snapshot_qty
                            except Exception:
                                closed_qty = abs(int(fill_qty))
                            self._record_exit_path_pnl(
                                reason=cached_reason,
                                order_tag=order_tag,
                                pnl_dollars=(fill_price - entry_price) * 100 * closed_qty,
                                engine_tag=fallback_engine_bucket,
                            )
                            self._diag_intraday_result_count += 1
                            self._diag_intraday_results_by_engine[fallback_engine_bucket] = (
                                int(
                                    self._diag_intraday_results_by_engine.get(
                                        fallback_engine_bucket, 0
                                    )
                                )
                                + 1
                            )
                            self._inc_micro_dte_counter(
                                self._diag_micro_dte_win if is_win else self._diag_micro_dte_loss,
                                snapshot.get("entry_dte") if snapshot else None,
                            )
                            if hasattr(self, "pnl_tracker"):
                                self.pnl_tracker.record_trade(
                                    symbol=symbol,
                                    engine="OPT_INTRADAY",
                                    entry_date=str(snapshot.get("entry_time", str(self.Time)))[:10],
                                    exit_date=str(self.Time.date()),
                                    entry_price=entry_price,
                                    exit_price=fill_price,
                                    quantity=closed_qty,
                                )
                        self._greeks_breach_logged = False  # Reset for next position
            except Exception as e:
                self.Log(f"OPT_TRACK_ERROR: {symbol}: {e}")

    def _handle_order_rejection(self, symbol: str, order_event) -> None:
        """
        V2.20: Event-driven state recovery on order rejection/cancellation.

        Mirrors _on_fill() pattern — routes rejection events to the correct
        engine using symbol-based matching to clear pending locks ("zombie states").

        Called from OnOrderEvent for both OrderStatus.Invalid and OrderStatus.Canceled,
        AFTER existing specific handlers (margin CB, orphan legs, exit retry).

        Args:
            symbol: String symbol of the rejected/canceled order.
            order_event: Original OrderEvent from broker.
        """
        # Route 1: Trend symbols - V6.11: Updated to include UGL, UCO
        if symbol in config.TREND_SYMBOLS:
            # Trend MOO recovery — clear stuck pending slot
            if self.trend_engine.is_pending_moo(symbol):
                self.trend_engine.cancel_pending_moo(symbol)
                # Cooldown: skip until next EOD cycle (trend signals only fire at 15:45)
                self._trend_rejection_cooldown_until = self.Time + timedelta(hours=18)
                self.Log(
                    f"TREND_RECOVERY: {symbol} rejected | Pending MOO cleared | "
                    f"Cooldown until next EOD"
                )
            # Cold start warm entry recovery (both checks run, not elif)
            if (
                self.cold_start_engine.is_cold_start_active()
                and self.cold_start_engine.has_warm_entry_executed()
                and self.cold_start_engine.get_warm_entry_symbol() == symbol
            ):
                self.cold_start_engine.cancel_warm_entry()

        # Route 2: MR symbols - V6.11: Updated to include SPXL
        elif symbol in config.MR_SYMBOLS:
            self.mr_engine.cancel_pending_entry()
            # Cooldown: 15 minutes before MR can retry
            self._mr_rejection_cooldown_until = self.Time + timedelta(minutes=15)
            self.Log(
                f"MR_RECOVERY: {symbol} rejected | Pending state reset | "
                f"Cooldown 15min until {self._mr_rejection_cooldown_until}"
            )

        # Route 3: QQQ options
        elif "QQQ" in symbol and ("C" in symbol or "P" in symbol):
            symbol_norm = self._normalize_symbol_str(symbol)
            if self.options_engine.cancel_pending_engine_exit(symbol_norm):
                self._clear_engine_close_guard(symbol_norm)
                self.Log(
                    f"OPT_MICRO_EXIT_RECOVERY: Close rejected/canceled | "
                    f"Symbol={symbol_norm} | Exit lock cleared"
                )
            elif symbol_norm in self._intraday_close_in_progress_symbols:
                # Clear stale in-progress close guard even when options-engine lock is absent.
                # Prevents sticky EOD sweep state that blocks OCO recovery/retries.
                self._clear_engine_close_guard(symbol_norm)
                self.Log(
                    f"OPT_MICRO_EXIT_RECOVERY: Cleared stale close guard | Symbol={symbol_norm}"
                )

            # Check pending state to determine sub-mode (most specific first)
            spread_rejection_handled = False
            if self.options_engine.has_pending_spread_entry():
                pending_long, pending_short = self.options_engine.get_pending_spread_legs()
                pending_symbols = set()
                if pending_long is not None:
                    pending_symbols.add(self._normalize_symbol_str(pending_long.symbol))
                if pending_short is not None:
                    pending_symbols.add(self._normalize_symbol_str(pending_short.symbol))
                if symbol_norm in pending_symbols:
                    # V2.21: Parse broker margin for adaptive retry sizing
                    self._parse_and_store_rejection_margin(order_event)
                    self.options_engine.cancel_pending_spread_entry()
                    if self.portfolio_router:
                        self.portfolio_router.unregister_spread_margin_by_legs(
                            str(pending_long.symbol) if pending_long is not None else "",
                            str(pending_short.symbol) if pending_short is not None else None,
                        )
                    # Cooldown: 30 minutes before spread can retry
                    self._options_spread_cooldown_until = self.Time + timedelta(minutes=30)
                    # V3.0 P0-B: Clear main.py spread tracking state on rejection
                    if self._spread_fill_tracker is not None:
                        self.Log("REJECTION_CLEANUP: Clearing spread fill tracker")
                        self._spread_fill_tracker = None
                    if self._pending_spread_orders:
                        self.Log(
                            f"REJECTION_CLEANUP: Clearing "
                            f"{len(self._pending_spread_orders)} pending spread orders"
                        )
                        self._pending_spread_orders.clear()
                        self._pending_spread_orders_reverse.clear()
                    self.Log(
                        f"OPT_MACRO_RECOVERY: Spread rejected | Pending + margin cleared | "
                        f"Cooldown 30min until {self._options_spread_cooldown_until}"
                    )
                    spread_rejection_handled = True
                else:
                    throttle_key = (
                        "SPREAD_REJECT_UNMATCHED|"
                        f"{symbol_norm}|{','.join(sorted(pending_symbols)) or 'NONE'}"
                    )
                    if self.options_engine.should_log_vass_rejection(throttle_key):
                        self.Log(
                            f"OPT_MACRO_RECOVERY: Ignored unmatched spread rejection | "
                            f"Canceled={symbol_norm} | Pending={','.join(sorted(pending_symbols)) or 'NONE'}"
                        )
            if (not spread_rejection_handled) and self.options_engine.has_pending_engine_entry():
                pending_symbol = self.options_engine.get_pending_entry_contract_symbol()
                pending_lane = self.options_engine.get_pending_engine_entry_lane(symbol_norm)
                if pending_lane is None:
                    self._diag_micro_pending_cancel_ignored_count += 1
                    self.Log(
                        f"OPT_INTRADAY_RECOVERY: Ignored unmatched cancel | "
                        f"Canceled={symbol_norm} Pending={pending_symbol or 'UNKNOWN'}"
                    )
                else:
                    cleared_lane = self.options_engine.cancel_pending_engine_entry(
                        engine=pending_lane,
                        symbol=symbol_norm,
                    )
                    lane = str(cleared_lane or pending_lane or "MICRO").upper()
                    # Cooldown: 15 minutes before the rejected lane can retry.
                    lane_cooldown_until = self.Time + timedelta(minutes=15)
                    self._set_engine_lane_cooldown(lane, lane_cooldown_until)
                    self.Log(
                        f"OPT_INTRADAY_RECOVERY: Intraday rejected | Lane={lane} | "
                        f"Pending + counter cleared | Cooldown 15min until {lane_cooldown_until}"
                    )
            elif (not spread_rejection_handled) and self.options_engine.has_pending_swing_entry():
                self.options_engine.cancel_pending_swing_entry()
                # Cooldown: 30 minutes before swing can retry
                self._options_swing_cooldown_until = self.Time + timedelta(minutes=30)
                self.Log(
                    f"OPT_SWING_RECOVERY: Swing rejected | Pending cleared | "
                    f"Cooldown 30min until {self._options_swing_cooldown_until}"
                )

    def _handle_spread_leg_close(self, symbol: str, fill_price: float, fill_qty: float) -> None:
        """
        V2.6: Handle a spread leg close with quantity validation.

        Fixes Bug #8 (no qty validation on close).

        Args:
            symbol: Closed option symbol.
            fill_price: Fill price.
            fill_qty: Fill quantity (negative for sells).
        """
        norm_symbol = self._normalize_symbol_str(symbol)
        spread = None
        for candidate in self.options_engine.get_spread_positions():
            long_norm = self._normalize_symbol_str(candidate.long_leg.symbol)
            short_norm = self._normalize_symbol_str(candidate.short_leg.symbol)
            if norm_symbol == long_norm or norm_symbol == short_norm:
                spread = candidate
                break
        if spread is None:
            return

        fill_qty_abs = int(abs(fill_qty))
        spread_key = self._build_spread_runtime_key(spread)
        tracker = self._spread_close_trackers.get(spread_key)
        if tracker is None:
            tracker = {
                "expected_qty": spread.num_spreads,
                "long_closed": False,
                "short_closed": False,
                "long_qty": 0,
                "short_qty": 0,
                "long_notional": 0.0,
                "short_notional": 0.0,
            }
            self._spread_close_trackers[spread_key] = tracker

        # Track which leg closed and accumulate quantity
        long_norm = self._normalize_symbol_str(spread.long_leg.symbol)
        short_norm = self._normalize_symbol_str(spread.short_leg.symbol)
        if norm_symbol == long_norm:
            tracker["long_qty"] += fill_qty_abs
            tracker["long_notional"] += fill_price * fill_qty_abs
            tracker["long_closed"] = tracker["long_qty"] >= tracker["expected_qty"]
            self.Log(
                f"SPREAD: Long leg closed | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty_abs} | "
                f"Total closed={tracker['long_qty']}/{tracker['expected_qty']}"
            )
        elif norm_symbol == short_norm:
            tracker["short_qty"] += fill_qty_abs
            tracker["short_notional"] += fill_price * fill_qty_abs
            tracker["short_closed"] = tracker["short_qty"] >= tracker["expected_qty"]
            self.Log(
                f"SPREAD: Short leg closed | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty_abs} | "
                f"Total closed={tracker['short_qty']}/{tracker['expected_qty']}"
            )

        # Check if both legs reached expected close quantity.
        if tracker["long_closed"] and tracker["short_closed"]:
            expected_qty = int(tracker["expected_qty"])
            if tracker["long_qty"] != expected_qty:
                self.Log(
                    f"SPREAD_WARNING: Long leg quantity mismatch | "
                    f"Closed={tracker['long_qty']} Expected={expected_qty}"
                )
            if tracker["short_qty"] != expected_qty:
                self.Log(
                    f"SPREAD_WARNING: Short leg quantity mismatch | "
                    f"Closed={tracker['short_qty']} Expected={expected_qty}"
                )

            # Count fill reconciliation only when both legs fully match expected quantity.
            if tracker["long_qty"] == expected_qty and tracker["short_qty"] == expected_qty:
                self._diag_spread_exit_fill_count += 1

            # Use weighted average close prices to avoid partial-fill accounting distortion.
            close_long_price = (
                float(tracker["long_notional"]) / float(tracker["long_qty"])
                if tracker["long_qty"] > 0
                else 0.0
            )
            close_short_price = (
                float(tracker["short_notional"]) / float(tracker["short_qty"])
                if tracker["short_qty"] > 0
                else 0.0
            )
            close_value = close_long_price - close_short_price
            # Unified sign convention for debit/credit spreads:
            # win when close value is greater than entry net value.
            is_win = close_value > spread.net_debit
            self.options_engine.record_spread_result(is_win)
            result_str = "WIN" if is_win else "LOSS"
            self.Log(
                f"SPREAD_RESULT: {result_str} | Type={spread.spread_type} | "
                f"Entry={spread.net_debit:.2f} | Close={close_value:.2f}"
            )
            pnl_pct = (
                ((close_value - spread.net_debit) / abs(spread.net_debit))
                if spread.net_debit
                else 0.0
            )
            exit_reason = self._spread_last_exit_reason.get(spread_key, "FILL_CLOSE_RECONCILED")
            self._budget_log(
                f"SPREAD: EXIT | Reason={exit_reason} | "
                f"Type={spread.spread_type} | Entry={spread.net_debit:.2f} | "
                f"Close={close_value:.2f} | PnL={pnl_pct:+.1%}",
                priority=1,
            )
            self._record_exit_path_pnl(
                reason=exit_reason,
                pnl_dollars=(close_value - spread.net_debit) * 100 * spread.num_spreads,
                engine_tag="VASS",
            )
            self._record_order_lifecycle_event(
                status="SPREAD_EXIT_FILLED",
                order_id=0,
                symbol=str(long_norm or ""),
                quantity=int(expected_qty),
                fill_price=float(close_value),
                order_type=str(spread.spread_type or ""),
                order_tag="SPREAD_CLOSE",
                trace_id="",
                message=(
                    f"Key={spread_key} | Short={short_norm} | Reason={exit_reason} | "
                    f"Entry={spread.net_debit:.4f} | Close={close_value:.4f} | PnL={pnl_pct:+.4f}"
                ),
                source="SPREAD_RECONCILE",
            )

            stop_pct = float(getattr(config, "SPREAD_STOP_LOSS_PCT", 0.0))
            if stop_pct > 0 and pnl_pct <= -stop_pct:
                self._diag_spread_loss_beyond_stop_count += 1
                self.Log(
                    f"SPREAD_STOP_BREACH: Type={spread.spread_type} | "
                    f"PnL={pnl_pct:+.1%} <= -{stop_pct:.0%} | "
                    f"Reason={exit_reason}"
                )

            # V6.12: Record spread trade in monthly P&L tracker
            if hasattr(self, "pnl_tracker"):
                # Calculate realized P&L (×100 for options multiplier, ×num_spreads for quantity)
                spread_pnl = (close_value - spread.net_debit) * 100 * spread.num_spreads
                # Record as single trade with net P&L
                self.pnl_tracker.record_trade(
                    symbol=f"SPREAD:{spread.spread_type}",
                    engine="OPT_SPREAD",
                    entry_date=spread.entry_time[:10]
                    if spread.entry_time
                    else str(self.Time.date()),
                    exit_date=str(self.Time.date()),
                    entry_price=spread.net_debit,
                    exit_price=close_value,
                    quantity=spread.num_spreads,
                    realized_pnl=spread_pnl,
                )

            # Both legs closed - remove spread position
            removed = self.options_engine.remove_spread_position(symbol)
            if removed is not None:
                self._record_spread_removal(
                    reason="fill_path",
                    count=1,
                    context="FILL_CLOSE_RECONCILED",
                )
                if self.portfolio_router:
                    self.portfolio_router.unregister_spread_margin_by_legs(
                        str(removed.long_leg.symbol),
                        str(removed.short_leg.symbol),
                    )
            else:
                self.Log(
                    "SPREAD_DIAG_WARNING: Fill-path counter skipped | "
                    f"Reason=REMOVE_RETURNED_NONE | Symbol={symbol}"
                )
            self._greeks_breach_logged = False  # Reset for next position
            self._spread_forced_close_retry.pop(spread_key, None)
            self._spread_forced_close_reason.pop(spread_key, None)
            self._spread_forced_close_cancel_counts.pop(spread_key, None)
            self._spread_forced_close_retry_cycles.pop(spread_key, None)
            self._spread_last_close_submit_at.pop(spread_key, None)
            self._spread_last_exit_reason.pop(spread_key, None)
            self._spread_close_trackers.pop(spread_key, None)

            self.Log("SPREAD: Position removed - both legs closed")

    def _handle_spread_leg_fill(self, symbol: str, fill_price: float, fill_qty: float) -> None:
        """
        V2.6: Handle a spread leg fill with atomic tracking.

        Fixes Bug #1 (race condition), Bug #6 (quantity tracking), Bug #7 (timeout).

        Uses SpreadFillTracker to store symbols at creation time, preventing race
        condition where options_engine pending legs are cleared before second fill.

        Args:
            symbol: Filled option symbol.
            fill_price: Fill price.
            fill_qty: Fill quantity (absolute value).
        """
        fill_qty = int(abs(fill_qty))  # Ensure positive integer

        # Initialize tracker on FIRST leg fill (capture symbols now, before they can be cleared)
        if self._spread_fill_tracker is None:
            seed = self.options_engine.get_pending_spread_tracker_seed()
            if seed is None:
                self.Log(f"SPREAD_ERROR: Fill received but no pending spread | {symbol}")
                return

            self._spread_fill_tracker = SpreadFillTracker(
                long_leg_symbol=seed["long_leg_symbol"],
                short_leg_symbol=seed["short_leg_symbol"],
                expected_quantity=int(seed["expected_quantity"]),
                timeout_minutes=config.SPREAD_FILL_TIMEOUT_MINUTES,
                created_at=str(self.Time),
                spread_type=seed.get("spread_type"),
            )
            self.Log(
                f"SPREAD: Fill tracker created | "
                f"Long={seed['long_leg_symbol'][-15:]} Short={seed['short_leg_symbol'][-15:]} "
                f"Expected={int(seed['expected_quantity'])}"
            )

        tracker = self._spread_fill_tracker

        # Check for timeout
        if tracker.is_expired(str(self.Time)):
            self.Log(
                f"SPREAD_ERROR: Fill tracker expired | "
                f"Created={tracker.created_at} Current={self.Time}"
            )
            self._cleanup_stale_spread_state()
            return

        # Record fill by symbol match (using normalized keys from tracker + event)
        symbol_norm = self._normalize_symbol_str(symbol)
        long_norm = self._normalize_symbol_str(tracker.long_leg_symbol)
        short_norm = self._normalize_symbol_str(tracker.short_leg_symbol)
        if symbol_norm and symbol_norm == long_norm:
            tracker.record_long_fill(fill_price, fill_qty, str(self.Time))
            self.Log(
                f"SPREAD: Long leg filled | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty} | "
                f"Total={tracker.long_fill_qty}"
            )

        elif symbol_norm and symbol_norm == short_norm:
            tracker.record_short_fill(fill_price, fill_qty, str(self.Time))
            self.Log(
                f"SPREAD: Short leg filled | {symbol[-20:]} @ ${fill_price:.2f} x{fill_qty} | "
                f"Total={tracker.short_fill_qty}"
            )
        else:
            self.Log(
                f"SPREAD_WARNING: Unknown fill symbol | {symbol} | "
                f"Expected Long={tracker.long_leg_symbol[-15:]} Short={tracker.short_leg_symbol[-15:]}"
            )
            return

        # Check if both legs filled with expected quantity
        spread = None
        if tracker.is_complete():
            # Validate quantities match (Bug #6 fix)
            if not tracker.quantities_match():
                self.Log(
                    f"SPREAD_ERROR: Quantity mismatch | "
                    f"Long={tracker.long_fill_qty} Short={tracker.short_fill_qty} "
                    f"Expected={tracker.expected_quantity}"
                )
                if config.SPREAD_FILL_QTY_MISMATCH_ACTION == "LOG_AND_CLOSE":
                    self._emergency_close_spread_legs()
                    self._spread_fill_tracker = None
                    return

            # Both legs filled - register spread position
            spread = self.options_engine.register_spread_entry(
                long_leg_fill_price=tracker.long_fill_price,
                short_leg_fill_price=tracker.short_fill_price,
                entry_time=str(self.Time),
                current_date=str(self.Time.date()),
                regime_score=self._get_effective_regime_score_for_options(),
            )

            if spread:
                self._diag_spread_entry_fill_count += 1
                self.Log(
                    f"SPREAD: Position registered | {spread.spread_type} | "
                    f"Debit=${spread.net_debit:.2f} | Max Profit=${spread.max_profit:.2f} | "
                    f"Qty={tracker.expected_quantity}"
                )

            # Clear tracker
            self._spread_fill_tracker = None

    def _queue_spread_close_retry_on_cancel(self, symbol: str, order_event) -> None:
        """
        V6.21: Queue forced spread close retry when broker cancels a close leg.

        We only queue retries for active spread symbols where the canceled order
        appears to be reducing an existing position (close-side quantity).
        """
        symbol_norm = self._normalize_symbol_str(symbol)
        if "QQQ" not in symbol_norm or ("C" not in symbol_norm and "P" not in symbol_norm):
            return

        spread = None
        for candidate in self.options_engine.get_spread_positions():
            long_norm = self._normalize_symbol_str(candidate.long_leg.symbol)
            short_norm = self._normalize_symbol_str(candidate.short_leg.symbol)
            if symbol_norm in {long_norm, short_norm}:
                spread = candidate
                break
        if spread is None:
            return

        try:
            order = self.Transactions.GetOrderById(order_event.OrderId)
        except Exception:
            order = None
        if order is None:
            return

        # Only queue retries for close-side cancels:
        # long-leg close => order qty < 0 while holdings qty > 0
        # short-leg close => order qty > 0 while holdings qty < 0
        holding, _ = self._find_portfolio_holding(
            order_event.Symbol, security_type=SecurityType.Option
        )
        holdings_qty = int(getattr(holding, "Quantity", 0) or 0) if holding is not None else 0
        is_close_side = (order.Quantity < 0 < holdings_qty) or (order.Quantity > 0 > holdings_qty)
        if not is_close_side:
            return

        long_symbol = self._normalize_symbol_str(spread.long_leg.symbol)
        short_symbol = self._normalize_symbol_str(spread.short_leg.symbol)
        spread_key = self._build_spread_runtime_key(spread)
        self._cancel_spread_linked_oco(
            long_symbol, short_symbol, reason="SPREAD_CLOSE_CANCELED_RETRY"
        )
        cancel_count = self._spread_forced_close_cancel_counts.get(spread_key, 0) + 1
        self._spread_forced_close_cancel_counts[spread_key] = cancel_count
        self._diag_spread_exit_canceled_count += 1
        escalation_count = int(getattr(config, "SPREAD_CLOSE_CANCEL_ESCALATION_COUNT", 2))

        self._spread_forced_close_retry[spread_key] = self.Time
        self._spread_forced_close_reason[
            spread_key
        ] = f"ORDER_CANCELED:{getattr(order, 'Type', 'UNKNOWN')}"
        self._spread_last_close_submit_at[spread_key] = self.Time

        if cancel_count >= escalation_count:
            self._diag_spread_close_escalation_count += 1
            order_tag = str(getattr(order, "Tag", "") or "")
            self._record_order_lifecycle_event(
                status="SPREAD_EXIT_CANCELED",
                order_id=int(order_event.OrderId or 0),
                symbol=str(symbol_norm or ""),
                quantity=int(getattr(order, "Quantity", 0) or 0),
                fill_price=0.0,
                order_type=str(getattr(order, "Type", "UNKNOWN")),
                order_tag=order_tag,
                trace_id=self._extract_trace_id_from_tag(order_tag) or "",
                message=(
                    f"Key={spread_key} | Long={long_symbol} | Short={short_symbol} | "
                    f"CancelCount={cancel_count} | EscalationThreshold={escalation_count}"
                ),
                source="SPREAD_RETRY",
            )
            self.Log(
                f"SPREAD_CLOSE_ESCALATED: Cancel threshold reached | "
                f"OrderId={order_event.OrderId} | SpreadKey={spread_key} | "
                f"Tag={self._compact_tag_for_log(order_tag)} | "
                f"Long={long_symbol} | Short={short_symbol} | "
                f"CancelCount={cancel_count} >= {escalation_count} | "
                f"Submitting immediate sequential close"
            )
            self.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=long_symbol,
                    target_weight=0.0,
                    source="OPT",
                    urgency=Urgency.IMMEDIATE,
                    reason="SPREAD_CLOSE_ESCALATED",
                    requested_quantity=spread.num_spreads,
                    metadata={
                        "spread_close_short": True,
                        "spread_short_leg_symbol": short_symbol,
                        "spread_short_leg_quantity": spread.num_spreads,
                        "spread_key": self._build_spread_runtime_key(spread),
                        "exit_type": "SPREAD_CLOSE_ESCALATED",
                        "spread_exit_code": "SPREAD_CLOSE_ESCALATED",
                        "spread_exit_reason": "SPREAD_CLOSE_ESCALATED",
                        "spread_exit_emergency": True,
                    },
                )
            )
            self._diag_spread_exit_signal_count += 1
            self._diag_spread_exit_submit_count += 1
            self._spread_last_close_submit_at[spread_key] = self.Time
        else:
            order_tag = str(getattr(order, "Tag", "") or "")
            self._record_order_lifecycle_event(
                status="SPREAD_EXIT_CANCELED",
                order_id=int(order_event.OrderId or 0),
                symbol=str(symbol_norm or ""),
                quantity=int(getattr(order, "Quantity", 0) or 0),
                fill_price=0.0,
                order_type=str(getattr(order, "Type", "UNKNOWN")),
                order_tag=order_tag,
                trace_id=self._extract_trace_id_from_tag(order_tag) or "",
                message=(
                    f"Key={spread_key} | Long={long_symbol} | Short={short_symbol} | "
                    f"CancelCount={cancel_count} | Escalated=False"
                ),
                source="SPREAD_RETRY",
            )
            self.Log(
                f"SPREAD_RETRY_QUEUED: Close leg canceled | "
                f"OrderId={order_event.OrderId} | SpreadKey={spread_key} | "
                f"Tag={self._compact_tag_for_log(order_tag)} | Symbol={symbol} | "
                f"Long={long_symbol} | Short={short_symbol} | "
                f"OrderQty={order.Quantity} | CancelCount={cancel_count}"
            )
