"""Iron Condor position model for the IC engine.

An iron condor is two credit spreads (bull put + bear call) on the same expiry.
Net credit is collected upfront; max loss = wing_width - net_credit per side.

Breakeven WR math (managed exits):
  win  = IC_TARGET_CAPTURE_PCT * credit  (default 60%)
  loss = IC_STOP_LOSS_MULTIPLE  * credit (default 150%)
  WR_be = loss / (loss + win) = 1.50 / (1.50 + 0.60) = 71.4%
  Target realized WR >= 74-75% to cover friction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engines.satellite.options_primitives import OptionContract


@dataclass
class RollRecord:
    """Records a single roll event within an IC campaign."""

    roll_time: str  # ISO timestamp
    rolled_side: str  # "PUT" or "CALL"
    closed_credit: float  # Per-spread credit of closed side at entry
    realized_pnl: float  # $ realized on the close (from fills)
    new_short_strike: float
    new_long_strike: float
    new_credit: float  # Per-spread credit of replacement
    new_expiry: str
    roll_reason: str  # EXIT_IC_ROLL_PUT / EXIT_IC_ROLL_CALL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roll_time": self.roll_time,
            "rolled_side": self.rolled_side,
            "closed_credit": self.closed_credit,
            "realized_pnl": self.realized_pnl,
            "new_short_strike": self.new_short_strike,
            "new_long_strike": self.new_long_strike,
            "new_credit": self.new_credit,
            "new_expiry": self.new_expiry,
            "roll_reason": self.roll_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RollRecord":
        return cls(
            roll_time=data["roll_time"],
            rolled_side=data["rolled_side"],
            closed_credit=float(data.get("closed_credit", 0.0)),
            realized_pnl=float(data.get("realized_pnl", 0.0)),
            new_short_strike=float(data.get("new_short_strike", 0.0)),
            new_long_strike=float(data.get("new_long_strike", 0.0)),
            new_credit=float(data.get("new_credit", 0.0)),
            new_expiry=data.get("new_expiry", ""),
            roll_reason=data.get("roll_reason", ""),
        )


@dataclass
class IronCondorPosition:
    """Tracks a 4-leg iron condor position (bull put credit + bear call credit)."""

    # ── Put credit spread (bull put): sell higher put, buy lower put ──
    short_put: OptionContract
    long_put: OptionContract

    # ── Call credit spread (bear call): sell lower call, buy higher call ──
    short_call: OptionContract
    long_call: OptionContract

    # ── Entry economics ──
    net_credit: float  # Total premium collected (both sides)
    put_wing_width: float  # short_put.strike - long_put.strike
    call_wing_width: float  # long_call.strike - short_call.strike
    max_loss: float  # max(put_wing_width, call_wing_width) - net_credit
    credit_to_width: float  # net_credit / max(put_wing_width, call_wing_width)
    num_spreads: int  # Contract count per side

    # ── Entry context ──
    entry_time: str
    regime_at_entry: float
    entry_vix: float
    entry_adx: float
    entry_dte: int = 30  # DTE at entry, drives hold guard duration
    entry_underlying_price: float = 0.0  # V12.25: underlying anchor for thesis invalidation
    entry_transition_overlay: str = (
        ""  # V12.32: transition state at entry for order_lifecycle attribution
    )
    condor_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # ── Lifecycle ──
    is_closing: bool = False
    highest_pnl_pct: float = 0.0  # MFE: highest P&L as % of credit
    mfe_lock_tier: int = 0  # MFE ratchet: 0=none, 1=breakeven, 2=harvest floor

    # ── Close lifecycle tracking ──
    close_attempt_count: int = 0  # Times orphan recovery re-emitted close signals
    last_close_signal_time: Optional[str] = None  # ISO timestamp of last close emission

    # ── Diagnostics ──
    entry_cw_tier: str = ""  # LOW_VIX / MID_VIX / HIGH_VIX
    stop_dw: float = 0.0  # Projected stop D/W at entry
    implied_wr_be: float = 0.0  # Implied expiry breakeven WR (1 - C/W)
    exit_pnl_estimate: float = 0.0  # Snapshot PnL at software-exit trigger (for accounting)

    # ── Entry-frozen exit params (V12.33: survive config changes across deploys) ──
    entry_stop_mult: float = 0.0  # IC_STOP_LOSS_MULTIPLE at entry time (0 = use config)
    entry_mfe_t1_trigger: float = 0.0  # IC_MFE_T1_TRIGGER at entry (0 = use config)
    entry_mfe_t2_trigger: float = 0.0  # IC_MFE_T2_TRIGGER at entry (0 = use config)
    entry_mfe_t1_floor: float = -1.0  # IC_MFE_T1_FLOOR_PCT at entry (-1 = use config)
    entry_mfe_t2_floor: float = -1.0  # IC_MFE_T2_FLOOR_PCT at entry (-1 = use config)

    # ── V12.37: Per-side rolling ──
    put_side_credit: float = 0.0  # short_put.mid - long_put.mid (per-spread)
    call_side_credit: float = 0.0  # short_call.mid - long_call.mid (per-spread)
    put_side_active: bool = True  # False after put side rolled off
    call_side_active: bool = True  # False after call side rolled off
    roll_count: int = 0  # Number of rolls applied
    max_rolls: int = 1  # Cap (v1 hardcoded to 1)
    cumulative_credit: float = 0.0  # Total credit across original + rolls (per-spread)
    cumulative_realized_pnl: float = 0.0  # Running realized P&L from closed sides (fills only)
    roll_history: List[RollRecord] = field(default_factory=list)

    # ── V12.37: Pending roll state machine ──
    is_rolling: bool = False  # True between side-close fill and replacement fill
    rolling_side: str = ""  # "PUT" or "CALL"
    roll_pending_since: Optional[str] = None  # ISO timestamp when roll started
    roll_trigger_side_pnl_estimate: float = 0.0  # Trigger-time losing-side PnL estimate
    pending_roll_close_realized_pnl: float = (
        0.0  # Realized $ from tested-side close, awaiting replacement or campaign close
    )

    # ── Derived helpers ──

    @property
    def put_short_strike(self) -> float:
        return self.short_put.strike

    @property
    def call_short_strike(self) -> float:
        return self.short_call.strike

    @property
    def range_width(self) -> float:
        """Distance between short strikes — the profitable zone."""
        return self.call_short_strike - self.put_short_strike

    @property
    def max_wing_width(self) -> float:
        return max(self.put_wing_width, self.call_wing_width)

    # ── Serialization ──

    def to_dict(self) -> Dict[str, Any]:
        return {
            "short_put": self.short_put.to_dict(),
            "long_put": self.long_put.to_dict(),
            "short_call": self.short_call.to_dict(),
            "long_call": self.long_call.to_dict(),
            "net_credit": self.net_credit,
            "put_wing_width": self.put_wing_width,
            "call_wing_width": self.call_wing_width,
            "max_loss": self.max_loss,
            "credit_to_width": self.credit_to_width,
            "num_spreads": self.num_spreads,
            "entry_time": self.entry_time,
            "regime_at_entry": self.regime_at_entry,
            "entry_vix": self.entry_vix,
            "entry_adx": self.entry_adx,
            "entry_dte": self.entry_dte,
            "entry_underlying_price": self.entry_underlying_price,
            "entry_transition_overlay": self.entry_transition_overlay,
            "condor_id": self.condor_id,
            "is_closing": self.is_closing,
            "highest_pnl_pct": self.highest_pnl_pct,
            "mfe_lock_tier": self.mfe_lock_tier,
            "close_attempt_count": self.close_attempt_count,
            "last_close_signal_time": self.last_close_signal_time,
            "entry_cw_tier": self.entry_cw_tier,
            "stop_dw": self.stop_dw,
            "implied_wr_be": self.implied_wr_be,
            "exit_pnl_estimate": self.exit_pnl_estimate,
            "entry_stop_mult": self.entry_stop_mult,
            "entry_mfe_t1_trigger": self.entry_mfe_t1_trigger,
            "entry_mfe_t2_trigger": self.entry_mfe_t2_trigger,
            "entry_mfe_t1_floor": self.entry_mfe_t1_floor,
            "entry_mfe_t2_floor": self.entry_mfe_t2_floor,
            # V12.37: Rolling fields
            "put_side_credit": self.put_side_credit,
            "call_side_credit": self.call_side_credit,
            "put_side_active": self.put_side_active,
            "call_side_active": self.call_side_active,
            "roll_count": self.roll_count,
            "max_rolls": self.max_rolls,
            "cumulative_credit": self.cumulative_credit,
            "cumulative_realized_pnl": self.cumulative_realized_pnl,
            "roll_history": [r.to_dict() for r in self.roll_history],
            "is_rolling": self.is_rolling,
            "rolling_side": self.rolling_side,
            "roll_pending_since": self.roll_pending_since,
            "roll_trigger_side_pnl_estimate": self.roll_trigger_side_pnl_estimate,
            "pending_roll_close_realized_pnl": self.pending_roll_close_realized_pnl,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IronCondorPosition":
        return cls(
            short_put=OptionContract.from_dict(data["short_put"]),
            long_put=OptionContract.from_dict(data["long_put"]),
            short_call=OptionContract.from_dict(data["short_call"]),
            long_call=OptionContract.from_dict(data["long_call"]),
            net_credit=data["net_credit"],
            put_wing_width=data["put_wing_width"],
            call_wing_width=data["call_wing_width"],
            max_loss=data["max_loss"],
            credit_to_width=data["credit_to_width"],
            num_spreads=data["num_spreads"],
            entry_time=data["entry_time"],
            regime_at_entry=data["regime_at_entry"],
            entry_vix=data["entry_vix"],
            entry_adx=data.get("entry_adx", 0.0),
            entry_dte=int(data.get("entry_dte", 30)),
            entry_underlying_price=float(data.get("entry_underlying_price", 0.0) or 0.0),
            entry_transition_overlay=str(data.get("entry_transition_overlay", "") or ""),
            condor_id=data.get("condor_id", uuid.uuid4().hex[:12]),
            is_closing=data.get("is_closing", False),
            highest_pnl_pct=data.get("highest_pnl_pct", 0.0),
            mfe_lock_tier=int(data.get("mfe_lock_tier", 0)),
            close_attempt_count=int(data.get("close_attempt_count", 0) or 0),
            last_close_signal_time=data.get("last_close_signal_time"),
            entry_cw_tier=data.get("entry_cw_tier", ""),
            stop_dw=data.get("stop_dw", 0.0),
            implied_wr_be=data.get("implied_wr_be", 0.0),
            exit_pnl_estimate=float(data.get("exit_pnl_estimate", 0.0) or 0.0),
            entry_stop_mult=float(data.get("entry_stop_mult", 0.0) or 0.0),
            entry_mfe_t1_trigger=float(data.get("entry_mfe_t1_trigger", 0.0) or 0.0),
            entry_mfe_t2_trigger=float(data.get("entry_mfe_t2_trigger", 0.0) or 0.0),
            entry_mfe_t1_floor=float(
                data.get("entry_mfe_t1_floor", -1.0)
                if data.get("entry_mfe_t1_floor") is not None
                else -1.0
            ),
            entry_mfe_t2_floor=float(
                data.get("entry_mfe_t2_floor", -1.0)
                if data.get("entry_mfe_t2_floor") is not None
                else -1.0
            ),
            # V12.37: Rolling fields
            put_side_credit=float(data.get("put_side_credit", 0.0) or 0.0),
            call_side_credit=float(data.get("call_side_credit", 0.0) or 0.0),
            put_side_active=data.get("put_side_active", True),
            call_side_active=data.get("call_side_active", True),
            roll_count=int(data.get("roll_count", 0) or 0),
            max_rolls=int(data.get("max_rolls", 1) or 1),
            cumulative_credit=float(data.get("cumulative_credit", 0.0) or 0.0),
            cumulative_realized_pnl=float(data.get("cumulative_realized_pnl", 0.0) or 0.0),
            roll_history=[RollRecord.from_dict(r) for r in data.get("roll_history", [])],
            is_rolling=data.get("is_rolling", False),
            rolling_side=str(data.get("rolling_side", "") or ""),
            roll_pending_since=data.get("roll_pending_since"),
            roll_trigger_side_pnl_estimate=float(
                data.get("roll_trigger_side_pnl_estimate", 0.0) or 0.0
            ),
            pending_roll_close_realized_pnl=float(
                data.get("pending_roll_close_realized_pnl", 0.0) or 0.0
            ),
        )
