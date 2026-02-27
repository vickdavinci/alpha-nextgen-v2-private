from __future__ import annotations

from typing import Any

from AlgorithmImports import SecurityType, Slice

import config


class MainReconcileMixin:
    def _is_primary_market_open(self) -> bool:
        """Return True when the primary equity market session is open."""
        try:
            exchange_hours = self.Securities[self.qqq].Exchange.Hours
            return bool(exchange_hours.IsOpen(self.Time, False))
        except Exception:
            # Conservative fallback if exchange metadata is unavailable.
            return self.Time.weekday() < 5

    def _on_engine_reconcile(self) -> None:
        """
        #8 fix: periodic intraday broker-vs-engine reconciliation.

        Reduces zombie/orphan persistence from full-day to sub-day windows.
        """
        if self.IsWarmingUp:
            return
        if not self._is_primary_market_open():
            return
        now_dt = self.Time
        if self._last_reconcile_positions_run is not None:
            elapsed_min = (now_dt - self._last_reconcile_positions_run).total_seconds() / 60.0
            if elapsed_min < 20:
                return
        self._reconcile_positions(mode="intraday")

    def _reconcile_spread_state(self) -> None:
        """V9.2: Run a single guarded Friday spread reconcile sweep."""
        today = str(self.Time.date())
        if self._friday_spread_reconcile_date == today:
            return
        self._friday_spread_reconcile_date = today
        self._reconcile_positions(mode="friday")

    def _build_spread_runtime_key(self, spread: Any) -> str:
        """Build canonical spread key for runtime trackers."""
        return (
            f"{self._normalize_symbol_str(spread.long_leg.symbol)}|"
            f"{self._normalize_symbol_str(spread.short_leg.symbol)}|"
            f"{str(getattr(spread, 'entry_time', '') or '')}"
        )

    def _record_spread_exit_reason(self, spread_key: str, reason: str) -> None:
        """Persist latest spread exit reason for fill-path attribution."""
        if not spread_key:
            return
        reason_str = str(reason or "").strip()
        if not reason_str:
            return
        self._spread_last_exit_reason[spread_key] = reason_str[:180]

    def _record_spread_removal(self, reason: str, count: int = 1, context: str = "") -> None:
        """Centralized spread-removal diagnostics accounting."""
        if count <= 0:
            return
        self._diag_spread_position_removed_count += count
        if reason == "fill_path":
            self._diag_spread_removed_fill_path_count += count
        elif reason == "ghost_path":
            self._diag_spread_ghost_removed_count += count
        else:
            self.Log(
                f"SPREAD_DIAG_WARNING: Unknown removal reason '{reason}' | "
                f"Count={count} | Context={context}"
            )

    def _reconcile_spread_ghosts(self, mode: str) -> int:
        """
        Reconcile spread state ghosts with mode-aware clearing policy.

        Modes:
            - sod: immediate guarded clear when both legs are flat
            - friday: immediate guarded clear once per day sweep
            - intraday: non-destructive health check; emergency clear only after
              N consecutive flat detections
        """
        mode_norm = str(mode or "sod").strip().lower()
        spreads = list(self.options_engine.get_spread_positions())
        if not spreads:
            return 0

        threshold = max(1, int(getattr(config, "SPREAD_GHOST_INTRADAY_CLEAR_CONSECUTIVE", 2)))
        health_log_minutes = max(1, int(getattr(config, "SPREAD_GHOST_HEALTH_LOG_MINUTES", 60)))
        cleared = 0

        for spread in spreads:
            spread_key = self._build_spread_runtime_key(spread)

            long_held = self.Portfolio[spread.long_leg.symbol].Invested
            short_held = self.Portfolio[spread.short_leg.symbol].Invested
            if long_held or short_held:
                self._spread_ghost_flat_streak_by_key.pop(spread_key, None)
                self._spread_ghost_last_log_by_key.pop(spread_key, None)
                continue

            streak = self._spread_ghost_flat_streak_by_key.get(spread_key, 0) + 1
            self._spread_ghost_flat_streak_by_key[spread_key] = streak

            should_clear = mode_norm in {"sod", "friday"}
            if mode_norm == "intraday" and streak >= threshold:
                should_clear = True

            last_log = self._spread_ghost_last_log_by_key.get(spread_key)
            due = (
                last_log is None
                or (self.Time - last_log).total_seconds() / 60.0 >= health_log_minutes
            )
            if (due or should_clear) and self._should_log_backtest_category(
                "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
            ):
                self.Log(
                    f"SPREAD_GHOST_HEALTH: Mode={mode_norm.upper()} | "
                    f"Key={spread_key} | FlatStreak={streak} | "
                    f"Threshold={threshold}"
                )
                self._spread_ghost_last_log_by_key[spread_key] = self.Time

            if not should_clear:
                continue

            self._clear_spread_runtime_trackers_by_key(spread_key)
            removed = self.options_engine.remove_spread_position(str(spread.long_leg.symbol))
            if removed is None:
                continue

            self._record_spread_removal(
                reason="ghost_path",
                count=1,
                context=f"RECON_{mode_norm.upper()}_BOTH_LEGS_FLAT",
            )
            cleared += 1
            self.Log(
                f"SPREAD_GHOST_CLEAR: Mode={mode_norm.upper()} | "
                f"Key={spread_key} | FlatStreak={streak}"
            )
            if self.portfolio_router:
                self.portfolio_router.unregister_spread_margin_by_legs(
                    str(removed.long_leg.symbol),
                    str(removed.short_leg.symbol),
                )

        return cleared

    def _clear_spread_runtime_trackers_by_key(self, spread_key: str) -> None:
        """Clear runtime tracker maps for a spread key (long|short)."""
        self._spread_close_trackers.pop(spread_key, None)
        self._spread_forced_close_retry.pop(spread_key, None)
        self._spread_forced_close_reason.pop(spread_key, None)
        self._spread_forced_close_cancel_counts.pop(spread_key, None)
        self._spread_forced_close_retry_cycles.pop(spread_key, None)
        self._spread_last_close_submit_at.pop(spread_key, None)
        self._spread_last_exit_reason.pop(spread_key, None)
        self._spread_exit_mark_cache.pop(spread_key, None)
        self._spread_ghost_flat_streak_by_key.pop(spread_key, None)
        self._spread_ghost_last_log_by_key.pop(spread_key, None)

    def _check_splits(self, data: Slice) -> bool:
        """
        Check for stock splits on proxy and traded symbols.

        Split handling:
            - Proxy symbol split (SPY, RSP, HYG, IEF): Freeze ALL processing
            - Traded symbol split: Freeze only that symbol

        Args:
            data: Current data slice from OnData.

        Returns:
            True if proxy split detected (freeze all), False otherwise.
        """
        # Check proxy symbols - freeze EVERYTHING if any split
        for proxy in self.proxy_symbols:
            if data.Splits.ContainsKey(proxy):
                # Only log split once per symbol per day
                proxy_str = str(proxy)
                if proxy_str not in self._splits_logged_today:
                    self.Log(f"SPLIT: {proxy_str} (proxy) - freezing all")
                    self._splits_logged_today.add(proxy_str)
                return True

        # Check traded symbols - freeze only that symbol
        for symbol in self.traded_symbols:
            if data.Splits.ContainsKey(symbol):
                symbol_str = str(symbol)
                self.symbols_to_skip.add(symbol_str)
                # Register with risk engine for tracking
                self.risk_engine.register_split(symbol_str)
                # Only log split once per symbol per day
                if symbol_str not in self._splits_logged_today:
                    self.Log(f"SPLIT: {symbol_str} - freezing symbol")
                    self._splits_logged_today.add(symbol_str)

        return False

    def _reconcile_positions(self, mode: str = "sod") -> None:
        """
        Reconcile internal position tracking with broker state.

        Modes:
            - sod: baseline reconciliation (allows guarded spread ghost clear)
            - intraday: orphan cleanup + guarded emergency spread clear only
            - friday: single guarded late-week sweep
        """
        try:
            if not self._is_primary_market_open():
                return
            mode_norm = str(mode or "sod").strip().lower()
            if mode_norm not in {"sod", "intraday", "friday"}:
                mode_norm = "sod"
            self._last_reconcile_positions_run = self.Time

            # Mode-aware spread ghost health checks and guarded clears.
            self._reconcile_spread_ghosts(mode=mode_norm)

            option_holdings = {}
            option_symbols = {}
            for kvp in self.Portfolio:
                holding = kvp.Value
                if not holding.Invested:
                    continue
                symbol = holding.Symbol
                if symbol.SecurityType != SecurityType.Option:
                    continue
                option_holdings[str(symbol)] = int(holding.Quantity)
                option_symbols[str(symbol)] = symbol

            tracked_symbols = set()
            for spread in self.options_engine.get_spread_positions():
                tracked_symbols.add(str(spread.long_leg.symbol))
                tracked_symbols.add(str(spread.short_leg.symbol))

            for intraday in self.options_engine.get_engine_positions():
                if intraday is not None and intraday.contract is not None:
                    tracked_symbols.add(str(intraday.contract.symbol))

            single = self.options_engine.get_position()
            if single is not None:
                tracked_symbols.add(str(single.contract.symbol))

            # Keep intraday tracker symbols in reconcile scope when holdings still exist.
            # This avoids false orphan liquidations during transient state desync.
            for sym in list(self._micro_open_symbols) + list(self._itm_open_symbols):
                if sym in option_holdings:
                    tracked_symbols.add(sym)

            if tracked_symbols and not option_holdings:
                if mode_norm != "intraday":
                    spread_count_before = len(self.options_engine.get_spread_positions())
                    self.options_engine.clear_all_positions()
                    self._spread_close_trackers.clear()
                    self._spread_forced_close_retry.clear()
                    self._spread_forced_close_reason.clear()
                    self._spread_forced_close_cancel_counts.clear()
                    self._spread_forced_close_retry_cycles.clear()
                    self._spread_last_close_submit_at.clear()
                    self._spread_exit_mark_cache.clear()
                    self._spread_ghost_flat_streak_by_key.clear()
                    self._spread_ghost_last_log_by_key.clear()
                    if spread_count_before > 0:
                        # Broker is flat while engine still tracked live spreads.
                        # Count as state removals only (not confirmed fill reconciliations).
                        self._record_spread_removal(
                            reason="ghost_path",
                            count=spread_count_before,
                            context=f"RECON_{mode_norm.upper()}_ZOMBIE_CLEAR",
                        )
                    if self.portfolio_router:
                        self.portfolio_router.clear_all_spread_margins()
                    self.Log(
                        f"RECON_ZOMBIE_CLEARED: Cleared stale internal option state | "
                        f"Mode={mode_norm.upper()} | Tracked={len(tracked_symbols)}"
                    )
                    tracked_symbols = set()
                else:
                    if self._should_log_backtest_category(
                        "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                    ):
                        self.Log(
                            f"RECON_INTRADAY_SKIP_ZOMBIE_CLEAR: Tracked={len(tracked_symbols)} | "
                            f"HoldingOptions={len(option_holdings)}"
                        )
                    # Keep guarded spread ghost policy intact intraday, but clear stale
                    # single-leg state when broker is flat and no option orders remain.
                    has_open_option_orders = any(
                        order.Symbol.SecurityType == SecurityType.Option
                        for order in self.Transactions.GetOpenOrders()
                    )
                    if not has_open_option_orders:
                        for intraday_pos in self.options_engine.get_engine_positions():
                            if intraday_pos is not None and intraday_pos.contract is not None:
                                intraday_sym = self._normalize_symbol_str(
                                    intraday_pos.contract.symbol
                                )
                                self.options_engine.remove_engine_position(symbol=intraday_sym)
                                self._clear_micro_symbol_tracking(intraday_sym)
                        swing_pos = self.options_engine.get_position()
                        if swing_pos is not None:
                            swing_sym = self._normalize_symbol_str(swing_pos.contract.symbol)
                            self.options_engine.remove_position(swing_sym)
                            self._clear_micro_symbol_tracking(swing_sym)
                        if self.options_engine.has_pending_engine_entry():
                            self.options_engine.cancel_pending_engine_entry()
                        if self.options_engine.has_pending_swing_entry():
                            self.options_engine.cancel_pending_swing_entry()
                        self.options_engine.cancel_pending_engine_exit()
                        for stale_sym in list(self._micro_open_symbols) + list(
                            self._itm_open_symbols
                        ):
                            self._clear_micro_symbol_tracking(stale_sym)
                        tracked_symbols = set()
                        if self._should_log_backtest_category(
                            "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                        ):
                            self.Log(
                                "RECON_INTRADAY_STALE_SINGLE_CLEARED: Broker flat, no open option orders"
                            )

            # Friday sweep is spread-state only to avoid introducing new close-order behavior.
            if mode_norm == "friday":
                return

            orphan_symbols = [s for s in option_holdings.keys() if s not in tracked_symbols]
            orphan_set = set(orphan_symbols)

            # Clear orphan guard state for symbols that are no longer orphaned.
            stale_symbols = [
                s for s in list(self._recon_orphan_seen_streak.keys()) if s not in orphan_set
            ]
            for stale_sym in stale_symbols:
                self._recon_orphan_seen_streak.pop(stale_sym, None)
                self._recon_orphan_first_seen_at.pop(stale_sym, None)
                self._recon_orphan_last_log_at.pop(stale_sym, None)

            intraday_min_streak = int(getattr(config, "RECON_INTRADAY_ORPHAN_MIN_STREAK", 2))
            intraday_min_age_min = float(
                getattr(config, "RECON_INTRADAY_ORPHAN_MIN_AGE_MINUTES", 20)
            )
            intraday_guard_log_min = float(
                getattr(config, "RECON_INTRADAY_ORPHAN_LOG_THROTTLE_MINUTES", 30)
            )

            for sym_str in orphan_symbols:
                try:
                    today = str(self.Time.date())
                    # V10.8: avoid RECON_ORPHAN churn when same-day MICRO sweep/force-close is already active.
                    sweep_submitted_today = (
                        self._intraday_force_exit_submitted_symbols.get(sym_str) == today
                    )
                    if sweep_submitted_today or sym_str in self._intraday_close_in_progress_symbols:
                        if self._should_log_backtest_category(
                            "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                        ):
                            self.Log(
                                f"RECON_ORPHAN_SKIP_SWEEP_IN_PROGRESS: {sym_str} | Mode={mode_norm.upper()}"
                            )
                        continue

                    if self._recon_orphan_close_submitted.get(sym_str) == today:
                        if self._has_open_order_for_symbol(
                            sym_str, tag_contains="RECON_ORPHAN_OPTION"
                        ):
                            continue
                        self._recon_orphan_close_submitted.pop(sym_str, None)

                    # Avoid duplicate orphan close submits while a prior order is still open.
                    has_pending_orphan_close = self._has_open_order_for_symbol(
                        sym_str, tag_contains="RECON_ORPHAN_OPTION"
                    )
                    has_any_open_order = self._has_open_order_for_symbol(sym_str)
                    if has_pending_orphan_close:
                        continue

                    broker_symbol = option_symbols[sym_str]
                    holding = self.Portfolio[broker_symbol]
                    if not holding.Invested or abs(float(holding.Quantity)) <= 0:
                        self.Log(
                            f"RECON_ORPHAN_SKIP: {sym_str} | No live position at liquidation time"
                        )
                        self._clear_micro_symbol_tracking(sym_str)
                        self._recon_orphan_seen_streak.pop(sym_str, None)
                        self._recon_orphan_first_seen_at.pop(sym_str, None)
                        self._recon_orphan_last_log_at.pop(sym_str, None)
                        continue

                    # Never orphan-liquidate while any open order exists for the symbol.
                    # This avoids SOD churn when live OCO protection exists but internal
                    # tracking temporarily desynced.
                    if mode_norm != "intraday" and has_any_open_order:
                        if self._should_log_backtest_category(
                            "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                        ):
                            self.Log(
                                f"RECON_ORPHAN_SKIP_OPEN_ORDERS: {sym_str} | "
                                f"Mode={mode_norm.upper()} | Qty={holding.Quantity}"
                            )
                        continue

                    if mode_norm == "intraday":
                        recent_tag = self._get_recent_symbol_fill_tag(sym_str, max_age_minutes=90)
                        if recent_tag:
                            if self._should_log_backtest_category(
                                "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                            ):
                                self.Log(
                                    f"RECON_ORPHAN_SKIP_RECENT_FILL: {sym_str} | Tag={recent_tag}"
                                )
                            continue
                        first_seen = self._recon_orphan_first_seen_at.get(sym_str)
                        if first_seen is None:
                            first_seen = self.Time
                            self._recon_orphan_first_seen_at[sym_str] = first_seen
                        streak = int(self._recon_orphan_seen_streak.get(sym_str, 0)) + 1
                        self._recon_orphan_seen_streak[sym_str] = streak
                        age_min = (self.Time - first_seen).total_seconds() / 60.0

                        # Guard intraday orphan liquidation to avoid transient desync churn.
                        if (
                            streak < intraday_min_streak
                            or age_min < intraday_min_age_min
                            or has_any_open_order
                        ):
                            last_log_at = self._recon_orphan_last_log_at.get(sym_str)
                            should_log = (
                                last_log_at is None
                                or (self.Time - last_log_at).total_seconds() / 60.0
                                >= intraday_guard_log_min
                            )
                            if should_log and self._should_log_backtest_category(
                                "LOG_SPREAD_RECONCILE_BACKTEST_ENABLED", False
                            ):
                                self.Log(
                                    f"RECON_ORPHAN_GUARD_HOLD: {sym_str} | "
                                    f"Mode=INTRADAY | Streak={streak}/{intraday_min_streak} | "
                                    f"AgeMin={age_min:.1f}/{intraday_min_age_min:.1f} | "
                                    f"OpenOrders={1 if has_any_open_order else 0}"
                                )
                                self._recon_orphan_last_log_at[sym_str] = self.Time
                            continue

                    close_qty = -int(holding.Quantity)
                    ticket = self._submit_option_close_market_order(
                        symbol=broker_symbol,
                        quantity=close_qty,
                        reason="RECON_ORPHAN_OPTION",
                    )
                    if ticket is None:
                        self.Log(
                            f"RECON_ORPHAN_CLOSE_SKIPPED: {sym_str} | Qty={holding.Quantity} | "
                            "Reason=NoCloseQuantity"
                        )
                        continue
                    self._recon_orphan_close_submitted[sym_str] = today
                    self._clear_micro_symbol_tracking(sym_str)
                    self._recon_orphan_seen_streak.pop(sym_str, None)
                    self._recon_orphan_first_seen_at.pop(sym_str, None)
                    self._recon_orphan_last_log_at.pop(sym_str, None)
                    self.Log(
                        f"RECON_ORPHAN_CLOSE_SUBMITTED: {sym_str} | "
                        f"Qty={option_holdings.get(sym_str, 0)} | Mode={mode_norm.upper()}"
                    )
                except Exception as e:
                    self.Log(f"RECON_ORPHAN_CLOSE_FAILED: {sym_str} | {e}")

            qqq_holding = self.Portfolio[self.qqq]
            # Assignment containment: QQQ equity should never persist in options-only flow.
            # Liquidate whenever QQQ shares are present after reconciliation.
            if qqq_holding.Invested:
                self.Log(
                    f"RECON_ASSIGNMENT_EQUITY_LIQUIDATED: QQQ Qty={qqq_holding.Quantity} | "
                    f"Value=${qqq_holding.HoldingsValue:,.2f}"
                )
                self.Liquidate(self.qqq, tag="ASSIGNMENT_RECONCILE")
        except Exception as e:
            self.Log(f"RECON_ERROR: {e}")
