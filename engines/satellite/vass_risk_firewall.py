"""VASS risk firewall helpers extracted from options_engine."""

from __future__ import annotations

import config
from models.enums import Urgency
from models.target_weight import TargetWeight


def _is_credit_theta_first_active_for_spread(self, spread: Optional["SpreadPosition"]) -> bool:
    """Return True when credit THETA_FIRST mode is active for the given spread."""
    if spread is None:
        return False
    if not bool(getattr(config, "VASS_CREDIT_THETA_FIRST_ENABLED", False)):
        return False
    spread_type = str(getattr(spread, "spread_type", "") or "").upper()
    is_credit = spread_type in {"BULL_PUT_CREDIT", "BEAR_CALL_CREDIT"}
    if not is_credit:
        return False
    if not bool(getattr(config, "VASS_CREDIT_THETA_FIRST_REQUIRE_REGIME_CONFIRMED", True)):
        return True

    regime_score = None
    try:
        transition_ctx = self._get_regime_transition_context() or {}
        raw_score = transition_ctx.get(
            "effective_score",
            transition_ctx.get("intraday_score", transition_ctx.get("eod_score")),
        )
        if raw_score is not None:
            regime_score = float(raw_score)
    except Exception:
        regime_score = None
    if regime_score is None:
        return False

    bull_confirm_min = float(getattr(config, "VASS_REGIME_CONFIRMED_BULL_MIN", 57.0))
    bear_confirm_max = float(getattr(config, "VASS_REGIME_CONFIRMED_BEAR_MAX", 43.0))
    if spread_type == "BULL_PUT_CREDIT":
        return regime_score >= bull_confirm_min
    if spread_type == "BEAR_CALL_CREDIT":
        return regime_score <= bear_confirm_max
    return False


def _is_bearish_spread_fresh_ogp_exempt(self, spread: Optional["SpreadPosition"]) -> bool:
    """Return True when a bearish spread is in a bearish regime for fresh-trade OGP."""
    if spread is None:
        return False

    spread_type = str(getattr(spread, "spread_type", "") or "").upper()
    if spread_type not in {"BEAR_CALL_CREDIT", "BEAR_PUT_DEBIT"}:
        return False

    regime_score = None
    try:
        transition_ctx = self._get_regime_transition_context() or {}
        raw_score = transition_ctx.get(
            "effective_score",
            transition_ctx.get("intraday_score", transition_ctx.get("eod_score")),
        )
        if raw_score is not None:
            regime_score = float(raw_score)
    except Exception:
        regime_score = None
    if regime_score is None:
        return False

    bear_regime_max = float(
        getattr(config, "SPREAD_REGIME_BEARISH", getattr(config, "REGIME_NEUTRAL", 50.0))
    )
    return regime_score < bear_regime_max


def _resolve_spread_live_dte(spread: Optional["SpreadPosition"]) -> int:
    """Best-effort spread DTE from leg metadata."""
    if spread is None:
        return 0
    try:
        dte = int(getattr(spread.long_leg, "days_to_expiry", 0) or 0)
        if dte > 0:
            return dte
    except Exception:
        pass
    try:
        dte = int(getattr(spread.short_leg, "days_to_expiry", 0) or 0)
        if dte > 0:
            return dte
    except Exception:
        pass
    return 0


def check_friday_firewall_exit_impl(
    self,
    current_vix: float,
    current_date: str,
    vix_close_all_threshold: float = 25.0,
    vix_keep_fresh_threshold: float = 15.0,
    spread_override: Optional[SpreadPosition] = None,
) -> Optional[List[TargetWeight]]:
    """
    V2.4.1: Friday Firewall - close swing options before weekend.

    Safety-first approach to weekend risk management:
    1. VIX > 25: Close ALL swing options (high volatility = high gap risk)
    2. Fresh trade (opened today) AND VIX >= 15: Close it (gambling protection)
    3. Fresh trade AND VIX < 15: Keep it (calm market exception)
    4. Older trades AND VIX <= 25: Keep them (already survived initial risk)

    Args:
        current_vix: Current VIX level.
        current_date: Current date string (YYYY-MM-DD format).
        vix_close_all_threshold: VIX level above which ALL positions close (default 25).
        vix_keep_fresh_threshold: VIX level below which fresh trades can stay (default 15).

    Returns:
        List of TargetWeights for positions to close, or None if no action needed.
    """
    exit_signals = []

    # Check spread position
    spread = spread_override or self.get_spread_position()
    if spread is not None:
        entry_date = (
            spread.entry_time.split()[0] if " " in spread.entry_time else spread.entry_time[:10]
        )
        is_fresh_trade = entry_date == current_date
        spread_type = str(getattr(spread, "spread_type", "") or "").upper()
        is_credit_spread = spread_type in {"BULL_PUT_CREDIT", "BEAR_CALL_CREDIT"}
        is_debit_spread = spread_type in {
            "BULL_CALL",
            "BULL_CALL_DEBIT",
            "BEAR_PUT",
            "BEAR_PUT_DEBIT",
        }
        allow_debit = bool(getattr(config, "FRIDAY_FIREWALL_APPLY_TO_VASS_DEBIT", False))
        allow_credit = bool(getattr(config, "FRIDAY_FIREWALL_APPLY_TO_VASS_CREDIT", True))

        skip_by_policy = (is_debit_spread and not allow_debit) or (
            is_credit_spread and not allow_credit
        )
        if not skip_by_policy and is_credit_spread:
            theta_mode_active = _is_credit_theta_first_active_for_spread(self, spread)
            suppress_dte_gt = int(
                getattr(config, "VASS_CREDIT_THETA_FIRST_SUPPRESS_FRIDAY_FIREWALL_DTE_GT", 21) or 0
            )
            current_dte = _resolve_spread_live_dte(spread)
            if theta_mode_active and suppress_dte_gt > 0 and current_dte > suppress_dte_gt:
                if self.algorithm is not None and hasattr(
                    self.algorithm, "_diag_vass_friday_firewall_skipped_dte_count"
                ):
                    self.algorithm._diag_vass_friday_firewall_skipped_dte_count = (
                        int(
                            getattr(
                                self.algorithm,
                                "_diag_vass_friday_firewall_skipped_dte_count",
                                0,
                            )
                            or 0
                        )
                        + 1
                    )
                self.log(
                    "FRIDAY_FIREWALL_SKIPPED_DTE: "
                    f"Type={spread_type} | DTE={current_dte} > {suppress_dte_gt} | "
                    f"Entry={entry_date} Fresh={is_fresh_trade} | VIX={current_vix:.1f}",
                    trades_only=True,
                )
                skip_by_policy = True
        if skip_by_policy:
            self.log(
                f"FRIDAY_FIREWALL: Skipping spread by policy | Type={spread_type} | "
                f"Entry={entry_date} Fresh={is_fresh_trade} | VIX={current_vix:.1f}",
                trades_only=True,
            )
            spread = None

        if spread is not None:
            should_close = False
            close_reason = ""

            # Rule 1: VIX > threshold - close ALL
            if current_vix > vix_close_all_threshold:
                should_close = True
                close_reason = f"VIX_HIGH ({current_vix:.1f} > {vix_close_all_threshold})"

            # Rule 2: Fresh trade + VIX >= 15 - close (gambling protection)
            elif is_fresh_trade and current_vix >= vix_keep_fresh_threshold:
                should_close = True
                close_reason = (
                    f"FRESH_TRADE_PROTECTION (VIX={current_vix:.1f} >= {vix_keep_fresh_threshold})"
                )

            # Rule 3: Fresh trade + VIX < 15 - keep (calm market)
            elif is_fresh_trade and current_vix < vix_keep_fresh_threshold:
                self.log(
                    f"FRIDAY_FIREWALL: Keeping fresh spread (calm market) | "
                    f"VIX={current_vix:.1f} < {vix_keep_fresh_threshold}"
                )

            # Rule 4: Older trade + VIX <= 25 - keep
            else:
                self.log(
                    f"FRIDAY_FIREWALL: Keeping spread (established trade) | "
                    f"Entry={entry_date} | VIX={current_vix:.1f}"
                )

            if should_close:
                self.log(
                    f"FRIDAY_FIREWALL: Closing spread | {close_reason} | "
                    f"Entry={entry_date} Fresh={is_fresh_trade}",
                    trades_only=True,
                )
                # V2.5 FIX: Close both legs via COMBO order (atomic execution)
                exit_signals.append(
                    TargetWeight(
                        symbol=self._symbol_str(spread.long_leg.symbol),
                        target_weight=0.0,
                        source="OPT",
                        urgency=Urgency.IMMEDIATE,
                        reason=f"FRIDAY_FIREWALL: {close_reason}",
                        requested_quantity=spread.num_spreads,
                        metadata={
                            "spread_close_short": True,
                            "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                            "spread_short_leg_quantity": spread.num_spreads,
                            "spread_key": self._build_spread_key(spread),
                            "exit_type": "FRIDAY_FIREWALL",
                            "spread_exit_code": "FRIDAY_FIREWALL",
                            "spread_exit_reason": close_reason,
                        },
                    )
                )

    # Check single-leg position
    if self._position is not None:
        position = self._position
        entry_date = (
            position.entry_time.split()[0]
            if " " in position.entry_time
            else position.entry_time[:10]
        )
        is_fresh_trade = entry_date == current_date

        should_close = False
        close_reason = ""

        # Rule 1: VIX > threshold - close ALL
        if current_vix > vix_close_all_threshold:
            should_close = True
            close_reason = f"VIX_HIGH ({current_vix:.1f} > {vix_close_all_threshold})"

        # Rule 2: Fresh trade + VIX >= 15 - close
        elif is_fresh_trade and current_vix >= vix_keep_fresh_threshold:
            should_close = True
            close_reason = (
                f"FRESH_TRADE_PROTECTION (VIX={current_vix:.1f} >= {vix_keep_fresh_threshold})"
            )

        # Rule 3 & 4: Keep if calm or established
        elif is_fresh_trade and current_vix < vix_keep_fresh_threshold:
            self.log(
                f"FRIDAY_FIREWALL: Keeping fresh single-leg (calm market) | "
                f"VIX={current_vix:.1f} < {vix_keep_fresh_threshold}"
            )
        else:
            self.log(
                f"FRIDAY_FIREWALL: Keeping single-leg (established trade) | "
                f"Entry={entry_date} | VIX={current_vix:.1f}"
            )

        if should_close:
            self.log(
                f"FRIDAY_FIREWALL: Closing single-leg | {close_reason} | "
                f"Entry={entry_date} Fresh={is_fresh_trade}",
                trades_only=True,
            )
            exit_signals.append(
                TargetWeight(
                    symbol=self._symbol_str(position.contract.symbol),
                    target_weight=0.0,
                    source="OPT",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"FRIDAY_FIREWALL: {close_reason}",
                    requested_quantity=max(1, int(getattr(position, "num_contracts", 1))),
                )
            )

    return exit_signals if exit_signals else None


# =========================================================================
# V6.13 P0: OVERNIGHT GAP PROTECTION (All Days)
# =========================================================================


def check_overnight_gap_protection_exit_impl(
    self,
    current_vix: float,
    current_date: str,
) -> Optional[List[TargetWeight]]:
    """
    V6.13 P0: Close swing spreads before overnight risk when VIX is elevated.

    Rules:
    1) If VIX >= SWING_OVERNIGHT_VIX_CLOSE_ALL → close ALL spreads
    2) If trade opened today AND VIX >= SWING_OVERNIGHT_VIX_CLOSE_FRESH → close fresh spread
    """
    if not getattr(config, "SWING_OVERNIGHT_GAP_PROTECTION_ENABLED", True):
        return None

    spreads = self.get_spread_positions()
    if not spreads:
        return None

    close_all_threshold = getattr(config, "SWING_OVERNIGHT_VIX_CLOSE_ALL", 30.0)
    close_fresh_threshold = getattr(config, "SWING_OVERNIGHT_VIX_CLOSE_FRESH", 22.0)

    exit_signals: List[TargetWeight] = []
    for spread in spreads:
        entry_date = (
            spread.entry_time.split()[0] if " " in spread.entry_time else spread.entry_time[:10]
        )
        is_fresh_trade = entry_date == current_date
        spread_type = str(getattr(spread, "spread_type", "") or "")
        try:
            entry_net = float(getattr(spread, "net_debit", 0.0) or 0.0)
        except Exception:
            entry_net = 0.0
        is_credit_spread = bool(entry_net < 0 or "CREDIT" in spread_type.upper())

        reason = None
        fresh_trade_ogp = False
        if current_vix >= close_all_threshold:
            reason = f"OVERNIGHT_GAP_PROTECTION: VIX {current_vix:.1f} >= {close_all_threshold}"
        elif is_fresh_trade and current_vix >= close_fresh_threshold:
            fresh_trade_ogp = True
            reason = f"OVERNIGHT_GAP_PROTECTION: Fresh trade + VIX {current_vix:.1f} >= {close_fresh_threshold}"

        if reason is None:
            continue

        if fresh_trade_ogp and _is_bearish_spread_fresh_ogp_exempt(self, spread):
            self.log(
                "OVERNIGHT_GAP_PROTECTION: Skipping fresh-trade exit for bearish spread in bear regime | "
                f"VIX={current_vix:.1f} | Type={spread_type} | Entry={entry_date} Fresh={is_fresh_trade}",
                trades_only=True,
            )
            continue

        theta_mode_active = _is_credit_theta_first_active_for_spread(self, spread)
        ogp_vix_min = float(getattr(config, "VASS_CREDIT_THETA_FIRST_OGP_VIX_CLOSE_ALL_MIN", 40.0))
        if is_credit_spread and theta_mode_active and current_vix < ogp_vix_min:
            self.log(
                "OVERNIGHT_GAP_PROTECTION: Skipping credit spread in THETA_FIRST mode | "
                f"VIX={current_vix:.1f} < {ogp_vix_min:.1f} | "
                f"Type={spread_type} | Entry={entry_date} Fresh={is_fresh_trade}",
                trades_only=True,
            )
            continue

        self.log(
            f"OVERNIGHT_GAP_PROTECTION: Closing spread | {reason} | "
            f"Entry={entry_date} Fresh={is_fresh_trade}",
            trades_only=True,
        )
        exit_signals.append(
            TargetWeight(
                symbol=self._symbol_str(spread.long_leg.symbol),
                target_weight=0.0,
                source="OPT",
                urgency=Urgency.IMMEDIATE,
                reason=reason,
                requested_quantity=spread.num_spreads,
                metadata={
                    "spread_close_short": True,
                    "spread_short_leg_symbol": self._symbol_str(spread.short_leg.symbol),
                    "spread_short_leg_quantity": spread.num_spreads,
                    "spread_key": self._build_spread_key(spread),
                    "spread_type": spread_type,
                    "is_credit_spread": is_credit_spread,
                    "spread_entry_debit": float(max(0.0, entry_net)),
                    "spread_entry_credit": float(max(0.0, -entry_net)),
                    "options_lane": "VASS",
                    "options_strategy": spread_type or "VASS_UNCLASSIFIED",
                    "exit_type": "OVERNIGHT_GAP_PROTECTION",
                    "spread_exit_code": "OVERNIGHT_GAP_PROTECTION",
                    "spread_exit_reason": reason,
                    "spread_exit_emergency": True,
                },
            )
        )

    return exit_signals if exit_signals else None
