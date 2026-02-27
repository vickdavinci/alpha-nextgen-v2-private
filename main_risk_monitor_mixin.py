from __future__ import annotations

from typing import Optional

from AlgorithmImports import SecurityType, Slice

from engines.core.risk_engine import GreeksSnapshot
from models.enums import Urgency
from models.target_weight import TargetWeight


class MainRiskMonitorMixin:
    def _monitor_risk_greeks(self, data: Slice) -> None:
        # Monitor spread exits/greeks with fresh chain values when available.
        # Skip if no options position
        if not self.options_engine.has_position():
            return

        # Transition handoff: de-risk wrong-way open positions immediately after overlay flips.
        if self._apply_transition_handoff_open_position_derisk(data):
            return

        # V2.3: Check spread exit conditions if we have a spread position
        if self.options_engine.has_spread_position():
            self._check_spread_exit(data)
            return  # Spread exit handling is separate from single-leg Greeks

        # CRITICAL: Fetch FRESH Greeks from OptionChain (not cached values)
        # Greeks change rapidly, especially for 0-2 DTE options
        greeks = self._get_fresh_position_greeks()
        if greeks is None:
            # Fall back to cached Greeks if chain not available
            greeks = self.options_engine.calculate_position_greeks()
            if greeks is None:
                return

        # Update risk engine with Greeks
        self.risk_engine.update_greeks(greeks)

        # Check for Greeks breach
        breach, reasons = self.options_engine.check_greeks_breach(self.risk_engine)
        if breach:
            # Only log Greeks breach once per position to prevent log overflow
            if not self._greeks_breach_logged:
                self.Log(f"GREEKS_BREACH: {', '.join(reasons)}")
                self._greeks_breach_logged = True
            # Emit exit signals for real option holdings (never synthetic symbols).
            signals_emitted = 0
            seen_symbols = set()
            for kvp in self.Portfolio:
                holding = kvp.Value
                if (
                    not holding.Invested
                    or holding.Symbol.SecurityType != SecurityType.Option
                    or int(holding.Quantity) == 0
                ):
                    continue
                symbol_str = self._normalize_symbol_str(holding.Symbol)
                if symbol_str in seen_symbols:
                    continue
                seen_symbols.add(symbol_str)
                if self._has_open_non_oco_order_for_symbol(symbol_str):
                    continue
                self.portfolio_router.receive_signal(
                    TargetWeight(
                        symbol=symbol_str,
                        target_weight=0.0,
                        source="RISK",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"GREEKS_BREACH: {', '.join(reasons)}",
                        requested_quantity=abs(int(holding.Quantity)),
                    )
                )
                signals_emitted += 1
            if signals_emitted == 0:
                self.Log("GREEKS_BREACH_EXIT_SKIP: No live option holdings found")
            return  # Exit already triggered, don't check other exits

        # V2.3.11: Check expiring options force exit (15:45 for 0 DTE)
        # CRITICAL: Prevents auto-exercise of ITM options held into close
        position = self.options_engine.get_position()
        intraday_positions = self.options_engine.get_engine_positions()
        expiring_candidates = []
        if position is not None:
            expiring_candidates.append(position)
        expiring_candidates.extend(intraday_positions)

        expiring_exit_emitted = False
        for any_position in expiring_candidates:
            # Get contract expiry date
            any_symbol = self._normalize_symbol_str(any_position.contract.symbol)
            contract_expiry = self._get_option_expiry_date(any_position.contract.symbol, data)
            current_date = str(self.Time.date())
            current_price = self._get_option_current_price(any_position.contract.symbol, data)

            if (
                current_price is not None
                and contract_expiry is not None
                and not self._has_open_non_oco_order_for_symbol(any_symbol)
            ):
                signal = self.options_engine.check_expiring_options_force_exit(
                    current_date=current_date,
                    current_hour=self.Time.hour,
                    current_minute=self.Time.minute,
                    current_price=current_price,
                    contract_expiry_date=contract_expiry,
                    position=any_position,
                )
                if signal is not None:
                    self.portfolio_router.receive_signal(signal)
                    expiring_exit_emitted = True
        if expiring_exit_emitted:
            return  # Force exits take priority, skip other exit checks

        # V2.3.10: Check single-leg exit signals (profit target, stop, DTE exit)
        # This prevents options from being held to expiration/exercise
        if position is not None:
            swing_symbol = self._normalize_symbol_str(position.contract.symbol)
            if self._has_open_non_oco_order_for_symbol(swing_symbol):
                position = None
            else:
                # Get current option price from chain
                current_price = self._get_option_current_price(position.contract.symbol, data)
                current_dte = self._get_option_current_dte(position.contract.symbol, data)

                if current_price is not None:
                    signal = self.options_engine.check_exit_signals(
                        current_price=current_price,
                        current_dte=current_dte,
                    )
                    if signal is not None:
                        self._single_leg_last_exit_reason[swing_symbol] = str(
                            getattr(signal, "reason", "") or ""
                        )[:180]
                        self.portfolio_router.receive_signal(signal)
                        # V9.2 FIX: Cancel OCO on software exit (swing single-leg)
                        try:
                            sym_str = str(position.contract.symbol)
                            if self.oco_manager.cancel_by_symbol(sym_str, reason="SOFTWARE_EXIT"):
                                self.Log(
                                    f"OCO_CANCEL: {sym_str} | "
                                    f"Reason=SOFTWARE_EXIT ({signal.reason})"
                                )
                        except Exception as e:
                            self.Log(f"OCO_CANCEL_ERROR: Swing exit OCO cancel | {e}")

        # V6.22 FIX: Software backup for intraday positions (OCO single-point-of-failure fix)
        # Previously intraday had NO software stop/profit/DTE check — relied solely on OCO.
        # If OCO was cancelled (199 events in 2022), position bled until force-exit.
        for intraday_position in intraday_positions:
            intra_symbol = self._normalize_symbol_str(intraday_position.contract.symbol)
            if not self._has_open_non_oco_order_for_symbol(intra_symbol):
                intra_price = self._get_option_current_price(
                    intraday_position.contract.symbol, data
                )
                intra_dte = self._get_option_current_dte(intraday_position.contract.symbol, data)

                if intra_price is not None:
                    signal = self.options_engine.check_exit_signals(
                        current_price=intra_price,
                        current_dte=intra_dte,
                        position=intraday_position,
                    )
                    if signal is not None:
                        self._single_leg_last_exit_reason[intra_symbol] = str(
                            getattr(signal, "reason", "") or ""
                        )[:180]
                        self.portfolio_router.receive_signal(signal)
                        # V9.2 FIX: Cancel OCO immediately when software backup triggers
                        # an exit. Without this, the OCO stop/limit orders remain active
                        # and could fire after the software exit order fills, creating
                        # accidental short positions from orphaned sell orders.
                        try:
                            sym_str = str(intraday_position.contract.symbol)
                            if self.oco_manager.cancel_by_symbol(sym_str, reason="SOFTWARE_EXIT"):
                                self.Log(
                                    f"OCO_CANCEL: {sym_str} | "
                                    f"Reason=SOFTWARE_EXIT ({signal.reason})"
                                )
                        except Exception as e:
                            self.Log(f"OCO_CANCEL_ERROR: Software exit OCO cancel | {e}")
                    else:
                        # Keep broker OCO rails aligned with software trailing-stop updates.
                        # Without this, software can tighten stop_price while broker OCO stays stale.
                        live_qty = abs(self._get_option_holding_quantity(intra_symbol))
                        if live_qty <= 0:
                            live_qty = abs(int(getattr(intraday_position, "num_contracts", 0) or 0))
                        if live_qty > 0:
                            self._sync_engine_oco(
                                symbol=intra_symbol,
                                position=intraday_position,
                                quantity=live_qty,
                                reason="TRAIL_REFRESH",
                            )

    def _get_fresh_position_greeks(self) -> Optional[GreeksSnapshot]:
        """
        Fetch fresh Greeks from OptionChain for current position.

        CRITICAL: Greeks cached at entry become stale within minutes.
        For 0-2 DTE options, Greeks can change 50%+ in an hour.
        This method fetches live Greeks from the data feed.

        Returns:
            Fresh GreeksSnapshot or None if chain/contract not available.
        """
        # Get current position symbol
        position = self.options_engine.get_position()
        if position is None:
            return None

        position_symbol = position.contract.symbol

        # Get options chain from CurrentSlice (this function has no Slice param)
        if self.CurrentSlice is None:
            return None
        chain = (
            self.CurrentSlice.OptionChains[self._qqq_option_symbol]
            if self._qqq_option_symbol in self.CurrentSlice.OptionChains
            else None
        )
        if chain is None:
            return None

        # CRITICAL: Wrap chain iteration in try-catch to handle malformed data
        try:
            # Find our contract in the chain and get fresh Greeks
            for contract in chain:
                if str(contract.Symbol) == position_symbol:
                    # Found our contract - extract fresh Greeks
                    delta = contract.Greeks.Delta if hasattr(contract, "Greeks") else None
                    gamma = contract.Greeks.Gamma if hasattr(contract, "Greeks") else None
                    vega = contract.Greeks.Vega if hasattr(contract, "Greeks") else None
                    theta = contract.Greeks.Theta if hasattr(contract, "Greeks") else None

                    if delta is not None:
                        # Update position with fresh Greeks
                        self.options_engine.update_position_greeks(delta, gamma, vega, theta)

                        return GreeksSnapshot(
                            delta=delta,
                            gamma=gamma or 0.0,
                            vega=vega or 0.0,
                            theta=theta or 0.0,
                        )
                    break
        except Exception as e:
            # Chain iteration failed - log and continue with cached Greeks
            self.Log(f"GREEKS_REFRESH_ERROR: {e}")

        return None
