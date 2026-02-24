from __future__ import annotations

from AlgorithmImports import OptionRight, SecurityType

import config


class MainIntradayCloseMixin:
    def _check_expiration_hammer_v2(self) -> None:
        """
        V2.4.4 P0: Expiration Hammer V2 - Close ALL options expiring TODAY.

        This is called every minute during trading hours and checks ALL broker
        positions for options expiring today. If found and it's past 2:00 PM,
        immediately liquidate them.

        This is a CRITICAL safety check that runs independently of the options
        engine's tracked positions. It catches any options that slipped through.

        V2.33: Uses atomic close pattern - ALWAYS closes shorts first, then longs.
        """
        if self.IsWarmingUp:
            return

        # Only check at 2:00 PM or later
        if self.Time.hour < config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR:
            return
        if (
            self.Time.hour == config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_HOUR
            and self.Time.minute < config.OPTIONS_EXPIRING_TODAY_FORCE_CLOSE_MINUTE
        ):
            return

        current_date = self.Time.strftime("%Y-%m-%d")

        # V2.33: Collect ALL options to close FIRST, then close atomically
        expiration_hammer_symbols = []  # Options expiring TODAY
        early_exercise_symbols = []  # ITM options near expiry

        # Scan ALL portfolio positions for expiring options
        for holding in self.Portfolio.Values:
            if not holding.Invested:
                continue

            # Check if this is an option
            if holding.Symbol.SecurityType != SecurityType.Option:
                continue

            # Get expiry date from the symbol
            try:
                expiry = holding.Symbol.ID.Date
                expiry_date = expiry.strftime("%Y-%m-%d")

                if expiry_date == current_date:
                    # EXPIRING TODAY - collect for atomic close
                    qty = holding.Quantity
                    symbol_str = str(holding.Symbol)
                    self.Log(
                        f"EXPIRATION_HAMMER_V2: QUEUED {symbol_str} | "
                        f"Qty={qty} | Expires TODAY ({expiry_date}) | "
                        f"Time={self.Time.strftime('%H:%M')} | "
                        f"P0 FIX: Preventing auto-exercise"
                    )
                    expiration_hammer_symbols.append(holding.Symbol)

                # V2.28: Early exercise guard — close ITM single-leg options near expiry
                # Prevents costly early exercise (Q1 2022: 2 exercises cost -$5,614)
                # Only for single-leg options (spreads have their own DTE exit)
                elif not self.options_engine.has_spread_position():
                    days_to_expiry = (expiry - self.Time).days
                    if days_to_expiry <= config.EARLY_EXERCISE_GUARD_DTE:
                        # Check if option is ITM
                        underlying_price = self.Securities[self.qqq].Price
                        strike = holding.Symbol.ID.StrikePrice
                        is_call = holding.Symbol.ID.OptionRight == OptionRight.Call
                        itm_buffer = config.EARLY_EXERCISE_GUARD_ITM_BUFFER
                        is_itm = (is_call and underlying_price > strike * (1 + itm_buffer)) or (
                            not is_call and underlying_price < strike * (1 - itm_buffer)
                        )
                        if is_itm:
                            qty = holding.Quantity
                            symbol_str = str(holding.Symbol)
                            self.Log(
                                f"EARLY_EXERCISE_GUARD: QUEUED {symbol_str} | "
                                f"Qty={qty} | DTE={days_to_expiry} | ITM | "
                                f"Strike={strike} Underlying={underlying_price:.2f}"
                            )
                            early_exercise_symbols.append(holding.Symbol)
            except Exception as e:
                self.Log(f"EXPIRATION_HAMMER_V2: Error checking {holding.Symbol} - {e}")

        # V2.33 CRITICAL: Close all collected options ATOMICALLY (shorts first, then longs)
        if expiration_hammer_symbols:
            self.Log(
                f"EXPIRATION_HAMMER_V2: Closing {len(expiration_hammer_symbols)} expiring options atomically"
            )
            self._close_options_atomic(
                symbols_to_close=expiration_hammer_symbols,
                reason="EXPIRATION_HAMMER_V2",
                clear_tracking=True,
            )

        if early_exercise_symbols:
            # Don't clear tracking again if hammer already did
            clear_tracking = len(expiration_hammer_symbols) == 0
            self.Log(
                f"EARLY_EXERCISE_GUARD: Closing {len(early_exercise_symbols)} ITM options atomically"
            )
            self._close_options_atomic(
                symbols_to_close=early_exercise_symbols,
                reason="EARLY_EXERCISE_GUARD",
                clear_tracking=clear_tracking,
            )

        # V2.25 Fix #2: Safety net — liquidate any QQQ equity from missed assignments
        # If exercise detection (Fix #1) fails, this catches stale QQQ shares daily at 14:00
        try:
            qqq_holding = self.Portfolio[self.qqq]
            if qqq_holding.Invested:
                self.Log(
                    f"ASSIGNMENT_SAFETY_NET: QQQ equity detected | "
                    f"Qty={qqq_holding.Quantity} | Value=${qqq_holding.HoldingsValue:,.2f} | "
                    f"LIQUIDATING stale assignment shares"
                )
                self.Liquidate(self.qqq, tag="ASSIGNMENT_SAFETY_NET")
        except Exception as e:
            self.Log(f"ASSIGNMENT_SAFETY_NET: Error checking QQQ - {e}")

    def _on_intraday_options_force_close(self) -> None:
        """
        V2.1.1: Intraday options force close at configured force-exit time.

        Forces close of all intraday mode options positions (0-2 DTE).
        These must close before the final liquidity fade into the close.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return
        if self._intraday_force_close_ran_date == self.Time.date():
            return
        self._intraday_force_close_ran_date = self.Time.date()

        # V2.4.4 P0: Run Expiration Hammer V2 as part of force close
        self._check_expiration_hammer_v2()

        # Check for intraday positions to close
        for intraday_pos in self.options_engine.get_intraday_positions():
            if intraday_pos is None:
                continue
            # Get current option price
            symbol = self._normalize_symbol_str(intraday_pos.contract.symbol)
            current_price = self._get_option_mark_price(symbol, fallback=intraday_pos.entry_price)
            hold_allowed = self._should_hold_intraday_symbol_overnight(symbol)
            entry_price = float(getattr(intraday_pos, "entry_price", 0.0) or 0.0)
            eod_loss_breach = hold_allowed and self._is_micro_eod_loss_breach(
                symbol=symbol,
                entry_price=entry_price,
                current_price=current_price,
            )
            itm_eod_harvest = hold_allowed and self._should_itm_eod_harvest(
                symbol=symbol,
                intraday_pos=intraday_pos,
                entry_price=entry_price,
                current_price=current_price,
            )

            if hold_allowed and not eod_loss_breach and not itm_eod_harvest:
                self.Log(f"INTRADAY_FORCE_EXIT: HOLD_SKIP {symbol} (ITM overnight policy)")
                continue

            # V2.25 Fix #4: Double-sell guard — verify position is still held
            # Prevents creating orphan shorts if limit/profit-target already closed
            try:
                if not self.Portfolio[intraday_pos.contract.symbol].Invested:
                    self.Log(
                        f"INTRADAY_FORCE_EXIT: SKIP | {symbol} already closed | "
                        f"Clearing stale _intraday_position"
                    )
                    self.options_engine.remove_intraday_position(symbol=symbol)
                    self._clear_intraday_close_guard(symbol)
                    continue
            except Exception:
                pass  # If symbol lookup fails, proceed with force close

            if self._has_open_non_oco_order_for_symbol(symbol):
                continue

            # Cancel active OCO before force-close to avoid orphan sell orders
            # creating accidental short options after the position is closed.
            try:
                if self.oco_manager.cancel_by_symbol(symbol, reason="INTRADAY_FORCE_CLOSE"):
                    self.Log(f"INTRADAY_FORCE_EXIT: Cancelled active OCO for {symbol}")
            except Exception as e:
                self.Log(f"INTRADAY_FORCE_EXIT: OCO cancel failed for {symbol} | {e}")

            signal = self.options_engine.check_intraday_force_exit(
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                current_price=current_price,
                ignore_hold_policy=(eod_loss_breach or itm_eod_harvest),
                engine=self.options_engine.find_intraday_lane_by_symbol(symbol),
                symbol=symbol,
            )

            if signal:
                # Idempotency: only one force-close submit per symbol per day.
                submitted_date = self._intraday_force_exit_submitted_symbols.get(signal.symbol)
                if submitted_date == str(self.Time.date()):
                    live_qty = abs(self._get_option_holding_quantity(signal.symbol))
                    if live_qty <= 0:
                        self.Log(
                            f"INTRADAY_FORCE_EXIT: SKIP duplicate submit | "
                            f"{signal.symbol} | Date={submitted_date}"
                        )
                        continue
                    self.Log(
                        f"INTRADAY_FORCE_EXIT: RETRY | {signal.symbol} | "
                        f"Qty={live_qty} still held"
                    )
                live_qty = abs(self._get_option_holding_quantity(signal.symbol))
                if live_qty <= 0:
                    self.Log(f"INTRADAY_FORCE_EXIT: SKIP no live holding | {signal.symbol}")
                    continue
                signal.requested_quantity = live_qty
                self._intraday_close_in_progress_symbols.add(signal.symbol)
                self._intraday_force_exit_submitted_symbols[signal.symbol] = str(self.Time.date())
                self.portfolio_router.receive_signal(signal)
                self._process_immediate_signals()

        # Safety net: close any live MICRO-tagged holdings even if intraday state is missing.
        # This prevents overnight orphan risk from fill/cancel race conditions.
        if self._micro_open_symbols:
            for holding in self.Portfolio.Values:
                try:
                    if not holding.Invested or holding.Symbol.SecurityType != SecurityType.Option:
                        continue
                    live_symbol = self._normalize_symbol_str(holding.Symbol)
                    if live_symbol not in self._micro_open_symbols:
                        continue
                    hold_allowed = self._should_hold_intraday_symbol_overnight(live_symbol)
                    if hold_allowed:
                        entry_price = 0.0
                        snapshot = self._intraday_entry_snapshot.get(live_symbol, {})
                        if snapshot and snapshot.get("entry_price", 0) > 0:
                            entry_price = float(snapshot.get("entry_price", 0.0))
                        elif holding.AveragePrice > 0:
                            entry_price = float(holding.AveragePrice)
                        current_price = self._get_option_mark_price(
                            live_symbol, fallback=float(holding.Price)
                        )
                        if not self._is_micro_eod_loss_breach(
                            symbol=live_symbol,
                            entry_price=entry_price,
                            current_price=current_price,
                        ):
                            continue
                    live_qty = int(holding.Quantity)
                    if live_qty == 0:
                        self._micro_open_symbols.discard(live_symbol)
                        continue
                    submitted_date = self._intraday_force_exit_submitted_symbols.get(live_symbol)
                    if submitted_date == str(self.Time.date()):
                        # Retry same-day only if still live and no non-OCO order is active.
                        if abs(self._get_option_holding_quantity(live_symbol)) <= 0:
                            self._clear_intraday_close_guard(live_symbol)
                            continue
                        if self._has_open_non_oco_order_for_symbol(live_symbol):
                            continue
                        self.Log(f"INTRADAY_FORCE_EXIT_SWEEP: RETRY same-day | {live_symbol}")

                    try:
                        self.oco_manager.cancel_by_symbol(
                            live_symbol, reason="INTRADAY_FORCE_CLOSE_SWEEP"
                        )
                    except Exception:
                        pass

                    self._intraday_close_in_progress_symbols.add(live_symbol)
                    self._intraday_force_exit_submitted_symbols[live_symbol] = str(self.Time.date())
                    self._diag_micro_eod_sweep_close_count += 1
                    self.Log(
                        f"INTRADAY_FORCE_EXIT_SWEEP: Closing MICRO holding from holdings ledger | "
                        f"{live_symbol} | Qty={live_qty}"
                    )
                    self._submit_option_close_market_order(
                        symbol=holding.Symbol,
                        quantity=-live_qty,
                        reason="MICRO_EOD_SWEEP",
                        engine_hint="MICRO",
                    )
                except Exception as e:
                    self.Log(f"INTRADAY_FORCE_EXIT_SWEEP_ERROR: {e}")

    def _ensure_oco_for_open_options(self) -> None:
        """
        V6.6.2: Ensure every open single-leg options position has an active OCO.

        If a position exists without an OCO (e.g., OCO submission failed after-hours),
        create and submit one at the next market session to prevent expiry losses.
        """
        if (
            self.IsWarmingUp
            or not hasattr(self, "options_engine")
            or not hasattr(self, "oco_manager")
        ):
            return

        # Skip if we currently hold a spread (OCO only for single-leg options)
        if self.options_engine.has_spread_position():
            return

        positions_for_oco = self.options_engine.get_intraday_positions()
        if not positions_for_oco:
            swing_pos = self.options_engine.get_position()
            if swing_pos is not None:
                positions_for_oco = [swing_pos]
        if not positions_for_oco:
            return
        # Process one symbol per call to limit churn.
        position = positions_for_oco[0]
        if position is None or position.contract is None:
            return

        symbol = self._normalize_symbol_str(position.contract.symbol)

        # Don't recover OCO while close is in progress for this symbol.
        if symbol in self._intraday_close_in_progress_symbols:
            return

        # Never re-arm OCO while a non-OCO order is in flight for this symbol.
        # This avoids submit/cancel races between software exits/retries and OCO recovery.
        if self._has_open_non_oco_order_for_symbol(symbol):
            return

        # Skip OCO recovery in force-close window to avoid close-race amplification.
        try:
            exit_hour, exit_min = map(
                int, getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15").split(":")
            )
            cutoff = int(getattr(config, "OCO_RECOVERY_CUTOFF_MINUTES_BEFORE_FORCE_EXIT", 20))
            now_minutes = self.Time.hour * 60 + self.Time.minute
            force_minutes = exit_hour * 60 + exit_min
            if now_minutes >= force_minutes - cutoff:
                return
        except Exception:
            pass

        # If OCO already active, nothing to do
        if self.oco_manager.has_active_pair(symbol):
            return

        # Throttle OCO recovery retries per symbol (minutes, not once/day).
        today = str(self.Time.date())
        last_attempt = self._last_oco_recovery_attempt.get(symbol)
        retry_interval_min = max(1, int(getattr(config, "OCO_RECOVERY_RETRY_MINUTES", 5)))
        if isinstance(last_attempt, str):
            # Backward compatibility if old date-string format remains in memory.
            if last_attempt == today:
                return
        elif last_attempt is not None:
            try:
                elapsed_min = (self.Time - last_attempt).total_seconds() / 60.0
                if elapsed_min < retry_interval_min:
                    return
            except Exception:
                pass

        # Ensure we still hold the position
        try:
            qc_symbol = self.Symbol(symbol)
            holding = self.Portfolio[qc_symbol]
            if not holding.Invested:
                self._clear_intraday_close_guard(symbol)
                return
            qty = abs(int(holding.Quantity))
            if qty <= 0:
                self._clear_intraday_close_guard(symbol)
                return
        except Exception:
            # Fallback to tracked quantity if symbol lookup fails
            qty = int(position.num_contracts) if position.num_contracts else 0
            if qty <= 0:
                return

        # Create and submit OCO
        oco_pair = self.oco_manager.create_oco_pair(
            symbol=symbol,
            entry_price=position.entry_price,
            stop_price=position.stop_price,
            target_price=position.target_price,
            quantity=qty,
            current_date=today,
            tag_context=f"{self._oco_engine_prefix_for_strategy(getattr(position, 'entry_strategy', 'UNKNOWN'))}:{getattr(position, 'entry_strategy', 'UNKNOWN')}",
        )
        submitted = False
        if oco_pair:
            submitted = self.oco_manager.submit_oco_pair(oco_pair, current_time=str(self.Time))

        self._last_oco_recovery_attempt[symbol] = self.Time
        if submitted:
            self.Log(
                f"OCO_RECOVER: Created missing OCO | {symbol} | "
                f"Stop=${position.stop_price:.2f} Target=${position.target_price:.2f} Qty={qty}"
            )
        else:
            self.Log(
                f"OCO_RECOVER: Failed to submit (market closed or error) | {symbol} | "
                f"RetryIn={retry_interval_min}m"
            )

    def _liquidate_all_spread_aware(
        self, reason: str = "GOVERNOR_SHUTDOWN", exempt_symbols: set = None
    ) -> None:
        # Portfolio-scan liquidation: close option shorts, then longs, then equities.
        if exempt_symbols is None:
            exempt_symbols = set()

        # V2.33: Scan Portfolio for ALL options positions (not just tracked spread)
        short_options = []  # (symbol, quantity) - negative qty, need to buy back
        long_options = []  # (symbol, quantity) - positive qty, need to sell
        equity_positions = []  # Non-options positions

        for kvp in self.Portfolio:
            holding = kvp.Value
            if not holding.Invested:
                continue

            symbol = holding.Symbol
            qty = holding.Quantity

            # V3.1: Skip exempt symbols (hedges)
            if symbol in exempt_symbols:
                self.Log(f"{reason}: Exempting hedge {symbol}")
                continue

            # Check if this is an options position (SecurityType.Option)
            if symbol.SecurityType == SecurityType.Option:
                if qty < 0:
                    short_options.append((symbol, qty))
                else:
                    long_options.append((symbol, qty))
            else:
                equity_positions.append((symbol, qty))

        # Step 1: Buy back ALL short options first (eliminates naked exposure)
        # This MUST happen before selling any long options
        for symbol, qty in short_options:
            try:
                # qty is negative, so we buy abs(qty) to close
                close_qty = abs(qty)
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=close_qty,
                    reason=reason,
                )
                self.Log(f"{reason}: Closed short option {str(symbol)[-21:]} x{close_qty}")
            except Exception as e:
                self.Log(f"{reason}: Failed to close short {str(symbol)[-21:]} | {e}")

        # Step 2: Sell ALL long options (safe now - all shorts closed)
        for symbol, qty in long_options:
            try:
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=-qty,
                    reason=reason,
                )
                self.Log(f"{reason}: Closed long option {str(symbol)[-21:]} x{qty}")
            except Exception as e:
                self.Log(f"{reason}: Failed to close long {str(symbol)[-21:]} | {e}")

        # Step 3: Clear all options engine tracking state
        if self.options_engine:
            self.options_engine.clear_spread_position()
            self.options_engine.cancel_pending_spread_entry()
            self.options_engine.cancel_pending_intraday_entry()
        if self.portfolio_router:
            self.portfolio_router.clear_all_spread_margins()

        # V2.33: Clear main.py spread tracking dicts
        if self._spread_fill_tracker is not None:
            self.Log(f"{reason}: Clearing spread fill tracker")
            self._spread_fill_tracker = None
        if self._pending_spread_orders:
            self.Log(f"{reason}: Clearing {len(self._pending_spread_orders)} pending spread orders")
            self._pending_spread_orders.clear()
            self._pending_spread_orders_reverse.clear()

        # Step 4: Liquidate equity positions (trend, MR, hedges)
        for symbol, qty in equity_positions:
            try:
                self.MarketOrder(symbol, -qty, tag=reason)
                self.Log(f"{reason}: Closed equity {symbol} x{qty}")
            except Exception as e:
                self.Log(f"{reason}: Failed to close equity {symbol} | {e}")

        # Log summary
        total_closed = len(short_options) + len(long_options) + len(equity_positions)
        self.Log(
            f"{reason}: Liquidation complete | "
            f"Short opts={len(short_options)} | Long opts={len(long_options)} | "
            f"Equity={len(equity_positions)} | Total={total_closed}"
        )

    def _close_options_atomic(
        self,
        symbols_to_close: list = None,
        reason: str = "OPTIONS_CLOSE",
        clear_tracking: bool = True,
    ) -> int:
        """
        V2.33: ATOMIC options close - ALWAYS closes shorts first, then longs.

        This is the ONLY method that should be used to close options positions.
        NEVER call Liquidate() directly on option symbols!

        Args:
            symbols_to_close: Optional list of specific option symbols to close.
                            If None, closes ALL options in portfolio.
            reason: Tag for logging and order tracking.
            clear_tracking: Whether to clear options engine tracking state.

        Returns:
            Number of options positions closed.
        """
        # Collect options to close (from specific list or entire portfolio)
        short_options = []  # (symbol, qty) - shorts have negative qty
        long_options = []  # (symbol, qty) - longs have positive qty

        if symbols_to_close is not None:
            # Close specific symbols
            for symbol in symbols_to_close:
                if symbol in self.Portfolio and self.Portfolio[symbol].Invested:
                    holding = self.Portfolio[symbol]
                    if holding.Symbol.SecurityType == SecurityType.Option:
                        if holding.Quantity < 0:
                            short_options.append((holding.Symbol, holding.Quantity))
                        else:
                            long_options.append((holding.Symbol, holding.Quantity))
        else:
            # Close all options in portfolio
            for kvp in self.Portfolio:
                holding = kvp.Value
                if holding.Invested and holding.Symbol.SecurityType == SecurityType.Option:
                    if holding.Quantity < 0:
                        short_options.append((holding.Symbol, holding.Quantity))
                    else:
                        long_options.append((holding.Symbol, holding.Quantity))

        # CRITICAL: Close ALL shorts FIRST (buy to close)
        for symbol, qty in short_options:
            try:
                close_qty = abs(qty)
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=close_qty,
                    reason=reason,
                )
                self.Log(f"{reason}: Closed SHORT {str(symbol)[-21:]} x{close_qty}")
            except Exception as e:
                self.Log(f"{reason}: FAILED short close {str(symbol)[-21:]} | {e}")

        # THEN close ALL longs (sell to close) - safe now, no naked shorts
        for symbol, qty in long_options:
            try:
                self._submit_option_close_market_order(
                    symbol=symbol,
                    quantity=-qty,
                    reason=reason,
                )
                self.Log(f"{reason}: Closed LONG {str(symbol)[-21:]} x{qty}")
            except Exception as e:
                self.Log(f"{reason}: FAILED long close {str(symbol)[-21:]} | {e}")

        # Clear tracking state if requested
        if clear_tracking:
            if self.options_engine:
                self.options_engine.clear_spread_position()
                self.options_engine.cancel_pending_spread_entry()
                self.options_engine.cancel_pending_intraday_entry()
            if self.portfolio_router:
                self.portfolio_router.clear_all_spread_margins()
            if self._spread_fill_tracker is not None:
                self._spread_fill_tracker = None
            if self._pending_spread_orders:
                self._pending_spread_orders.clear()
                self._pending_spread_orders_reverse.clear()

        total_closed = len(short_options) + len(long_options)
        if total_closed > 0:
            self.Log(
                f"{reason}: Atomic close complete | "
                f"Shorts={len(short_options)} Longs={len(long_options)}"
            )
        return total_closed

    def _intraday_force_exit_fallback(self) -> None:
        """
        V6.12: Safety net - force-close intraday position after configured close +5min if scheduled close missed.

        This prevents intraday options from carrying overnight due to scheduler issues.
        """
        # Only run once per day
        if getattr(self, "_intraday_force_exit_fallback_date", None) == self.Time.date():
            return

        # Only after configured force-close + 5 minutes.
        exit_time = getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15")
        exit_hour, exit_minute = map(int, exit_time.split(":"))
        fallback_hour = exit_hour
        fallback_minute = exit_minute + 5
        if fallback_minute >= 60:
            fallback_hour += fallback_minute // 60
            fallback_minute = fallback_minute % 60
        if self.Time.hour < fallback_hour or (
            self.Time.hour == fallback_hour and self.Time.minute < fallback_minute
        ):
            return

        # Check if we have intraday positions
        if not hasattr(self, "options_engine") or not self.options_engine.has_intraday_position():
            self._intraday_force_exit_fallback_date = self.Time.date()
            return

        submitted_any = False
        for intraday_pos in self.options_engine.get_intraday_positions():
            if intraday_pos is None or intraday_pos.contract is None:
                continue
            symbol = self._normalize_symbol_str(intraday_pos.contract.symbol)
            mark_price = self._get_option_mark_price(symbol, fallback=0.0)
            entry_price = float(getattr(intraday_pos, "entry_price", 0.0) or 0.0)
            hold_allowed = self._should_hold_intraday_symbol_overnight(symbol)
            eod_loss_breach = hold_allowed and self._is_micro_eod_loss_breach(
                symbol=symbol,
                entry_price=entry_price,
                current_price=mark_price,
            )
            itm_eod_harvest = hold_allowed and self._should_itm_eod_harvest(
                symbol=symbol,
                intraday_pos=intraday_pos,
                entry_price=entry_price,
                current_price=mark_price,
            )
            if hold_allowed and not eod_loss_breach and not itm_eod_harvest:
                self.Log(f"INTRADAY_FORCE_EXIT_FALLBACK: HOLD_SKIP {symbol}")
                continue
            if symbol in self._intraday_close_in_progress_symbols:
                continue
            if self._has_open_non_oco_order_for_symbol(symbol):
                continue
            price = self.Securities[symbol].Price if self.Securities.ContainsKey(symbol) else 0
            if price <= 0:
                try:
                    sec = self.Securities[symbol]
                    bid = sec.BidPrice or 0
                    ask = sec.AskPrice or 0
                    if bid > 0 and ask > 0:
                        price = (bid + ask) / 2
                except Exception:
                    price = 0

            if price <= 0:
                self.Log(f"INTRADAY_FORCE_EXIT_FALLBACK: No valid price for {symbol} - skip")
                continue

            signal = self.options_engine.check_intraday_force_exit(
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                current_price=price,
                ignore_hold_policy=(eod_loss_breach or itm_eod_harvest),
                engine=self.options_engine.find_intraday_lane_by_symbol(symbol),
                symbol=symbol,
            )
            if signal:
                live_qty = abs(self._get_option_holding_quantity(signal.symbol))
                if live_qty > 0:
                    signal.requested_quantity = live_qty
                self._intraday_close_in_progress_symbols.add(signal.symbol)
                self._intraday_force_exit_submitted_symbols[signal.symbol] = str(self.Time.date())
                self.Log(f"INTRADAY_FORCE_EXIT_FALLBACK: Triggered for {symbol}")
                self.portfolio_router.receive_signal(signal)
                submitted_any = True

        if submitted_any:
            self._process_immediate_signals()
        # Mark done after handling concrete submit attempts.
        self._intraday_force_exit_fallback_date = self.Time.date()

    def _reconcile_intraday_close_guards(self) -> None:
        """Clear stale close-in-progress guards after positions are flat."""
        if not self._intraday_close_in_progress_symbols:
            return
        stale = []
        for symbol in self._intraday_close_in_progress_symbols:
            if abs(self._get_option_holding_quantity(symbol)) <= 0:
                stale.append(symbol)
        for symbol in stale:
            self._clear_intraday_close_guard(symbol)
