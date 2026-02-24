"""Options entry signal evaluator extracted from options_engine."""

from __future__ import annotations

import config
from models.enums import IntradayStrategy, OptionsMode, Urgency
from models.target_weight import TargetWeight


def check_entry_signal_impl(
    self,
    adx_value: float,
    current_price: float,
    ma200_value: float,
    iv_rank: float,
    best_contract: Optional[OptionContract],
    current_hour: int,
    current_minute: int,
    current_date: str,
    portfolio_value: float,
    regime_score: float = 50.0,
    gap_filter_triggered: bool = False,
    vol_shock_active: bool = False,
    time_guard_active: bool = False,
    size_multiplier: float = 1.0,
    direction: Optional[OptionDirection] = None,
) -> Optional[TargetWeight]:
    """
    Check for options entry signal (single-leg fallback path).

    V2.3.20: Added size_multiplier for cold start reduced sizing.
    V6.0: Direction now passed from conviction resolution.

    Args:
        adx_value: Current ADX(14) value.
        current_price: Current QQQ price.
        ma200_value: 200-day moving average value.
        ma50_value: 50-day moving average value (used for bearish trend gate on bullish debit).
        iv_rank: IV percentile (0-100).
        best_contract: Best available option contract.
        current_hour: Current hour (0-23) Eastern.
        current_minute: Current minute (0-59).
        current_date: Current date string.
        portfolio_value: Total portfolio value.
        regime_score: Market regime score (0-100). Must be >= 40.
        gap_filter_triggered: True if gap filter is active.
        vol_shock_active: True if vol shock pause is active.
        time_guard_active: True if time guard is active.
        size_multiplier: Position size multiplier (default 1.0). V2.3.20: Set to 0.5
            during cold start to reduce risk.
        direction: V6.0: Direction from conviction resolution (CALL or PUT).

    Returns:
        TargetWeight for entry, or None if no signal.
    """
    # Check if already have a position
    if self._position is not None:
        return None

    # V2.3 FIX: Check if entry already attempted today (prevents order spam)
    if self._entry_attempted_today:
        return None

    # V2.9: Check trade limits (Bug #4 fix) - Uses comprehensive counter
    if not self._can_trade_options(OptionsMode.SWING):
        return None

    # Check safeguards
    if gap_filter_triggered:
        self.log("OPT: Entry blocked - gap filter active")
        return None

    if vol_shock_active:
        self.log("OPT: Entry blocked - vol shock active")
        return None

    if time_guard_active:
        self.log("OPT: Entry blocked - time guard active")
        return None

    # Check if we have a valid contract
    if best_contract is None:
        return None

    # V6.0: Direction now passed from conviction resolution
    # Caller (main.py) has already resolved VASS conviction vs macro direction
    if direction is None:
        self.log("OPT: Entry blocked - direction not provided (conviction resolution required)")
        return None

    # Validate contract direction matches resolved direction
    if best_contract.direction != direction:
        self.log(
            f"OPT: Entry blocked - contract direction {best_contract.direction.value} "
            f"doesn't match resolved direction {direction.value}"
        )
        return None

    # GAP #3 FIX: Minimum premium validation ($0.50 per spec)
    if best_contract.mid_price < config.OPTIONS_MIN_PREMIUM:
        self.log(
            f"OPT: Entry blocked - premium ${best_contract.mid_price:.2f} < "
            f"min ${config.OPTIONS_MIN_PREMIUM:.2f}"
        )
        return None

    # Validate DTE range (1-4 days per spec)
    if best_contract.days_to_expiry < config.OPTIONS_DTE_MIN:
        self.log(
            f"OPT: Entry blocked - DTE {best_contract.days_to_expiry} < "
            f"min {config.OPTIONS_DTE_MIN}"
        )
        return None

    if best_contract.days_to_expiry > config.OPTIONS_DTE_MAX:
        self.log(
            f"OPT: Entry blocked - DTE {best_contract.days_to_expiry} > "
            f"max {config.OPTIONS_DTE_MAX}"
        )
        return None

    # V2.3.16: DTE-based delta validation
    # Swing mode (DTE > 5): Allows higher delta (0.70 target) for directional trades
    # Intraday mode (DTE <= 5): Narrower ATM range (0.40-0.60) for quick scalps
    contract_delta = abs(best_contract.delta)  # Use absolute value
    dte = best_contract.days_to_expiry
    is_swing_dte = dte > config.OPTIONS_SWING_DTE_THRESHOLD

    if is_swing_dte:
        # Swing mode: Use wider delta bounds (0.55-0.85)
        delta_min = config.OPTIONS_SWING_DELTA_MIN
        delta_max = config.OPTIONS_SWING_DELTA_MAX
        mode_label = "Swing"
    else:
        # V2.15: Strategy-aware delta bounds for intraday
        # Use defensive coding in case _state is not initialized (tests)
        state = getattr(self, "_state", None)
        current_strategy = getattr(state, "recommended_strategy", None) if state else None
        current_strategy = self._canonical_intraday_strategy(current_strategy)

        if current_strategy == IntradayStrategy.ITM_MOMENTUM:
            # ITM_ENGINE canonical delta source: use ITM_* when enabled.
            if bool(getattr(config, "ITM_ENGINE_ENABLED", False)):
                delta_min = float(getattr(config, "ITM_DELTA_MIN", 0.70))
                delta_max = float(getattr(config, "ITM_DELTA_MAX", 0.80))
            else:
                delta_min = float(getattr(config, "INTRADAY_ITM_DELTA_MIN", 0.70))
                delta_max = float(getattr(config, "INTRADAY_ITM_DELTA_MAX", 0.80))
            mode_label = "Intraday-ITM"
        elif current_strategy in (
            IntradayStrategy.MICRO_DEBIT_FADE,
            IntradayStrategy.MICRO_OTM_MOMENTUM,
            IntradayStrategy.DEBIT_FADE,
        ):
            # MICRO fade/momentum tracks use dedicated delta bands.
            if current_strategy == IntradayStrategy.MICRO_DEBIT_FADE:
                delta_min = float(
                    getattr(
                        config,
                        "MICRO_DEBIT_FADE_DELTA_MIN",
                        getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MIN", 0.20),
                    )
                )
                delta_max = float(
                    getattr(
                        config,
                        "MICRO_DEBIT_FADE_DELTA_MAX",
                        getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MAX", 0.50),
                    )
                )
                mode_label = "Intraday-MICRO_FADE"
            else:
                delta_min = float(
                    getattr(
                        config,
                        "MICRO_OTM_MOMENTUM_DELTA_MIN",
                        getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MIN", 0.20),
                    )
                )
                delta_max = float(
                    getattr(
                        config,
                        "MICRO_OTM_MOMENTUM_DELTA_MAX",
                        getattr(config, "INTRADAY_DEBIT_FADE_DELTA_MAX", 0.50),
                    )
                )
                mode_label = "Intraday-MICRO_OTM"
        else:
            # Default for other strategies (CREDIT_SPREAD, etc.)
            delta_min = config.OPTIONS_INTRADAY_DELTA_MIN
            delta_max = config.OPTIONS_INTRADAY_DELTA_MAX
            mode_label = "Intraday"

    if contract_delta < delta_min:
        self.log(
            f"OPT: Entry blocked - Delta {contract_delta:.2f} < "
            f"min {delta_min} ({mode_label} mode, DTE={dte})"
        )
        return None

    if contract_delta > delta_max:
        self.log(
            f"OPT: Entry blocked - Delta {contract_delta:.2f} > "
            f"max {delta_max} ({mode_label} mode, DTE={dte})"
        )
        return None

    # Calculate entry score
    entry_score = self.calculate_entry_score(
        adx_value=adx_value,
        current_price=current_price,
        ma200_value=ma200_value,
        iv_rank=iv_rank,
        bid_ask_spread_pct=best_contract.spread_pct,
        open_interest=best_contract.open_interest,
    )

    # Check minimum score
    if not entry_score.is_valid:
        return None

    # Late day constraint: only 20% stops after 2:30 PM
    is_late_day = current_hour > config.OPTIONS_LATE_DAY_HOUR or (
        current_hour == config.OPTIONS_LATE_DAY_HOUR
        and current_minute >= config.OPTIONS_LATE_DAY_MINUTE
    )

    if is_late_day:
        tier = self.get_stop_tier(entry_score.total)
        if tier["stop_pct"] > config.OPTIONS_LATE_DAY_MAX_STOP:
            self.log(
                f"OPT: Entry blocked - late day (after 14:30), "
                f"stop {tier['stop_pct']:.0%} > max {config.OPTIONS_LATE_DAY_MAX_STOP:.0%}"
            )
            return None

    # Calculate position size
    # V2.3.8: Pass DTE for 0DTE-specific tighter stops
    premium = best_contract.mid_price
    num_contracts, stop_pct, stop_price, target_price = self.calculate_position_size(
        entry_score=entry_score.total,
        premium=premium,
        portfolio_value=portfolio_value,
        days_to_expiry=best_contract.days_to_expiry,
    )

    # V6.0: Apply cold start multiplier (macro gate removed - conviction handles direction)
    if size_multiplier < 1.0:
        min_size = getattr(config, "OPTIONS_MIN_COMBINED_SIZE_PCT", 0.10)
        if size_multiplier < min_size:
            self.log(
                f"OPT: Entry blocked - cold start size {size_multiplier:.0%} < min {min_size:.0%}"
            )
            return None

        num_contracts = max(1, int(num_contracts * size_multiplier))
        self.log(
            f"OPT: Sizing reduced to {num_contracts} contracts (SizeMult={size_multiplier:.0%})"
        )

    # V6.19: Apply choppy-market scaling to single-leg entries too.
    choppy_scale = self.get_choppy_market_scale()
    if choppy_scale < 1.0 and num_contracts > 1:
        choppy_adjusted = max(1, int(num_contracts * choppy_scale))
        self.log(
            f"OPT: Choppy market reduction | {num_contracts} -> {choppy_adjusted} contracts | "
            f"ChoppyScale={choppy_scale:.0%}"
        )
        num_contracts = choppy_adjusted

    if num_contracts <= 0:
        self.log("OPT: Entry blocked - cannot calculate position size")
        return None

    # Store pending entry details for register_entry
    self._pending_contract = best_contract
    self._pending_entry_score = entry_score.total
    self._pending_num_contracts = num_contracts
    self._pending_stop_pct = stop_pct
    self._pending_stop_price = stop_price
    self._pending_target_price = target_price
    self._pending_entry_strategy = "SWING_SINGLE"

    # V2.3 FIX: Mark that we attempted entry today (prevents retry spam)
    self._entry_attempted_today = True

    reason = (
        f"OPT Entry: Score={entry_score.total:.2f} "
        f"({entry_score.score_adx:.2f}+{entry_score.score_momentum:.2f}+"
        f"{entry_score.score_iv:.2f}+{entry_score.score_liquidity:.2f}), "
        f"{best_contract.direction.value} {best_contract.strike}, "
        f"x{num_contracts}, Stop={stop_pct:.0%}"
    )

    self.log(
        f"OPT: ENTRY_SIGNAL | {reason} | "
        f"Δ={best_contract.delta:.2f} DTE={best_contract.days_to_expiry} | "
        f"Premium=${premium:.2f} | Target=${target_price:.2f} | Stop=${stop_price:.2f}",
        trades_only=True,
    )

    # V2.4.1 FIX: Use actual allocation value, not 1.0
    # Was returning 1.0 instead of actual allocation (0.1875)
    return TargetWeight(
        symbol=self._symbol_str(best_contract.symbol),
        target_weight=config.OPTIONS_SWING_ALLOCATION,  # V2.4.1: Actual allocation
        source="OPT",
        urgency=Urgency.IMMEDIATE,
        reason=reason,
        requested_quantity=num_contracts,  # V2.3.2: Pass risk-calculated contracts
        metadata={"contract_price": best_contract.mid_price},  # V2.19: For router price lookup
    )


# =========================================================================
# V2.3 DEBIT SPREAD ENTRY SIGNAL
# =========================================================================
