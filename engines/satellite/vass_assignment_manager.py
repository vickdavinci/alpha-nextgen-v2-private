"""VASS assignment-risk helpers extracted from options_engine."""

from __future__ import annotations

import config
from engines.satellite.options_primitives import SpreadStrategy
from models.enums import Urgency
from models.target_weight import TargetWeight


def is_short_leg_deep_itm_impl(
    self,
    short_leg: "OptionContract",
    underlying_price: float,
    current_dte: int,
) -> Tuple[bool, str]:
    """
    V5.3 P0 Fix 1: Check if short leg is deep ITM (assignment risk).

    A short option is considered deep ITM when:
    - DTE <= threshold (default 3)
    - AND (delta > threshold OR intrinsic value > threshold)

    Args:
        short_leg: The short leg of the spread
        underlying_price: Current price of underlying (QQQ)
        current_dte: Days to expiration

    Returns:
        Tuple of (is_deep_itm, reason)
    """
    if not getattr(config, "DEEP_ITM_EXIT_ENABLED", True):
        return False, ""

    dte_threshold = getattr(config, "DEEP_ITM_EXIT_DTE_THRESHOLD", 3)
    delta_threshold = getattr(config, "DEEP_ITM_EXIT_DELTA_THRESHOLD", 0.80)
    intrinsic_pct_threshold = getattr(config, "DEEP_ITM_EXIT_INTRINSIC_PCT", 0.05)

    # Only check if DTE is within threshold
    if current_dte > dte_threshold:
        return False, ""

    # Calculate intrinsic value
    is_put = "P" in str(short_leg.symbol).upper()
    strike = short_leg.strike

    if is_put:
        # Put is ITM when strike > underlying
        intrinsic = max(0, strike - underlying_price)
        is_itm = strike > underlying_price
    else:
        # Call is ITM when strike < underlying
        intrinsic = max(0, underlying_price - strike)
        is_itm = strike < underlying_price

    if not is_itm:
        return False, ""

    intrinsic_pct = intrinsic / underlying_price if underlying_price > 0 else 0

    # Check delta threshold (if available)
    short_delta = abs(getattr(short_leg, "delta", 0.5))
    is_deep_by_delta = short_delta >= delta_threshold

    # Check intrinsic threshold
    is_deep_by_intrinsic = intrinsic_pct >= intrinsic_pct_threshold

    if is_deep_by_delta or is_deep_by_intrinsic:
        reason = (
            f"DEEP_ITM_SHORT: DTE={current_dte} | Delta={short_delta:.2f} | "
            f"Intrinsic=${intrinsic:.2f} ({intrinsic_pct:.1%}) | "
            f"Strike={strike} vs Underlying={underlying_price:.2f}"
        )
        return True, reason

    return False, ""


def check_overnight_itm_short_risk_impl(
    self,
    short_leg: "OptionContract",
    underlying_price: float,
    current_dte: int,
    current_hour: int,
    current_minute: int,
) -> Tuple[bool, str]:
    """
    V5.3 P0 Fix 2: Block holding short ITM options overnight if DTE <= 2.

    Assignment typically happens overnight. If a short option is ITM
    near market close with low DTE, force close to avoid assignment.

    Returns:
        Tuple of (should_close, reason)
    """
    if not getattr(config, "OVERNIGHT_ITM_SHORT_BLOCK_ENABLED", True):
        return False, ""

    dte_threshold = getattr(config, "OVERNIGHT_ITM_SHORT_DTE_THRESHOLD", 2)
    check_hour = getattr(config, "OVERNIGHT_ITM_SHORT_CHECK_TIME_HOUR", 15)
    check_minute = getattr(config, "OVERNIGHT_ITM_SHORT_CHECK_TIME_MINUTE", 0)

    # Only check at or after the configured time
    current_time_mins = current_hour * 60 + current_minute
    check_time_mins = check_hour * 60 + check_minute

    if current_time_mins < check_time_mins:
        return False, ""

    # Only check if DTE is within threshold
    if current_dte > dte_threshold:
        return False, ""

    # Check if short leg is ITM
    is_put = "P" in str(short_leg.symbol).upper()
    strike = short_leg.strike

    if is_put:
        is_itm = strike > underlying_price
    else:
        is_itm = strike < underlying_price

    if is_itm:
        reason = (
            f"OVERNIGHT_ITM_BLOCK: Short {'PUT' if is_put else 'CALL'} "
            f"Strike={strike} is ITM (Underlying={underlying_price:.2f}) | "
            f"DTE={current_dte} | Time={current_hour}:{current_minute:02d} | "
            f"Closing to prevent overnight assignment risk"
        )
        return True, reason

    return False, ""


def check_assignment_margin_buffer_impl(
    self,
    spread: "SpreadPosition",
    underlying_price: float,
    available_margin: float,
) -> Tuple[bool, str]:
    """
    V5.3 P0 Fix 3: Check if margin buffer is sufficient for assignment risk.

    V6.7 FIX: Use spread's actual max loss, not naked short exposure.
    For vertical spreads, the long leg covers the short leg, so:
    - Debit spreads: max loss = net debit paid
    - Credit spreads: max loss = width - credit received

    Only triggers if we don't have enough margin buffer to handle
    the spread's actual max loss (not the naked assignment value).

    Returns:
        Tuple of (should_close, reason)
    """
    if not getattr(config, "ASSIGNMENT_MARGIN_BUFFER_ENABLED", True):
        return False, ""

    buffer_pct = getattr(config, "ASSIGNMENT_MARGIN_BUFFER_PCT", 0.20)
    num_contracts = spread.num_spreads

    # V6.7 FIX: Calculate actual max loss for the SPREAD, not naked short
    # In a vertical spread, the long leg covers the short leg on assignment
    # - If short call assigned: Exercise long call to deliver shares
    # - If short put assigned: Exercise long put to sell shares
    # Therefore, max loss is limited to the spread's defined risk
    if spread.spread_type in ["BULL_CALL", "BEAR_PUT"]:
        # Debit spreads: max loss = net debit paid (what we paid to open)
        actual_max_loss = spread.net_debit * 100 * num_contracts
    else:
        # Credit spreads: max loss = width - credit received
        credit_received = abs(spread.net_debit)  # Stored as negative for credits
        actual_max_loss = (spread.width - credit_received) * 100 * num_contracts

    # Required margin buffer based on actual max loss
    required_buffer = actual_max_loss * buffer_pct

    if available_margin < required_buffer:
        reason = (
            f"MARGIN_BUFFER_INSUFFICIENT: Spread max loss=${actual_max_loss:,.0f} | "
            f"Required buffer=${required_buffer:,.0f} ({buffer_pct:.0%}) | "
            f"Available margin=${available_margin:,.0f}"
        )
        return True, reason

    return False, ""


def check_short_leg_itm_exit_impl(
    self,
    short_leg: "OptionContract",
    underlying_price: float,
) -> Tuple[bool, str]:
    """
    V6.9 P0 Fix 5: Check if short leg is ITM beyond threshold (any DTE).

    Unlike DEEP_ITM_EXIT which requires DTE <= 3, this guard triggers
    at ANY DTE when the short leg goes ITM by the threshold percentage.
    This catches early assignment risk that the Aug 2022 backtest exposed
    (assignments at DTE=4 were missed by DTE<=3 guards).

    Args:
        short_leg: The short leg of the spread
        underlying_price: Current price of underlying (QQQ)

    Returns:
        Tuple of (should_exit, reason)
    """
    if not getattr(config, "SHORT_LEG_ITM_EXIT_ENABLED", True):
        return False, ""

    itm_threshold = getattr(config, "SHORT_LEG_ITM_EXIT_THRESHOLD", 0.02)

    # Determine if call or put
    is_put = "P" in str(short_leg.symbol).upper()
    strike = short_leg.strike

    if is_put:
        # Put is ITM when strike > underlying
        if strike <= underlying_price:
            return False, ""  # Not ITM
        itm_amount = strike - underlying_price
        itm_pct = itm_amount / max(underlying_price, 1e-9)
    else:
        # Call is ITM when strike < underlying
        if strike >= underlying_price:
            return False, ""  # Not ITM
        itm_amount = underlying_price - strike
        itm_pct = itm_amount / max(underlying_price, 1e-9)

    if itm_pct >= itm_threshold:
        # Throttle diagnostic logging to avoid repeated spam in fast loops.
        interval_min = int(getattr(config, "SHORT_LEG_ITM_EXIT_LOG_INTERVAL", 15))
        now_dt = self.algorithm.Time if self.algorithm is not None else None
        if now_dt is not None and interval_min > 0:
            sym_key = str(short_leg.symbol)
            last_dt = self._last_short_leg_itm_exit_log.get(sym_key)
            if last_dt is None or (now_dt - last_dt).total_seconds() >= interval_min * 60:
                self.log(
                    f"SHORT_LEG_ITM_EXIT_TRIGGER: {sym_key} | ITM={itm_pct:.1%} >= {itm_threshold:.1%} | "
                    f"Underlying={underlying_price:.2f} Strike={strike:.2f}",
                    trades_only=True,
                )
                self._last_short_leg_itm_exit_log[sym_key] = now_dt
        reason = (
            f"SHORT_LEG_ITM_EXIT: Short {'PUT' if is_put else 'CALL'} "
            f"Strike={strike} is {itm_pct:.1%} ITM (threshold={itm_threshold:.1%}) | "
            f"Underlying={underlying_price:.2f} | ITM$={itm_amount:.2f} | "
            f"Closing to prevent assignment"
        )
        return True, reason

    return False, ""


def check_premarket_itm_shorts_impl(
    self,
    underlying_price: float,
    spread_override: Optional[SpreadPosition] = None,
) -> Optional[List[TargetWeight]]:
    """
    V6.10 P0: Pre-market ITM check at 09:25 ET.

    Check all short legs BEFORE market open to catch overnight gaps.
    If a short leg went ITM overnight, queue for immediate close at 09:30.

    This is called from main.py at 09:25 ET via scheduled event.

    Args:
        underlying_price: Current/pre-market price of underlying (QQQ)

    Returns:
        List of TargetWeights to close spread at market open, or None
    """
    if not getattr(config, "PREMARKET_ITM_CHECK_ENABLED", True):
        return None

    spread = spread_override or self.get_spread_position()
    if spread is None:
        return None

    # Skip if already closing
    if spread.is_closing:
        return None

    short_leg = spread.short_leg

    # Check if short leg is ITM
    # Use a tighter threshold for pre-market (any ITM = close)
    is_put = "P" in str(short_leg.symbol).upper()
    strike = short_leg.strike

    is_itm = False
    itm_pct = 0.0

    if is_put:
        # Put is ITM when strike > underlying
        if strike > underlying_price:
            is_itm = True
            itm_pct = (strike - underlying_price) / strike
    else:
        # Call is ITM when strike < underlying
        if strike < underlying_price:
            is_itm = True
            itm_pct = (underlying_price - strike) / strike

    if not is_itm:
        self.log(
            f"PREMARKET_ITM_CHECK: Short {'PUT' if is_put else 'CALL'} "
            f"Strike={strike} is OTM | Underlying={underlying_price:.2f} | No action needed",
            trades_only=False,
        )
        return None

    # Short leg is ITM - queue for immediate close
    exit_reason = (
        f"PREMARKET_ITM_CLOSE: Short {'PUT' if is_put else 'CALL'} "
        f"Strike={strike} is {itm_pct:.1%} ITM at pre-market | "
        f"Underlying={underlying_price:.2f} | "
        f"Closing at market open to prevent assignment"
    )

    self.log(
        f"PREMARKET_ITM_CHECK: {exit_reason}",
        trades_only=True,
    )

    # Mark as closing
    spread.is_closing = True

    # Return exit signal for market open
    num_contracts = spread.num_spreads
    is_credit_spread = spread.spread_type in (
        "BULL_PUT_CREDIT",
        "BEAR_CALL_CREDIT",
        SpreadStrategy.BULL_PUT_CREDIT.value,
        SpreadStrategy.BEAR_CALL_CREDIT.value,
    )
    credit_received = abs(spread.net_debit) if is_credit_spread else 0.0
    return [
        TargetWeight(
            symbol=self._symbol_str(spread.long_leg.symbol),
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=f"PREMARKET_ITM: {exit_reason}",
            requested_quantity=num_contracts,
            metadata={
                "spread_type": spread.spread_type,
                "spread_close_short": True,
                "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                "spread_short_leg_quantity": num_contracts,
                "spread_key": self._build_spread_key(spread),
                "spread_width": spread.width,
                "is_credit_spread": is_credit_spread,
                "spread_credit_received": credit_received,
                "exit_type": "PREMARKET_ITM",
                "spread_exit_code": "PREMARKET_ITM_CLOSE",
                "spread_exit_reason": exit_reason,
                "spread_exit_emergency": True,
            },
        )
    ]


def check_assignment_risk_exit_impl(
    self,
    underlying_price: float,
    current_dte: int,
    current_hour: int,
    current_minute: int,
    available_margin: float = 0,
    spread_override: Optional[SpreadPosition] = None,
) -> Optional[List[TargetWeight]]:
    """
    V5.3 P0: Check if spread should be closed due to assignment risk.

    This is a PRIORITY check that runs BEFORE normal exit conditions.
    Assignment risk takes precedence over profit/loss targets.

    Args:
        underlying_price: Current price of underlying (QQQ)
        current_dte: Days to expiration
        current_hour: Current hour (ET)
        current_minute: Current minute
        available_margin: Available margin for assignment buffer check

    Returns:
        List of TargetWeights to close spread, or None
    """
    spread = spread_override or self.get_spread_position()
    if spread is None:
        return None

    # Skip if already closing
    if spread.is_closing:
        return None

    short_leg = spread.short_leg
    exit_reason = None

    # Grace period to avoid immediate churn exits right after spread entry.
    # Mandatory DTE close remains active.
    assignment_grace_minutes = getattr(config, "SPREAD_ASSIGNMENT_GRACE_MINUTES", 45)
    in_assignment_grace = False
    if assignment_grace_minutes > 0 and getattr(self, "algorithm", None) is not None:
        try:
            from datetime import datetime

            entry_dt = datetime.strptime(spread.entry_time[:19], "%Y-%m-%d %H:%M:%S")
            now_dt = self.algorithm.Time
            minutes_live = (now_dt - entry_dt).total_seconds() / 60.0
            in_assignment_grace = 0 <= minutes_live < assignment_grace_minutes
        except Exception:
            in_assignment_grace = False

    # V6.10 P0: MANDATORY DTE FORCE CLOSE (Nuclear Option)
    # Close ALL spreads at DTE=1 regardless of P&L - last line of defense
    force_close_enabled = getattr(config, "SPREAD_FORCE_CLOSE_ENABLED", True)
    force_close_dte = getattr(config, "SPREAD_FORCE_CLOSE_DTE", 1)
    if force_close_enabled and current_dte <= force_close_dte:
        exit_reason = (
            f"MANDATORY_DTE_CLOSE: DTE={current_dte} <= {force_close_dte} | "
            f"Closing ALL spreads to prevent assignment risk"
        )

    # V6.9 P0 Fix 5: Short leg ITM exit (any DTE) - CHECK FIRST
    # This catches assignments at any DTE, not just near expiry
    if not in_assignment_grace:
        should_exit, itm_reason = self._check_short_leg_itm_exit(
            short_leg=short_leg,
            underlying_price=underlying_price,
        )
        if should_exit:
            exit_reason = itm_reason

    # P0 Fix 1: Deep ITM short leg (DTE <= 3)
    if exit_reason is None and not in_assignment_grace:
        is_deep_itm, deep_itm_reason = self._is_short_leg_deep_itm(
            short_leg=short_leg,
            underlying_price=underlying_price,
            current_dte=current_dte,
        )
        if is_deep_itm:
            exit_reason = deep_itm_reason

    # P0 Fix 2: Overnight ITM short block
    if exit_reason is None and not in_assignment_grace:
        should_close, overnight_reason = self._check_overnight_itm_short_risk(
            short_leg=short_leg,
            underlying_price=underlying_price,
            current_dte=current_dte,
            current_hour=current_hour,
            current_minute=current_minute,
        )
        if should_close:
            exit_reason = overnight_reason

    # P0 Fix 3: Margin buffer insufficient
    if exit_reason is None and available_margin > 0 and not in_assignment_grace:
        should_close, margin_reason = self._check_assignment_margin_buffer(
            spread=spread,
            underlying_price=underlying_price,
            available_margin=available_margin,
        )
        if should_close:
            exit_reason = margin_reason

    if exit_reason is None:
        return None

    self.log(
        f"ASSIGNMENT_RISK_EXIT: {exit_reason}",
        trades_only=True,
    )
    exit_code = str(exit_reason).split(":", 1)[0].split(" ", 1)[0]

    # Mark as closing to prevent duplicate signals
    spread.is_closing = True

    # Return exit signal (same structure as normal spread exit)
    # V6.5 FIX: Added spread_close_short and requested_quantity for proper combo close
    num_contracts = spread.num_spreads
    is_credit_spread = spread.spread_type in (
        "BULL_PUT_CREDIT",
        "BEAR_CALL_CREDIT",
        SpreadStrategy.BULL_PUT_CREDIT.value,
        SpreadStrategy.BEAR_CALL_CREDIT.value,
    )
    credit_received = abs(spread.net_debit) if is_credit_spread else 0.0
    return [
        TargetWeight(
            symbol=self._symbol_str(spread.long_leg.symbol),
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=f"ASSIGNMENT_RISK: {exit_reason}",
            requested_quantity=num_contracts,
            metadata={
                "spread_type": spread.spread_type,
                "spread_close_short": True,  # V6.5 FIX: Required for combo close
                "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                "spread_short_leg_quantity": num_contracts,
                "spread_key": self._build_spread_key(spread),  # V6.5 FIX: Positive for close
                "spread_width": spread.width,
                "is_credit_spread": is_credit_spread,
                "spread_credit_received": credit_received,
                "exit_type": "ASSIGNMENT_RISK",
                "spread_exit_code": exit_code,
                "spread_exit_reason": str(exit_reason),
                "spread_exit_emergency": True,
            },
        )
    ]


def get_assignment_aware_size_multiplier_impl(
    self,
    spread_width: float,
    portfolio_value: float,
    requested_contracts: int,
) -> float:
    """
    V6.0 Fix: Assignment-aware position sizing using spread width.

    For vertical spreads, max loss is limited to spread width, NOT underlying price.
    Previous bug used underlying price (~$358) instead of spread width (~$6),
    causing 20 contracts to be reduced to 1.

    Args:
        spread_width: Width between strikes (e.g., $6 for $358/$352 spread)
        portfolio_value: Total portfolio value
        requested_contracts: Requested number of contracts

    Returns:
        Size multiplier (0.0 to 1.0)
    """
    if not getattr(config, "ASSIGNMENT_AWARE_SIZING_ENABLED", True):
        return 1.0

    # Skip assignment-aware sizing in test mode (algorithm is None)
    if self.algorithm is None:
        return 1.0

    max_exposure_pct = getattr(config, "ASSIGNMENT_SIZING_MAX_EXPOSURE_PCT", 0.50)

    # Max exposure we allow = portfolio_value * max_exposure_pct
    max_exposure = portfolio_value * max_exposure_pct

    # V6.0 FIX: Max loss on spread = spread_width * 100 * contracts
    # NOT underlying_price * 100 * contracts (that's for naked options)
    potential_exposure = spread_width * 100 * requested_contracts

    if potential_exposure <= max_exposure:
        return 1.0

    # Calculate how many contracts we can safely hold
    safe_contracts = int(max_exposure / (spread_width * 100))
    if safe_contracts <= 0:
        return 0.0

    multiplier = safe_contracts / requested_contracts
    self.log(
        f"ASSIGNMENT_SIZING: Reduced {requested_contracts} -> {safe_contracts} contracts | "
        f"Max exposure={max_exposure_pct:.0%} of ${portfolio_value:,.0f} = ${max_exposure:,.0f} | "
        f"Potential=${potential_exposure:,.0f} (width=${spread_width:.2f})",
        trades_only=True,
    )

    return multiplier


def handle_partial_assignment_impl(
    self,
    assigned_symbol: str,
    assigned_quantity: int,
) -> Optional[List[TargetWeight]]:
    """
    V5.3 P0 Fix 4: Handle partial assignment of spread.

    When one leg of a spread is assigned, we need to close the remaining leg
    to avoid naked exposure.

    Args:
        assigned_symbol: Symbol that was assigned
        assigned_quantity: Quantity that was assigned

    Returns:
        List of TargetWeights to close orphaned legs, or None
    """
    if not getattr(config, "PARTIAL_ASSIGNMENT_DETECTION_ENABLED", True):
        return None

    spread = None
    for s in self.get_spread_positions():
        if assigned_symbol == s.short_leg.symbol or assigned_symbol == s.long_leg.symbol:
            spread = s
            break
    if spread is None:
        return None
    auto_close = getattr(config, "PARTIAL_ASSIGNMENT_AUTO_CLOSE", True)

    # Check if assigned symbol matches our short leg
    is_short_assigned = assigned_symbol == spread.short_leg.symbol
    is_long_assigned = assigned_symbol == spread.long_leg.symbol

    if not (is_short_assigned or is_long_assigned):
        return None

    self.log(
        f"PARTIAL_ASSIGNMENT_DETECTED: {assigned_symbol} x{assigned_quantity} | "
        f"Spread: {spread.spread_type} | "
        f"{'Short' if is_short_assigned else 'Long'} leg assigned",
        trades_only=True,
    )

    if not auto_close:
        self.log(
            "PARTIAL_ASSIGNMENT: Auto-close disabled, manual intervention required",
            trades_only=True,
        )
        return None

    # Close the remaining leg
    remaining_leg = spread.long_leg if is_short_assigned else spread.short_leg
    remaining_qty = spread.num_spreads

    spread.is_closing = True

    return [
        TargetWeight(
            symbol=self._symbol_str(remaining_leg.symbol),
            target_weight=0.0,
            source="OPT",
            urgency=Urgency.IMMEDIATE,
            reason=f"PARTIAL_ASSIGNMENT: Closing orphaned {'long' if is_short_assigned else 'short'} leg",
            requested_quantity=max(1, int(remaining_qty)),
            metadata={
                "exit_type": "PARTIAL_ASSIGNMENT",
                "assigned_leg": assigned_symbol,
            },
        )
    ]
