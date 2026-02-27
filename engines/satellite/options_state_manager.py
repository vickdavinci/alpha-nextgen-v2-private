"""Options state and greeks lifecycle helpers extracted from options_engine."""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from engines.core.risk_engine import GreeksSnapshot
from engines.satellite.iv_sensor import VIXSnapshot
from engines.satellite.options_primitives import MicroRegimeState, OptionsPosition, SpreadPosition
from models.enums import OptionsMode


def calculate_position_greeks_impl(self) -> Optional[GreeksSnapshot]:
    """
    Calculate Greeks for current position.

    Returns per-contract Greeks for risk limit checking.
    Risk limits are per-contract (e.g., delta 0.80 = too deep ITM).
    Theta is normalized to percentage of position value for threshold comparison.

    Returns:
        GreeksSnapshot for risk engine, or None if no position.
    """
    if self._position is None:
        return None

    contract = self._position.contract

    # Calculate position value for theta normalization
    # Position value = num_contracts × mid_price × 100 (shares per contract)
    position_value = self._position.num_contracts * contract.mid_price * 100
    if position_value <= 0:
        # Fallback to entry price if mid_price not available
        position_value = self._position.num_contracts * self._position.entry_price * 100

    # Normalize theta to percentage of position value
    # Raw theta is in dollars/day, threshold CB_THETA_WARNING=-0.02 means -2%/day max
    # Total theta = per-contract theta × num_contracts
    total_theta_dollars = contract.theta * self._position.num_contracts
    theta_pct = total_theta_dollars / position_value if position_value > 0 else 0.0

    # V2.3 FIX: Skip theta check for swing mode (5-45 DTE)
    # Swing mode options naturally have higher theta decay but more time to recover.
    # Only enforce theta limits for intraday mode (0-2 DTE) where decay matters critically.
    if not config.CB_THETA_SWING_CHECK_ENABLED and contract.days_to_expiry > 2:
        theta_pct = 0.0  # Set to 0 to pass theta check

    # Return per-contract Greeks for delta/gamma/vega, normalized theta for percentage check
    return GreeksSnapshot(
        delta=contract.delta,
        gamma=contract.gamma,
        vega=contract.vega,
        theta=theta_pct,  # Now expressed as percentage (e.g., -0.01 = -1%/day)
    )


def update_position_greeks_impl(
    self,
    delta: float,
    gamma: float,
    vega: float,
    theta: float,
) -> None:
    """
    Update Greeks on current position's contract.

    Called when new Greeks data is received from broker/data feed.

    Args:
        delta: Current delta (-1 to +1 for puts/calls).
        gamma: Current gamma.
        vega: Current vega.
        theta: Current theta (daily decay, typically negative).
    """
    if self._position is None:
        return

    # Update the contract's Greeks
    self._position.contract.delta = delta
    self._position.contract.gamma = gamma
    self._position.contract.vega = vega
    self._position.contract.theta = theta

    self.log(f"OPT: Greeks updated | " f"D={delta:.3f} G={gamma:.4f} V={vega:.3f} T={theta:.4f}")


def check_greeks_breach_impl(
    self,
    risk_engine: "RiskEngine",
) -> Tuple[bool, List[str]]:
    """
    Check if current position Greeks breach risk limits.

    Updates risk engine with current Greeks and checks for breach.

    Args:
        risk_engine: Risk engine instance.

    Returns:
        Tuple of (is_breach, list of symbols to close).
    """
    greeks = self.calculate_position_greeks()

    if greeks is None:
        # No position, clear risk engine Greeks state
        risk_engine.update_greeks(GreeksSnapshot())
        return False, []

    # Update risk engine with current Greeks
    risk_engine.update_greeks(greeks)

    # Check for breach
    is_breach, options_to_close = risk_engine.check_cb_greeks_breach()

    if is_breach:
        self.log(
            f"OPT: GREEKS_BREACH | "
            f"D={greeks.delta:.2f} G={greeks.gamma:.4f} "
            f"V={greeks.vega:.2f} T={greeks.theta:.4f}"
        )

    return is_breach, options_to_close


# =========================================================================
# STATE PERSISTENCE
# =========================================================================


def get_state_for_persistence_impl(self) -> Dict[str, Any]:
    """Get state for ObjectStore."""
    primary_spread = self.get_spread_position()
    return {
        # Legacy position (backwards compatibility)
        "position": self._position.to_dict() if self._position else None,
        "trades_today": self._trades_today,
        "last_trade_date": self._last_trade_date,
        # V2.1.1 dual-mode state
        # Mirror canonical swing position to avoid dual-key drift.
        "swing_position": (self._position.to_dict() if self._position else None),
        "intraday_position": (
            self._intraday_position.to_dict() if self._intraday_position else None
        ),
        "intraday_positions": {
            k: [p.to_dict() for p in (v or []) if p is not None]
            for k, v in self._intraday_positions.items()
        },
        "intraday_position_engine": self._intraday_position_engine,
        "intraday_trades_today": self._intraday_trades_today,
        "intraday_call_trades_today": self._intraday_call_trades_today,
        "intraday_put_trades_today": self._intraday_put_trades_today,
        "intraday_itm_trades_today": self._intraday_itm_trades_today,
        "intraday_micro_trades_today": self._intraday_micro_trades_today,
        "swing_trades_today": self._swing_trades_today,
        "total_options_trades_today": self._total_options_trades_today,
        "current_mode": self._current_mode.value,
        "micro_regime_state": self._micro_regime_engine.get_state().to_dict(),
        "micro_regime_runtime_state": {
            "vix_history": [
                {
                    "timestamp": snap.timestamp,
                    "value": float(snap.value),
                    "change_from_open_pct": float(snap.change_from_open_pct),
                }
                for snap in list(self._micro_regime_engine._vix_history)
            ],
            "vix_15min_ago": float(self._micro_regime_engine._vix_15min_ago),
            "vix_30min_ago": float(self._micro_regime_engine._vix_30min_ago),
            "qqq_open": float(self._micro_regime_engine._qqq_open),
        },
        # V2.16-BT: Persist spread position for multi-day backtests
        # Mirror primary from canonical spread list to avoid dual-key drift.
        "spread_position": (primary_spread.to_dict() if primary_spread else None),
        "spread_positions": [s.to_dict() for s in self.get_spread_positions()],
        # Market open data
        "vix_at_open": self._vix_at_open,
        "spy_at_open": self._spy_at_open,
        "spy_gap_pct": self._spy_gap_pct,
        # Runtime guard/cooldown state (restart-safe)
        "rejection_margin_cap": self._rejection_margin_cap,
        "spread_failure_cooldown_until": self._spread_failure_cooldown_until,
        "spread_failure_cooldown_until_by_dir": {
            str(k.value if hasattr(k, "value") else k).upper(): str(v)
            for k, v in self._spread_failure_cooldown_until_by_dir.items()
        },
        "spread_exit_signal_cooldown": {
            k: v.strftime("%Y-%m-%d %H:%M:%S") for k, v in self._spread_exit_signal_cooldown.items()
        },
        "gamma_pin_exit_triggered": bool(self._gamma_pin_exit_triggered),
        "last_spread_exit_time": self._last_spread_exit_time,
        "spread_attempt_last_mark_by_key": dict(self._spread_attempt_last_mark_by_key),
        # V2.27: Win Rate Gate state
        "spread_result_history": self._spread_result_history,
        "win_rate_shutoff": self._win_rate_shutoff,
        "win_rate_shutoff_date": self._win_rate_shutoff_date,
        "paper_track_history": self._paper_track_history,
        "vass_entry_state": self._vass_entry_engine.to_dict(),
        "spread_neutrality_warn_by_key": {
            k: v.strftime("%Y-%m-%d %H:%M:%S")
            for k, v in self._spread_neutrality_warn_by_key.items()
        },
        "last_intraday_close_time": (
            self._last_intraday_close_time.strftime("%Y-%m-%d %H:%M:%S")
            if self._last_intraday_close_time is not None
            else None
        ),
        "last_intraday_close_strategy": self._last_intraday_close_strategy,
        "pending_intraday_entry_engine": self._pending_intraday_entry_engine,
        "pending_intraday_exit_engine": self._pending_intraday_exit_engine,
        "pending_intraday_entries": {
            k: {
                "symbol": v.get("symbol"),
                "lane": v.get("lane"),
                "entry_score": v.get("entry_score"),
                "num_contracts": v.get("num_contracts"),
                "entry_strategy": v.get("entry_strategy"),
                "stop_pct": v.get("stop_pct"),
                "created_at": v.get("created_at"),
            }
            for k, v in self._pending_intraday_entries.items()
        },
        "pending_intraday_exit_lanes": list(self._pending_intraday_exit_lanes),
        "pending_intraday_exit_symbols": list(self._pending_intraday_exit_symbols),
        "call_consecutive_losses": self._call_consecutive_losses,
        "call_cooldown_until_date": (
            self._call_cooldown_until_date.isoformat()
            if self._call_cooldown_until_date is not None
            else None
        ),
        "put_consecutive_losses": self._put_consecutive_losses,
        "put_cooldown_until_date": (
            self._put_cooldown_until_date.isoformat()
            if self._put_cooldown_until_date is not None
            else None
        ),
        "itm_horizon_state": self._itm_horizon_engine.to_dict(),
    }


def restore_state_impl(self, state: Dict[str, Any]) -> None:
    """
    Restore state from ObjectStore.

    Default behavior clears stale intraday positions on restore.
    V10.1 allows restoring ITM_MOMENTUM intraday positions when hold policy
    explicitly permits overnight carry.
    """
    # V2.16-BT: Get current date for expiry validation (defensive for tests)
    algorithm = getattr(self, "algorithm", None) or getattr(self, "_algorithm", None)
    if algorithm and hasattr(algorithm, "Time"):
        current_date = algorithm.Time.strftime("%Y-%m-%d")
    else:
        # Fallback for tests where _algorithm not initialized
        # Use far-past date so positions in tests are never considered expired
        current_date = "2020-01-01"

    # Legacy position (backwards compatibility)
    position_data = state.get("position")
    if position_data:
        position = OptionsPosition.from_dict(position_data)
        # V2.16-BT: Validate legacy position hasn't expired
        contract_expiry = position.contract.expiry if position.contract else None
        if contract_expiry and contract_expiry < current_date:
            self.log(
                f"OPT: ZOMBIE_CLEAR - Legacy position expired {contract_expiry} < {current_date}. "
                "Clearing stale position."
            )
            self._position = None
        else:
            self._position = position
    else:
        self._position = None

    self._trades_today = state.get("trades_today", 0)
    self._last_trade_date = state.get("last_trade_date")

    # V2.1.1 dual-mode state
    swing_data = state.get("swing_position")
    if swing_data:
        position = OptionsPosition.from_dict(swing_data)
        # V2.16-BT: Validate swing position hasn't expired
        contract_expiry = position.contract.expiry if position.contract else None
        if contract_expiry and contract_expiry < current_date:
            self.log(
                f"OPT: ZOMBIE_CLEAR - Swing position expired {contract_expiry} < {current_date}. "
                "Clearing stale position."
            )
            self._swing_position = None
        else:
            self._swing_position = position
    else:
        self._swing_position = None

    # Canonicalize legacy dual swing keys to prevent slot/state drift.
    if self._position is None and self._swing_position is not None:
        self._position = self._swing_position
    elif self._position is not None and self._swing_position is None:
        self._swing_position = self._position

    intraday_data = state.get("intraday_position")
    if intraday_data:
        position = OptionsPosition.from_dict(intraday_data)
        contract_expiry = position.contract.expiry if position.contract else None
        if contract_expiry and contract_expiry < current_date:
            self.log(
                f"OPT: ZOMBIE_CLEAR - Intraday position expired {contract_expiry} < {current_date}. "
                "Clearing stale position."
            )
            self._intraday_position = None
            self._intraday_position_engine = None
        elif self.should_hold_intraday_overnight(position):
            self._intraday_position = position
            self._intraday_position_engine = self._engine_lane_from_strategy(
                position.entry_strategy
            )
            live_dte = self._get_position_live_dte(position)
            self.log(
                f"OPT: STATE_RESTORE - Hold-enabled intraday position restored | "
                f"Strategy={position.entry_strategy} | DTE={live_dte}"
            )
        else:
            force_hh, force_mm = self._get_intraday_force_exit_hhmm()
            self.log(
                "OPT: STATE_RESTORE - Clearing intraday position (non-hold strategy/policy) | "
                f"Cutoff={force_hh:02d}:{force_mm:02d}"
            )
            self._intraday_position = None
            self._intraday_position_engine = None
    else:
        self._intraday_position = None
        self._intraday_position_engine = None

    def _should_restore_intraday_position(
        position: Optional[OptionsPosition], lane_hint: str
    ) -> bool:
        if position is None or position.contract is None:
            return False
        contract_expiry = position.contract.expiry if position.contract else None
        if contract_expiry and contract_expiry < current_date:
            self.log(
                f"OPT: ZOMBIE_CLEAR - Intraday position expired {contract_expiry} < {current_date}. "
                f"Clearing stale position | Lane={lane_hint}"
            )
            return False
        if self.should_hold_intraday_overnight(position):
            return True
        force_hh, force_mm = self._get_intraday_force_exit_hhmm()
        self.log(
            "OPT: STATE_RESTORE - Clearing intraday lane position (non-hold strategy/policy) | "
            f"Lane={lane_hint} | Cutoff={force_hh:02d}:{force_mm:02d}"
        )
        return False

    intraday_positions_data = state.get("intraday_positions") or {}
    if isinstance(intraday_positions_data, dict) and intraday_positions_data:
        self._intraday_positions = {"MICRO": [], "ITM": []}
        for lane in ("MICRO", "ITM"):
            row = intraday_positions_data.get(lane)
            if isinstance(row, list):
                restored = []
                for item in row:
                    if not item:
                        continue
                    try:
                        pos = OptionsPosition.from_dict(item)
                        if _should_restore_intraday_position(pos, lane):
                            restored.append(pos)
                    except Exception:
                        continue
                self._intraday_positions[lane] = restored
            elif isinstance(row, dict):
                # Backward compatibility: single-position payload.
                try:
                    pos = OptionsPosition.from_dict(row)
                    if _should_restore_intraday_position(pos, lane):
                        self._intraday_positions[lane] = [pos]
                    else:
                        self._intraday_positions[lane] = []
                except Exception:
                    self._intraday_positions[lane] = []
        self._refresh_legacy_intraday_mirrors()
    else:
        self._intraday_positions = {"MICRO": [], "ITM": []}
        if self._intraday_position is not None:
            lane = self._engine_lane_from_strategy(
                getattr(self._intraday_position, "entry_strategy", "")
            )
            self._intraday_positions[lane] = [self._intraday_position]

    self._intraday_position_engine = (
        state.get("intraday_position_engine") or self._intraday_position_engine
    )
    if self._intraday_position is None:
        self._refresh_legacy_intraday_mirrors()

    self._pending_intraday_entry_engine = state.get("pending_intraday_entry_engine")
    self._pending_intraday_exit_engine = state.get("pending_intraday_exit_engine")
    self._pending_intraday_entries = {}
    for row_key, row in (state.get("pending_intraday_entries") or {}).items():
        if isinstance(row, dict):
            lane = str(row.get("lane") or "").upper()
            if lane not in ("MICRO", "ITM") and "|" in str(row_key):
                lane = str(row_key).split("|", 1)[0].upper()
            symbol_norm = self._symbol_key(row.get("symbol") or "")
            if not symbol_norm:
                symbol_norm = self._pending_intraday_symbol_from_key(str(row_key))
            if not symbol_norm:
                symbol_norm = self._symbol_key(row_key)
            if not symbol_norm:
                continue
            key = self._pending_intraday_entry_key(symbol=symbol_norm, lane=lane)
            self._pending_intraday_entries[key] = {
                "symbol": symbol_norm,
                "lane": lane,
                "entry_score": row.get("entry_score"),
                "num_contracts": row.get("num_contracts"),
                "entry_strategy": row.get("entry_strategy"),
                "stop_pct": row.get("stop_pct"),
                "created_at": row.get("created_at"),
            }
    self._pending_intraday_exit_lanes = set(
        str(x).upper() for x in (state.get("pending_intraday_exit_lanes") or []) if x
    )
    self._pending_intraday_exit_symbols = set(
        self._symbol_str(x) for x in (state.get("pending_intraday_exit_symbols") or []) if x
    )
    self._pending_intraday_exit_symbols.discard("")
    self._sync_pending_engine_exit_flags()
    self._intraday_trades_today = state.get("intraday_trades_today", 0)
    self._intraday_call_trades_today = state.get("intraday_call_trades_today", 0)
    self._intraday_put_trades_today = state.get("intraday_put_trades_today", 0)
    self._intraday_itm_trades_today = state.get("intraday_itm_trades_today", 0)
    self._intraday_micro_trades_today = state.get("intraday_micro_trades_today", 0)
    self._call_consecutive_losses = int(state.get("call_consecutive_losses", 0) or 0)
    self._put_consecutive_losses = int(state.get("put_consecutive_losses", 0) or 0)
    call_cooldown = state.get("call_cooldown_until_date")
    put_cooldown = state.get("put_cooldown_until_date")
    try:
        self._call_cooldown_until_date = (
            datetime.strptime(call_cooldown, "%Y-%m-%d").date() if call_cooldown else None
        )
    except Exception:
        self._call_cooldown_until_date = None
    try:
        self._put_cooldown_until_date = (
            datetime.strptime(put_cooldown, "%Y-%m-%d").date() if put_cooldown else None
        )
    except Exception:
        self._put_cooldown_until_date = None
    self._swing_trades_today = state.get("swing_trades_today", 0)
    self._total_options_trades_today = state.get("total_options_trades_today", self._trades_today)

    # V2.16-BT: Restore spread position with expiry validation
    restored_spreads: List[SpreadPosition] = []
    spread_positions_data = state.get("spread_positions")
    if spread_positions_data:
        for row in spread_positions_data:
            try:
                spread = SpreadPosition.from_dict(row)
                if spread.long_leg.expiry and spread.long_leg.expiry < current_date:
                    self.log(
                        f"OPT: ZOMBIE_CLEAR - Spread position expired {spread.long_leg.expiry} < {current_date}. "
                        "Clearing stale spread."
                    )
                    continue
                restored_spreads.append(spread)
            except Exception:
                continue
    else:
        spread_data = state.get("spread_position")
        if spread_data:
            spread = SpreadPosition.from_dict(spread_data)
            if spread.long_leg.expiry and spread.long_leg.expiry < current_date:
                self.log(
                    f"OPT: ZOMBIE_CLEAR - Spread position expired {spread.long_leg.expiry} < {current_date}. "
                    "Clearing stale spread."
                )
            else:
                restored_spreads.append(spread)

    self._spread_positions = restored_spreads
    self._spread_position = self._spread_positions[0] if self._spread_positions else None
    if self._spread_positions:
        self.log(
            f"OPT: STATE_RESTORE - Spread positions restored | "
            f"Count={len(self._spread_positions)} | "
            f"Primary={self._spread_positions[0].spread_type} x{self._spread_positions[0].num_spreads}",
        )

    mode_value = state.get("current_mode", "SWING")
    self._current_mode = OptionsMode(mode_value)

    micro_state_data = state.get("micro_regime_state")
    if micro_state_data:
        self._micro_regime_engine._state = MicroRegimeState.from_dict(micro_state_data)

    runtime_state = state.get("micro_regime_runtime_state", {}) or {}
    try:
        self._micro_regime_engine._vix_history.clear()
        for row in runtime_state.get("vix_history", []) or []:
            try:
                self._micro_regime_engine._vix_history.append(
                    VIXSnapshot(
                        timestamp=str(row.get("timestamp", "")),
                        value=float(row.get("value", 0.0) or 0.0),
                        change_from_open_pct=float(row.get("change_from_open_pct", 0.0) or 0.0),
                    )
                )
            except Exception:
                continue
        self._micro_regime_engine._vix_15min_ago = float(
            runtime_state.get("vix_15min_ago", 0.0) or 0.0
        )
        self._micro_regime_engine._vix_30min_ago = float(
            runtime_state.get("vix_30min_ago", 0.0) or 0.0
        )
        self._micro_regime_engine._qqq_open = float(runtime_state.get("qqq_open", 0.0) or 0.0)
    except Exception:
        self._micro_regime_engine._vix_history.clear()
        self._micro_regime_engine._vix_15min_ago = 0.0
        self._micro_regime_engine._vix_30min_ago = 0.0
        self._micro_regime_engine._qqq_open = 0.0

    # Market open data
    self._vix_at_open = state.get("vix_at_open", 0.0)
    self._spy_at_open = state.get("spy_at_open", 0.0)
    self._spy_gap_pct = state.get("spy_gap_pct", 0.0)

    # Runtime guard/cooldown state (restart-safe)
    cap = state.get("rejection_margin_cap")
    try:
        self._rejection_margin_cap = float(cap) if cap is not None else None
    except Exception:
        self._rejection_margin_cap = None

    raw_cooldown = state.get("spread_failure_cooldown_until")
    self._spread_failure_cooldown_until = str(raw_cooldown) if raw_cooldown else None

    raw_by_dir = state.get("spread_failure_cooldown_until_by_dir", {}) or {}
    self._spread_failure_cooldown_until_by_dir = {}
    if isinstance(raw_by_dir, dict):
        for k, v in raw_by_dir.items():
            key = str(k).upper()
            if key in {"BULLISH", "BEARISH", "CALL", "PUT"} and v:
                self._spread_failure_cooldown_until_by_dir[key] = str(v)

    self._spread_exit_signal_cooldown = {}
    for k, v in (state.get("spread_exit_signal_cooldown", {}) or {}).items():
        try:
            self._spread_exit_signal_cooldown[str(k)] = datetime.strptime(
                str(v)[:19], "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            continue

    self._gamma_pin_exit_triggered = bool(state.get("gamma_pin_exit_triggered", False))
    raw_last_exit = state.get("last_spread_exit_time")
    self._last_spread_exit_time = str(raw_last_exit) if raw_last_exit else None
    raw_attempt_marks = state.get("spread_attempt_last_mark_by_key", {}) or {}
    self._spread_attempt_last_mark_by_key = (
        {str(k): str(v) for k, v in raw_attempt_marks.items()}
        if isinstance(raw_attempt_marks, dict)
        else {}
    )

    # V2.27: Win Rate Gate state
    self._spread_result_history = state.get("spread_result_history", [])
    self._win_rate_shutoff = state.get("win_rate_shutoff", False)
    raw_shutoff_date = state.get("win_rate_shutoff_date")
    self._win_rate_shutoff_date = str(raw_shutoff_date)[:10] if raw_shutoff_date else None
    self._paper_track_history = state.get("paper_track_history", [])
    vass_state = state.get("vass_entry_state")
    if not isinstance(vass_state, dict):
        # Backward-compat restore from legacy top-level keys.
        vass_state = {
            "last_entry_at_by_signature": state.get("vass_last_entry_at_by_signature", {}) or {},
            "cooldown_until_by_signature": state.get("vass_cooldown_until_by_signature", {}) or {},
            "last_entry_date_by_direction": state.get("vass_last_entry_date_by_direction", {})
            or {},
            "consecutive_losses": state.get("vass_consecutive_losses", 0) or 0,
            "loss_breaker_pause_until": state.get("vass_loss_breaker_pause_until"),
        }
    else:
        # Preserve old top-level fields if present and embedded state lacks them.
        if "consecutive_losses" not in vass_state:
            vass_state["consecutive_losses"] = state.get("vass_consecutive_losses", 0) or 0
        if "loss_breaker_pause_until" not in vass_state:
            vass_state["loss_breaker_pause_until"] = state.get("vass_loss_breaker_pause_until")
    try:
        self._vass_entry_engine.from_dict(vass_state)
    except Exception:
        self._vass_entry_engine.reset()
    self._spread_neutrality_warn_by_key = {}
    for k, v in (state.get("spread_neutrality_warn_by_key", {}) or {}).items():
        try:
            self._spread_neutrality_warn_by_key[str(k)] = datetime.strptime(
                str(v)[:19], "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            continue
    if self._spread_neutrality_warn_by_key:
        active_keys = {self._build_spread_key(s) for s in self._spread_positions}
        self._spread_neutrality_warn_by_key = {
            k: v for k, v in self._spread_neutrality_warn_by_key.items() if k in active_keys
        }

    self._last_intraday_close_time = None
    last_close = state.get("last_intraday_close_time")
    if last_close:
        try:
            self._last_intraday_close_time = datetime.strptime(
                str(last_close)[:19], "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            self._last_intraday_close_time = None
    self._last_intraday_close_strategy = state.get("last_intraday_close_strategy")
    try:
        self._itm_horizon_engine.from_dict(state.get("itm_horizon_state", {}) or {})
    except Exception:
        self._itm_horizon_engine.reset()


def reset_options_engine_state_impl(self) -> None:
    """Reset engine state."""
    # Legacy
    self._position = None
    self._trades_today = 0
    self._last_trade_date = None

    # V2.1.1
    self._swing_position = None
    self._intraday_position = None
    self._intraday_position_engine = None
    self._intraday_positions = {"MICRO": [], "ITM": []}
    self._spread_positions = []
    self._spread_position = None
    self._intraday_trades_today = 0
    self._intraday_call_trades_today = 0
    self._intraday_put_trades_today = 0
    self._intraday_itm_trades_today = 0
    self._intraday_micro_trades_today = 0
    self._current_mode = OptionsMode.SWING
    self._micro_regime_engine.reset_daily()
    self._vix_at_open = 0.0
    self._spy_at_open = 0.0
    self._spy_gap_pct = 0.0

    # V2.3: Reset spam prevention flags
    self._entry_attempted_today = False
    self._spread_attempts_today_by_key = {}
    self._spread_attempt_last_mark_by_key = {}
    self._swing_time_warning_logged = False

    # V2.3.2: Reset pending intraday entry flag
    self._pending_intraday_entry = False
    self._pending_intraday_entry_since = None
    self._pending_intraday_entry_engine = None
    self._pending_intraday_entries = {}

    # V2.3.3: Reset pending intraday exit flag
    self._pending_intraday_exit = False
    self._pending_intraday_exit_engine = None
    self._pending_intraday_exit_lanes = set()
    self._pending_intraday_exit_symbols = set()
    self._transition_context_snapshot = None
    self._rejection_margin_cap = None
    self._spread_failure_cooldown_until = None
    self._spread_failure_cooldown_until_by_dir = {}
    self._last_spread_scan_time = None
    self._last_spread_failure_stats = None
    self._last_credit_failure_stats = None
    self._last_entry_validation_failure = None
    self._last_intraday_validation_failure_by_lane = {"MICRO": None, "ITM": None}
    self._last_intraday_validation_detail_by_lane = {"MICRO": None, "ITM": None}
    self._last_trade_limit_failure = None
    self._last_trade_limit_detail = None
    self._spread_exit_signal_cooldown = {}
    self._call_consecutive_losses = 0
    self._call_cooldown_until_date = None
    self._put_consecutive_losses = 0
    self._put_cooldown_until_date = None
    self._spread_result_history = []
    self._paper_track_history = []
    self._win_rate_shutoff = False
    self._win_rate_shutoff_date = None
    self._vass_entry_engine.reset()
    self._spread_neutrality_warn_by_key = {}
    self._spread_hold_guard_logged.clear()
    self._intraday_force_exit_hold_skip_log_date = {}
    self._last_intraday_close_time = None
    self._last_intraday_close_strategy = None
    self._itm_horizon_engine.reset()

    self.log("OPT: Engine reset - all positions cleared")


def reset_options_engine_daily_state_impl(self, current_date: str) -> None:
    """Reset daily trade counter at start of new day."""
    if current_date != self._last_trade_date:
        self._trades_today = 0
        self._intraday_trades_today = 0
        self._intraday_call_trades_today = 0
        self._intraday_put_trades_today = 0
        self._intraday_itm_trades_today = 0
        self._intraday_micro_trades_today = 0
        self._swing_trades_today = 0  # V2.9
        self._total_options_trades_today = 0  # V2.9
        self._last_trade_date = current_date

        # V2.3 FIX: Reset entry attempt flag for new day
        self._entry_attempted_today = False
        self._spread_attempts_today_by_key = {}
        self._spread_attempt_last_mark_by_key = {}
        self._swing_time_warning_logged = False
        # V2.21: Clear rejection margin cap for new day
        self._rejection_margin_cap = None

        # V2.3.2: Reset pending intraday entry flag
        self._pending_intraday_entry = False
        self._pending_intraday_entry_since = None
        self._pending_intraday_entry_engine = None
        self._pending_intraday_entries = {}

        # V2.3.3: Reset pending intraday exit flag
        self._pending_intraday_exit = False
        self._pending_intraday_exit_engine = None
        self._pending_intraday_exit_lanes = set()
        self._pending_intraday_exit_symbols = set()
        self._transition_context_snapshot = None
        if not self.has_intraday_position():
            self._intraday_position_engine = None
        self._intraday_force_exit_hold_skip_log_date = {}
        self._last_intraday_close_time = None
        self._last_intraday_close_strategy = None

        # Reset Micro Regime Engine for new day
        self._micro_regime_engine.reset_daily()
        self._vass_entry_engine.reset_daily()

        # V2.4.3: Clear spread failure cooldown for new day
        self._spread_failure_cooldown_until = None
        self._spread_failure_cooldown_until_by_dir = {}
        self._last_spread_scan_time = None

        # Keep intraday state whenever a live broker holding still exists.
        # This avoids reset->orphan churn when an expected force-close fails.
        for lane, lane_positions in list(self._intraday_positions.items()):
            if not lane_positions:
                continue
            kept_positions = []
            for intraday_pos in list(lane_positions):
                keep_position = self.should_hold_intraday_overnight(intraday_pos)
                if not keep_position and self.algorithm is not None:
                    try:
                        sym = intraday_pos.contract.symbol
                        broker_symbol = sym
                        if isinstance(sym, str):
                            broker_symbol = self.algorithm.Symbol(sym)
                        sec = self.algorithm.Portfolio[broker_symbol]
                        if sec is not None and sec.Invested and abs(int(sec.Quantity)) > 0:
                            intraday_pos.num_contracts = abs(int(sec.Quantity))
                            keep_position = True
                    except Exception:
                        keep_position = False

                if keep_position:
                    kept_positions.append(intraday_pos)
                    self.log(
                        f"OPT: DAILY_RESET_KEEP - preserving live intraday position | Lane={lane}",
                        trades_only=True,
                    )
                else:
                    self.log(
                        f"OPT: WARNING - Intraday position found at daily reset, clearing | Lane={lane}"
                    )
            self._intraday_positions[str(lane).upper()] = kept_positions
        self._refresh_legacy_intraday_mirrors()

        if self._spread_neutrality_warn_by_key:
            active_keys = {self._build_spread_key(s) for s in self._spread_positions}
            self._spread_neutrality_warn_by_key = {
                k: v for k, v in self._spread_neutrality_warn_by_key.items() if k in active_keys
            }

        self._itm_horizon_engine.emit_daily_summary(current_date)
        self.log(f"OPT: Daily reset for {current_date}")


# =========================================================================
# V2.9: TRADE COUNTER ENFORCEMENT (Bug #4 Fix)
# =========================================================================
