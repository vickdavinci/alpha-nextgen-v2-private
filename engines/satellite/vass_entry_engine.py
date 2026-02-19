"""VASS entry engine: isolates strategy routing and anti-cluster guards for swing spreads."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

import config
from models.enums import OptionDirection


class VASSEntryEngine:
    """Encapsulates VASS entry routing, filters, and per-signature/day guards."""

    def __init__(self, log_func: Optional[Callable[[str, bool], None]] = None):
        self._log_func = log_func
        self._last_entry_at_by_signature: Dict[str, datetime] = {}
        self._cooldown_until_by_signature: Dict[str, datetime] = {}
        self._last_entry_date_by_direction: Dict[str, str] = {}
        self._consecutive_losses: int = 0
        self._loss_breaker_pause_until: Optional[str] = None  # YYYY-MM-DD

    def _log(self, message: str, trades_only: bool = False) -> None:
        if self._log_func:
            self._log_func(message, trades_only)

    def select_strategy(
        self,
        *,
        direction: str,
        iv_environment: str,
        is_intraday: bool,
        spread_strategy_enum: Any,
    ) -> Tuple[Any, int, int]:
        """Return (SpreadStrategy, dte_min, dte_max) for VASS routing."""
        matrix = {
            ("BULLISH", "LOW"): (
                spread_strategy_enum.BULL_CALL_DEBIT,
                config.VASS_LOW_IV_DTE_MIN,
                config.VASS_LOW_IV_DTE_MAX,
            ),
            ("BULLISH", "MEDIUM"): (
                spread_strategy_enum.BULL_CALL_DEBIT,
                config.VASS_MEDIUM_IV_DTE_MIN,
                config.VASS_MEDIUM_IV_DTE_MAX,
            ),
            ("BULLISH", "HIGH"): (
                spread_strategy_enum.BULL_PUT_CREDIT,
                config.VASS_HIGH_IV_DTE_MIN,
                config.VASS_HIGH_IV_DTE_MAX,
            ),
            ("BEARISH", "LOW"): (
                spread_strategy_enum.BEAR_PUT_DEBIT,
                config.VASS_LOW_IV_DTE_MIN,
                config.VASS_LOW_IV_DTE_MAX,
            ),
            ("BEARISH", "MEDIUM"): (
                spread_strategy_enum.BEAR_PUT_DEBIT,
                config.VASS_MEDIUM_IV_DTE_MIN,
                config.VASS_MEDIUM_IV_DTE_MAX,
            ),
            ("BEARISH", "HIGH"): (
                spread_strategy_enum.BEAR_CALL_CREDIT,
                config.VASS_HIGH_IV_DTE_MIN,
                config.VASS_HIGH_IV_DTE_MAX,
            ),
        }

        key = (direction, iv_environment)
        if key in matrix:
            strategy, dte_min, dte_max = matrix[key]
            self._log(
                f"VASS: {direction} + {iv_environment} IV -> {strategy.value} | "
                f"DTE={dte_min}-{dte_max} | Intraday={is_intraday}"
            )
            return strategy, dte_min, dte_max

        self._log(f"VASS: Unknown key {key}, defaulting to MEDIUM debit spread")
        if direction == "BULLISH":
            return spread_strategy_enum.BULL_CALL_DEBIT, 7, 21
        return spread_strategy_enum.BEAR_PUT_DEBIT, 7, 21

    def _parse_hhmm_to_minutes(self, hhmm: str, default_minutes: int) -> int:
        """Parse HH:MM into minutes-from-midnight; fallback to default on parse failure."""
        try:
            parts = str(hhmm).split(":")
            if len(parts) != 2:
                return default_minutes
            hh = int(parts[0])
            mm = int(parts[1])
            if hh < 0 or hh > 23 or mm < 0 or mm > 59:
                return default_minutes
            return hh * 60 + mm
        except Exception:
            return default_minutes

    def check_swing_filters(
        self,
        *,
        direction: OptionDirection,
        spy_gap_pct: float,
        spy_intraday_change_pct: float,
        vix_intraday_change_pct: float,
        current_hour: int,
        current_minute: int,
    ) -> Tuple[bool, str]:
        """Simple intraday filters for swing-mode entries."""
        time_minutes = current_hour * 60 + current_minute
        start_minutes = self._parse_hhmm_to_minutes(
            str(getattr(config, "SWING_TIME_WINDOW_START", "10:00")), 10 * 60
        )
        end_minutes = self._parse_hhmm_to_minutes(
            str(getattr(config, "SWING_TIME_WINDOW_END", "14:30")), 14 * 60 + 30
        )
        if not (start_minutes <= time_minutes <= end_minutes):
            return False, "TIME_WINDOW"

        if abs(spy_gap_pct) > config.SWING_GAP_THRESHOLD:
            if direction == OptionDirection.CALL and spy_gap_pct > 0:
                return False, f"Gap up {spy_gap_pct:.1f}% - reversal risk for calls"
            if direction == OptionDirection.PUT and spy_gap_pct < 0:
                return False, f"Gap down {spy_gap_pct:.1f}% - bounce risk for puts"

        if spy_intraday_change_pct < config.SWING_EXTREME_SPY_DROP:
            return False, f"SPY extreme drop {spy_intraday_change_pct:.1f}% - pause entries"

        if vix_intraday_change_pct > config.SWING_EXTREME_VIX_SPIKE:
            return False, f"VIX spike +{vix_intraday_change_pct:.1f}% - pause entries"

        return True, ""

    def _add_trading_days(self, start: datetime, days: int) -> datetime:
        """Add trading days (skip weekends) to a datetime."""
        result = start
        remaining = max(0, int(days))
        while remaining > 0:
            result += timedelta(days=1)
            if result.weekday() < 5:
                remaining -= 1
        return result

    def should_block_for_loss_breaker(self, current_date: str) -> bool:
        """Return True when VASS loss breaker pause is active for current trading date."""
        if not bool(getattr(config, "VASS_LOSS_BREAKER_ENABLED", False)):
            return False
        pause_until = self._loss_breaker_pause_until
        if not pause_until:
            return False
        try:
            trade_date = datetime.strptime(str(current_date)[:10], "%Y-%m-%d").date()
            pause_date = datetime.strptime(str(pause_until)[:10], "%Y-%m-%d").date()
            if trade_date <= pause_date:
                return True
            self._loss_breaker_pause_until = None
            return False
        except Exception:
            self._loss_breaker_pause_until = None
            return False

    def record_spread_result(self, *, is_win: bool, now_dt: Optional[datetime]) -> Optional[str]:
        """Update VASS breaker state from spread result; returns pause-until when armed."""
        if not bool(getattr(config, "VASS_LOSS_BREAKER_ENABLED", False)):
            return None
        if is_win:
            self._consecutive_losses = 0
            self._loss_breaker_pause_until = None
            return None

        self._consecutive_losses += 1
        threshold = int(getattr(config, "VASS_LOSS_BREAKER_THRESHOLD", 3))
        if self._consecutive_losses < threshold or now_dt is None:
            return None

        pause_days = max(1, int(getattr(config, "VASS_LOSS_BREAKER_PAUSE_DAYS", 1)))
        pause_until = self._add_trading_days(now_dt, pause_days)
        self._loss_breaker_pause_until = pause_until.date().isoformat()
        self._consecutive_losses = 0
        return self._loss_breaker_pause_until

    def check_similar_entry_guard(
        self,
        *,
        signature: str,
        now_dt: Optional[datetime],
    ) -> Optional[str]:
        """Return rejection code when same-signature entry is blocked."""
        if not signature or now_dt is None:
            return None

        min_gap_min = int(getattr(config, "VASS_SIMILAR_ENTRY_MIN_GAP_MINUTES", 15))
        last_entry = self._last_entry_at_by_signature.get(signature)
        if last_entry is not None:
            elapsed_min = (now_dt - last_entry).total_seconds() / 60.0
            if 0 <= elapsed_min < min_gap_min:
                self._log(
                    f"VASS_SIGNATURE_BLOCK: Burst guard | Sig={signature} | "
                    f"Elapsed={elapsed_min:.1f}m < {min_gap_min}m"
                )
                return "E_VASS_SIMILAR_15M_BLOCK"

        cooldown_until = self._cooldown_until_by_signature.get(signature)
        if cooldown_until is not None and now_dt < cooldown_until:
            self._log(
                f"VASS_SIGNATURE_BLOCK: Cooldown guard | Sig={signature} | "
                f"Now={now_dt} < Until={cooldown_until}"
            )
            return "E_VASS_SIMILAR_3D_COOLDOWN"

        if self._last_entry_at_by_signature:
            stale_cutoff = now_dt - timedelta(days=10)
            stale = [k for k, ts in self._last_entry_at_by_signature.items() if ts < stale_cutoff]
            for key in stale:
                self._last_entry_at_by_signature.pop(key, None)
                self._cooldown_until_by_signature.pop(key, None)
        return None

    def record_signature_entry(self, *, signature: str, entry_dt: Optional[datetime]) -> None:
        if not signature or entry_dt is None:
            return
        cooldown_days = int(getattr(config, "VASS_SIMILAR_ENTRY_COOLDOWN_DAYS", 3))
        self._last_entry_at_by_signature[signature] = entry_dt
        self._cooldown_until_by_signature[signature] = entry_dt + timedelta(days=cooldown_days)

    def check_direction_day_gap(
        self,
        *,
        direction: Optional[OptionDirection],
        current_date: str,
        algorithm: Any,
    ) -> Optional[str]:
        if not bool(getattr(config, "VASS_DIRECTION_DAY_GAP_ENABLED", True)):
            return None
        if direction is None:
            return None

        dir_label = "BULLISH" if direction == OptionDirection.CALL else "BEARISH"
        today = str(current_date or "")[:10]
        if not today:
            try:
                today = str(algorithm.Time.date()) if algorithm is not None else ""
            except Exception:
                today = ""
        if not today:
            return None

        last_date = self._last_entry_date_by_direction.get(dir_label)
        if last_date == today:
            return f"R_DIRECTION_DAY_GAP: {dir_label} already entered on {today}"
        return None

    def record_direction_day_entry(
        self,
        *,
        direction: Optional[OptionDirection],
        entry_dt: Optional[datetime],
    ) -> None:
        if direction is None or entry_dt is None:
            return
        dir_label = "BULLISH" if direction == OptionDirection.CALL else "BEARISH"
        self._last_entry_date_by_direction[dir_label] = str(entry_dt.date())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_entry_at_by_signature": {
                k: v.strftime("%Y-%m-%d %H:%M:%S")
                for k, v in self._last_entry_at_by_signature.items()
            },
            "cooldown_until_by_signature": {
                k: v.strftime("%Y-%m-%d %H:%M:%S")
                for k, v in self._cooldown_until_by_signature.items()
            },
            "last_entry_date_by_direction": dict(self._last_entry_date_by_direction),
            "consecutive_losses": self._consecutive_losses,
            "loss_breaker_pause_until": self._loss_breaker_pause_until,
        }

    def from_dict(self, state: Dict[str, Any]) -> None:
        self._last_entry_at_by_signature = {}
        self._cooldown_until_by_signature = {}
        self._last_entry_date_by_direction = {
            str(k).upper(): str(v)[:10]
            for k, v in (state.get("last_entry_date_by_direction", {}) or {}).items()
            if str(k).upper() in {"BULLISH", "BEARISH"} and str(v)
        }
        self._consecutive_losses = int(state.get("consecutive_losses", 0) or 0)
        raw_pause = state.get("loss_breaker_pause_until")
        self._loss_breaker_pause_until = str(raw_pause)[:10] if raw_pause else None
        for k, v in (state.get("last_entry_at_by_signature", {}) or {}).items():
            try:
                self._last_entry_at_by_signature[str(k)] = datetime.strptime(
                    str(v)[:19], "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                continue
        for k, v in (state.get("cooldown_until_by_signature", {}) or {}).items():
            try:
                self._cooldown_until_by_signature[str(k)] = datetime.strptime(
                    str(v)[:19], "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                continue

    def reset_daily(self) -> None:
        """No-op for now; guards are multi-day by design."""

    def reset(self) -> None:
        self._last_entry_at_by_signature = {}
        self._cooldown_until_by_signature = {}
        self._last_entry_date_by_direction = {}
        self._consecutive_losses = 0
        self._loss_breaker_pause_until = None
