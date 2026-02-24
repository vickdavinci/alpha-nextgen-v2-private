from __future__ import annotations

from AlgorithmImports import PortfolioTarget, SecurityType, Slice

import config
from engines.core.capital_engine import CapitalState
from engines.core.regime_engine import RegimeState
from engines.core.risk_engine import KSTier, RiskCheckResult
from models.enums import Urgency
from models.target_weight import TargetWeight


class MainSignalGenerationMixin:
    def _process_immediate_signals(self) -> None:
        """
        Process pending signals with IMMEDIATE urgency.

        Routes to PortfolioRouter which validates and executes via MarketOrder.
        """
        # V2.18: Block immediate orders in market close blackout window (RPT-5 fix)
        if self._is_market_close_blackout():
            pending_count = self.portfolio_router.get_pending_count()
            if pending_count > 0:
                self.Log(
                    f"MARKET_CLOSE_GUARD: {pending_count} immediate orders blocked | "
                    f"Time={self.Time.strftime('%H:%M')} in 15:58-16:00 blackout"
                )
                # Clear pending to prevent them executing at wrong time
                self.portfolio_router.clear_pending()
            return

        # Skip if no pending signals
        if self.portfolio_router.get_pending_count() == 0:
            return

        # Get current state
        capital_state = self.capital_engine.calculate(self.Portfolio.TotalPortfolioValue)
        current_positions = self._get_current_positions()
        current_prices = self._get_current_prices()

        # V2.19 FIX: Inject option prices from pending signal metadata
        # _get_current_prices() only includes HELD options (V2.19 perf fix),
        # but NEW entries aren't held yet. Use price from chain data stored in metadata.
        for signal in self.portfolio_router.get_pending_signals():
            if signal.source in ("OPT", "OPT_INTRADAY") and signal.symbol not in current_prices:
                price = signal.metadata.get("contract_price", 0) if signal.metadata else 0

                # V6.12: Fallback to bid/ask mid if metadata price is 0
                if price <= 0 and self.Securities.ContainsKey(signal.symbol):
                    try:
                        sec = self.Securities[signal.symbol]
                        bid = sec.BidPrice or 0
                        ask = sec.AskPrice or 0
                        if bid > 0 and ask > 0:
                            price = (bid + ask) / 2
                            self.Log(
                                f"V2.19_INJECT_FALLBACK: {signal.symbol} | "
                                f"Using bid/ask mid=${price:.2f} (bid={bid:.2f}, ask={ask:.2f})"
                            )
                    except Exception:
                        pass  # Keep price as 0, will be logged below

                if price > 0:
                    current_prices[signal.symbol] = price
                else:
                    self.Log(
                        f"V2.19_INJECT_WARNING: {signal.symbol} | price=0 | "
                        f"No valid price found - sizing may be incorrect"
                    )

        try:
            # Calculate max single position in dollars from percentage
            max_single_position = capital_state.tradeable_eq * config.MAX_SINGLE_POSITION_PCT
            self.portfolio_router.process_immediate(
                tradeable_equity=capital_state.tradeable_eq,
                current_positions=current_positions,
                current_prices=current_prices,
                max_single_position=max_single_position,
                available_cash=self.Portfolio.Cash,
                locked_amount=capital_state.locked_amount,
                current_time=str(self.Time),
            )
            self._capture_router_rejections(stage="IMMEDIATE")
        except Exception as e:
            self.Log(f"SIGNAL_ERROR: Failed to process immediate signals - {e}")
            self._capture_router_rejections(stage="IMMEDIATE_ERROR")

    def _process_eod_signals(self, capital_state: CapitalState) -> None:
        """
        Process pending signals with EOD urgency using SetHoldings.

        Uses QC's SetHoldings which automatically handles sell-before-buy
        ordering to avoid "Insufficient buying power" errors.

        Args:
            capital_state: Current capital state for sizing.
        """
        # Skip if no pending signals
        if self.portfolio_router.get_pending_count() == 0:
            return

        try:
            # Get aggregated weights from router
            weights = self.portfolio_router.drain_pending_signals()

            if not weights:
                return

            # Aggregate weights by symbol (take highest weight for same symbol)
            aggregated = self.portfolio_router.aggregate_weights(weights)

            # V2.26: Apply Drawdown Governor scaling to allocations
            # V2.32/V6.11: EXEMPT hedges (SH) — we want MORE hedging during drawdowns, not less
            # V2.32: Apply sizing floor for options, exempt bearish options if configured
            HEDGE_SYMBOLS = {"SH"}

            # V3.0 FIX: Improved bearish detection - check existing position OR signal source
            # This allows NEW bearish entries at Governor 0%, not just existing positions
            def is_bearish_signal(agg) -> bool:
                """Check if signal is bearish (PUT spread entry or exit)."""
                # Check existing spread position
                if hasattr(self, "options_engine") and self.options_engine.has_spread_position():
                    for spread in self.options_engine.get_spread_positions():
                        if spread.spread_type == "BEAR_PUT":
                            return True
                # Check signal source/reasons for bearish indicators
                for source in agg.sources:
                    if "BEAR" in source.upper() or "PUT" in source.upper():
                        return True
                for reason in agg.reasons:
                    if "BEAR" in reason.upper() or "PUT" in reason.upper():
                        return True
                # Check symbol for PUT option (option symbols contain P for put)
                symbol_str = str(agg.symbol)
                if len(symbol_str) > 10 and "QQQ" in symbol_str:
                    # QQQ option symbol format: QQQ YYMMDDP00123000 (P = put)
                    # Extract option type from symbol
                    if "P0" in symbol_str:  # PUT option
                        return True
                return False

            if self._governor_scale < 1.0:
                scaled_count = 0
                options_exempt_count = 0
                for symbol, agg in aggregated.items():
                    if agg.target_weight > 0:  # Only scale entries/holds, not exits (weight=0)
                        # V2.32: Hedges exempt
                        if symbol in HEDGE_SYMBOLS:
                            continue

                        # V2.32: Check if this is an options position (QQQ options)
                        is_option = len(str(symbol)) > 5 and "QQQ" in str(symbol)

                        if is_option:
                            # V3.0: Exempt bearish options entirely if configured
                            # Uses improved detection that checks signal source, not just existing position
                            if is_bearish_signal(agg) and config.GOVERNOR_EXEMPT_BEARISH_OPTIONS:
                                options_exempt_count += 1
                                continue

                            # V2.32: Apply sizing floor for options
                            effective_scale = max(
                                self._governor_scale, config.GOVERNOR_OPTIONS_SIZING_FLOOR
                            )
                            agg.target_weight *= effective_scale
                        else:
                            # Non-option positions get full governor scaling
                            agg.target_weight *= self._governor_scale

                        scaled_count += 1

                if self._governor_scale == 0.0:
                    self.Log("GOVERNOR: SHUTDOWN | All non-hedge allocations zeroed")
                else:
                    exempt_msg = (
                        f", {options_exempt_count} bear options exempt"
                        if options_exempt_count
                        else ""
                    )
                    self.Log(
                        f"GOVERNOR: Scaling {scaled_count} positions by {self._governor_scale:.0%} "
                        f"(hedges exempt{exempt_msg})"
                    )

            # Validate against max position size
            max_single_position_pct = config.MAX_SINGLE_POSITION_PCT
            for symbol, agg in aggregated.items():
                if agg.target_weight > max_single_position_pct:
                    agg.target_weight = max_single_position_pct

            # Build list of portfolio targets for SetHoldings
            targets = []
            for symbol, agg in aggregated.items():
                # Get the actual Symbol object
                symbol_obj = None
                for s in self.traded_symbols:
                    if str(s.Value) == symbol:
                        symbol_obj = s
                        break

                if symbol_obj and agg.target_weight >= 0:
                    targets.append(PortfolioTarget(symbol_obj, agg.target_weight))

            if targets:
                # Log what we're doing (limited to avoid log overflow)
                if len(targets) <= 5:
                    self.Log(f"EOD_REBALANCE: {len(targets)} targets")

                # SetHoldings handles sell-before-buy automatically
                self.SetHoldings(targets)
            self._capture_router_rejections(stage="EOD")

        except Exception as e:
            self.Log(f"SIGNAL_ERROR: Failed to process EOD signals - {e}")
            self._capture_router_rejections(stage="EOD_ERROR")

    def _generate_trend_signals_eod(self, regime_state: RegimeState) -> None:
        """
        Generate Trend Engine signals at end of day.

        V2.3 Enhancement: Position limits to reserve capital for options.
        - Max 2 concurrent trend positions (config.MAX_CONCURRENT_TREND_POSITIONS)
        - Priority order: config.TREND_PRIORITY_ORDER
        - Always processes exit signals regardless of position count

        Checks for:
            - Entry signals (MA200 + ADX confirmation)
            - Exit signals (Chandelier stop, regime deterioration)

        Args:
            regime_state: Current regime state.
        """
        # V6.4: Skip trend engine in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_TREND_ENABLED:
            return

        is_cold_start = (
            self.cold_start_engine.is_active()
            if hasattr(self.cold_start_engine, "is_active")
            else False
        )
        has_warm_entry = (
            self.cold_start_engine.has_warm_entry()
            if hasattr(self.cold_start_engine, "has_warm_entry")
            else False
        )
        current_date = str(self.Time.date())

        # V2.18: Count current trend positions AND pending MOO orders for position limit enforcement
        # Fix: Previously only counted invested positions, missing pending MOOs that would fill next day
        trend_symbols = config.TREND_PRIORITY_ORDER  # V6.11: ["QLD", "UGL", "UCO", "SSO"]
        current_trend_positions = sum(
            1 for sym in trend_symbols if self.Portfolio[getattr(self, sym.lower())].Invested
        )
        # V2.18: Also count pending MOO orders to prevent exceeding limit
        pending_moo_count = self.trend_engine.get_pending_moo_count()
        total_committed_positions = current_trend_positions + pending_moo_count
        max_positions = config.MAX_CONCURRENT_TREND_POSITIONS  # Default: 2
        entries_allowed = max_positions - total_committed_positions

        # Log position status (V2.18: Include pending MOO count)
        if entries_allowed < len(trend_symbols) or pending_moo_count > 0:
            self.Log(
                f"TREND: Position limit check | Invested={current_trend_positions} | "
                f"Pending MOO={pending_moo_count} | Total={total_committed_positions} | "
                f"Max={max_positions} | Entries allowed={entries_allowed}"
            )

        # V2.27: Skip-day enforcement after Tier 2+ kill switch (block entries only, exits still run)
        skip_entries = self.risk_engine.is_ks_skip_day(str(self.Time.date()))
        if skip_entries:
            self.Log("TREND: Entry blocked - KS skip day active")

        # V2.3: Collect entry candidates with their ADX scores for prioritization
        entry_candidates = []

        # Build symbol data map for cleaner iteration.
        # V2.4: Added SMA50 for structural trend exit.
        symbol_data = {
            "QLD": (self.qld, self.qld_ma200, self.qld_adx, self.qld_atr, self.qld_sma50),
            "SSO": (self.sso, self.sso_ma200, self.sso_adx, self.sso_atr, self.sso_sma50),
            "UGL": (self.ugl, self.ugl_ma200, self.ugl_adx, self.ugl_atr, self.ugl_sma50),
            "UCO": (self.uco, self.uco_ma200, self.uco_adx, self.uco_atr, self.uco_sma50),
        }

        # Process each symbol in priority order
        for symbol in trend_symbols:
            security, ma200_ind, adx_ind, atr_ind, sma50_ind = symbol_data[symbol]

            # Skip if indicators not ready (SMA50 needs 50 days warmup)
            if not (ma200_ind.IsReady and adx_ind.IsReady and atr_ind.IsReady):
                continue

            close = self.Securities[security].Close
            high = self.Securities[security].High
            ma200 = ma200_ind.Current.Value
            adx = adx_ind.Current.Value
            atr = atr_ind.Current.Value
            # V2.4: SMA50 for structural trend exit (may not be ready during warmup)
            sma50 = sma50_ind.Current.Value if sma50_ind.IsReady else None

            # ALWAYS check exit signals for invested positions
            if self.Portfolio[security].Invested:
                signal = self.trend_engine.check_exit_signals(
                    symbol=symbol,
                    close=close,
                    high=high,
                    ma200=ma200,
                    adx=adx,
                    regime_score=regime_state.smoothed_score,
                    atr=atr,
                    sma50=sma50,  # V2.4: Pass SMA50 for structural trend exit
                )
                if signal:
                    self.portfolio_router.receive_signal(signal)
            else:
                # Collect entry candidates (will filter by position limit later)
                signal = self.trend_engine.check_entry_signal(
                    symbol=symbol,
                    close=close,
                    ma200=ma200,
                    adx=adx,
                    regime_score=regime_state.smoothed_score,
                    is_cold_start_active=is_cold_start,
                    has_warm_entry=has_warm_entry,
                    atr=atr,
                    current_date=current_date,
                )
                if signal:
                    # Store with ADX for sorting (higher ADX = stronger trend)
                    entry_candidates.append((signal, adx))

        # V2.20: Respect rejection cooldown for trend entries
        if (
            self._trend_rejection_cooldown_until is not None
            and self.Time < self._trend_rejection_cooldown_until
        ):
            if entry_candidates:
                self.Log(
                    f"TREND: Entries blocked by rejection cooldown | "
                    f"Until {self._trend_rejection_cooldown_until}"
                )
            entry_candidates = []

        # V2.27: Block entries on KS skip day (exits still processed above)
        if skip_entries:
            entry_candidates = []

        # V2.3: Apply position limit - only send top N entry signals
        if entry_candidates and entries_allowed > 0:
            # Sort by ADX descending (strongest trends first)
            entry_candidates.sort(key=lambda x: x[1], reverse=True)

            # Send only as many entries as allowed
            for signal, adx in entry_candidates[:entries_allowed]:
                # V2.27: Apply KS Tier 1 sizing reduction to trend entries
                if (
                    self._last_risk_result is not None
                    and self._last_risk_result.sizing_multiplier < 1.0
                ):
                    original_weight = signal.target_weight
                    signal.target_weight *= self._last_risk_result.sizing_multiplier
                    self.Log(
                        f"TREND: Tier1 sizing | {signal.symbol} weight "
                        f"{original_weight:.2f} → {signal.target_weight:.2f}"
                    )
                self.Log(
                    f"TREND: ENTRY_APPROVED {signal.symbol} | ADX={adx:.1f} | "
                    f"Slot {current_trend_positions + 1}/{max_positions}"
                )
                self.portfolio_router.receive_signal(signal)
                # V2.19: Mark pending ONLY after approval (not in check_entry_signal)
                self.trend_engine.mark_pending_moo(signal.symbol, current_date)
                current_trend_positions += 1

            # Log blocked entries
            blocked = entry_candidates[entries_allowed:]
            for signal, adx in blocked:
                self.Log(
                    f"TREND: ENTRY_BLOCKED {signal.symbol} | ADX={adx:.1f} | "
                    f"Reason: Position limit ({max_positions}) reached"
                )

    def _generate_options_signals(
        self,
        regime_state: RegimeState,
        capital_state: CapitalState,
        size_multiplier: float = 1.0,
        is_eod_scan: bool = False,
    ) -> None:
        """
        Generate Options Engine signals at end of day.

        V2.3: Debit spread entry using regime-based direction.
        - Regime > 60: Bull Call Spread
        - Regime < 45: Bear Put Spread
        - Regime 45-60: No trade (neutral)

        V2.3.20: Added size_multiplier for cold start reduced sizing.

        Args:
            regime_state: Current regime state.
            capital_state: Current capital state.
            size_multiplier: Position size multiplier (default 1.0). Set to 0.5
                during cold start to reduce risk while still participating.
        """
        size_multiplier *= self._premarket_vix_size_mult

        # Skip if indicators not ready
        if not self.qqq_adx.IsReady or not self.qqq_sma200.IsReady:
            return

        # V2.27: Tier 1 blocks new options entries
        if self._last_risk_result is not None and not self._last_risk_result.can_enter_options:
            self.Log("OPTIONS_EOD: Blocked by KS Tier 1 (REDUCE)")
            return

        # V2.27: Skip-day enforcement after Tier 2+ kill switch
        if self.risk_engine.is_ks_skip_day(str(self.Time.date())):
            self.Log("OPTIONS_EOD: Blocked - KS skip day active")
            return

        # V2.33: Direction-aware governor gating for EOD options
        # This was missing in V2.32 causing the enter→liquidate death spiral
        #
        # Investment thesis alignment:
        # - Bear PUT spreads REDUCE portfolio risk → allowed even at low governor
        # - Bull CALL spreads INCREASE risk → require higher governor
        #
        # The V2.32 death spiral happened because:
        # - Regime was 63-71 (bullish) but portfolio drawdown was 10-16%
        # - System entered BULL_CALL spreads (wrong for drawdown protection)
        # - Governor liquidated next morning → forced loss → repeat
        #
        # V3.5 fix: Allow PUT spreads in NEUTRAL zone at low governor
        # "Bearish" for governor purposes = anything not BULL (regime <= 70)
        # PUT spreads reduce risk → allowed at low governor in NEUTRAL/CAUTIOUS/BEAR
        regime_score_for_governor = self._get_effective_regime_score_for_options()
        is_put_direction = regime_score_for_governor <= config.SPREAD_REGIME_BULLISH  # <= 70

        if self._governor_scale == 0.0:
            # Governor SHUTDOWN (16%+ drawdown)
            # Only PUT spreads allowed - they hedge/profit from continued decline
            if not is_put_direction:
                self.Log(
                    f"OPTIONS_EOD: Blocked by Governor SHUTDOWN | "
                    f"Scale=0% | Regime={regime_score_for_governor:.0f} (BULL) | "
                    f"Only PUT spreads allowed at 0%"
                )
                return
            else:
                self.Log(
                    f"OPTIONS_EOD: PUT spread allowed at Governor 0% | "
                    f"Regime={regime_score_for_governor:.0f} | Thesis: PUT spreads active in non-BULL"
                )
                # Continue to spread entry logic below

        # V5.2 BINARY: No intermediate states - only 100% or 0%
        # If scale is not 100% and not 0%, log warning (shouldn't happen)
        elif self._governor_scale < 1.0:
            if not is_put_direction:
                self.Log(
                    f"OPTIONS_EOD: Blocked by Governor | "
                    f"Scale={self._governor_scale:.0%} < 100% for CALL | "
                    f"Regime={regime_score_for_governor:.0f}"
                )
                return

        # Skip if swing positions already maxed out
        can_swing, _ = self.options_engine.can_enter_swing()
        if not can_swing:
            return

        # CRITICAL FIX: Validate options symbol is resolved before use
        # Symbol may not be fully resolved on first trading day or after gaps
        if not self._validate_options_symbol():
            return

        # Get options chain from CurrentSlice (scheduled function, no Slice param)
        if self.CurrentSlice is None:
            return
        chain = self._get_valid_options_chain(self.CurrentSlice.OptionChains, mode_label="SWING")
        if chain is None:
            return

        # Get current values
        qqq_price, adx_value, ma200_value, ma50_value = self._get_options_market_snapshot()
        transition_ctx = self._get_transition_execution_context()
        regime_score = float(
            transition_ctx.get("transition_score", self._get_decision_regime_score_for_options())
            or self._get_decision_regime_score_for_options()
        )

        # V2.1: Calculate IV rank from options chain
        iv_rank = self._calculate_iv_rank(chain)

        # V5.3: Check position limits before scanning
        can_swing, swing_reason = self.options_engine.can_enter_swing()
        if not can_swing:
            # Debug log - skip in backtest to avoid log limits
            return

        self.options_engine.run_vass_entry_cycle(
            chain=chain,
            regime_score=regime_score,
            qqq_price=qqq_price,
            adx_value=adx_value,
            ma200_value=ma200_value,
            ma50_value=ma50_value,
            iv_rank=iv_rank,
            size_multiplier=size_multiplier,
            is_eod_scan=is_eod_scan,
        )

    def _handle_kill_switch(self, risk_result: RiskCheckResult) -> None:
        """
        V2.27: Handle graduated kill switch tiers.

        Tier 2 (TREND_EXIT): Liquidate trend + MR. Keep spreads (decouple).
        Tier 3 (FULL_EXIT): Liquidate everything including spreads. Reset cold start.

        Tier 1 (REDUCE) is handled inline through risk_result flags — it doesn't
        call this handler.

        Args:
            risk_result: Risk check result containing ks_tier and symbols to liquidate.
        """
        # V2.3 FIX: Only handle kill switch ONCE per day
        if self._kill_switch_handled_today:
            return
        self._kill_switch_handled_today = True

        tier = risk_result.ks_tier

        # Calculate P&L diagnostics for logging
        options_gross_pnl = 0.0
        options_long_pnl = 0.0
        options_short_pnl = 0.0
        trend_pnl = 0.0
        # V6.11: Use config for trend symbols
        trend_symbols = config.TREND_SYMBOLS

        for kvp in self.Portfolio:
            holding = kvp.Value
            if holding.Invested:
                if holding.Symbol.SecurityType == SecurityType.Option:
                    options_gross_pnl += holding.UnrealizedProfit
                    if holding.Quantity > 0:
                        options_long_pnl += holding.UnrealizedProfit
                    else:
                        options_short_pnl += holding.UnrealizedProfit
                elif str(holding.Symbol.Value) in trend_symbols:
                    trend_pnl += holding.UnrealizedProfit

        self.Log(
            f"SPREAD_PNL_DIAG: Long={options_long_pnl:+,.0f} Short={options_short_pnl:+,.0f} "
            f"Net={options_gross_pnl:+,.0f} | Trend={trend_pnl:+,.0f}"
        )

        total_loss_pct = 0.0
        if self.equity_prior_close > 0:
            total_loss_pct = (
                self.Portfolio.TotalPortfolioValue - self.equity_prior_close
            ) / self.equity_prior_close

        self.Log(
            f"KS_GRADUATED: {tier.value} at {self.Time} | "
            f"Equity={self.Portfolio.TotalPortfolioValue:,.2f} | "
            f"Loss={total_loss_pct:.2%} | Options P&L=${options_gross_pnl:,.0f} | "
            f"Trend P&L=${trend_pnl:,.0f}"
        )

        # Trigger in scheduler (disables all trading)
        self.scheduler.trigger_kill_switch()

        # ---- TIER 3: FULL EXIT ----
        if tier == KSTier.FULL_EXIT:
            self.Log("KS_FULL_EXIT: Liquidating ALL positions")

            # V2.33 CRITICAL: Close ALL options FIRST using atomic close (shorts before longs)
            # This MUST happen before any equity liquidation to prevent naked short margin errors
            self._close_options_atomic(reason="KS_TIER3_OPTIONS", clear_tracking=True)

            # Now safe to liquidate equity positions
            # symbols_to_liquidate is List[str] (equity only, options handled above)
            for symbol in risk_result.symbols_to_liquidate:
                self.Liquidate(symbol)

            # Clear options state and reset cold start
            self.options_engine.clear_all_positions()
            if config.KS_COLD_START_RESET_ON_TIER_3:
                self.cold_start_engine.reset()

            # V3.0 P0-A: Reset all engine internal state after full liquidation
            self.trend_engine.reset()
            if hasattr(self, "mr_engine") and self.mr_engine:
                self.mr_engine.reset()
            if hasattr(self, "hedge_engine") and self.hedge_engine:
                self.hedge_engine.reset()
            # Clear main.py spread tracking dicts (may already be cleared by atomic close)
            self._spread_fill_tracker = None
            self._pending_spread_orders.clear()
            self._pending_spread_orders_reverse.clear()
            self._pending_exit_orders.clear()
            self._exit_retry_scheduled_at.clear()
            self.Log("KS_CLEANUP: All engine state reset after Tier 3 liquidation")

        # ---- TIER 2: TREND EXIT ----
        elif tier == KSTier.TREND_EXIT:
            # V2.33: Close options FIRST using atomic close (shorts before longs)
            # V2.27: Spread decouple — keep active spreads, they have -50% stop
            if config.KILL_SWITCH_SPREAD_DECOUPLE:
                spreads = self.options_engine.get_spread_positions()
                spread_count = sum(s.num_spreads for s in spreads) if spreads else 0
                self.Log(
                    f"KS_SPREAD_DECOUPLE: Keeping {spread_count} active spreads | "
                    f"Monitored by -{config.SPREAD_STOP_LOSS_PCT:.0%} spread stop"
                )
                # Close single-leg options only (NOT spread legs) using atomic close
                self._ks_close_single_leg_options_atomic()
            else:
                # Legacy: close everything including spreads atomically
                self._close_options_atomic(reason="KS_TIER2_OPTIONS", clear_tracking=True)
                self.options_engine.clear_all_positions()

            # NOW liquidate trend + MR equity positions (options already handled above)
            # symbols_to_liquidate is List[str] (equity only, options handled above)
            equity_count = 0
            for symbol in risk_result.symbols_to_liquidate:
                self.Liquidate(symbol)
                equity_count += 1
            self.Log(f"KS_TREND_EXIT: Liquidated {equity_count} equity symbols")

            if config.KS_COLD_START_RESET_ON_TIER_2:
                self.cold_start_engine.reset()

            # V3.0 P0-A: Reset trend + MR state after Tier 2 liquidation (hedge stays)
            self.trend_engine.reset()
            if hasattr(self, "mr_engine") and self.mr_engine:
                self.mr_engine.reset()
            self.Log("KS_CLEANUP: Trend + MR state reset after Tier 2 liquidation")

    def _scan_mr_signals(self, data: Slice) -> None:
        """
        Scan for Mean Reversion entry signals.

        Checks TQQQ and SOXL for oversold conditions (RSI < 25).

        Args:
            data: Current data slice.
        """
        # V6.4: Skip MR engine in isolation mode if disabled
        if config.ISOLATION_TEST_MODE and not config.ISOLATION_MR_ENABLED:
            return

        # V2.20: Respect rejection cooldown before scanning
        if (
            self._mr_rejection_cooldown_until is not None
            and self.Time < self._mr_rejection_cooldown_until
        ):
            return

        # Get required context
        regime_score = self._last_regime_score
        days_running = (
            self.cold_start_engine.get_days_running()
            if hasattr(self.cold_start_engine, "get_days_running")
            else 5
        )
        gap_filter = getattr(self, "_gap_filter_active", False)
        vol_shock = getattr(self, "_vol_shock_active", False)
        time_guard = getattr(self, "_time_guard_active", False)

        # Calculate average volumes from rolling windows
        tqqq_avg_vol = self._get_average_volume(self.tqqq_volumes)
        soxl_avg_vol = self._get_average_volume(self.soxl_volumes)

        # DEBUG: Disabled to save log space
        # if self.Time.weekday() == 0 and self.Time.hour == 10 and self.Time.minute == 5:
        #     mr_pos = self.mr_engine.get_position_symbol()
        #     broker_tqqq = self.Portfolio[self.tqqq].Invested
        #     broker_soxl = self.Portfolio[self.soxl].Invested
        #     self.Log(f"MR_WEEKLY: regime={regime_score:.1f} mr_pos={mr_pos} broker_tqqq={broker_tqqq} broker_soxl={broker_soxl}")

        # Check TQQQ
        if self.tqqq_rsi.IsReady and not self.Portfolio[self.tqqq].Invested:
            tqqq_price = self.Securities[self.tqqq].Price
            tqqq_open = (
                data.Bars[self.tqqq].Open if data.Bars.ContainsKey(self.tqqq) else tqqq_price
            )
            tqqq_volume = (
                float(data.Bars[self.tqqq].Volume) if data.Bars.ContainsKey(self.tqqq) else 0.0
            )
            # Use open price as VWAP approximation for intraday
            tqqq_vwap = (tqqq_open + tqqq_price) / 2.0

            # DEBUG: Disabled to save log space
            # if self.Time.hour == 10 and self.Time.minute == 10:
            #     drop_pct = (tqqq_open - tqqq_price) / tqqq_open if tqqq_open > 0 else 0
            #     vol_ratio = tqqq_volume / tqqq_avg_vol if tqqq_avg_vol > 0 else 0
            #     self.Log(f"MR_TQQQ: RSI={self.tqqq_rsi.Current.Value:.1f} drop={drop_pct:.2%} vol_ratio={vol_ratio:.2f}")

            signal = self.mr_engine.check_entry_signal(
                symbol="TQQQ",
                current_price=tqqq_price,
                open_price=tqqq_open,
                rsi_value=self.tqqq_rsi.Current.Value,
                current_volume=tqqq_volume,
                avg_volume=tqqq_avg_vol,
                vwap=tqqq_vwap,
                regime_score=regime_score,
                days_running=days_running,
                gap_filter_triggered=gap_filter,
                vol_shock_active=vol_shock,
                time_guard_active=time_guard,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                vix_value=self._current_vix,  # V2.1: Pass VIX for regime filter
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

        # Check SOXL
        if self.soxl_rsi.IsReady and not self.Portfolio[self.soxl].Invested:
            soxl_price = self.Securities[self.soxl].Price
            soxl_open = (
                data.Bars[self.soxl].Open if data.Bars.ContainsKey(self.soxl) else soxl_price
            )
            soxl_volume = (
                float(data.Bars[self.soxl].Volume) if data.Bars.ContainsKey(self.soxl) else 0.0
            )
            # Use open price as VWAP approximation for intraday
            soxl_vwap = (soxl_open + soxl_price) / 2.0

            signal = self.mr_engine.check_entry_signal(
                symbol="SOXL",
                current_price=soxl_price,
                open_price=soxl_open,
                rsi_value=self.soxl_rsi.Current.Value,
                current_volume=soxl_volume,
                avg_volume=soxl_avg_vol,
                vwap=soxl_vwap,
                regime_score=regime_score,
                days_running=days_running,
                gap_filter_triggered=gap_filter,
                vol_shock_active=vol_shock,
                time_guard_active=time_guard,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                vix_value=self._current_vix,  # V2.1: Pass VIX for regime filter
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

        # V6.11: Check SPXL (3× S&P 500 for broader market bounce)
        if self.spxl_rsi.IsReady and not self.Portfolio[self.spxl].Invested:
            spxl_price = self.Securities[self.spxl].Price
            spxl_open = (
                data.Bars[self.spxl].Open if data.Bars.ContainsKey(self.spxl) else spxl_price
            )
            spxl_volume = (
                float(data.Bars[self.spxl].Volume) if data.Bars.ContainsKey(self.spxl) else 0.0
            )
            spxl_vwap = (spxl_open + spxl_price) / 2.0
            spxl_avg_vol = self._get_average_volume(self.spxl_volumes)

            signal = self.mr_engine.check_entry_signal(
                symbol="SPXL",
                current_price=spxl_price,
                open_price=spxl_open,
                rsi_value=self.spxl_rsi.Current.Value,
                current_volume=spxl_volume,
                avg_volume=spxl_avg_vol,
                vwap=spxl_vwap,
                regime_score=regime_score,
                days_running=days_running,
                gap_filter_triggered=gap_filter,
                vol_shock_active=vol_shock,
                time_guard_active=time_guard,
                current_hour=self.Time.hour,
                current_minute=self.Time.minute,
                vix_value=self._current_vix,
            )
            if signal:
                self.portfolio_router.receive_signal(signal)

    def _on_mr_force_close(self) -> None:
        """
        MR force close at 15:45 ET.

        Forces close of all Mean Reversion positions (TQQQ, SOXL).
        These are intraday-only symbols that cannot be held overnight.
        """
        # Skip during warmup - no orders allowed
        if self.IsWarmingUp:
            return
        if self._mr_force_close_ran_date == self.Time.date():
            return
        self._mr_force_close_ran_date = self.Time.date()

        # V6.11: Check all MR symbols (TQQQ, SOXL, SPXL)
        for mr_symbol, mr_name in [(self.tqqq, "TQQQ"), (self.soxl, "SOXL"), (self.spxl, "SPXL")]:
            if self.Portfolio[mr_symbol].Invested:
                signal = TargetWeight(
                    symbol=mr_name,
                    target_weight=0.0,
                    source="MR",
                    urgency=Urgency.IMMEDIATE,
                    reason="TIME_EXIT_15:45",
                )
                self.portfolio_router.receive_signal(signal)

        # Process the signals immediately
        self._process_immediate_signals()

        # CRITICAL FAILSAFE: Direct Liquidate() call to ensure 3x ETFs are closed
        # This runs AFTER signal processing as a belt-and-suspenders safety measure
        # 3x ETFs held overnight can suffer catastrophic losses from gaps
        # V6.11: Updated for all MR symbols
        for mr_symbol, mr_name in [(self.tqqq, "TQQQ"), (self.soxl, "SOXL"), (self.spxl, "SPXL")]:
            if self.Portfolio[mr_symbol].Invested:
                self.Log(f"MR_FAILSAFE: Force liquidating {mr_name} via direct Liquidate()")
                self.Liquidate(mr_symbol, tag="MR_FAILSAFE_15:45")
