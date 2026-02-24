from AlgorithmImports import *

import config
from engines.satellite.premarket_vix_actions import apply_premarket_vix_actions
from models.enums import Urgency
from models.target_weight import TargetWeight


class MainPremarketMixin:
    """Pre-market setup and ladder helpers extracted from main.py (move-only)."""

    def _on_pre_market_setup(self) -> None:
        """
        Pre-market setup at 09:25 ET.

        Resets daily state (kill switch, panic mode, etc.) for new day.
        Sets equity_prior_close baseline for kill switch calculation.
        Sets SPY prior close for gap filter.
        """
        # Skip during warmup
        if self.IsWarmingUp:
            return
        # Reset daily state (kill switch, panic mode, etc.) at start of new day
        self.risk_engine.reset_daily_state()
        self.scheduler.reset_daily()  # V2.3 FIX: Reset scheduler kill switch flag
        self._kill_switch_handled_today = False  # V2.3: Allow kill switch to trigger again today
        # Reset once-per-day handler guards at daily boundary.
        self._intraday_force_close_ran_date = None
        self._mr_force_close_ran_date = None
        self._eod_processing_ran_date = None
        self._market_close_ran_date = None
        # Reset sample key continuity only; keep prior score for overnight transition detection.
        self._regime_detector_last_update_key = None
        self._regime_detector_last_raw = {}
        self._regime_overlay_ambiguous_bars = 0

        # V2.3 FIX: Reset options engine daily state (entry flags, trade counters)
        current_date_str = str(self.Time.date())
        self.options_engine.reset_daily(current_date_str)

        # V3.0 P0-C: Reset satellite engine daily state
        if hasattr(self, "hedge_engine") and self.hedge_engine:
            self.hedge_engine.reset()
        if hasattr(self, "mr_engine") and self.mr_engine:
            self.mr_engine.reset()

        # V2.3 DEBUG: Log daily reset confirmation (only in live mode)
        if self.LiveMode:
            self.Log(f"DAILY_RESET: All flags cleared at {self.Time}")

        self.equity_prior_close = self.Portfolio.TotalPortfolioValue
        self.risk_engine.set_equity_prior_close(self.equity_prior_close)
        self._last_regime_effective_log_at = None

        # V5.1: Get regime score for Governor decisions
        # Regime Guard checks this for step-up eligibility
        regime_score_for_governor = self.regime_engine.get_previous_score()

        # V2.26/V5.1: Drawdown Governor - check cumulative DD from peak, scale allocations
        # V5.1: Now passes regime_score for Regime Guard check on step-up
        self._governor_scale = self.risk_engine.check_drawdown_governor(
            self.equity_prior_close, regime_score_for_governor
        )

        # V3.0: Regime Override - if bullish regime persists, force Governor step-up
        # V5.1: DISABLED by default (GOVERNOR_REGIME_OVERRIDE_ENABLED = False)
        # This was the root cause of the 2017 death spiral (27 liquidation events)
        # Kept for backward compatibility - method checks config flag internally
        current_date_str = str(self.Time.date())
        if self.risk_engine.check_governor_regime_override(
            regime_score_for_governor, current_date_str
        ):
            # Override was applied, update local scale
            self._governor_scale = self.risk_engine.get_governor_scale()

        # V2.26: Governor at 0% = full shutdown - liquidate all positions
        # V3.1/V6.11: EXEMPT hedge positions (SH) - they were legitimately opened at EOD
        # and should NOT be liquidated the next morning (causes guaranteed hedge losses)
        if self._governor_scale == 0.0 and self.Portfolio.Invested:
            # Check which positions to liquidate (exempt hedges)
            hedge_symbols = {self.sh}
            has_non_hedge = any(
                kvp.Value.Invested and kvp.Value.Symbol not in hedge_symbols
                for kvp in self.Portfolio
            )
            if has_non_hedge:
                self.Log(
                    f"GOVERNOR: SHUTDOWN - Liquidating non-hedge positions | "
                    f"Equity=${self.equity_prior_close:,.0f} | Hedges exempt"
                )
                self._liquidate_all_spread_aware("GOVERNOR_SHUTDOWN", exempt_symbols=hedge_symbols)
                self.portfolio_router.clear_pending()

        # Set SPY prior close for gap filter
        self.spy_prior_close = self.Securities[self.spy].Close
        self.risk_engine.set_spy_prior_close(self.spy_prior_close)

        # V6.14: Seed prior closes on first trading day if needed.
        if self._vix_prior_close <= 0:
            self._vix_prior_close = self._get_vix_level()
        if self._uvxy_prior_close <= 0 and hasattr(self, "uvxy"):
            uvxy_close = self.Securities[self.uvxy].Close
            if uvxy_close > 0:
                self._uvxy_prior_close = uvxy_close

        # V6.14: Pre-market VIX shock ladder (shared protection across options modes)
        self._update_premarket_vix_ladder()
        self._apply_premarket_vix_actions()

        # V6.10 P0: Pre-market ITM check for spread positions
        # Check if any short legs went ITM overnight and queue for close
        if getattr(config, "PREMARKET_ITM_CHECK_ENABLED", True):
            self._check_premarket_itm_shorts()
        # Weekend/holiday ITM guard: queue exits on adverse post-gap open risk.
        self._queue_itm_weekend_gap_exit_signals()

        # V3.0: Schedule dynamic EOD events based on actual market close time
        # Handles early close days (1:00 PM) automatically
        self._schedule_dynamic_eod_events()

    def _schedule_dynamic_eod_events(self) -> None:
        """
        V3.0: Schedule EOD events dynamically based on actual market close time.

        Queries Exchange.Hours.GetNextMarketClose() to determine today's actual
        close time, then schedules MR force close, EOD processing, and market
        close events relative to that time.

        Handles:
            - Normal days (4:00 PM close): Events at 15:15/15:45/16:00
            - Early close days (1:00 PM): Events at 12:15/12:45/13:00
        """
        try:
            # Get actual market close time for today
            market_hours = self.Securities[self.spy].Exchange.Hours
            market_close = market_hours.GetNextMarketClose(self.Time, False)
            is_normal_close = market_close.hour == 16 and market_close.minute == 0

            # #10 fix: avoid duplicate static+dynamic schedules on normal close days.
            # Static fallback schedules already exist for configured force-close/15:45/16:00.
            if not is_normal_close:
                self.scheduler.schedule_dynamic_eod_events(market_close)
            else:
                self.Log(
                    "EOD_SCHEDULE: Normal close detected | Using static fallback schedules only"
                )

            # Also schedule intraday options force close dynamically
            from datetime import timedelta

            opt_offset = getattr(config, "INTRADAY_OPTIONS_OFFSET_MINUTES", 45)
            opt_close_time = market_close - timedelta(minutes=opt_offset)
            static_force_exit = getattr(config, "INTRADAY_FORCE_EXIT_TIME", "15:15")
            static_h, static_m = map(int, static_force_exit.split(":"))
            # Keep dynamic scheduling for early-close sessions or when dynamic time differs.
            if not (
                is_normal_close
                and opt_close_time.hour == static_h
                and opt_close_time.minute == static_m
            ):
                self.Schedule.On(
                    self.DateRules.On(self.Time.year, self.Time.month, self.Time.day),
                    self.TimeRules.At(opt_close_time.hour, opt_close_time.minute),
                    self._on_intraday_options_force_close,
                )

        except Exception as e:
            # Fallback to fixed times if dynamic scheduling fails
            self.Log(f"EOD_SCHEDULE_ERROR: {e} - using fixed 15:45/16:00 fallback")
            # Fixed fallback schedules are registered in _setup_schedules() if needed

    def _check_premarket_itm_shorts(self) -> None:
        """
        V6.10 P0: Pre-market ITM check at 09:25 ET.

        Check all spread short legs BEFORE market open to catch overnight gaps.
        If a short leg went ITM overnight, queue for immediate close at 09:30.

        This prevents assignment losses from overnight gaps that bypass
        the regular trading-hours ITM checks.
        """
        # Skip if options engine not initialized or no spread position
        if not hasattr(self, "options_engine") or self.options_engine is None:
            return

        spreads = self.options_engine.get_spread_positions()
        if not spreads:
            return  # V6.12: Silent return, no log needed for normal case

        # Get current QQQ price (pre-market)
        qqq_price = self.Securities[self.qqq].Price
        if qqq_price <= 0:
            self.Log(f"PREMARKET_ITM_CHECK: Invalid QQQ price {qqq_price} - skipping")
            return

        # Call options engine pre-market check
        exit_signals = []
        for spread in spreads:
            signals = self.options_engine.check_premarket_itm_shorts(
                underlying_price=qqq_price,
                spread_override=spread,
            )
            if signals:
                exit_signals.extend(signals)

        if exit_signals:
            # Queue the exit signals for immediate execution at market open
            self.Log(
                f"PREMARKET_ITM_CHECK: ITM short detected - queuing close for market open | "
                f"QQQ={qqq_price:.2f}"
            )
            # Process through portfolio router for proper execution
            for signal in exit_signals:
                signal.symbol = self._normalize_symbol_str(signal.symbol)
                if signal.metadata:
                    short_leg_sym = signal.metadata.get("spread_short_leg_symbol")
                    if short_leg_sym is not None:
                        signal.metadata["spread_short_leg_symbol"] = self._normalize_symbol_str(
                            short_leg_sym
                        )
                self.portfolio_router.receive_signal(signal)

    def _get_premarket_vix_gap_proxy_pct(self) -> float:
        """
        Estimate overnight VIX gap using UVXY close-to-preopen move.

        QC backtests provide CBOE VIX at daily resolution, so pre-open VIX gap
        is approximated from UVXY gap using ~1.5x relationship.
        """
        if self._uvxy_prior_close <= 0:
            return 0.0
        uvxy_now = self.Securities[self.uvxy].Price if hasattr(self, "uvxy") else 0.0
        if uvxy_now <= 0:
            return 0.0
        uvxy_gap_pct = (uvxy_now - self._uvxy_prior_close) / self._uvxy_prior_close * 100.0
        return uvxy_gap_pct / 1.5

    def _update_premarket_vix_ladder(self) -> None:
        """Set the daily pre-market VIX ladder state (L0-L3)."""
        self._premarket_vix_ladder_level = 0
        self._premarket_vix_ladder_reason = "L0_NORMAL"
        self._premarket_vix_size_mult = 1.0
        self._premarket_vix_entry_block_until = None
        self._premarket_vix_call_block_until = None
        self._premarket_vix_shock_pct = 0.0
        self._premarket_vix_shock_memory_until = None

        if not getattr(config, "PREMARKET_VIX_LADDER_ENABLED", True):
            return

        vix_level = self._get_vix_level()
        vix_gap_proxy_pct = self._get_premarket_vix_gap_proxy_pct()
        vix_shock_level = max(vix_level, self._vix_prior_close * (1.0 + vix_gap_proxy_pct / 100.0))

        if (
            vix_shock_level >= config.PREMARKET_VIX_L3_LEVEL
            or vix_gap_proxy_pct >= config.PREMARKET_VIX_L3_GAP_PCT
        ):
            self._premarket_vix_ladder_level = 3
            self._premarket_vix_size_mult = config.PREMARKET_VIX_L3_SIZE_MULT
            self._premarket_vix_entry_block_until = (
                config.PREMARKET_VIX_L3_ENTRY_BLOCK_UNTIL_HOUR,
                config.PREMARKET_VIX_L3_ENTRY_BLOCK_UNTIL_MINUTE,
            )
            self._premarket_vix_ladder_reason = (
                f"L3_PANIC | VIX={vix_level:.1f} | Shock={vix_shock_level:.1f} | "
                f"GapProxy={vix_gap_proxy_pct:+.1f}%"
            )
        elif (
            vix_shock_level >= config.PREMARKET_VIX_L2_LEVEL
            or vix_gap_proxy_pct >= config.PREMARKET_VIX_L2_GAP_PCT
        ):
            self._premarket_vix_ladder_level = 2
            self._premarket_vix_size_mult = config.PREMARKET_VIX_L2_SIZE_MULT
            self._premarket_vix_call_block_until = (
                config.PREMARKET_VIX_L2_CALL_BLOCK_UNTIL_HOUR,
                config.PREMARKET_VIX_L2_CALL_BLOCK_UNTIL_MINUTE,
            )
            self._premarket_vix_ladder_reason = (
                f"L2_STRESS | VIX={vix_level:.1f} | Shock={vix_shock_level:.1f} | "
                f"GapProxy={vix_gap_proxy_pct:+.1f}%"
            )
        elif (
            vix_shock_level >= config.PREMARKET_VIX_L1_LEVEL
            or vix_gap_proxy_pct >= config.PREMARKET_VIX_L1_GAP_PCT
        ):
            self._premarket_vix_ladder_level = 1
            self._premarket_vix_size_mult = config.PREMARKET_VIX_L1_SIZE_MULT
            self._premarket_vix_ladder_reason = (
                f"L1_ELEVATED | VIX={vix_level:.1f} | Shock={vix_shock_level:.1f} | "
                f"GapProxy={vix_gap_proxy_pct:+.1f}%"
            )

        if self._premarket_vix_ladder_level > 0:
            self.Log(f"PREMARKET_VIX_LADDER: {self._premarket_vix_ladder_reason}")

        # V6.16: Persist overnight panic context into early session for Micro/VASS.
        # Without this, intraday logic can "restart" from high VIX open and misclassify calming.
        if (
            getattr(config, "MICRO_SHOCK_MEMORY_ENABLED", True)
            and self._vix_prior_close > 0
            and self._premarket_vix_ladder_level
            >= getattr(config, "MICRO_SHOCK_MEMORY_MIN_LADDER_LEVEL", 2)
        ):
            self._premarket_vix_shock_pct = max(
                0.0, (vix_shock_level - self._vix_prior_close) / self._vix_prior_close
            )
            self._premarket_vix_shock_memory_until = (
                getattr(config, "MICRO_SHOCK_MEMORY_UNTIL_HOUR", 13),
                getattr(config, "MICRO_SHOCK_MEMORY_UNTIL_MINUTE", 0),
            )
            until_h, until_m = self._premarket_vix_shock_memory_until
            self.Log(
                f"PREMARKET_SHOCK_MEMORY: Active until {until_h:02d}:{until_m:02d} | "
                f"Shock={self._premarket_vix_shock_pct:+.1%} | "
                f"Ladder={self._premarket_vix_ladder_level}"
            )

    def _apply_premarket_vix_actions(self) -> None:
        apply_premarket_vix_actions(self)

    def _is_premarket_ladder_entry_block_active(self) -> bool:
        """Return True when ladder blocks all new options entries."""
        if self._premarket_vix_entry_block_until is None:
            return False
        block_hour, block_minute = self._premarket_vix_entry_block_until
        return (self.Time.hour, self.Time.minute) < (block_hour, block_minute)

    def _is_premarket_ladder_call_block_active(self) -> bool:
        """Return True when ladder blocks new CALL direction entries."""
        if self._premarket_vix_call_block_until is None:
            return False
        block_hour, block_minute = self._premarket_vix_call_block_until
        return (self.Time.hour, self.Time.minute) < (block_hour, block_minute)

    def _is_premarket_shock_memory_active(self) -> bool:
        """Return True while overnight VIX shock memory should affect intraday decisions."""
        if self._premarket_vix_shock_memory_until is None:
            return False
        block_hour, block_minute = self._premarket_vix_shock_memory_until
        return (self.Time.hour, self.Time.minute) < (block_hour, block_minute)

    def _get_premarket_shock_memory_pct(self) -> float:
        """Get active overnight VIX shock memory as decimal percentage."""
        if not self._is_premarket_shock_memory_active():
            return 0.0
        return max(0.0, self._premarket_vix_shock_pct)

    def _queue_itm_weekend_gap_exit_signals(self) -> None:
        """Queue post-weekend/holiday ITM exits on adverse gap or vol shock."""
        if not bool(getattr(config, "ITM_WEEKEND_GAP_EXIT_ENABLED", True)):
            return
        if self._last_market_close_check is None:
            return
        days_gap = (self.Time.date() - self._last_market_close_check).days
        if days_gap < 3:
            return

        qqq_prior_close = float(self.Securities[self.qqq].Close or 0.0)
        qqq_now = float(self.Securities[self.qqq].Price or 0.0)
        if qqq_prior_close <= 0 or qqq_now <= 0:
            return

        adverse_gap_threshold = float(getattr(config, "ITM_WEEKEND_GAP_ADVERSE_PCT", 0.01))
        vix_shock_threshold = float(getattr(config, "ITM_WEEKEND_GAP_VIX_SHOCK_PCT", 0.15))
        qqq_gap_pct = (qqq_now - qqq_prior_close) / qqq_prior_close
        vix_shock_pct = max(0.0, float(self._get_premarket_vix_gap_proxy_pct()) / 100.0)

        queued = 0
        for intraday_pos in self.options_engine.get_intraday_positions():
            if intraday_pos is None or intraday_pos.contract is None:
                continue
            strategy = str(getattr(intraday_pos, "entry_strategy", "") or "").upper()
            if strategy != "ITM_MOMENTUM":
                continue
            if not self.options_engine.should_hold_intraday_overnight(intraday_pos):
                continue

            symbol = self._normalize_symbol_str(intraday_pos.contract.symbol)
            is_call = "C" in symbol
            is_put = "P" in symbol
            adverse_gap = (is_call and qqq_gap_pct <= -adverse_gap_threshold) or (
                is_put and qqq_gap_pct >= adverse_gap_threshold
            )
            vix_shock = vix_shock_pct >= vix_shock_threshold
            if not adverse_gap and not vix_shock:
                continue

            live_qty = abs(self._get_option_holding_quantity(symbol))
            if live_qty <= 0:
                continue

            reasons = []
            if adverse_gap:
                reasons.append(f"ADVERSE_GAP {qqq_gap_pct:+.2%}")
            if vix_shock:
                reasons.append(f"VIX_SHOCK {vix_shock_pct:+.1%}")
            reason_text = " + ".join(reasons) if reasons else "POST_GAP_RISK"

            self.portfolio_router.receive_signal(
                TargetWeight(
                    symbol=symbol,
                    target_weight=0.0,
                    source="OPT_INTRADAY",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"ITM_WEEKEND_GAP_EXIT: {reason_text}",
                    requested_quantity=live_qty,
                    metadata={
                        "intraday_strategy": "ITM_MOMENTUM",
                        "weekend_guard": "POST_GAP",
                    },
                )
            )
            queued += 1
            self.Log(
                f"ITM_WEEKEND_GAP_EXIT_QUEUED: {symbol} | Reason={reason_text} | "
                f"Qty={live_qty} | GapDays={days_gap}"
            )

        if queued > 0:
            self.Log(
                f"ITM_WEEKEND_GAP_EXIT: Queued={queued} | QQQ_Gap={qqq_gap_pct:+.2%} | "
                f"VIX_Shock={vix_shock_pct:+.1%} | GapDays={days_gap}"
            )
