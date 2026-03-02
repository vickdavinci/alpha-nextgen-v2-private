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
from typing import Any, Dict, Optional

from engines.satellite.options_primitives import OptionContract


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
        )
