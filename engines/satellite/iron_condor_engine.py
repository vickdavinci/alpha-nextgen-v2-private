"""Iron Condor Engine — VIX-Adaptive Non-Directional Options Strategy.

Sells symmetric OTM credit spreads on both sides of QQQ (put credit + call credit)
to collect premium in neutral/range-bound regimes. Profits from time decay when
QQQ stays between the short strikes.

Architecture:
  - Signal-only engine: emits TargetWeight objects, never places orders.
  - Fully lane-isolated: lane=IC, strategy=IRON_CONDOR, source=OPT_IC.
  - Two-stage entry funnel: IC_ENV_OK → IC_STRUCTURE_OK.
  - Two-phase contract search: build pools → construct complete condors → rank.
  - No partial/single-spread fallback — full condor or no trade.

Managed-exit WR math:
  win  = 0.60 * C  (IC_TARGET_CAPTURE_PCT)
  loss = 1.50 * C  (IC_STOP_LOSS_MULTIPLE)
  WR_be = 1.50 / (1.50 + 0.60) = 71.4%
  Target realized WR >= 74-75% to cover friction.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import config
from engines.satellite.condor_models import IronCondorPosition, RollRecord
from engines.satellite.options_primitives import OptionContract, SpreadFillTracker
from models.enums import OptionDirection, Urgency
from models.target_weight import TargetWeight

# ── Reject reason codes ──
R_IC_DISABLED = "R_IC_DISABLED"
R_IC_REGIME_OUT_OF_RANGE = "R_IC_REGIME_OUT_OF_RANGE"
R_IC_REGIME_NOT_PERSISTENT = "R_IC_REGIME_NOT_PERSISTENT"
R_IC_TRANSITION_BLOCK = "R_IC_TRANSITION_BLOCK"
R_IC_FAST_OVERLAY_STRESS = "R_IC_FAST_OVERLAY_STRESS"
R_IC_VIX_OUT_OF_RANGE = "R_IC_VIX_OUT_OF_RANGE"
R_IC_ADX_TOO_HIGH = "R_IC_ADX_TOO_HIGH"
R_IC_EVENT_DAY_BLOCK = "R_IC_EVENT_DAY_BLOCK"
R_IC_OUTSIDE_ENTRY_WINDOW = "R_IC_OUTSIDE_ENTRY_WINDOW"
R_IC_POSITION_LIMIT = "R_IC_POSITION_LIMIT"
R_IC_DAILY_TRADE_LIMIT = "R_IC_DAILY_TRADE_LIMIT"
R_IC_BUDGET_EXCEEDED = "R_IC_BUDGET_EXCEEDED"
R_IC_DAILY_LOSS_STOP = "R_IC_DAILY_LOSS_STOP"
R_IC_LOSS_BREAKER_ACTIVE = "R_IC_LOSS_BREAKER_ACTIVE"
R_IC_MARGIN_INSUFFICIENT = "R_IC_MARGIN_INSUFFICIENT"
R_IC_NO_CHAIN = "R_IC_NO_CHAIN"
R_IC_NO_PUT_POOL = "R_IC_NO_PUT_POOL"
R_IC_NO_CALL_POOL = "R_IC_NO_CALL_POOL"
R_IC_WING_NOT_FOUND_PUT = "R_IC_WING_NOT_FOUND_PUT"
R_IC_WING_NOT_FOUND_CALL = "R_IC_WING_NOT_FOUND_CALL"
R_IC_NO_COMPLETE_CONDOR = "R_IC_NO_COMPLETE_CONDOR"
R_IC_CHAIN_QUALITY_FAIL = "R_IC_CHAIN_QUALITY_FAIL"
R_IC_CW_BELOW_MIN = "R_IC_CW_BELOW_MIN"
R_IC_STOP_DW_UNFEASIBLE = "R_IC_STOP_DW_UNFEASIBLE"
R_IC_DELTA_ASYMMETRY = "R_IC_DELTA_ASYMMETRY"
R_IC_WING_ASYMMETRY = "R_IC_WING_ASYMMETRY"
R_IC_LIQUIDITY_FAIL = "R_IC_LIQUIDITY_FAIL"
R_IC_SLIPPAGE_FAIL = "R_IC_SLIPPAGE_FAIL"
R_IC_PER_TRADE_RISK_EXCEEDED = "R_IC_PER_TRADE_RISK_EXCEEDED"
R_IC_PENDING_ENTRY = "R_IC_PENDING_ENTRY"
R_IC_STRIKE_REUSE = "R_IC_STRIKE_REUSE"
R_IC_REGIME_VELOCITY_BLOCK = "R_IC_REGIME_VELOCITY_BLOCK"
R_IC_INSIDE_EXPECTED_MOVE = "R_IC_INSIDE_EXPECTED_MOVE"
R_IC_CALL_OTM_TOO_TIGHT = "R_IC_CALL_OTM_TOO_TIGHT"
R_IC_REJECTION_COOLDOWN = "R_IC_REJECTION_COOLDOWN"

# ── Exit reason codes ──
EXIT_IC_VIX_SPIKE = "IC_VIX_SPIKE_EXIT"
EXIT_IC_PROFIT_TARGET = "IC_PROFIT_TARGET"
EXIT_IC_STOP_LOSS = "IC_STOP_LOSS"
EXIT_IC_WING_BREACH_PUT = "IC_WING_BREACH_PUT"
EXIT_IC_WING_BREACH_CALL = "IC_WING_BREACH_CALL"
EXIT_IC_TIME_EXIT = "IC_TIME_EXIT"
EXIT_IC_REGIME_BREAK = "IC_REGIME_BREAK"
EXIT_IC_FRIDAY_CLOSE = "IC_FRIDAY_CLOSE"
EXIT_IC_DAILY_LOSS_STOP = "IC_DAILY_LOSS_STOP"
EXIT_IC_ASSIGNMENT_RISK = "IC_ASSIGNMENT_RISK"
EXIT_IC_HARD_STOP_HOLD = "IC_HARD_STOP_DURING_HOLD"
EXIT_IC_EOD_HOLD_GATE = "IC_EOD_HOLD_RISK_GATE"
EXIT_IC_MFE_LOCK = "IC_MFE_LOCK"
EXIT_IC_UNDERLYING_INVALIDATION = "IC_UNDERLYING_INVALIDATION"
EXIT_IC_ROLL_PUT = "IC_ROLL_PUT"
EXIT_IC_ROLL_CALL = "IC_ROLL_CALL"

# ── Roll rejection codes ──
R_IC_ROLL_MAX_REACHED = "R_IC_ROLL_MAX_REACHED"
R_IC_ROLL_NO_REPLACEMENT = "R_IC_ROLL_NO_REPLACEMENT"
R_IC_ROLL_CREDIT_INSUFFICIENT = "R_IC_ROLL_CREDIT_INSUFFICIENT"


class IronCondorEngine:
    """VIX-Adaptive Iron Condor engine for neutral-regime premium collection."""

    def __init__(
        self,
        log_func: Optional[Callable[[str, bool], None]] = None,
        signal_lifecycle_cb: Optional[Callable[..., None]] = None,
        regime_decision_cb: Optional[Callable[..., None]] = None,
    ):
        self._log_func = log_func
        self._signal_lifecycle_cb = signal_lifecycle_cb
        self._regime_decision_cb = regime_decision_cb

        # ── State ──
        self._positions: List[IronCondorPosition] = []
        self._trades_today: int = 0
        self._daily_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._loss_breaker_pause_until: Optional[str] = None  # date string
        self._pending_entry: bool = False
        self._pending_condor_id: Optional[str] = None
        self._pending_condor: Optional[IronCondorPosition] = None
        self._pending_fills: Dict[str, bool] = {}  # condor side → filled
        self._pending_entry_since: Optional[datetime] = None
        self._regime_neutral_days: int = 0  # Consecutive neutral-regime trading days
        self._regime_neutral_last_date: Optional[str] = None  # Last date counted
        self._regime_score_history: List[Tuple[str, float]] = []  # (date_str, score) for velocity
        self._last_scan_time: Optional[str] = None  # Scan throttle timestamp
        self._hold_guard_logged: set = set()  # Suppress repeat hold guard logs

        # ── Rejection recovery (V12.33) ──
        self._rejection_cooldown_until: Optional[datetime] = None
        self._rejection_streak_count: int = 0
        self._rejection_streak_first_at: Optional[datetime] = None

        # ── Fill tracking (owns 4-leg tracker state) ──
        self._side_fill_trackers: Dict[str, SpreadFillTracker] = {}

        # ── Diagnostics ──
        self._diag_candidates: int = 0
        self._diag_approved: int = 0
        self._diag_dropped: int = 0
        self._diag_drop_codes: Dict[str, int] = {}
        self._diag_exit_reasons: Dict[str, int] = {}
        self._diag_wins: int = 0
        self._diag_losses: int = 0
        self._diag_total_pnl: float = 0.0

    # ── Logging ──

    def _log(self, message: str, trades_only: bool = False) -> None:
        if self._log_func:
            text = str(message or "")
            if text and not text.startswith("IC:"):
                text = f"IC: {text}"
            self._log_func(text, trades_only)

    def _record_drop(
        self,
        code: str,
        *,
        signal_id: str = "",
        trace_id: str = "",
        gate_name: str = "",
        reason: str = "",
    ) -> None:
        self._diag_dropped += 1
        self._diag_drop_codes[code] = self._diag_drop_codes.get(code, 0) + 1
        self._emit_lifecycle(
            "DROPPED",
            signal_id=signal_id,
            trace_id=trace_id,
            code=code,
            gate_name=gate_name or code,
            reason=reason,
        )

    def _emit_lifecycle(
        self,
        event: str,
        *,
        signal_id: str = "",
        trace_id: str = "",
        code: str = "",
        gate_name: str = "",
        reason: str = "",
    ) -> None:
        """Emit signal_lifecycle artifact row via callback (log-budget-immune)."""
        if self._signal_lifecycle_cb is None:
            return
        try:
            self._signal_lifecycle_cb(
                engine="IC",
                event=event,
                signal_id=signal_id,
                trace_id=trace_id,
                direction="NEUTRAL",
                strategy="IRON_CONDOR",
                code=code,
                gate_name=gate_name,
                reason=reason,
            )
        except Exception:
            pass

    def _emit_regime_decision(
        self,
        decision: str,
        gate_name: str,
        threshold_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit regime_decision artifact row via callback (log-budget-immune)."""
        if self._regime_decision_cb is None:
            return
        try:
            self._regime_decision_cb(
                engine="IC",
                decision=decision,
                strategy_attempted="IRON_CONDOR",
                gate_name=gate_name,
                threshold_snapshot=threshold_snapshot,
            )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    # ENTRY LOGIC
    # ══════════════════════════════════════════════════════════════════════

    def run_entry_cycle(
        self,
        *,
        chain,
        qqq_price: float,
        regime_score: float,
        adx_value: float,
        vix_current: float,
        effective_portfolio_value: float,
        transition_ctx: Dict[str, Any],
        current_time: datetime,
        margin_remaining: float,
        ic_open_risk: float,
        daily_pnl: float,
    ) -> Optional[List[TargetWeight]]:
        """Run IC entry cycle. Returns list of TargetWeight signals or None.

        Two-stage funnel:
          Stage 1 — IC_ENV_OK: regime, VIX, ADX, transition, event-day, timing
          Stage 2 — IC_STRUCTURE_OK: contract search, C/W, D/W, delta/wing symmetry
        """
        self._diag_candidates += 1
        self._daily_pnl = daily_pnl
        self._emit_lifecycle("CANDIDATE", gate_name="IC_ENTRY_CYCLE")

        # ── Stage 1: IC_ENV_OK ──
        env_reject = self._check_env_gates(
            regime_score=regime_score,
            adx_value=adx_value,
            vix_current=vix_current,
            transition_ctx=transition_ctx,
            current_time=current_time,
            effective_portfolio_value=effective_portfolio_value,
            margin_remaining=margin_remaining,
            ic_open_risk=ic_open_risk,
        )
        if env_reject:
            self._record_drop(env_reject)
            return None

        # ── Stage 2: IC_STRUCTURE_OK ──
        condor = self._search_and_build_condor(
            chain=chain,
            qqq_price=qqq_price,
            vix_current=vix_current,
            regime_score=regime_score,
            adx_value=adx_value,
            current_time=current_time,
            effective_portfolio_value=effective_portfolio_value,
        )
        if condor is None:
            return None

        # ── Capture transition overlay for order_lifecycle attribution ──
        condor.entry_transition_overlay = str(
            transition_ctx.get("transition_overlay") or transition_ctx.get("transition_state") or ""
        ).upper()

        # ── Build signals ──
        self._diag_approved += 1
        self._pending_entry = True
        self._pending_condor_id = condor.condor_id
        self._pending_condor = condor
        self._pending_fills = {"PUT_CREDIT": False, "CALL_CREDIT": False}
        self._pending_entry_since = current_time
        signals = self._build_entry_signals(condor, current_time)
        self._emit_regime_decision(
            "PASS",
            "IC_STRUCTURE_OK",
            {
                "credit_to_width": round(condor.credit_to_width, 4),
                "num_spreads": int(condor.num_spreads),
                "entry_dte": int(condor.entry_dte),
                "entry_vix": round(float(condor.entry_vix or 0.0), 2),
            },
        )

        _up = condor.entry_underlying_price
        _put_d = (_up - condor.put_short_strike) / _up if _up > 0 else 0
        _call_d = (condor.call_short_strike - _up) / _up if _up > 0 else 0
        _min_d = min(_put_d, _call_d)

        self._emit_lifecycle(
            "APPROVED",
            signal_id=condor.condor_id,
            trace_id=condor.condor_id,
            code="R_OK",
            gate_name="IC_STRUCTURE_OK",
            reason=(
                f"C/W={condor.credit_to_width:.3f} spreads={condor.num_spreads} "
                f"dist={_min_d:.3f}"
            ),
        )
        self._log(
            f"ENTRY_SIGNAL | condor_id={condor.condor_id} "
            f"| put_short={condor.put_short_strike} call_short={condor.call_short_strike} "
            f"| C/W={condor.credit_to_width:.3f} | credit=${condor.net_credit:.2f} "
            f"| max_loss=${condor.max_loss:.2f} | spreads={condor.num_spreads} "
            f"| dist={_min_d:.3f} | put_d={_put_d:.3f} | call_d={_call_d:.3f} "
            f"| VIX={vix_current:.1f} | regime={regime_score:.1f} | ADX={adx_value:.1f}",
            trades_only=True,
        )
        return signals

    def _check_env_gates(
        self,
        *,
        regime_score: float,
        adx_value: float,
        vix_current: float,
        transition_ctx: Dict[str, Any],
        current_time: datetime,
        effective_portfolio_value: float,
        margin_remaining: float,
        ic_open_risk: float,
    ) -> Optional[str]:
        """Check environmental gates. Returns reject code or None if all pass."""
        # Gate: Engine enabled
        if not bool(getattr(config, "IRON_CONDOR_ENGINE_ENABLED", False)):
            return R_IC_DISABLED

        # Gate: Pending entry lock
        if self._pending_entry:
            return R_IC_PENDING_ENTRY

        # Gate: Rejection cooldown (V12.33)
        if (
            self._rejection_cooldown_until is not None
            and current_time < self._rejection_cooldown_until
        ):
            return R_IC_REJECTION_COOLDOWN

        # Gate: Loss breaker
        pause_until = self._loss_breaker_pause_until
        if pause_until:
            current_date_str = current_time.strftime("%Y-%m-%d")
            if current_date_str <= pause_until:
                return R_IC_LOSS_BREAKER_ACTIVE

        # Gate: Daily trade limit
        max_trades = int(getattr(config, "IC_MAX_TRADES_PER_DAY", 2))
        if self._trades_today >= max_trades:
            return R_IC_DAILY_TRADE_LIMIT

        # Gate: Position limit
        max_concurrent = int(getattr(config, "IC_MAX_CONCURRENT", 2))
        active_count = len([p for p in self._positions if not p.is_closing])
        if active_count >= max_concurrent:
            return R_IC_POSITION_LIMIT

        # Gate: Daily loss stop
        daily_loss_pct = float(getattr(config, "IC_DAILY_LOSS_PCT", 0.015))
        if effective_portfolio_value > 0 and self._daily_pnl < 0:
            loss_ratio = abs(self._daily_pnl) / effective_portfolio_value
            if loss_ratio >= daily_loss_pct:
                return R_IC_DAILY_LOSS_STOP

        # Gate: Budget / open risk cap (purely %-based, scales with portfolio)
        ic_open_risk_pct = float(getattr(config, "IC_OPEN_RISK_PCT", 0.03))
        max_open_risk = ic_open_risk_pct * effective_portfolio_value
        if ic_open_risk >= max_open_risk:
            return R_IC_BUDGET_EXCEEDED

        # Gate: Margin
        min_margin = effective_portfolio_value * float(
            getattr(config, "OPTIONS_MIN_MARGIN_PCT", 0.02)
        )
        if margin_remaining < min_margin:
            return R_IC_MARGIN_INSUFFICIENT

        # Gate: Regime in neutral band (uses EOD score — stable across the day)
        regime_min = float(getattr(config, "IC_REGIME_MIN", 45))
        regime_max = float(getattr(config, "IC_REGIME_MAX", 60))
        if not (regime_min <= regime_score <= regime_max):
            self._regime_neutral_days = 0
            self._regime_neutral_last_date = None
            self._emit_regime_decision(
                "BLOCK",
                R_IC_REGIME_OUT_OF_RANGE,
                {"regime_score": regime_score, "min": regime_min, "max": regime_max},
            )
            return R_IC_REGIME_OUT_OF_RANGE

        # Gate: Regime persistence — count consecutive DAYS, not intraday bars.
        # EOD score is constant within a day, so deduplicate by calendar date.
        today_str = current_time.strftime("%Y-%m-%d")
        if self._regime_neutral_last_date != today_str:
            self._regime_neutral_days += 1
            self._regime_neutral_last_date = today_str
        persistence_req = int(getattr(config, "IC_REGIME_PERSISTENCE_DAYS", 2))
        if self._regime_neutral_days < persistence_req:
            self._emit_regime_decision(
                "BLOCK",
                R_IC_REGIME_NOT_PERSISTENT,
                {"neutral_days": self._regime_neutral_days, "required": persistence_req},
            )
            return R_IC_REGIME_NOT_PERSISTENT

        # Gate: Regime velocity — block if score trending directionally over N days
        velocity_window = int(getattr(config, "IC_REGIME_VELOCITY_WINDOW", 5))
        velocity_max = float(getattr(config, "IC_REGIME_VELOCITY_MAX", 8.0))
        if self._regime_score_history and self._regime_score_history[-1][0] == today_str:
            self._regime_score_history[-1] = (today_str, regime_score)
        else:
            self._regime_score_history.append((today_str, regime_score))
        if len(self._regime_score_history) > velocity_window:
            self._regime_score_history = self._regime_score_history[-velocity_window:]
        if len(self._regime_score_history) >= velocity_window:
            oldest_score = self._regime_score_history[0][1]
            delta = regime_score - oldest_score
            if abs(delta) > velocity_max:
                self._emit_regime_decision(
                    "BLOCK",
                    R_IC_REGIME_VELOCITY_BLOCK,
                    {"delta": delta, "max": velocity_max, "window": velocity_window},
                )
                return R_IC_REGIME_VELOCITY_BLOCK

        # Gate: Transition overlay — block DETERIORATION/AMBIGUOUS
        # Canonical key is transition_overlay; transition_state is legacy fallback.
        transition_state = str(
            transition_ctx.get("transition_overlay") or transition_ctx.get("transition_state") or ""
        ).upper()
        if transition_state in {"DETERIORATION", "AMBIGUOUS"}:
            self._emit_regime_decision(
                "BLOCK",
                R_IC_TRANSITION_BLOCK,
                {"transition_state": transition_state},
            )
            return R_IC_TRANSITION_BLOCK

        # Gate: Fast overlay — block STRESS/EARLY_STRESS
        fast_overlay = str(transition_ctx.get("fast_overlay", "") or "").upper()
        if fast_overlay in {"STRESS", "EARLY_STRESS"}:
            self._emit_regime_decision(
                "BLOCK",
                R_IC_FAST_OVERLAY_STRESS,
                {"fast_overlay": fast_overlay},
            )
            return R_IC_FAST_OVERLAY_STRESS

        # Gate: VIX range
        vix_min = float(getattr(config, "IC_VIX_MIN", 14.0))
        vix_max = float(getattr(config, "IC_VIX_MAX", 32.0))
        if not (vix_min <= vix_current <= vix_max):
            self._emit_regime_decision(
                "BLOCK",
                R_IC_VIX_OUT_OF_RANGE,
                {"vix": vix_current, "min": vix_min, "max": vix_max},
            )
            return R_IC_VIX_OUT_OF_RANGE

        # Gate: ADX (low trend)
        adx_max = float(getattr(config, "IC_ADX_MAX", 20.0))
        if adx_value > adx_max:
            self._emit_regime_decision(
                "BLOCK",
                R_IC_ADX_TOO_HIGH,
                {"adx": adx_value, "max": adx_max},
            )
            return R_IC_ADX_TOO_HIGH

        # Gate: Entry time window
        start_h = int(getattr(config, "IC_ENTRY_START_HOUR", 10))
        start_m = int(getattr(config, "IC_ENTRY_START_MINUTE", 15))
        end_h = int(getattr(config, "IC_ENTRY_END_HOUR", 14))
        end_m = int(getattr(config, "IC_ENTRY_END_MINUTE", 30))
        t_minutes = current_time.hour * 60 + current_time.minute
        if not (start_h * 60 + start_m <= t_minutes <= end_h * 60 + end_m):
            return R_IC_OUTSIDE_ENTRY_WINDOW

        # Gate: Event day block
        if bool(getattr(config, "IC_EVENT_DAY_BLOCK_ENABLED", True)):
            # Support both canonical and legacy context keys.
            if bool(
                transition_ctx.get("is_event_day", False)
                or transition_ctx.get("is_macro_event_day", False)
                or transition_ctx.get("has_macro_event", False)
            ):
                self._emit_regime_decision(
                    "BLOCK",
                    R_IC_EVENT_DAY_BLOCK,
                    {"is_event_day": True},
                )
                return R_IC_EVENT_DAY_BLOCK

        # All regime/market gates passed
        self._emit_regime_decision("PASS", "IC_ENV_OK")
        return None

    # ── Contract search ──

    def _search_and_build_condor(
        self,
        *,
        chain,
        qqq_price: float,
        vix_current: float,
        regime_score: float,
        adx_value: float,
        current_time: datetime,
        effective_portfolio_value: float,
    ) -> Optional[IronCondorPosition]:
        """VASS-style progressive contract search with elastic relaxation.

        Three-layer fallback:
          Layer 1 — DTE range fallback: try [(21,35), (35,45), (14,21)]
          Layer 2 — Elastic delta widening: [0, ±0.03, ±0.06, ±0.10]
          Layer 3 — C/W floor relaxation: [0, -0.03, -0.05] (down to absolute floor)

        Only records a drop code if ALL layers exhausted.
        """
        if chain is None:
            self._record_drop(R_IC_NO_CHAIN)
            return None

        # Scan throttle: don't re-scan within N minutes
        throttle_min = int(getattr(config, "IC_SCAN_THROTTLE_MINUTES", 15))
        if self._last_scan_time and throttle_min > 0:
            try:
                last_scan = datetime.strptime(self._last_scan_time, "%Y-%m-%d %H:%M:%S")
                if (current_time - last_scan).total_seconds() < throttle_min * 60:
                    return None
            except (ValueError, TypeError):
                pass
        self._last_scan_time = current_time.strftime("%Y-%m-%d %H:%M:%S")

        # Extract all contracts from chain once (shared across all fallback passes)
        contracts = self._extract_chain_contracts(chain, qqq_price, current_time)
        if not contracts:
            self._record_drop(R_IC_NO_CHAIN)
            return None

        # ── Layer 1: DTE range fallback ──
        dte_ranges = getattr(config, "IC_DTE_RANGES", None)
        if not dte_ranges:
            dte_min = int(getattr(config, "IC_DTE_MIN", 21))
            dte_max = int(getattr(config, "IC_DTE_MAX", 45))
            dte_ranges = [(dte_min, dte_max)]

        fallback_stats: List[str] = []

        for dte_min, dte_max in dte_ranges:
            result = self._search_single_dte_range(
                contracts=contracts,
                dte_min=dte_min,
                dte_max=dte_max,
                qqq_price=qqq_price,
                vix_current=vix_current,
                regime_score=regime_score,
                adx_value=adx_value,
                current_time=current_time,
                effective_portfolio_value=effective_portfolio_value,
                fallback_stats=fallback_stats,
            )
            if result is not None:
                return result

        # All DTE ranges exhausted
        self._record_drop(R_IC_NO_COMPLETE_CONDOR)
        stats_str = "; ".join(fallback_stats) if fallback_stats else "no_stats"
        self._log(
            f"SEARCH_FAIL | code={R_IC_NO_COMPLETE_CONDOR} "
            f"| chain_size={len(contracts)} | ranges_tried={len(dte_ranges)} "
            f"| detail=[{stats_str}]",
            trades_only=False,
        )
        return None

    def _search_single_dte_range(
        self,
        *,
        contracts: List[OptionContract],
        dte_min: int,
        dte_max: int,
        qqq_price: float,
        vix_current: float,
        regime_score: float,
        adx_value: float,
        current_time: datetime,
        effective_portfolio_value: float,
        fallback_stats: List[str],
    ) -> Optional[IronCondorPosition]:
        """Search within a single DTE range using elastic delta widening + C/W relaxation."""
        base_delta_min = float(getattr(config, "IC_SHORT_DELTA_MIN", 0.16))
        base_delta_max = float(getattr(config, "IC_SHORT_DELTA_MAX", 0.22))
        elastic_steps = getattr(config, "IC_ELASTIC_DELTA_STEPS", [0.0, 0.03, 0.06, 0.10])
        elastic_floor = float(getattr(config, "IC_ELASTIC_DELTA_FLOOR", 0.10))
        elastic_ceiling = float(getattr(config, "IC_ELASTIC_DELTA_CEILING", 0.30))
        min_oi = int(getattr(config, "IC_MIN_OPEN_INTEREST", 100))
        max_spread_pct = float(getattr(config, "IC_MAX_SPREAD_PCT", 0.30))
        min_pool_depth = int(getattr(config, "IC_MIN_POOL_DEPTH", 2))
        wing_width = self._get_wing_width_for_vix(vix_current)
        tolerance = int(getattr(config, "IC_WING_WIDTH_FALLBACK_TOLERANCE", 1))

        # ── Layer 2: Elastic delta widening ──
        for widen_step in elastic_steps:
            delta_min = max(elastic_floor, base_delta_min - widen_step)
            delta_max = min(elastic_ceiling, base_delta_max + widen_step)

            # Filter contracts for this DTE + delta band
            eligible_puts: List[OptionContract] = []
            eligible_calls: List[OptionContract] = []

            for c in contracts:
                if not (dte_min <= c.days_to_expiry <= dte_max):
                    continue
                if c.open_interest < min_oi:
                    continue
                if c.bid <= 0 or c.ask <= c.bid:
                    continue
                if c.spread_pct > max_spread_pct:
                    continue
                abs_delta = abs(c.delta)
                if delta_min <= abs_delta <= delta_max:
                    if c.direction == OptionDirection.PUT:
                        eligible_puts.append(c)
                    elif c.direction == OptionDirection.CALL:
                        eligible_calls.append(c)

            if len(eligible_puts) < min_pool_depth or len(eligible_calls) < min_pool_depth:
                continue  # Try next widen step

            # ── Layer 3: C/W relaxation + condor construction ──
            result = self._build_best_condor(
                contracts=contracts,
                eligible_puts=eligible_puts,
                eligible_calls=eligible_calls,
                wing_width=wing_width,
                tolerance=tolerance,
                qqq_price=qqq_price,
                vix_current=vix_current,
                regime_score=regime_score,
                adx_value=adx_value,
                current_time=current_time,
                effective_portfolio_value=effective_portfolio_value,
            )
            if result is not None:
                _p = result.entry_underlying_price
                _pd = (_p - result.put_short_strike) / _p if _p > 0 else 0
                _cd = (result.call_short_strike - _p) / _p if _p > 0 else 0
                self._log(
                    f"SEARCH_OK | DTE={dte_min}-{dte_max} | widen={widen_step:.2f} "
                    f"| delta=[{delta_min:.2f},{delta_max:.2f}] "
                    f"| puts={len(eligible_puts)} calls={len(eligible_calls)} "
                    f"| C/W={result.credit_to_width:.3f} "
                    f"| put_dist={_pd:.3f} call_dist={_cd:.3f}",
                    trades_only=False,
                )
                return result

        # This DTE range exhausted
        fallback_stats.append(
            f"DTE={dte_min}-{dte_max}|puts={len(eligible_puts)}|calls={len(eligible_calls)}"
        )
        return None

    def _build_best_condor(
        self,
        *,
        contracts: List[OptionContract],
        eligible_puts: List[OptionContract],
        eligible_calls: List[OptionContract],
        wing_width: int,
        tolerance: int,
        qqq_price: float,
        vix_current: float,
        regime_score: float,
        adx_value: float,
        current_time: datetime,
        effective_portfolio_value: float,
    ) -> Optional[IronCondorPosition]:
        """Construct best condor from eligible pools with C/W relaxation.

        Tries C/W floor at [base, base-0.03, base-0.05] down to absolute floor.
        Uses two-pass wing search: strict width → ±tolerance.
        """
        max_combos = int(getattr(config, "IC_MAX_CANDIDATE_COMBOS", 50))
        cw_relax_steps = getattr(config, "IC_CW_RELAX_STEPS", [0.0, 0.03, 0.05])
        cw_absolute_floor = float(getattr(config, "IC_CW_ABSOLUTE_FLOOR", 0.20))
        base_cw_floor = self._get_cw_floor_for_vix(vix_current)

        # Sort short legs by closeness to ideal delta (midpoint of band)
        delta_target = (
            float(getattr(config, "IC_SHORT_DELTA_MIN", 0.16))
            + float(getattr(config, "IC_SHORT_DELTA_MAX", 0.22))
        ) / 2
        eligible_puts.sort(key=lambda c: abs(abs(c.delta) - delta_target))
        eligible_calls.sort(key=lambda c: abs(abs(c.delta) - delta_target))

        for cw_relax in cw_relax_steps:
            effective_cw_floor = max(cw_absolute_floor, base_cw_floor - cw_relax)

            candidates: List[Tuple[float, IronCondorPosition]] = []
            combo_count = 0

            # Two-pass wing search: strict width → ±tolerance
            for width_tol in (0, tolerance):
                if candidates:
                    break  # Found candidates in strict pass

                for short_put in eligible_puts:
                    if combo_count >= max_combos:
                        break
                    long_put = self._find_wing_leg(
                        contracts, short_put, wing_width, "PUT", width_tol
                    )
                    if long_put is None:
                        continue

                    for short_call in eligible_calls:
                        if combo_count >= max_combos:
                            break
                        if short_call.expiry != short_put.expiry:
                            continue
                        long_call = self._find_wing_leg(
                            contracts, short_call, wing_width, "CALL", width_tol
                        )
                        if long_call is None:
                            continue

                        combo_count += 1
                        result = self._validate_and_score_condor(
                            short_put=short_put,
                            long_put=long_put,
                            short_call=short_call,
                            long_call=long_call,
                            qqq_price=qqq_price,
                            vix_current=vix_current,
                            regime_score=regime_score,
                            adx_value=adx_value,
                            current_time=current_time,
                            effective_portfolio_value=effective_portfolio_value,
                            cw_floor_override=effective_cw_floor,
                        )
                        if result is not None:
                            score, condor = result
                            candidates.append((score, condor))

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                return candidates[0][1]

        return None

    def _extract_chain_contracts(
        self, chain, qqq_price: float, current_time: datetime
    ) -> List[OptionContract]:
        """Extract OptionContract objects from QC chain."""
        results: List[OptionContract] = []
        try:
            for c in chain:
                try:
                    symbol_str = str(c.Symbol) if hasattr(c, "Symbol") else str(c)
                    right = getattr(c, "Right", None)
                    if right is None:
                        continue
                    is_call = str(right).upper() in {"CALL", "1", "C"}
                    direction = OptionDirection.CALL if is_call else OptionDirection.PUT

                    strike = float(getattr(c, "Strike", 0))
                    if strike <= 0:
                        continue

                    expiry_raw = getattr(c, "Expiry", None)
                    if expiry_raw is None:
                        continue
                    if hasattr(expiry_raw, "strftime"):
                        expiry_str = expiry_raw.strftime("%Y-%m-%d")
                        dte = (expiry_raw.date() - current_time.date()).days
                    else:
                        expiry_str = str(expiry_raw)[:10]
                        dte = 0

                    bid = float(getattr(c, "BidPrice", 0) or 0)
                    ask = float(getattr(c, "AskPrice", 0) or 0)
                    mid = (bid + ask) / 2 if (bid > 0 and ask > bid) else 0.0

                    greeks = getattr(c, "Greeks", None)
                    delta = float(getattr(greeks, "Delta", 0) or 0) if greeks else 0.0
                    gamma = float(getattr(greeks, "Gamma", 0) or 0) if greeks else 0.0
                    vega = float(getattr(greeks, "Vega", 0) or 0) if greeks else 0.0
                    theta = float(getattr(greeks, "Theta", 0) or 0) if greeks else 0.0

                    oi = int(getattr(c, "OpenInterest", 0) or 0)
                    volume = int(getattr(c, "Volume", 0) or 0)

                    results.append(
                        OptionContract(
                            symbol=symbol_str,
                            underlying="QQQ",
                            direction=direction,
                            strike=strike,
                            expiry=expiry_str,
                            delta=delta,
                            gamma=gamma,
                            vega=vega,
                            theta=theta,
                            bid=bid,
                            ask=ask,
                            mid_price=mid,
                            open_interest=oi,
                            days_to_expiry=dte,
                        )
                    )
                except Exception:
                    continue
        except Exception:
            pass
        return results

    def _get_wing_width_for_vix(self, vix: float) -> int:
        """Return wing width (dollars) based on VIX tier."""
        if vix < 16:
            return int(getattr(config, "IC_WING_WIDTH_LOW_VIX", 3))
        elif vix <= 25:
            return int(getattr(config, "IC_WING_WIDTH_MID_VIX", 4))
        else:
            return int(getattr(config, "IC_WING_WIDTH_HIGH_VIX", 5))

    def _get_cw_floor_for_vix(self, vix: float) -> float:
        """Return C/W floor based on VIX tier."""
        if vix < 16:
            return float(getattr(config, "IC_CW_FLOOR_LOW_VIX", 0.22))
        elif vix <= 25:
            return float(getattr(config, "IC_CW_FLOOR_MID_VIX", 0.20))
        else:
            return float(getattr(config, "IC_CW_FLOOR_HIGH_VIX", 0.18))

    def _find_wing_leg(
        self,
        contracts: List[OptionContract],
        short_leg: OptionContract,
        target_width: int,
        side: str,
        tolerance: int = 0,
    ) -> Optional[OptionContract]:
        """Find the long (protective) wing leg for a short leg.

        For PUT side: long_put.strike = short_put.strike - width (lower strike)
        For CALL side: long_call.strike = short_call.strike + width (higher strike)
        """
        min_oi = int(getattr(config, "IC_MIN_OPEN_INTEREST", 100))

        if side == "PUT":
            target_strike = short_leg.strike - target_width
            direction = OptionDirection.PUT
        else:
            target_strike = short_leg.strike + target_width
            direction = OptionDirection.CALL

        best: Optional[OptionContract] = None
        best_distance = float("inf")

        for c in contracts:
            if c.direction != direction:
                continue
            if c.expiry != short_leg.expiry:
                continue
            if c.open_interest < min_oi:
                continue
            if c.bid <= 0 or c.ask <= c.bid:
                continue

            distance = abs(c.strike - target_strike)
            if distance <= tolerance and distance < best_distance:
                best = c
                best_distance = distance

        return best

    def _validate_and_score_condor(
        self,
        *,
        short_put: OptionContract,
        long_put: OptionContract,
        short_call: OptionContract,
        long_call: OptionContract,
        qqq_price: float,
        vix_current: float,
        regime_score: float,
        adx_value: float,
        current_time: datetime,
        effective_portfolio_value: float,
        cw_floor_override: Optional[float] = None,
    ) -> Optional[Tuple[float, IronCondorPosition]]:
        """Validate a complete 4-leg condor and return (score, position) or None."""

        put_wing_width = short_put.strike - long_put.strike
        call_wing_width = long_call.strike - short_call.strike

        if put_wing_width <= 0 or call_wing_width <= 0:
            return None

        # ── Strike reuse guard (IC-vs-IC and IC-vs-pending) ──
        if bool(getattr(config, "IC_STRIKE_REUSE_GUARD_ENABLED", True)):
            new_strikes = {short_put.strike, long_put.strike, short_call.strike, long_call.strike}
            new_expiry = short_put.expiry  # All 4 legs share the same expiry

            # Check against active IC positions
            for existing in self._positions:
                if existing.is_closing:
                    continue
                ex_expiry = existing.short_put.expiry
                if ex_expiry != new_expiry:
                    continue
                ex_strikes = {
                    existing.short_put.strike,
                    existing.long_put.strike,
                    existing.short_call.strike,
                    existing.long_call.strike,
                }
                overlap = new_strikes & ex_strikes
                if overlap:
                    self._log(
                        f"IC_STRIKE_REUSE_BLOCKED: active overlap {overlap} "
                        f"| expiry={new_expiry} | existing={existing.condor_id}",
                        trades_only=True,
                    )
                    self._record_drop(R_IC_STRIKE_REUSE)
                    return None

            # Check against pending (unfilled) IC entry
            if self._pending_entry and self._pending_condor is not None:
                pc = self._pending_condor
                pc_expiry = pc.short_put.expiry
                if pc_expiry == new_expiry:
                    pc_strikes = {
                        pc.short_put.strike,
                        pc.long_put.strike,
                        pc.short_call.strike,
                        pc.long_call.strike,
                    }
                    overlap = new_strikes & pc_strikes
                    if overlap:
                        self._log(
                            f"IC_STRIKE_REUSE_BLOCKED: pending overlap {overlap} "
                            f"| expiry={new_expiry} | pending={pc.condor_id}",
                            trades_only=True,
                        )
                        self._record_drop(R_IC_STRIKE_REUSE)
                        return None

        # ── Credit calculation ──
        # Credit = sell short legs (collect premium) - buy long legs (pay premium)
        put_credit = short_put.mid_price - long_put.mid_price
        call_credit = short_call.mid_price - long_call.mid_price
        net_credit = put_credit + call_credit

        if net_credit <= 0:
            return None

        max_wing = max(put_wing_width, call_wing_width)
        credit_to_width = net_credit / max_wing
        max_loss = max_wing - net_credit

        # ── C/W floor gate (VIX-tiered, with optional relaxed override) ──
        cw_floor = (
            cw_floor_override
            if cw_floor_override is not None
            else self._get_cw_floor_for_vix(vix_current)
        )
        if credit_to_width < cw_floor:
            return None  # Don't record drop — caller handles relaxation retries

        # ── Implied expiry WR check ──
        implied_wr = 1.0 - credit_to_width
        max_implied_wr = float(getattr(config, "IC_MAX_IMPLIED_WR", 0.82))
        if implied_wr > max_implied_wr:
            self._record_drop(R_IC_CW_BELOW_MIN)
            return None

        # ── Stop D/W feasibility ──
        # Stop close debit = credit + stop_mult * credit = credit * (1 + stop_mult)
        stop_mult = float(getattr(config, "IC_STOP_LOSS_MULTIPLE", 1.50))
        stop_debit = net_credit * (1.0 + stop_mult)
        stop_dw = stop_debit / max_wing
        max_stop_dw = float(getattr(config, "IC_MAX_STOP_DW", 0.65))
        if stop_dw > max_stop_dw:
            self._record_drop(R_IC_STOP_DW_UNFEASIBLE)
            return None

        # ── Delta symmetry ──
        delta_sym_max = float(getattr(config, "IC_DELTA_SYMMETRY_MAX", 0.03))
        delta_diff = abs(abs(short_call.delta) - abs(short_put.delta))
        if delta_diff > delta_sym_max:
            self._record_drop(R_IC_DELTA_ASYMMETRY)
            return None

        # ── Wing symmetry ──
        wing_sym_max = float(getattr(config, "IC_WING_SYMMETRY_MAX", 1.0))
        wing_diff = abs(call_wing_width - put_wing_width)
        if wing_diff > wing_sym_max:
            self._record_drop(R_IC_WING_ASYMMETRY)
            return None

        # ── Slippage guard ──
        max_slippage = float(getattr(config, "IC_MAX_COMBO_SLIPPAGE", 0.15))
        avg_spread_pct = (
            short_put.spread_pct
            + long_put.spread_pct
            + short_call.spread_pct
            + long_call.spread_pct
        ) / 4.0
        if avg_spread_pct > max_slippage:
            self._record_drop(R_IC_SLIPPAGE_FAIL)
            return None

        # ── Expected move buffer gate (V12.33) ──
        # Reject condors where short strikes are inside the VIX-implied expected move.
        # EM = QQQ × (VIX/100) × √(DTE/365) × buffer_mult
        # This auto-adapts to VIX level and DTE: low vol → tighter OK, high vol → wider.
        em_buffer = float(getattr(config, "IC_EM_BUFFER_MULT", 1.0))
        if qqq_price > 0 and vix_current > 0 and em_buffer > 0:
            min_dte = min(short_put.days_to_expiry, short_call.days_to_expiry)
            em_pct = (vix_current / 100.0) * math.sqrt(max(1, min_dte) / 365.0) * em_buffer
            min_em_distance = qqq_price * em_pct

            put_distance = qqq_price - short_put.strike
            call_distance = short_call.strike - qqq_price

            if put_distance < min_em_distance or call_distance < min_em_distance:
                self._record_drop(R_IC_INSIDE_EXPECTED_MOVE)
                self._emit_regime_decision(
                    "BLOCKED",
                    "IC_EM_BUFFER",
                    threshold_snapshot={
                        "put_dist": round(put_distance, 2),
                        "call_dist": round(call_distance, 2),
                        "em_threshold": round(min_em_distance, 2),
                        "em_pct": round(em_pct, 4),
                        "vix": round(vix_current, 1),
                        "dte": min_dte,
                        "buffer_mult": em_buffer,
                    },
                )
                return None

        # ── ADX-aware call OTM floor (V12.36) ──
        # When ADX > threshold, require the call short to be >= N% OTM.
        # All 4 V12.35 winners had ADX < 18; losers IC-03/IC-13 had ADX > 18
        # with call shorts at 2.7-2.8% OTM that were breached within 2 days.
        adx_otm_threshold = float(getattr(config, "IC_ADX_CALL_OTM_ADX_THRESHOLD", 18.0))
        if adx_value > adx_otm_threshold and qqq_price > 0:
            min_call_otm_pct = float(getattr(config, "IC_ADX_CALL_OTM_MIN_PCT", 0.030))
            call_otm_pct = (short_call.strike - qqq_price) / qqq_price if qqq_price > 0 else 0
            if call_otm_pct < min_call_otm_pct:
                self._record_drop(R_IC_CALL_OTM_TOO_TIGHT)
                return None

        # ── Per-trade risk cap ──
        per_trade_risk_pct = float(getattr(config, "IC_PER_TRADE_RISK_PCT", 0.01))
        per_trade_max_risk = per_trade_risk_pct * effective_portfolio_value
        # Size by max loss: how many spreads can we afford?
        if max_loss <= 0:
            return None
        max_spreads_by_risk = int(per_trade_max_risk / (max_loss * 100))
        if max_spreads_by_risk < 1:
            self._record_drop(R_IC_PER_TRADE_RISK_EXCEEDED)
            return None

        # Cap by open risk budget (%-based, scales with portfolio)
        ic_open_risk_pct = float(getattr(config, "IC_OPEN_RISK_PCT", 0.03))
        max_open_risk = ic_open_risk_pct * effective_portfolio_value
        remaining_budget = max(0, max_open_risk - self.get_open_risk())
        max_spreads_by_budget = int(remaining_budget / (max_loss * 100)) if max_loss > 0 else 0
        if max_spreads_by_budget < 1:
            self._record_drop(R_IC_BUDGET_EXCEEDED)
            return None
        num_spreads = min(max_spreads_by_risk, max_spreads_by_budget, 10)

        # ── Determine VIX tier label ──
        if vix_current < 16:
            cw_tier = "LOW_VIX"
        elif vix_current <= 25:
            cw_tier = "MID_VIX"
        else:
            cw_tier = "HIGH_VIX"

        # ── Build position ──
        condor = IronCondorPosition(
            short_put=short_put,
            long_put=long_put,
            short_call=short_call,
            long_call=long_call,
            net_credit=net_credit,
            put_wing_width=put_wing_width,
            call_wing_width=call_wing_width,
            max_loss=max_loss,
            credit_to_width=credit_to_width,
            num_spreads=num_spreads,
            entry_time=current_time.strftime("%Y-%m-%d %H:%M:%S"),
            regime_at_entry=regime_score,
            entry_vix=vix_current,
            entry_adx=adx_value,
            entry_dte=min(short_put.days_to_expiry, short_call.days_to_expiry),
            entry_underlying_price=qqq_price,
            entry_cw_tier=cw_tier,
            stop_dw=stop_dw,
            implied_wr_be=implied_wr,
            # V12.37: Per-side credit for rolling
            put_side_credit=put_credit,
            call_side_credit=call_credit,
            max_rolls=int(getattr(config, "IC_ROLL_MAX_PER_CAMPAIGN", 1) or 1),
            cumulative_credit=net_credit,
            entry_stop_mult=stop_mult,
            entry_mfe_t1_trigger=float(getattr(config, "IC_MFE_T1_TRIGGER", 0.30)),
            entry_mfe_t2_trigger=float(getattr(config, "IC_MFE_T2_TRIGGER", 0.45)),
            entry_mfe_t1_floor=float(getattr(config, "IC_MFE_T1_FLOOR_PCT", 0.05)),
            entry_mfe_t2_floor=float(getattr(config, "IC_MFE_T2_FLOOR_PCT", 0.25)),
        )

        # ── Score: higher is better (V12.33: reweighted to prefer distance over richness) ──
        # Weight: distance (30%), C/W (20%), delta symmetry (20%), slippage (15%), credit (15%)
        put_dist_pct = (qqq_price - short_put.strike) / qqq_price if qqq_price > 0 else 0
        call_dist_pct = (short_call.strike - qqq_price) / qqq_price if qqq_price > 0 else 0
        min_dist_pct = min(put_dist_pct, call_dist_pct)
        # Normalize distance: 5% OTM = 1.0, 0% = 0.0
        distance_score = min(min_dist_pct / 0.05, 1.0)

        score = (
            0.30 * distance_score
            + 0.20 * credit_to_width
            + 0.20 * (1.0 - delta_diff / delta_sym_max)
            + 0.15 * (1.0 - avg_spread_pct / max_slippage)
            + 0.15 * min(net_credit / max_wing, 0.40)
        )
        cw_penalty_threshold = float(getattr(config, "IC_CW_SCORE_PENALTY_THRESHOLD", 0.35))
        cw_penalty_range = max(
            float(getattr(config, "IC_CW_SCORE_PENALTY_RANGE", 0.10)),
            0.0,
        )
        cw_penalty_max = max(
            float(getattr(config, "IC_CW_SCORE_PENALTY_MAX", 0.12)),
            0.0,
        )
        if credit_to_width > cw_penalty_threshold and cw_penalty_max > 0:
            if cw_penalty_range <= 0:
                cw_penalty = cw_penalty_max
            else:
                overflow = (credit_to_width - cw_penalty_threshold) / cw_penalty_range
                cw_penalty = min(max(overflow, 0.0), 1.0) * cw_penalty_max
            score -= cw_penalty

        return (score, condor)

    def _build_entry_signals(
        self, condor: IronCondorPosition, current_time: datetime
    ) -> List[TargetWeight]:
        """Build TargetWeight signals for router.

        Emits two signals: one for put credit spread, one for call credit spread.
        Both tagged with shared condor_id for pairing.
        """
        timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
        base_meta = {
            "options_lane": "IC",
            "options_strategy": "IRON_CONDOR",
            "trace_source": "IC:IRON_CONDOR",
            "trace_id": condor.condor_id,
            "condor_id": condor.condor_id,
            "net_credit": condor.net_credit,
            "credit_to_width": condor.credit_to_width,
            "max_loss": condor.max_loss,
            "entry_vix": condor.entry_vix,
            "regime_at_entry": condor.regime_at_entry,
            "transition_overlay": condor.entry_transition_overlay,
        }

        # Put credit spread signal (bull put): sell short_put, buy long_put
        # Router contract: symbol = long leg, spread_short_leg_symbol = short leg
        put_meta = dict(base_meta)
        put_meta["spread_side"] = "PUT_CREDIT"
        put_meta["spread_type"] = "CREDIT_PUT"
        put_meta["spread_short_leg_symbol"] = condor.short_put.symbol
        put_meta["spread_short_leg_quantity"] = condor.num_spreads
        put_meta["short_strike"] = condor.short_put.strike
        put_meta["long_strike"] = condor.long_put.strike
        put_meta["spread_width"] = condor.put_wing_width
        put_meta["spread_cost_or_credit"] = -(
            condor.short_put.mid_price - condor.long_put.mid_price
        )

        put_signal = TargetWeight(
            symbol=condor.long_put.symbol,
            target_weight=1.0,
            source="OPT_IC",
            urgency=Urgency.IMMEDIATE,
            reason=(
                f"IC_PUT_CREDIT | K_short={condor.short_put.strike} "
                f"K_long={condor.long_put.strike} | C/W={condor.credit_to_width:.3f}"
            ),
            timestamp=timestamp,
            metadata=put_meta,
            requested_quantity=condor.num_spreads,
        )

        # Call credit spread signal (bear call): sell short_call, buy long_call
        # Router contract: symbol = long leg, spread_short_leg_symbol = short leg
        call_meta = dict(base_meta)
        call_meta["spread_side"] = "CALL_CREDIT"
        call_meta["spread_type"] = "CREDIT_CALL"
        call_meta["spread_short_leg_symbol"] = condor.short_call.symbol
        call_meta["spread_short_leg_quantity"] = condor.num_spreads
        call_meta["short_strike"] = condor.short_call.strike
        call_meta["long_strike"] = condor.long_call.strike
        call_meta["spread_width"] = condor.call_wing_width
        call_meta["spread_cost_or_credit"] = -(
            condor.short_call.mid_price - condor.long_call.mid_price
        )

        call_signal = TargetWeight(
            symbol=condor.long_call.symbol,
            target_weight=1.0,
            source="OPT_IC",
            urgency=Urgency.IMMEDIATE,
            reason=(
                f"IC_CALL_CREDIT | K_short={condor.short_call.strike} "
                f"K_long={condor.long_call.strike} | C/W={condor.credit_to_width:.3f}"
            ),
            timestamp=timestamp,
            metadata=call_meta,
            requested_quantity=condor.num_spreads,
        )

        return [put_signal, call_signal]

    # ══════════════════════════════════════════════════════════════════════
    # EXIT LOGIC
    # ══════════════════════════════════════════════════════════════════════

    def check_exit_signals(
        self,
        *,
        condor: IronCondorPosition,
        combined_pnl: float,
        current_dte: int,
        vix_current: float,
        regime_score: float,
        qqq_price: float,
        current_time: datetime,
        put_side_pnl: Optional[float] = None,
        call_side_pnl: Optional[float] = None,
    ) -> Optional[Tuple[str, List[TargetWeight]]]:
        """Check exit conditions for a single condor. Returns (reason, signals) or None.

        Exit cascade with DTE-adaptive hold guard:

          PRE-GUARD (always fire):
            P0: VIX Spike → emergency close
            P8: Assignment risk → short leg ITM near expiry

          HOLD GUARD (position age < DTE-adaptive hold window):
            Emergency: Hard stop during hold (2.5× credit)
            EOD gate: De-risk at 15:45+ if held >= 4h (1.5× credit)
            Bypass: Profitable → fall through to main cascade
            Default: Block P2-P7

          POST-GUARD (full cascade after hold expires):
            P2: Profit target → 60% of credit captured
            P2B: MFE Lock Floor → was profitable, gave back gains (2-tier ratchet)
            P3: Stop loss → 150% of credit lost
            P4: Wing breach → short strike 2% ITM
            P5: Time exit → DTE <= 10
            P6: Regime break → regime outside neutral zone + buffer
            P7: Friday close → DTE < 14 heading into weekend

        V12.37: P2C roll trigger inserted between MFE lock and stop loss.
        Rolling closes only the tested side; untested side continues.
        """
        if condor.is_closing or condor.is_rolling:
            return None

        # V12.37: campaign-aware credit denominator for rolled condors (Gap B)
        if condor.roll_count > 0 and condor.cumulative_credit > 0:
            credit_100 = condor.cumulative_credit * 100 * condor.num_spreads
            combined_pnl = combined_pnl + condor.cumulative_realized_pnl
        else:
            credit_100 = condor.net_credit * 100 * condor.num_spreads
        loss_pct_of_credit = (
            (-combined_pnl / credit_100) if (credit_100 > 0 and combined_pnl < 0) else 0.0
        )

        # ── MFE HWM update (always, even during hold guard) ──
        pnl_pct_of_credit = combined_pnl / credit_100 if credit_100 > 0 else 0.0
        if pnl_pct_of_credit > condor.highest_pnl_pct:
            condor.highest_pnl_pct = pnl_pct_of_credit

        # ── PRE-GUARD: P0 — VIX Spike (always fires) ──
        vix_spike_threshold = float(getattr(config, "IC_VIX_SPIKE_EXIT", 30.0))
        if vix_current >= vix_spike_threshold:
            return self._build_exit(condor, EXIT_IC_VIX_SPIKE, current_time)

        # ── PRE-GUARD: P1 — Regime break (always fires, bypasses hold guard) ──
        # A neutral strategy has no edge once regime leaves the neutral band.
        # Unlike VASS (directional), both bull AND bear breaks are dangerous for IC.
        regime_min = float(getattr(config, "IC_REGIME_MIN", 45))
        regime_max = float(getattr(config, "IC_REGIME_MAX", 60))
        regime_buffer = float(getattr(config, "IC_REGIME_EXIT_BUFFER", 5))
        if regime_score < (regime_min - regime_buffer) or regime_score > (
            regime_max + regime_buffer
        ):
            return self._build_exit(condor, EXIT_IC_REGIME_BREAK, current_time)

        # ── PRE-GUARD: P1B — Underlying invalidation (always fires) ──
        # If the underlying moved too far from entry, the range thesis is dead.
        # Catches large moves that haven't yet triggered a regime break (regime lag).
        # V12.33: threshold = max(config_pct, entry_EM) so it can't fire inside
        # the expected move that the EM gate placed shorts outside of.
        if bool(getattr(config, "IC_UNDERLYING_INVALIDATION_ENABLED", True)):
            entry_px = float(condor.entry_underlying_price or 0.0)
            if entry_px > 0 and qqq_price > 0:
                move_pct = abs(qqq_price - entry_px) / entry_px
                cfg_threshold = float(getattr(config, "IC_UNDERLYING_INVALIDATION_PCT", 0.03))
                # Compute entry-time EM so invalidation can't fire inside 1σ range
                _evix = float(condor.entry_vix or 0.0)
                _edte = int(condor.entry_dte or 0)
                _embuf = float(getattr(config, "IC_EM_BUFFER_MULT", 1.0))
                if _evix > 0 and _edte > 0:
                    entry_em_pct = (_evix / 100.0) * math.sqrt(max(1, _edte) / 365.0) * _embuf
                else:
                    entry_em_pct = 0.0
                threshold = max(cfg_threshold, entry_em_pct)
                if move_pct >= threshold:
                    self._log(
                        f"IC_UNDERLYING_INVALIDATION: "
                        f"entry={entry_px:.2f} now={qqq_price:.2f} "
                        f"move={move_pct:.1%} >= {threshold:.1%} "
                        f"(cfg={cfg_threshold:.1%} em={entry_em_pct:.1%}) | "
                        f"id={condor.condor_id}",
                        trades_only=True,
                    )
                    return self._build_exit(condor, EXIT_IC_UNDERLYING_INVALIDATION, current_time)

        # ── PRE-GUARD: P2 — Assignment risk (always fires) ──
        div_guard_dte = int(getattr(config, "IC_DIVIDEND_GUARD_DTE", 3))
        if current_dte <= div_guard_dte:
            put_itm = (condor.put_short_strike - qqq_price) / qqq_price if qqq_price > 0 else 0
            call_itm = (qqq_price - condor.call_short_strike) / qqq_price if qqq_price > 0 else 0
            if put_itm > 0 or call_itm > 0:
                return self._build_exit(condor, EXIT_IC_ASSIGNMENT_RISK, current_time)

        # ── Daily loss stop ──
        # Checked at engine level in run_exit_cycle, not per-position

        # ── HOLD GUARD (DTE-adaptive) ──
        if bool(getattr(config, "IC_HOLD_GUARD_ENABLED", True)):
            entry_dt = datetime.strptime(condor.entry_time[:19], "%Y-%m-%d %H:%M:%S")
            live_minutes = (current_time - entry_dt).total_seconds() / 60.0

            # Compute DTE-adaptive hold window
            fraction = float(getattr(config, "IC_HOLD_GUARD_DTE_FRACTION", 0.33))
            min_days = int(getattr(config, "IC_HOLD_GUARD_MIN_DAYS", 5))
            max_days = int(getattr(config, "IC_HOLD_GUARD_MAX_DAYS", 15))
            hold_days = max(min_days, min(max_days, math.ceil(condor.entry_dte * fraction)))
            hold_minutes = hold_days * 1440

            if live_minutes < hold_minutes:
                # Layer 1: Hard stop during hold — catastrophic loss only
                hard_stop_mult = float(getattr(config, "IC_HOLD_HARD_STOP_CREDIT_MULT", 2.50))
                if loss_pct_of_credit >= hard_stop_mult:
                    self._log(
                        f"IC_HARD_STOP_DURING_HOLD: loss={loss_pct_of_credit:.2f}x >= "
                        f"{hard_stop_mult:.2f}x | id={condor.condor_id} "
                        f"| held={live_minutes:.0f}m/{hold_minutes}m",
                        trades_only=True,
                    )
                    return self._build_exit(condor, EXIT_IC_HARD_STOP_HOLD, current_time)

                # Layer 2: EOD risk gate during hold — overnight de-risk
                eod_enabled = bool(getattr(config, "IC_HOLD_EOD_GATE_ENABLED", True))
                eod_min_min = int(getattr(config, "IC_HOLD_EOD_GATE_MIN_MINUTES", 240))
                if eod_enabled and live_minutes >= eod_min_min:
                    is_eod = current_time.hour > 15 or (
                        current_time.hour == 15 and current_time.minute >= 45
                    )
                    eod_mult = float(getattr(config, "IC_HOLD_EOD_GATE_CREDIT_MULT", 1.50))
                    if is_eod and loss_pct_of_credit >= eod_mult:
                        self._log(
                            f"IC_EOD_HOLD_RISK_GATE: loss={loss_pct_of_credit:.2f}x >= "
                            f"{eod_mult:.2f}x at EOD | id={condor.condor_id}",
                            trades_only=True,
                        )
                        return self._build_exit(condor, EXIT_IC_EOD_HOLD_GATE, current_time)

                # Layer 3: optional rolling during hold
                if (
                    bool(getattr(config, "IC_ROLL_DURING_HOLD", False))
                    and bool(getattr(config, "IC_ROLL_ENABLED", False))
                    and put_side_pnl is not None
                    and call_side_pnl is not None
                ):
                    roll_result = self._check_roll_trigger(
                        condor=condor,
                        put_side_pnl=put_side_pnl,
                        call_side_pnl=call_side_pnl,
                        current_time=current_time,
                        vix_current=vix_current,
                        qqq_price=qqq_price,
                        current_dte=current_dte,
                    )
                    if roll_result is not None:
                        return roll_result

                # Layer 4: Profitable bypass — let profit target run
                if combined_pnl > 0:
                    pass  # fall through to main cascade
                else:
                    # BLOCK: suppress P2-P7 during hold
                    if condor.condor_id not in self._hold_guard_logged:
                        self._hold_guard_logged.add(condor.condor_id)
                        self._log(
                            f"IC_HOLD_GUARD: blocking cascade | id={condor.condor_id} "
                            f"| entry_dte={condor.entry_dte} | hold={hold_days}d "
                            f"| held={live_minutes:.0f}m/{hold_minutes}m "
                            f"| loss={loss_pct_of_credit:.2f}x",
                            trades_only=True,
                        )
                    return None

        # ── POST-GUARD: Main cascade ──

        # ── P2: Profit target ──
        target_pct = float(getattr(config, "IC_TARGET_CAPTURE_PCT", 0.60))
        if credit_100 > 0 and pnl_pct_of_credit >= target_pct:
            return self._build_exit(condor, EXIT_IC_PROFIT_TARGET, current_time)

        # ── P2B: MFE Lock Floor (use entry-frozen thresholds if available) ──
        if bool(getattr(config, "IC_MFE_LOCK_ENABLED", True)) and credit_100 > 0:
            t1 = (
                condor.entry_mfe_t1_trigger
                if condor.entry_mfe_t1_trigger > 0
                else float(getattr(config, "IC_MFE_T1_TRIGGER", 0.30))
            )
            t2 = (
                condor.entry_mfe_t2_trigger
                if condor.entry_mfe_t2_trigger > 0
                else float(getattr(config, "IC_MFE_T2_TRIGGER", 0.45))
            )
            floor_t1 = (
                condor.entry_mfe_t1_floor
                if condor.entry_mfe_t1_floor >= 0
                else float(getattr(config, "IC_MFE_T1_FLOOR_PCT", 0.05))
            )
            floor_t2 = (
                condor.entry_mfe_t2_floor
                if condor.entry_mfe_t2_floor >= 0
                else float(getattr(config, "IC_MFE_T2_FLOOR_PCT", 0.25))
            )

            # Ratchet tier up (never down)
            if condor.highest_pnl_pct >= t2:
                condor.mfe_lock_tier = max(condor.mfe_lock_tier, 2)
            elif condor.highest_pnl_pct >= t1:
                condor.mfe_lock_tier = max(condor.mfe_lock_tier, 1)

            # Compute floor and check
            floor_pnl_pct = None
            if condor.mfe_lock_tier >= 2:
                floor_pnl_pct = floor_t2
            elif condor.mfe_lock_tier >= 1:
                floor_pnl_pct = floor_t1

            if floor_pnl_pct is not None and pnl_pct_of_credit <= floor_pnl_pct:
                self._log(
                    f"IC_MFE_LOCK: T{condor.mfe_lock_tier} | pnl={pnl_pct_of_credit:.1%} <= "
                    f"floor={floor_pnl_pct:.1%} | MFE={condor.highest_pnl_pct:.1%} | "
                    f"id={condor.condor_id}",
                    trades_only=True,
                )
                return self._build_exit(condor, EXIT_IC_MFE_LOCK, current_time)

        # ── P2C: Per-side roll trigger (preempts full stop) ── V12.37
        if (
            bool(getattr(config, "IC_ROLL_ENABLED", False))
            and put_side_pnl is not None
            and call_side_pnl is not None
        ):
            roll_result = self._check_roll_trigger(
                condor=condor,
                put_side_pnl=put_side_pnl,
                call_side_pnl=call_side_pnl,
                current_time=current_time,
                vix_current=vix_current,
                qqq_price=qqq_price,
                current_dte=current_dte,
            )
            if roll_result is not None:
                return roll_result

        # ── P3: Stop loss (use entry-frozen multiplier if available) ──
        stop_mult = (
            condor.entry_stop_mult
            if condor.entry_stop_mult > 0
            else float(getattr(config, "IC_STOP_LOSS_MULTIPLE", 1.50))
        )
        if loss_pct_of_credit >= stop_mult:
            return self._build_exit(condor, EXIT_IC_STOP_LOSS, current_time)

        # ── P4: Wing breach (short strike ITM) — V12.38: only check active sides ──
        itm_exit_pct = float(getattr(config, "IC_SHORT_ITM_EXIT_PCT", 0.02))
        if condor.put_side_active:
            put_itm_depth = (
                (condor.put_short_strike - qqq_price) / qqq_price if qqq_price > 0 else 0
            )
            if put_itm_depth >= itm_exit_pct:
                return self._build_exit(condor, EXIT_IC_WING_BREACH_PUT, current_time)
        if condor.call_side_active:
            call_itm_depth = (
                (qqq_price - condor.call_short_strike) / qqq_price if qqq_price > 0 else 0
            )
            if call_itm_depth >= itm_exit_pct:
                return self._build_exit(condor, EXIT_IC_WING_BREACH_CALL, current_time)

        # ── P5: Time exit ──
        time_exit_dte = int(getattr(config, "IC_TIME_EXIT_DTE", 5))
        if current_dte <= time_exit_dte:
            return self._build_exit(condor, EXIT_IC_TIME_EXIT, current_time)

        # ── P6: Friday close ──
        friday_close_dte = int(getattr(config, "IC_FRIDAY_CLOSE_DTE", 8))
        if current_time.weekday() == 4 and current_dte < friday_close_dte:
            return self._build_exit(condor, EXIT_IC_FRIDAY_CLOSE, current_time)

        return None

    def run_exit_cycle(
        self,
        *,
        qqq_price: float,
        vix_current: float,
        regime_score: float,
        current_time: datetime,
        get_dte_func: Callable[[str], int],
        get_pnl_func: Callable[[IronCondorPosition], float],
        get_side_pnl_func: Optional[Callable[[IronCondorPosition, str], float]] = None,
    ) -> List[TargetWeight]:
        """Run exit checks on all open IC positions. Returns list of close signals."""
        all_signals: List[TargetWeight] = []

        for condor in list(self._positions):
            if condor.is_closing or condor.is_rolling:
                continue

            # Get current DTE from shortest leg
            min_dte = min(
                get_dte_func(condor.short_put.expiry),
                get_dte_func(condor.short_call.expiry),
            )

            # Get combined P&L (campaign-aware for rolled condors)
            combined_pnl = get_pnl_func(condor)

            # Get per-side P&L for roll trigger
            put_pnl = get_side_pnl_func(condor, "PUT") if get_side_pnl_func else None
            call_pnl = get_side_pnl_func(condor, "CALL") if get_side_pnl_func else None

            result = self.check_exit_signals(
                condor=condor,
                combined_pnl=combined_pnl,
                current_dte=min_dte,
                vix_current=vix_current,
                regime_score=regime_score,
                qqq_price=qqq_price,
                current_time=current_time,
                put_side_pnl=put_pnl,
                call_side_pnl=call_pnl,
            )

            if result is not None:
                reason, signals = result
                # Roll triggers set is_rolling (not is_closing) — condor stays alive
                is_roll = reason in (EXIT_IC_ROLL_PUT, EXIT_IC_ROLL_CALL)
                if not is_roll:
                    condor.is_closing = True
                    condor.close_attempt_count = 0
                    condor.last_close_signal_time = None
                condor.exit_pnl_estimate = float(combined_pnl or 0.0)
                all_signals.extend(signals)
                self._diag_exit_reasons[reason] = self._diag_exit_reasons.get(reason, 0) + 1

                self._emit_lifecycle(
                    "EXIT" if not is_roll else "ROLL_TRIGGER",
                    signal_id=condor.condor_id,
                    trace_id=condor.condor_id,
                    code=reason,
                    reason=f"PnL=${combined_pnl:.2f} DTE={min_dte}",
                )
                self._log(
                    f"{'EXIT' if not is_roll else 'ROLL'}_SIGNAL | "
                    f"condor_id={condor.condor_id} "
                    f"| reason={reason} | PnL=${combined_pnl:.2f} "
                    f"| DTE={min_dte} | VIX={vix_current:.1f} | regime={regime_score:.1f}",
                    trades_only=True,
                )

        return all_signals

    def _build_exit(
        self, condor: IronCondorPosition, reason: str, current_time: datetime
    ) -> Tuple[str, List[TargetWeight]]:
        """Build close signals for both sides of the condor."""
        timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
        base_meta = {
            "options_lane": "IC",
            "options_strategy": "IRON_CONDOR",
            "trace_source": f"IC:{reason}",
            "trace_id": condor.condor_id,
            "condor_id": condor.condor_id,
            "exit_reason": reason,
            "transition_overlay": condor.entry_transition_overlay,
        }

        signals = []
        # Close put credit spread: sell long put, buy back short put
        # V12.38: Only emit close for active sides (single-spread condors after roll)
        if condor.put_side_active:
            put_meta = dict(base_meta)
            put_meta["spread_side"] = "PUT_CREDIT_CLOSE"
            put_meta["spread_type"] = "CREDIT_PUT"
            put_meta["is_credit_spread"] = True
            put_meta["spread_close_short"] = True
            put_meta["spread_short_leg_symbol"] = condor.short_put.symbol
            put_meta["spread_short_leg_quantity"] = condor.num_spreads
            put_meta["spread_width"] = condor.put_wing_width
            signals.append(
                TargetWeight(
                    symbol=condor.long_put.symbol,
                    target_weight=0.0,
                    source="OPT_IC",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"IC_CLOSE_PUT | {reason} | condor_id={condor.condor_id}",
                    timestamp=timestamp,
                    metadata=put_meta,
                    requested_quantity=condor.num_spreads,
                )
            )

        # Close call credit spread: sell long call, buy back short call
        if condor.call_side_active:
            call_meta = dict(base_meta)
            call_meta["spread_side"] = "CALL_CREDIT_CLOSE"
            call_meta["spread_type"] = "CREDIT_CALL"
            call_meta["is_credit_spread"] = True
            call_meta["spread_close_short"] = True
            call_meta["spread_short_leg_symbol"] = condor.short_call.symbol
            call_meta["spread_short_leg_quantity"] = condor.num_spreads
            call_meta["spread_width"] = condor.call_wing_width
            signals.append(
                TargetWeight(
                    symbol=condor.long_call.symbol,
                    target_weight=0.0,
                    source="OPT_IC",
                    urgency=Urgency.IMMEDIATE,
                    reason=f"IC_CLOSE_CALL | {reason} | condor_id={condor.condor_id}",
                    timestamp=timestamp,
                    metadata=call_meta,
                    requested_quantity=condor.num_spreads,
                )
            )

        return (reason, signals)

    # ── V12.37: Per-side rolling methods ──

    def _check_roll_trigger(
        self,
        *,
        condor: IronCondorPosition,
        put_side_pnl: float,
        call_side_pnl: float,
        current_time: datetime,
        vix_current: float,
        qqq_price: float,
        current_dte: Optional[int] = None,
    ) -> Optional[Tuple[str, List[TargetWeight]]]:
        """Check if a tested side qualifies for rolling instead of full stop."""
        # Guard: rolling enabled, under max rolls, not already rolling
        if condor.roll_count >= condor.max_rolls:
            return None
        if condor.is_rolling:
            return None

        # V12.38: DTE floor — don't roll with very few days remaining
        min_dte = int(getattr(config, "IC_ROLL_MIN_DTE", 4))
        if current_dte is not None and current_dte < min_dte:
            return None

        # V12.38: VIX-adaptive trigger threshold
        if vix_current < 16:
            trigger_mult = float(getattr(config, "IC_ROLL_TRIGGER_LOW_VIX", 1.25))
        elif vix_current <= 25:
            trigger_mult = float(getattr(config, "IC_ROLL_TRIGGER_MID_VIX", 1.00))
        else:
            trigger_mult = float(getattr(config, "IC_ROLL_TRIGGER_HIGH_VIX", 0.75))

        # Check each active side for roll trigger
        for side, side_pnl, side_credit in [
            ("PUT", put_side_pnl, condor.put_side_credit),
            ("CALL", call_side_pnl, condor.call_side_credit),
        ]:
            if side == "PUT" and not condor.put_side_active:
                continue
            if side == "CALL" and not condor.call_side_active:
                continue

            side_credit_dollars = side_credit * 100 * condor.num_spreads
            if side_credit_dollars <= 0:
                continue

            # Trigger: side is losing and loss exceeds threshold
            if side_pnl < 0 and abs(side_pnl) >= trigger_mult * side_credit_dollars:
                reason = EXIT_IC_ROLL_PUT if side == "PUT" else EXIT_IC_ROLL_CALL
                self._log(
                    f"IC_ROLL_TRIGGER: side={side} loss=${side_pnl:.0f} >= "
                    f"{trigger_mult}x credit=${side_credit_dollars:.0f} | "
                    f"roll #{condor.roll_count + 1} | id={condor.condor_id}",
                    trades_only=True,
                )
                # Set rolling state (NOT is_closing — condor stays alive)
                condor.is_rolling = True
                condor.rolling_side = side
                condor.roll_pending_since = current_time.strftime("%Y-%m-%d %H:%M:%S")
                condor.roll_trigger_side_pnl_estimate = float(side_pnl or 0.0)

                return self._build_side_close(condor, side, reason, current_time)

        return None

    def _build_side_close(
        self,
        condor: IronCondorPosition,
        side: str,
        reason: str,
        current_time: datetime,
    ) -> Tuple[str, List[TargetWeight]]:
        """Build close signals for only one side (PUT or CALL) of the condor."""
        timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
        base_meta = {
            "options_lane": "IC",
            "options_strategy": "IRON_CONDOR",
            "trace_source": f"IC:{reason}",
            "trace_id": condor.condor_id,
            "condor_id": condor.condor_id,
            "exit_reason": reason,
            "transition_overlay": condor.entry_transition_overlay,
            "is_roll_close": True,
            "roll_side": side,
        }

        if side == "PUT":
            meta = dict(base_meta)
            meta["spread_side"] = "PUT_CREDIT_CLOSE"
            meta["spread_type"] = "CREDIT_PUT"
            meta["is_credit_spread"] = True
            meta["spread_close_short"] = True
            meta["spread_short_leg_symbol"] = condor.short_put.symbol
            meta["spread_short_leg_quantity"] = condor.num_spreads
            meta["spread_width"] = condor.put_wing_width
            signal = TargetWeight(
                symbol=condor.long_put.symbol,
                target_weight=0.0,
                source="OPT_IC",
                urgency=Urgency.IMMEDIATE,
                reason=f"IC_ROLL_CLOSE_PUT | {reason} | condor_id={condor.condor_id}",
                timestamp=timestamp,
                metadata=meta,
                requested_quantity=condor.num_spreads,
            )
        else:
            meta = dict(base_meta)
            meta["spread_side"] = "CALL_CREDIT_CLOSE"
            meta["spread_type"] = "CREDIT_CALL"
            meta["is_credit_spread"] = True
            meta["spread_close_short"] = True
            meta["spread_short_leg_symbol"] = condor.short_call.symbol
            meta["spread_short_leg_quantity"] = condor.num_spreads
            meta["spread_width"] = condor.call_wing_width
            signal = TargetWeight(
                symbol=condor.long_call.symbol,
                target_weight=0.0,
                source="OPT_IC",
                urgency=Urgency.IMMEDIATE,
                reason=f"IC_ROLL_CLOSE_CALL | {reason} | condor_id={condor.condor_id}",
                timestamp=timestamp,
                metadata=meta,
                requested_quantity=condor.num_spreads,
            )

        tracker_key = self._roll_close_tracker_key(condor.condor_id, side)
        tracker = SpreadFillTracker(
            long_leg_symbol=str(signal.symbol),
            short_leg_symbol=str(meta["spread_short_leg_symbol"]),
            expected_quantity=int(condor.num_spreads),
            timeout_minutes=int(getattr(config, "SPREAD_FILL_TIMEOUT_MINUTES", 5)),
            created_at=timestamp,
            spread_type=meta.get("spread_type"),
        )
        tracker._ic_side = tracker_key
        tracker._condor_id = condor.condor_id
        tracker._is_roll_close = True
        tracker._roll_side = side
        tracker._roll_close_entry_credit = float(
            condor.put_side_credit if side == "PUT" else condor.call_side_credit
        )
        self._side_fill_trackers[tracker_key] = tracker

        return (reason, [signal])

    def build_retry_close_signals(
        self,
        condor: IronCondorPosition,
        live_short_legs: List[str],
        current_time: datetime,
    ) -> List[TargetWeight]:
        """Build retry close signals for a stuck-closing condor.

        Called by the mixin when is_closing=True but legs remain live.
        Manages cooldown, attempt counting, and combo->sequential escalation.

        Args:
            condor: The IC position with is_closing=True.
            live_short_legs: List of leg attr names ("short_put", "short_call")
                that still have broker holdings and no pending orders.
            current_time: Current algorithm time.

        Returns:
            List of TargetWeight close signals (may be empty if cooldown active).
        """
        # 1. Cooldown check
        cooldown_min = float(getattr(config, "IC_CLOSE_RETRY_COOLDOWN_MIN", 5))
        if condor.last_close_signal_time:
            try:
                last_time = datetime.strptime(condor.last_close_signal_time, "%Y-%m-%d %H:%M:%S")
                elapsed_min = (current_time - last_time).total_seconds() / 60
                if elapsed_min < cooldown_min:
                    return []  # Too soon, wait
            except Exception:
                pass  # Unparseable timestamp — proceed with retry

        # 2. Increment attempt count
        condor.close_attempt_count += 1
        condor.last_close_signal_time = current_time.strftime("%Y-%m-%d %H:%M:%S")

        max_retries = int(getattr(config, "IC_CLOSE_MAX_RETRIES", 10))
        escalation = int(getattr(config, "IC_CLOSE_ESCALATION_THRESHOLD", 2))

        # 3. Max retries — abandon
        if condor.close_attempt_count > max_retries:
            self._log(
                f"IC_CLOSE_ABANDONED: attempt={condor.close_attempt_count} > max={max_retries} | "
                f"condor_id={condor.condor_id} | Clearing is_closing for manual intervention",
                trades_only=True,
            )
            condor.is_closing = False
            condor.close_attempt_count = 0
            condor.last_close_signal_time = None
            return []

        # 4. Determine escalation
        is_emergency = condor.close_attempt_count > escalation
        escalation_tag = "SEQUENTIAL" if is_emergency else "COMBO"

        self._log(
            f"IC_CLOSE_RETRY: attempt={condor.close_attempt_count} | mode={escalation_tag} | "
            f"condor_id={condor.condor_id} | live_legs={live_short_legs}",
            trades_only=True,
        )

        # 5. Build signals for live legs
        timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
        signals: List[TargetWeight] = []

        for leg_attr in live_short_legs:
            leg = getattr(condor, leg_attr, None)
            if leg is None:
                continue

            # Determine the paired long leg for combo close
            if leg_attr == "short_put":
                long_leg = condor.long_put
                side = "PUT_CREDIT_CLOSE"
                spread_type = "CREDIT_PUT"
                wing = condor.put_wing_width
            else:
                long_leg = condor.long_call
                side = "CALL_CREDIT_CLOSE"
                spread_type = "CREDIT_CALL"
                wing = condor.call_wing_width

            meta = {
                "options_lane": "IC",
                "options_strategy": "IRON_CONDOR",
                "trace_source": f"IC:ORPHAN_RETRY_{escalation_tag}",
                "trace_id": condor.condor_id,
                "condor_id": condor.condor_id,
                "spread_side": side,
                "spread_type": spread_type,
                "is_credit_spread": True,
                "spread_close_short": True,
                "spread_short_leg_symbol": leg.symbol,
                "spread_short_leg_quantity": condor.num_spreads,
                "spread_width": wing,
                "close_attempt": condor.close_attempt_count,
            }
            if is_emergency:
                meta["spread_exit_emergency"] = True

            signals.append(
                TargetWeight(
                    symbol=long_leg.symbol,
                    target_weight=0.0,
                    source="OPT_IC",
                    urgency=Urgency.IMMEDIATE,
                    reason=(
                        f"IC_CLOSE_RETRY_{escalation_tag} | attempt={condor.close_attempt_count} "
                        f"| {side} | condor_id={condor.condor_id}"
                    ),
                    timestamp=timestamp,
                    metadata=meta,
                    requested_quantity=condor.num_spreads,
                )
            )

        return signals

    # ══════════════════════════════════════════════════════════════════════
    # V12.37: ROLL FOLLOW-UP + REPLACEMENT
    # ══════════════════════════════════════════════════════════════════════

    def run_roll_follow_up(
        self,
        *,
        condor: IronCondorPosition,
        side_is_flat_func: Callable[[IronCondorPosition, str], bool],
        chain: Any,
        qqq_price: float,
        vix_current: float,
        current_time: datetime,
        effective_portfolio_value: float,
    ) -> Optional[List[TargetWeight]]:
        """Handle rolling condors after exit cycle.

        Called for each is_rolling condor. Checks if the closed side is flat
        (fills complete), then searches for a replacement spread.

        Per Gap C: if no replacement found, close remaining side immediately.
        """
        if not condor.is_rolling:
            return None

        if self._find_active_roll_close_tracker(condor.condor_id, condor.rolling_side) is not None:
            return None
        if self._find_active_roll_tracker(condor.condor_id, condor.rolling_side) is not None:
            return None

        # Stale timeout check
        stale_minutes = float(getattr(config, "IC_ROLL_PENDING_STALE_MINUTES", 10))
        if condor.roll_pending_since:
            try:
                roll_start = datetime.strptime(condor.roll_pending_since, "%Y-%m-%d %H:%M:%S")
                elapsed = (current_time - roll_start).total_seconds() / 60.0
                if elapsed > stale_minutes:
                    if side_is_flat_func(condor, condor.rolling_side):
                        self._log(
                            f"IC_ROLL_FINALIZED: stale timeout {elapsed:.0f}m > "
                            f"{stale_minutes:.0f}m | id={condor.condor_id}",
                            trades_only=True,
                        )
                        return self._finalize_side_close(condor, current_time)
                    self._log(
                        f"IC_ROLL_CLOSE_FAILED: stale timeout {elapsed:.0f}m > "
                        f"{stale_minutes:.0f}m before tested-side close filled | "
                        f"id={condor.condor_id}",
                        trades_only=True,
                    )
                    return self.handle_roll_close_failure(condor.condor_id, current_time)
            except Exception:
                pass

        # Check if the closed side is flat (fills complete)
        if not side_is_flat_func(condor, condor.rolling_side):
            return None  # Still waiting for side-close fills

        # Side is flat — search for replacement (V12.38: VIX gate)
        replacement_min_vix = float(getattr(config, "IC_ROLL_REPLACEMENT_MIN_VIX", 16))
        if vix_current < replacement_min_vix:
            self._log(
                f"IC_ROLL_SKIP_REPLACEMENT: VIX={vix_current:.1f} < {replacement_min_vix} | "
                f"finalizing side close only | id={condor.condor_id}",
                trades_only=True,
            )
            return self._finalize_side_close(condor, current_time)

        self._log(
            f"IC_ROLL_SIDE_FLAT: {condor.rolling_side} closed | "
            f"searching replacement | id={condor.condor_id}",
            trades_only=True,
        )

        replacement = self._search_replacement(
            condor=condor,
            chain=chain,
            qqq_price=qqq_price,
            vix_current=vix_current,
            current_time=current_time,
        )

        if replacement is None:
            self._log(
                f"IC_ROLL_NO_REPLACEMENT: no valid replacement for "
                f"{condor.rolling_side} | id={condor.condor_id}",
                trades_only=True,
            )
            self._record_drop(
                R_IC_ROLL_NO_REPLACEMENT,
                signal_id=condor.condor_id,
                trace_id=condor.condor_id,
                gate_name="IC_ROLL_REPLACEMENT",
                reason=f"side={condor.rolling_side}",
            )
            return self._finalize_side_close(condor, current_time)

        new_short, new_long, new_credit = replacement
        self._log(
            f"IC_ROLL_REPLACEMENT: {condor.rolling_side} | "
            f"K_short={new_short.strike} K_long={new_long.strike} | "
            f"credit={new_credit:.2f} | id={condor.condor_id}",
            trades_only=True,
        )
        self._emit_lifecycle(
            "APPROVED",
            signal_id=condor.condor_id,
            trace_id=condor.condor_id,
            code="IC_ROLL_REPLACEMENT",
            gate_name="IC_ROLL_REPLACEMENT",
            reason=(
                f"side={condor.rolling_side} | short={new_short.strike} "
                f"long={new_long.strike} | credit={new_credit:.2f}"
            ),
        )

        return self._build_roll_entry_signal(condor, new_short, new_long, new_credit, current_time)

    def _search_replacement(
        self,
        *,
        condor: IronCondorPosition,
        chain: Any,
        qqq_price: float,
        vix_current: float,
        current_time: datetime,
    ) -> Optional[Tuple[OptionContract, OptionContract, float]]:
        """Search for a replacement spread for the rolled side.

        Per Gap C: same-expiry only, skip EM/symmetry/daily-limit gates,
        apply liquidity + min credit recovery + further-OTM + strike reuse.
        """
        if chain is None:
            return None

        side = condor.rolling_side
        contracts = self._extract_chain_contracts(chain, qqq_price, current_time)
        if not contracts:
            return None

        # Get the original expiry and side credit for this side
        if side == "PUT":
            orig_expiry = condor.short_put.expiry
            orig_short_strike = condor.short_put.strike
            orig_side_credit = condor.put_side_credit
            direction = OptionDirection.PUT
        else:
            orig_expiry = condor.short_call.expiry
            orig_short_strike = condor.short_call.strike
            orig_side_credit = condor.call_side_credit
            direction = OptionDirection.CALL

        # Filter to same expiry, same direction, valid bid/ask
        min_oi = int(getattr(config, "IC_MIN_OPEN_INTEREST", 100))
        candidates = [
            c
            for c in contracts
            if c.direction == direction
            and c.expiry == orig_expiry
            and c.bid > 0
            and c.ask > c.bid
            and c.open_interest >= min_oi
        ]

        if not candidates:
            return None

        further_otm_pct = float(getattr(config, "IC_ROLL_FURTHER_OTM_MIN_PCT", 0.005))
        min_recovery = float(getattr(config, "IC_ROLL_MIN_CREDIT_RECOVERY_PCT", 0.50))
        min_credit = min_recovery * orig_side_credit

        wing_width = self._get_wing_width_for_vix(vix_current)
        max_leg_spread_pct = float(getattr(config, "IC_MAX_SPREAD_PCT", 0.30))
        max_combo_slippage = float(getattr(config, "IC_MAX_COMBO_SLIPPAGE", 0.15))

        # Collect all active strikes for strike reuse guard
        active_strikes = set()
        for pos in self._positions:
            if pos.is_closing:
                continue
            active_strikes.update(
                {
                    pos.short_put.strike,
                    pos.long_put.strike,
                    pos.short_call.strike,
                    pos.long_call.strike,
                }
            )

        best_result = None
        best_credit = -1.0

        for short_cand in candidates:
            # Further OTM constraint
            if side == "PUT":
                # New short put must be LOWER than original (further OTM for puts)
                min_distance = qqq_price * further_otm_pct
                if short_cand.strike >= orig_short_strike - min_distance:
                    continue
            else:
                # New short call must be HIGHER than original (further OTM for calls)
                min_distance = qqq_price * further_otm_pct
                if short_cand.strike <= orig_short_strike + min_distance:
                    continue

            # Strike reuse guard
            if short_cand.strike in active_strikes:
                continue

            # Find the protective wing
            long_cand = self._find_wing_leg(candidates, short_cand, wing_width, side, tolerance=1)
            if long_cand is None:
                continue

            # Keep replacement quality aligned with IC entry construction.
            if (
                short_cand.spread_pct > max_leg_spread_pct
                or long_cand.spread_pct > max_leg_spread_pct
            ):
                self._record_drop(R_IC_LIQUIDITY_FAIL)
                continue
            avg_spread_pct = (short_cand.spread_pct + long_cand.spread_pct) / 2.0
            if avg_spread_pct > max_combo_slippage:
                self._record_drop(R_IC_SLIPPAGE_FAIL)
                continue

            # Calculate credit
            new_credit = short_cand.mid_price - long_cand.mid_price
            if new_credit < min_credit:
                self._record_drop(R_IC_ROLL_CREDIT_INSUFFICIENT)
                continue

            # Pick highest credit replacement
            if new_credit > best_credit:
                best_credit = new_credit
                best_result = (short_cand, long_cand, new_credit)

        return best_result

    def _build_roll_entry_signal(
        self,
        condor: IronCondorPosition,
        new_short: OptionContract,
        new_long: OptionContract,
        new_credit: float,
        current_time: datetime,
    ) -> List[TargetWeight]:
        """Build entry signal for the replacement spread."""
        timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
        side = condor.rolling_side
        tracker_key = self._roll_tracker_key(condor.condor_id, side)

        spread_side = "PUT_CREDIT" if side == "PUT" else "CALL_CREDIT"
        spread_type = "CREDIT_PUT" if side == "PUT" else "CREDIT_CALL"
        wing_width = abs(new_short.strike - new_long.strike)

        meta = {
            "options_lane": "IC",
            "options_strategy": "IRON_CONDOR",
            "trace_source": f"IC:ROLL_ENTRY_{side}",
            "trace_id": condor.condor_id,
            "condor_id": condor.condor_id,
            "spread_side": spread_side,
            "spread_type": spread_type,
            "is_credit_spread": True,
            "spread_short_leg_symbol": new_short.symbol,
            "spread_short_leg_quantity": condor.num_spreads,
            "short_strike": new_short.strike,
            "long_strike": new_long.strike,
            "spread_width": wing_width,
            "spread_cost_or_credit": -new_credit,
            "transition_overlay": condor.entry_transition_overlay,
            "is_roll_entry": True,
            "roll_side": side,
            "roll_number": condor.roll_count + 1,
            "roll_tracker_key": tracker_key,
        }

        tracker = SpreadFillTracker(
            long_leg_symbol=new_long.symbol,
            short_leg_symbol=new_short.symbol,
            expected_quantity=int(condor.num_spreads),
            timeout_minutes=int(getattr(config, "SPREAD_FILL_TIMEOUT_MINUTES", 5)),
            created_at=timestamp,
            spread_type=spread_type,
        )
        tracker._ic_side = tracker_key
        tracker._condor_id = condor.condor_id
        tracker._is_roll_entry = True
        tracker._roll_side = side
        tracker._roll_new_short = new_short.to_dict()
        tracker._roll_new_long = new_long.to_dict()
        tracker._roll_new_credit = float(new_credit)
        tracker._roll_realized_pnl = float(condor.pending_roll_close_realized_pnl or 0.0)
        self._side_fill_trackers[tracker_key] = tracker

        signal = TargetWeight(
            symbol=new_long.symbol,
            target_weight=1.0,
            source="OPT_IC",
            urgency=Urgency.IMMEDIATE,
            reason=(
                f"IC_ROLL_ENTRY_{side} | K_short={new_short.strike} "
                f"K_long={new_long.strike} | credit={new_credit:.2f} "
                f"| roll #{condor.roll_count + 1} | condor_id={condor.condor_id}"
            ),
            timestamp=timestamp,
            metadata=meta,
            requested_quantity=condor.num_spreads,
        )

        return [signal]

    def _finalize_side_close(
        self, condor: IronCondorPosition, current_time: datetime
    ) -> List[TargetWeight]:
        """Finalize a rolled-off side: book P&L, increment roll_count, let surviving side ride.

        V12.38: Replaces _abandon_roll. Never destroys the profitable surviving side.
        The condor continues as a single-spread position. Campaign accounting
        (cumulative_realized_pnl + roll_count) ensures the exit cascade's combined_pnl
        path correctly includes the closed side's realized loss.
        """
        condor.is_rolling = False
        self._side_fill_trackers.pop(
            self._roll_tracker_key(condor.condor_id, condor.rolling_side), None
        )
        self._side_fill_trackers.pop(
            self._roll_close_tracker_key(condor.condor_id, condor.rolling_side), None
        )
        if condor.pending_roll_close_realized_pnl:
            condor.cumulative_realized_pnl += float(condor.pending_roll_close_realized_pnl)
            condor.pending_roll_close_realized_pnl = 0.0
        condor.roll_count += 1
        closed_side = condor.rolling_side
        condor.rolling_side = ""
        condor.roll_pending_since = None
        condor.roll_trigger_side_pnl_estimate = 0.0
        # NOT setting is_closing — surviving side rides

        self._emit_lifecycle(
            "ROLL_SIDE_FINALIZED",
            signal_id=condor.condor_id,
            trace_id=condor.condor_id,
            code="IC_ROLL_SIDE_FINALIZED",
            reason=f"closed {closed_side} side | surviving side rides | roll_count={condor.roll_count}",
        )

        return []

    def register_roll_close_fill(
        self,
        condor: IronCondorPosition,
        tracker: SpreadFillTracker,
        current_time: datetime,
    ) -> float:
        """Register actual tested-side close fill before replacement search."""
        side = str(condor.rolling_side or getattr(tracker, "_roll_side", "") or "").upper()
        entry_credit = float(getattr(tracker, "_roll_close_entry_credit", 0.0) or 0.0)
        close_debit = float(tracker.short_fill_price or 0.0) - float(tracker.long_fill_price or 0.0)
        realized_pnl = (entry_credit - close_debit) * 100.0 * int(condor.num_spreads)
        is_final_campaign_close = bool(condor.is_closing and not condor.is_rolling)

        condor.pending_roll_close_realized_pnl = float(realized_pnl)
        if side == "PUT":
            condor.put_side_active = False
        elif side == "CALL":
            condor.call_side_active = False
        condor.max_loss = self._calculate_open_structure_max_loss(condor)
        if is_final_campaign_close:
            condor.exit_pnl_estimate = float(condor.cumulative_realized_pnl + realized_pnl)

        self._log(
            f"IC_ROLL_CLOSE_FILL: side={side} | realized=${realized_pnl:.0f} | "
            f"close_debit={close_debit:.2f} | id={condor.condor_id}",
            trades_only=True,
        )
        self._emit_lifecycle(
            "ROLL_CLOSE_COMPLETE",
            signal_id=condor.condor_id,
            trace_id=condor.condor_id,
            code="IC_ROLL_CLOSE_FILL",
            reason=f"side={side} | realized=${realized_pnl:.0f}",
        )
        return realized_pnl

    def register_roll_fill(
        self,
        condor: IronCondorPosition,
        new_short: OptionContract,
        new_long: OptionContract,
        new_credit: float,
        realized_pnl: float,
        current_time: datetime,
    ) -> None:
        """Register a completed roll: update condor with replacement legs."""
        side = condor.rolling_side
        roll_record = RollRecord(
            roll_time=current_time.strftime("%Y-%m-%d %H:%M:%S"),
            rolled_side=side,
            closed_credit=(condor.put_side_credit if side == "PUT" else condor.call_side_credit),
            realized_pnl=realized_pnl,
            new_short_strike=new_short.strike,
            new_long_strike=new_long.strike,
            new_credit=new_credit,
            new_expiry=new_short.expiry,
            roll_reason=EXIT_IC_ROLL_PUT if side == "PUT" else EXIT_IC_ROLL_CALL,
        )

        # Update legs
        if side == "PUT":
            condor.short_put = new_short
            condor.long_put = new_long
            condor.put_side_credit = new_credit
            condor.put_side_active = True
            condor.put_wing_width = new_short.strike - new_long.strike
        else:
            condor.short_call = new_short
            condor.long_call = new_long
            condor.call_side_credit = new_credit
            condor.call_side_active = True
            condor.call_wing_width = new_long.strike - new_short.strike

        # Campaign accounting
        condor.roll_count += 1
        condor.cumulative_credit += new_credit
        condor.cumulative_realized_pnl += realized_pnl
        condor.roll_history.append(roll_record)

        # Clear rolling state
        condor.is_rolling = False
        condor.rolling_side = ""
        condor.roll_pending_since = None
        condor.roll_trigger_side_pnl_estimate = 0.0
        condor.pending_roll_close_realized_pnl = 0.0

        # Recalculate open-structure max loss from currently active sides only.
        condor.max_loss = self._calculate_open_structure_max_loss(condor)

        self._log(
            f"IC_ROLL_ENTRY_FILL: roll #{condor.roll_count} complete | "
            f"realized=${realized_pnl:.0f} | new_credit={new_credit:.2f} | "
            f"cum_credit={condor.cumulative_credit:.2f} | "
            f"id={condor.condor_id}",
            trades_only=True,
        )

        self._emit_lifecycle(
            "ROLL_COMPLETE",
            signal_id=condor.condor_id,
            trace_id=condor.condor_id,
            code="IC_ROLL_ENTRY_FILL",
            reason=f"roll #{condor.roll_count} | cum_credit={condor.cumulative_credit:.2f}",
        )

    # ══════════════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════

    def register_fill(self, condor: IronCondorPosition) -> None:
        """Register a filled iron condor position."""
        self._positions.append(condor)
        self._trades_today += 1
        self._pending_entry = False
        self._pending_condor_id = None
        self._pending_condor = None
        self._pending_fills.clear()
        self._pending_entry_since = None

    def _calculate_open_structure_max_loss(self, condor: IronCondorPosition) -> float:
        """Return open-position max loss from currently active sides only."""
        active_credit = 0.0
        active_widths = []
        if bool(getattr(condor, "put_side_active", True)):
            active_credit += float(getattr(condor, "put_side_credit", 0.0) or 0.0)
            active_widths.append(float(getattr(condor, "put_wing_width", 0.0) or 0.0))
        if bool(getattr(condor, "call_side_active", True)):
            active_credit += float(getattr(condor, "call_side_credit", 0.0) or 0.0)
            active_widths.append(float(getattr(condor, "call_wing_width", 0.0) or 0.0))
        if not active_widths:
            return 0.0
        return max(0.0, max(active_widths) - active_credit)

    def register_side_fill(self, spread_side: str) -> Optional[IronCondorPosition]:
        """Register one side of a pending IC combo fill.

        Args:
            spread_side: "PUT_CREDIT" or "CALL_CREDIT"

        Returns:
            The IronCondorPosition if both sides are now filled, else None.
        """
        if not self._pending_entry or self._pending_condor is None:
            self._log(
                f"IC_FILL_WARN: Side fill '{spread_side}' but no pending condor",
                trades_only=True,
            )
            return None

        self._pending_fills[spread_side] = True

        # Check if both sides filled
        if self._pending_fills.get("PUT_CREDIT") and self._pending_fills.get("CALL_CREDIT"):
            condor = self._pending_condor
            self.register_fill(condor)
            self._log(
                f"IC_FILLED | condor_id={condor.condor_id} "
                f"| put_short={condor.put_short_strike} call_short={condor.call_short_strike} "
                f"| credit=${condor.net_credit:.2f} | spreads={condor.num_spreads}",
                trades_only=True,
            )
            return condor

        self._log(
            f"IC_SIDE_FILL | side={spread_side} | condor_id={self._pending_condor_id} "
            f"| waiting for other side",
            trades_only=True,
        )
        return None

    def get_pending_spread_seeds(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """Return tracker seeds for both sides of the pending IC entry.

        Returns dict with keys 'PUT_CREDIT' and 'CALL_CREDIT', each containing
        {long_leg_symbol, short_leg_symbol, expected_quantity, spread_type, condor_id}.
        Returns None if no pending IC entry.
        """
        if not self._pending_entry or self._pending_condor is None:
            return None
        c = self._pending_condor
        return {
            "PUT_CREDIT": {
                "long_leg_symbol": c.long_put.symbol,
                "short_leg_symbol": c.short_put.symbol,
                "expected_quantity": c.num_spreads,
                "spread_type": "CREDIT_PUT",
                "condor_id": c.condor_id,
            },
            "CALL_CREDIT": {
                "long_leg_symbol": c.long_call.symbol,
                "short_leg_symbol": c.short_call.symbol,
                "expected_quantity": c.num_spreads,
                "spread_type": "CREDIT_CALL",
                "condor_id": c.condor_id,
            },
        }

    def cancel_pending_entry(self) -> None:
        """Cancel pending IC entry (e.g., on rejection or timeout)."""
        if self._pending_condor_id:
            self._log(
                f"IC_PENDING_CANCELLED | condor_id={self._pending_condor_id}",
                trades_only=True,
            )
        self._pending_entry = False
        self._pending_condor_id = None
        self._pending_condor = None
        self._pending_fills.clear()
        self._pending_entry_since = None

    def _roll_tracker_key(self, condor_id: str, side: str) -> str:
        """Return stable tracker key for a pending roll replacement entry."""
        return f"ROLL_{str(side or '').upper()}_{str(condor_id or '').upper()}"

    def _roll_close_tracker_key(self, condor_id: str, side: str) -> str:
        """Return stable tracker key for a pending tested-side close fill."""
        return f"ROLLCLOSE_{str(side or '').upper()}_{str(condor_id or '').upper()}"

    def _find_condor(self, condor_id: str) -> Optional[IronCondorPosition]:
        """Return live condor by ID when present."""
        condor_id_norm = str(condor_id or "").strip()
        if not condor_id_norm:
            return None
        for condor in self._positions:
            if condor.condor_id == condor_id_norm:
                return condor
        return None

    def _find_active_roll_tracker(self, condor_id: str, side: str) -> Optional[SpreadFillTracker]:
        """Return active fill tracker for a pending roll replacement entry."""
        tracker = self._side_fill_trackers.get(self._roll_tracker_key(condor_id, side))
        if tracker is None or not bool(getattr(tracker, "_is_roll_entry", False)):
            return None
        return tracker

    def _find_active_roll_close_tracker(
        self, condor_id: str, side: str
    ) -> Optional[SpreadFillTracker]:
        """Return active fill tracker for a pending tested-side roll close."""
        tracker = self._side_fill_trackers.get(self._roll_close_tracker_key(condor_id, side))
        if tracker is None or not bool(getattr(tracker, "_is_roll_close", False)):
            return None
        return tracker

    def get_fill_tracker_seed(self, *, fill_symbol: str = "") -> Optional[Dict[str, Any]]:
        """Return fill-tracker seed for IC initial entries or pending roll replacements."""
        fill_norm = self._norm(fill_symbol)
        for tracker_key, tracker in self._side_fill_trackers.items():
            tracker_symbols = {
                self._norm(getattr(tracker, "long_leg_symbol", "")),
                self._norm(getattr(tracker, "short_leg_symbol", "")),
            }
            if fill_norm and fill_norm not in tracker_symbols:
                continue
            return {
                "long_leg_symbol": tracker.long_leg_symbol,
                "short_leg_symbol": tracker.short_leg_symbol,
                "expected_quantity": int(getattr(tracker, "expected_quantity", 0) or 0),
                "spread_type": getattr(tracker, "spread_type", ""),
                "ic_side": str(getattr(tracker, "_ic_side", tracker_key) or tracker_key),
                "condor_id": str(getattr(tracker, "_condor_id", "") or ""),
                "is_roll_entry": bool(getattr(tracker, "_is_roll_entry", False)),
                "is_roll_close": bool(getattr(tracker, "_is_roll_close", False)),
                "roll_side": str(getattr(tracker, "_roll_side", "") or ""),
                "roll_new_short": getattr(tracker, "_roll_new_short", None),
                "roll_new_long": getattr(tracker, "_roll_new_long", None),
                "roll_new_credit": float(getattr(tracker, "_roll_new_credit", 0.0) or 0.0),
                "roll_realized_pnl": float(getattr(tracker, "_roll_realized_pnl", 0.0) or 0.0),
                "roll_close_entry_credit": float(
                    getattr(tracker, "_roll_close_entry_credit", 0.0) or 0.0
                ),
                "roll_tracker_key": tracker_key,
            }
        return None

    def handle_roll_fill_failure(
        self, condor_id: str, current_time: datetime
    ) -> List[TargetWeight]:
        """Finalize side close when replacement entry fill tracking fails.

        V12.38: No longer destroys the surviving side. The condor continues
        as a single-spread position.
        """
        condor = self._find_condor(condor_id)
        if condor is None or not condor.is_rolling:
            return []
        return self._finalize_side_close(condor, current_time)

    def handle_roll_close_failure(
        self, condor_id: str, current_time: datetime
    ) -> List[TargetWeight]:
        """Escalate a failed tested-side roll close into a full condor close."""
        condor = self._find_condor(condor_id)
        if condor is None or not condor.is_rolling:
            return []

        rolling_side = str(condor.rolling_side or "").upper()
        self._side_fill_trackers.pop(self._roll_close_tracker_key(condor_id, rolling_side), None)
        self._side_fill_trackers.pop(self._roll_tracker_key(condor_id, rolling_side), None)
        condor.is_rolling = False
        condor.rolling_side = ""
        condor.roll_pending_since = None
        condor.roll_trigger_side_pnl_estimate = 0.0
        condor.pending_roll_close_realized_pnl = 0.0
        condor.is_closing = True

        self._emit_lifecycle(
            "ROLL_CLOSE_FAILED",
            signal_id=condor.condor_id,
            trace_id=condor.condor_id,
            code="IC_ROLL_CLOSE_FAILED",
            reason="tested-side close failed; escalating to full condor close",
        )
        _, signals = self._build_exit(condor, "IC_ROLL_CLOSE_FAILURE", current_time)
        return signals

    # ══════════════════════════════════════════════════════════════════════
    # FILL TRACKING (V12.31 — extracted from main_orders_mixin)
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _norm(symbol) -> str:
        """Normalize symbol to uppercase string."""
        if symbol is None:
            return ""
        return str(symbol).strip().upper()

    def handle_leg_fill(
        self,
        symbol_norm: str,
        fill_price: float,
        fill_qty: int,
        current_time: str,
        seed: Dict[str, Any],
    ) -> Tuple[str, Optional[IronCondorPosition]]:
        """Handle an IC leg fill. Manages per-side trackers for interleaved fills.

        Returns:
            ("TRACKED", None)        — fill recorded, side not yet complete
            ("SIDE_COMPLETE", condor) — both sides filled, condor registered (or None if only one side done)
            ("TIMEOUT", None)        — tracker expired
            ("QTY_MISMATCH", None)   — fill quantities don't match expected
            ("UNKNOWN_SYMBOL", None)  — symbol doesn't match tracker legs
            ("ROLL_CLOSE_COMPLETE", condor) — tested side close filled and realized P&L booked
            ("ROLL_CLOSE_TIMEOUT", condor) — tested side close tracker expired
            ("ROLL_CLOSE_QTY_MISMATCH", condor) — tested side close fill quantities mismatched
            ("ROLL_COMPLETE", condor) — replacement spread filled and roll registered
            ("ROLL_TIMEOUT", condor) — replacement spread tracker expired
            ("ROLL_QTY_MISMATCH", condor) — replacement spread fill quantities mismatched
        """
        ic_side = str(seed.get("ic_side", "") or "").upper()
        is_roll_entry = bool(seed.get("is_roll_entry", False))
        is_roll_close = bool(seed.get("is_roll_close", False))
        tracker = self._side_fill_trackers.get(ic_side)

        if tracker is None:
            tracker = SpreadFillTracker(
                long_leg_symbol=seed["long_leg_symbol"],
                short_leg_symbol=seed["short_leg_symbol"],
                expected_quantity=int(seed["expected_quantity"]),
                timeout_minutes=int(getattr(config, "SPREAD_FILL_TIMEOUT_MINUTES", 5)),
                created_at=current_time,
                spread_type=seed.get("spread_type"),
            )
            tracker._ic_side = ic_side
            tracker._condor_id = seed.get("condor_id", "")
            tracker._is_roll_close = bool(seed.get("is_roll_close", False))
            tracker._roll_side = str(seed.get("roll_side", "") or "")
            tracker._roll_close_entry_credit = float(
                seed.get("roll_close_entry_credit", 0.0) or 0.0
            )
            self._side_fill_trackers[ic_side] = tracker
            self._log(
                f"IC: Side fill tracker created | Side={ic_side} | "
                f"Long={seed['long_leg_symbol'][-15:]} Short={seed['short_leg_symbol'][-15:]} "
                f"Expected={int(seed['expected_quantity'])}",
                trades_only=True,
            )

        # Check expiry
        if tracker.is_expired(current_time):
            self._log(
                f"IC_FILL_ERROR: Side tracker expired | Side={ic_side} | "
                f"Created={tracker.created_at} Current={current_time}",
                trades_only=True,
            )
            self._side_fill_trackers.pop(ic_side, None)
            if is_roll_close:
                return (
                    "ROLL_CLOSE_TIMEOUT",
                    self._find_condor(str(seed.get("condor_id", "") or "")),
                )
            if is_roll_entry:
                return ("ROLL_TIMEOUT", self._find_condor(str(seed.get("condor_id", "") or "")))
            self.cancel_pending_entry()
            return ("TIMEOUT", None)

        # Record fill
        long_norm = self._norm(tracker.long_leg_symbol)
        short_norm = self._norm(tracker.short_leg_symbol)
        if symbol_norm and symbol_norm == long_norm:
            tracker.record_long_fill(fill_price, fill_qty, current_time)
            self._log(
                f"IC: Long leg filled | Side={ic_side} | {symbol_norm[-20:]} @ ${fill_price:.2f} "
                f"x{fill_qty} | Total={tracker.long_fill_qty}",
                trades_only=True,
            )
        elif symbol_norm and symbol_norm == short_norm:
            tracker.record_short_fill(fill_price, fill_qty, current_time)
            self._log(
                f"IC: Short leg filled | Side={ic_side} | {symbol_norm[-20:]} @ ${fill_price:.2f} "
                f"x{fill_qty} | Total={tracker.short_fill_qty}",
                trades_only=True,
            )
        else:
            self._log(
                f"IC_FILL_WARNING: Unknown fill symbol for side tracker | Side={ic_side} | "
                f"Symbol={symbol_norm} | ExpectedLong={tracker.long_leg_symbol[-15:]} "
                f"ExpectedShort={tracker.short_leg_symbol[-15:]}",
                trades_only=True,
            )
            return ("UNKNOWN_SYMBOL", None)

        # Check completion
        if tracker.is_complete():
            if not tracker.quantities_match():
                self._log(
                    f"IC_FILL_ERROR: Quantity mismatch | Side={ic_side} | "
                    f"Long={tracker.long_fill_qty} Short={tracker.short_fill_qty} "
                    f"Expected={tracker.expected_quantity}",
                    trades_only=True,
                )
                self._side_fill_trackers.pop(ic_side, None)
                if is_roll_close:
                    return (
                        "ROLL_CLOSE_QTY_MISMATCH",
                        self._find_condor(str(seed.get("condor_id", "") or "")),
                    )
                if is_roll_entry:
                    return (
                        "ROLL_QTY_MISMATCH",
                        self._find_condor(str(seed.get("condor_id", "") or "")),
                    )
                self.cancel_pending_entry()
                return ("QTY_MISMATCH", None)

            if is_roll_close:
                condor = self._find_condor(str(seed.get("condor_id", "") or ""))
                if condor is None:
                    self._side_fill_trackers.pop(ic_side, None)
                    return ("ROLL_CLOSE_TIMEOUT", None)
                self.register_roll_close_fill(
                    condor=condor,
                    tracker=tracker,
                    current_time=datetime.fromisoformat(current_time),
                )
                self._side_fill_trackers.pop(ic_side, None)
                return ("ROLL_CLOSE_COMPLETE", condor)

            if is_roll_entry:
                condor = self._find_condor(str(seed.get("condor_id", "") or ""))
                if condor is None:
                    self._side_fill_trackers.pop(ic_side, None)
                    return ("ROLL_TIMEOUT", None)
                try:
                    new_short = OptionContract.from_dict(dict(seed.get("roll_new_short") or {}))
                    new_long = OptionContract.from_dict(dict(seed.get("roll_new_long") or {}))
                except Exception:
                    self._side_fill_trackers.pop(ic_side, None)
                    return ("ROLL_TIMEOUT", condor)
                self.register_roll_fill(
                    condor=condor,
                    new_short=new_short,
                    new_long=new_long,
                    new_credit=float(seed.get("roll_new_credit", 0.0) or 0.0),
                    realized_pnl=float(
                        seed.get("roll_realized_pnl", seed.get("roll_realized_pnl_estimate", 0.0))
                        or 0.0
                    ),
                    current_time=datetime.fromisoformat(current_time),
                )
                self._side_fill_trackers.pop(ic_side, None)
                return ("ROLL_COMPLETE", condor)

            condor = self.register_side_fill(ic_side)
            self._side_fill_trackers.pop(ic_side, None)
            if condor:
                return ("SIDE_COMPLETE", condor)
            return ("TRACKED", None)

        return ("TRACKED", None)

    def handle_rejection(self, symbol_norm: str) -> bool:
        """Check if rejected symbol belongs to pending IC entry and clean up.

        Returns True if IC handled the rejection, False otherwise.
        Does NOT call recover_pending_ic_unpaired_exposure — caller handles that.
        """
        seeds = self.get_pending_spread_seeds()
        if not seeds:
            return False

        ic_symbols = set()
        for seed in seeds.values():
            ic_symbols.add(self._norm(seed.get("long_leg_symbol", "")))
            ic_symbols.add(self._norm(seed.get("short_leg_symbol", "")))
        ic_symbols.discard("")

        if symbol_norm not in ic_symbols:
            return False

        self._side_fill_trackers.clear()
        self.cancel_pending_entry()
        return True

    def record_entry_rejection(self, current_time: datetime, is_insufficient_bp: bool) -> int:
        """Record an IC entry rejection and set appropriate cooldown.

        Args:
            current_time: Algorithm time at rejection.
            is_insufficient_bp: True if broker cited insufficient buying power.

        Returns:
            Cooldown duration in minutes (0 if no cooldown set).
        """
        if not is_insufficient_bp:
            # Non-BP rejections: clear streak, no special cooldown
            self._rejection_streak_count = 0
            self._rejection_streak_first_at = None
            return 0

        # Track streak within window
        window_min = max(1, int(getattr(config, "IC_REJECTION_STREAK_WINDOW_MINUTES", 30)))
        if (
            self._rejection_streak_first_at is not None
            and (current_time - self._rejection_streak_first_at).total_seconds() <= window_min * 60
        ):
            self._rejection_streak_count += 1
        else:
            # New streak
            self._rejection_streak_count = 1
            self._rejection_streak_first_at = current_time

        streak_max = max(1, int(getattr(config, "IC_REJECTION_STREAK_MAX", 3)))

        if self._rejection_streak_count >= streak_max:
            # Block for rest of day: set cooldown to midnight
            eod = current_time.replace(hour=23, minute=59, second=59)
            self._rejection_cooldown_until = eod
            self._log(
                f"IC_REJECTION_DAY_BLOCK: streak={self._rejection_streak_count} "
                f">= max={streak_max} | Blocking IC entries until EOD",
                trades_only=True,
            )
            return int((eod - current_time).total_seconds() / 60)
        elif self._rejection_streak_count > 1:
            cooldown = max(
                1,
                int(getattr(config, "IC_REJECTION_STREAK_COOLDOWN_MINUTES", 15)),
            )
            self._rejection_cooldown_until = current_time + timedelta(minutes=cooldown)
            self._log(
                f"IC_REJECTION_COOLDOWN: streak={self._rejection_streak_count} "
                f"| Cooldown={cooldown}min until {self._rejection_cooldown_until}",
                trades_only=True,
            )
            return cooldown
        else:
            cooldown = max(
                1,
                int(getattr(config, "IC_REJECTION_COOLDOWN_MINUTES", 5)),
            )
            self._rejection_cooldown_until = current_time + timedelta(minutes=cooldown)
            self._log(
                f"IC_REJECTION_COOLDOWN: first_reject | Cooldown={cooldown}min "
                f"until {self._rejection_cooldown_until}",
                trades_only=True,
            )
            return cooldown

    def is_fill_tracking_symbol(self, symbol_norm: str) -> bool:
        """Return True if symbol is being tracked by an IC side fill tracker."""
        for tracker in self._side_fill_trackers.values():
            tracker_symbols = {
                self._norm(getattr(tracker, "long_leg_symbol", "")),
                self._norm(getattr(tracker, "short_leg_symbol", "")),
            }
            if symbol_norm in tracker_symbols:
                return True
        return False

    def clear_fill_trackers(self) -> None:
        """Clear all side fill trackers (cleanup path)."""
        self._side_fill_trackers.clear()

    def has_active_fill_trackers(self) -> bool:
        """Return True if any side fill trackers are active."""
        return bool(self._side_fill_trackers)

    def remove_position(self, condor_id: str) -> Optional[IronCondorPosition]:
        """Remove a closed condor by ID. Returns removed position or None."""
        for i, p in enumerate(self._positions):
            if p.condor_id == condor_id:
                return self._positions.pop(i)
        return None

    def record_trade_result(self, pnl: float, current_date_str: Optional[str] = None) -> None:
        """Record trade result for loss breaker and diagnostics.

        Args:
            pnl: P&L of the closed trade.
            current_date_str: Algo date as YYYY-MM-DD (use self.Time.date() from caller).
                              Falls back to date.today() only as last resort.
        """
        self._daily_pnl += pnl
        self._diag_total_pnl += pnl

        if pnl >= 0:
            self._diag_wins += 1
            self._consecutive_losses = 0
        else:
            self._diag_losses += 1
            self._consecutive_losses += 1

            # Loss breaker check
            max_consecutive = int(getattr(config, "IC_LOSS_BREAKER_CONSECUTIVE", 3))
            if self._consecutive_losses >= max_consecutive:
                pause_days = int(getattr(config, "IC_LOSS_BREAKER_PAUSE_DAYS", 1))
                if current_date_str:
                    from datetime import date as _date_cls

                    try:
                        base = _date_cls.fromisoformat(current_date_str)
                    except Exception:
                        base = _date_cls.today()
                else:
                    from datetime import date as _date_cls

                    base = _date_cls.today()
                pause_date = base + timedelta(days=pause_days)
                self._loss_breaker_pause_until = pause_date.strftime("%Y-%m-%d")
                self._log(
                    f"LOSS_BREAKER_ACTIVE | consecutive={self._consecutive_losses} "
                    f"| pause_until={self._loss_breaker_pause_until}",
                    trades_only=True,
                )

    def get_open_risk(self) -> float:
        """Get total open IC risk in dollars (sum of max_loss * num_spreads * 100)."""
        return sum(p.max_loss * p.num_spreads * 100 for p in self._positions if not p.is_closing)

    @property
    def positions(self) -> List[IronCondorPosition]:
        return list(self._positions)

    @property
    def has_open_positions(self) -> bool:
        return any(not p.is_closing for p in self._positions)

    def clear_pending(self) -> None:
        """Clear pending entry state (e.g., on fill failure)."""
        self._pending_entry = False
        self._pending_condor_id = None
        self._pending_condor = None
        self._pending_fills.clear()
        self._pending_entry_since = None

    # ══════════════════════════════════════════════════════════════════════
    # DIAGNOSTICS
    # ══════════════════════════════════════════════════════════════════════

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return IC engine diagnostics for daily summary."""
        return {
            "candidates": self._diag_candidates,
            "approved": self._diag_approved,
            "dropped": self._diag_dropped,
            "drop_codes": dict(self._diag_drop_codes),
            "exit_reasons": dict(self._diag_exit_reasons),
            "wins": self._diag_wins,
            "losses": self._diag_losses,
            "total_pnl": round(self._diag_total_pnl, 2),
            "open_positions": len([p for p in self._positions if not p.is_closing]),
            "open_risk": round(self.get_open_risk(), 2),
            "trades_today": self._trades_today,
            "consecutive_losses": self._consecutive_losses,
            "loss_breaker_active": self._loss_breaker_pause_until is not None,
        }

    # ══════════════════════════════════════════════════════════════════════
    # STATE PERSISTENCE
    # ══════════════════════════════════════════════════════════════════════

    def to_dict(self) -> Dict[str, Any]:
        return {
            "positions": [p.to_dict() for p in self._positions],
            "trades_today": self._trades_today,
            "daily_pnl": self._daily_pnl,
            "consecutive_losses": self._consecutive_losses,
            "loss_breaker_pause_until": self._loss_breaker_pause_until,
            "pending_entry": self._pending_entry,
            "pending_condor_id": self._pending_condor_id,
            "pending_condor": self._pending_condor.to_dict() if self._pending_condor else None,
            "pending_fills": dict(self._pending_fills),
            "pending_entry_since": str(self._pending_entry_since)
            if self._pending_entry_since
            else None,
            "regime_neutral_days": self._regime_neutral_days,
            "regime_neutral_last_date": self._regime_neutral_last_date,
            "hold_guard_logged": list(self._hold_guard_logged),
            "diag_candidates": self._diag_candidates,
            "diag_approved": self._diag_approved,
            "diag_dropped": self._diag_dropped,
            "diag_drop_codes": dict(self._diag_drop_codes),
            "diag_exit_reasons": dict(self._diag_exit_reasons),
            "diag_wins": self._diag_wins,
            "diag_losses": self._diag_losses,
            "diag_total_pnl": self._diag_total_pnl,
            "rejection_cooldown_until": str(self._rejection_cooldown_until)
            if self._rejection_cooldown_until
            else None,
            "rejection_streak_count": self._rejection_streak_count,
            "rejection_streak_first_at": str(self._rejection_streak_first_at)
            if self._rejection_streak_first_at
            else None,
            "regime_score_history": list(self._regime_score_history),
            "last_scan_time": self._last_scan_time,
            # V12.36: Persist in-progress side fill trackers across restarts
            "side_fill_trackers": {
                side: {
                    "long_leg_symbol": t.long_leg_symbol,
                    "short_leg_symbol": t.short_leg_symbol,
                    "expected_quantity": t.expected_quantity,
                    "timeout_minutes": t.timeout_minutes,
                    "long_fill_price": t.long_fill_price,
                    "long_fill_qty": t.long_fill_qty,
                    "long_fill_time": t.long_fill_time,
                    "short_fill_price": t.short_fill_price,
                    "short_fill_qty": t.short_fill_qty,
                    "short_fill_time": t.short_fill_time,
                    "created_at": t.created_at,
                    "spread_type": t.spread_type,
                    "ic_side": getattr(t, "_ic_side", side),
                    "condor_id": getattr(t, "_condor_id", ""),
                    "is_roll_entry": bool(getattr(t, "_is_roll_entry", False)),
                    "is_roll_close": bool(getattr(t, "_is_roll_close", False)),
                    "roll_side": getattr(t, "_roll_side", ""),
                    "roll_new_short": getattr(t, "_roll_new_short", None),
                    "roll_new_long": getattr(t, "_roll_new_long", None),
                    "roll_new_credit": getattr(t, "_roll_new_credit", 0.0),
                    "roll_realized_pnl": getattr(t, "_roll_realized_pnl", 0.0),
                    "roll_close_entry_credit": getattr(t, "_roll_close_entry_credit", 0.0),
                }
                for side, t in self._side_fill_trackers.items()
            },
        }

    def from_dict(self, state: Dict[str, Any]) -> None:
        if not state:
            return
        # Positions
        self._positions = []
        for row in state.get("positions", []) or []:
            try:
                self._positions.append(IronCondorPosition.from_dict(row))
            except Exception:
                continue
        # Counters
        self._trades_today = int(state.get("trades_today", 0) or 0)
        self._daily_pnl = float(state.get("daily_pnl", 0.0) or 0)
        self._consecutive_losses = int(state.get("consecutive_losses", 0) or 0)
        self._loss_breaker_pause_until = state.get("loss_breaker_pause_until")
        self._pending_entry = bool(state.get("pending_entry", False))
        self._pending_condor_id = state.get("pending_condor_id")
        pending_condor_data = state.get("pending_condor")
        if pending_condor_data:
            try:
                self._pending_condor = IronCondorPosition.from_dict(pending_condor_data)
            except Exception:
                self._pending_condor = None
        else:
            self._pending_condor = None
        self._pending_fills = dict(state.get("pending_fills", {}) or {})
        _since_str = state.get("pending_entry_since")
        if _since_str:
            try:
                self._pending_entry_since = datetime.fromisoformat(str(_since_str))
            except Exception:
                self._pending_entry_since = None
        else:
            self._pending_entry_since = None
        self._regime_neutral_days = int(state.get("regime_neutral_days", 0) or 0)
        self._regime_neutral_last_date = state.get("regime_neutral_last_date")
        self._hold_guard_logged = set(state.get("hold_guard_logged", []) or [])
        # Diagnostics
        self._diag_candidates = int(state.get("diag_candidates", 0) or 0)
        self._diag_approved = int(state.get("diag_approved", 0) or 0)
        self._diag_dropped = int(state.get("diag_dropped", 0) or 0)
        self._diag_drop_codes = dict(state.get("diag_drop_codes", {}) or {})
        self._diag_exit_reasons = dict(state.get("diag_exit_reasons", {}) or {})
        self._diag_wins = int(state.get("diag_wins", 0) or 0)
        self._diag_losses = int(state.get("diag_losses", 0) or 0)
        self._diag_total_pnl = float(state.get("diag_total_pnl", 0.0) or 0)
        # Rejection recovery state
        _rcu = state.get("rejection_cooldown_until")
        try:
            self._rejection_cooldown_until = datetime.fromisoformat(str(_rcu)) if _rcu else None
        except Exception:
            self._rejection_cooldown_until = None
        self._rejection_streak_count = int(state.get("rejection_streak_count", 0) or 0)
        _rsfa = state.get("rejection_streak_first_at")
        try:
            self._rejection_streak_first_at = datetime.fromisoformat(str(_rsfa)) if _rsfa else None
        except Exception:
            self._rejection_streak_first_at = None
        # V12.36: Restore regime velocity history and scan throttle
        raw_history = state.get("regime_score_history", [])
        self._regime_score_history = []
        for item in raw_history or []:
            try:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    self._regime_score_history.append((str(item[0]), float(item[1])))
            except Exception:
                continue
        self._last_scan_time = state.get("last_scan_time")
        # V12.36: Restore in-progress side fill trackers
        self._side_fill_trackers = {}
        for side, tdata in (state.get("side_fill_trackers") or {}).items():
            try:
                tracker = SpreadFillTracker(
                    long_leg_symbol=str(tdata.get("long_leg_symbol", "")),
                    short_leg_symbol=str(tdata.get("short_leg_symbol", "")),
                    expected_quantity=int(tdata.get("expected_quantity", 0)),
                    timeout_minutes=int(tdata.get("timeout_minutes", 5)),
                    created_at=tdata.get("created_at"),
                    spread_type=tdata.get("spread_type"),
                )
                if tdata.get("long_fill_price") is not None:
                    tracker.long_fill_price = float(tdata["long_fill_price"])
                    tracker.long_fill_qty = int(tdata.get("long_fill_qty", 0))
                    tracker.long_fill_time = tdata.get("long_fill_time")
                if tdata.get("short_fill_price") is not None:
                    tracker.short_fill_price = float(tdata["short_fill_price"])
                    tracker.short_fill_qty = int(tdata.get("short_fill_qty", 0))
                    tracker.short_fill_time = tdata.get("short_fill_time")
                tracker._ic_side = str(tdata.get("ic_side", side))
                tracker._condor_id = str(tdata.get("condor_id", ""))
                tracker._is_roll_entry = bool(tdata.get("is_roll_entry", False))
                tracker._is_roll_close = bool(tdata.get("is_roll_close", False))
                tracker._roll_side = str(tdata.get("roll_side", "") or "")
                tracker._roll_new_short = tdata.get("roll_new_short")
                tracker._roll_new_long = tdata.get("roll_new_long")
                tracker._roll_new_credit = float(tdata.get("roll_new_credit", 0.0) or 0.0)
                tracker._roll_realized_pnl = float(
                    tdata.get(
                        "roll_realized_pnl",
                        tdata.get("roll_realized_pnl_estimate", 0.0),
                    )
                    or 0.0
                )
                tracker._roll_close_entry_credit = float(
                    tdata.get("roll_close_entry_credit", 0.0) or 0.0
                )
                self._side_fill_trackers[side] = tracker
            except Exception:
                continue

    def reset_daily(self) -> None:
        """Reset intraday state at start of new trading day."""
        # Log daily IC diagnostic summary before reset
        if self._diag_candidates > 0 or self._diag_approved > 0:
            top_drops = sorted(self._diag_drop_codes.items(), key=lambda x: -x[1])[:3]
            drop_str = ", ".join(f"{k}={v}" for k, v in top_drops) if top_drops else "none"
            self._log(
                f"DAILY_DIAG | scans={self._diag_candidates} "
                f"approved={self._diag_approved} dropped={self._diag_dropped} "
                f"| top_drops=[{drop_str}]",
                trades_only=True,
            )
        self._trades_today = 0
        self._daily_pnl = 0.0
        self._pending_entry = False
        self._pending_condor_id = None
        self._pending_condor = None
        self._pending_fills.clear()
        self._pending_entry_since = None
        # NOTE: _regime_neutral_days and _regime_neutral_last_date are intentionally
        # NOT reset here — they are cross-day counters that must accumulate across
        # consecutive neutral-regime days to satisfy IC_REGIME_PERSISTENCE_DAYS.
        # Resetting them here would prevent persistence from ever being satisfied.
        self._last_scan_time = None
        self._hold_guard_logged = set()
        self._side_fill_trackers = {}
        # Reset rejection recovery state (fresh day, fresh margin)
        self._rejection_cooldown_until = None
        self._rejection_streak_count = 0
        self._rejection_streak_first_at = None
        # Reset daily diagnostics
        self._diag_candidates = 0
        self._diag_approved = 0
        self._diag_dropped = 0
        self._diag_drop_codes = {}

    def reset(self) -> None:
        """Full state reset."""
        self._positions = []
        self._trades_today = 0
        self._daily_pnl = 0.0
        self._consecutive_losses = 0
        self._loss_breaker_pause_until = None
        self._pending_entry = False
        self._pending_condor_id = None
        self._pending_condor = None
        self._pending_fills = {}
        self._pending_entry_since = None
        self._regime_neutral_days = 0
        self._regime_neutral_last_date = None
        self._regime_score_history = []
        self._hold_guard_logged = set()
        self._side_fill_trackers = {}
        self._rejection_cooldown_until = None
        self._rejection_streak_count = 0
        self._rejection_streak_first_at = None
        self._diag_candidates = 0
        self._diag_approved = 0
        self._diag_dropped = 0
        self._diag_drop_codes = {}
        self._diag_exit_reasons = {}
        self._diag_wins = 0
        self._diag_losses = 0
        self._diag_total_pnl = 0.0
