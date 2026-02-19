"""ITM horizon engine: isolated ITM_V2 rules/state for multi-day intraday positions."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import config
from models.enums import OptionDirection


class ITMHorizonEngine:
    """Feature-flagged ITM_V2 decision/risk engine."""

    def __init__(self, log_func: Optional[Callable[[str, bool], None]] = None):
        self._log_func = log_func
        self._consecutive_losses = 0
        self._call_consecutive_losses = 0
        self._put_consecutive_losses = 0
        self._pause_until: Optional[str] = None
        self._call_pause_until: Optional[str] = None
        self._put_pause_until: Optional[str] = None
        self._last_exit_date_by_direction: Dict[str, str] = {}
        self._equity_history: List[Tuple[str, float]] = []
        self._dd_blocked = False
        self._diag_counts: Dict[str, int] = {}
        self._diag_block_codes: Dict[str, int] = {}

    def _log(self, message: str, trades_only: bool = False) -> None:
        if self._log_func:
            self._log_func(message, trades_only)

    def enabled(self) -> bool:
        return bool(getattr(config, "ITM_V2_ENABLED", False))

    def shadow_mode(self) -> bool:
        return bool(getattr(config, "ITM_V2_SHADOW_MODE", False))

    def _count(self, key: str) -> None:
        self._diag_counts[key] = self._diag_counts.get(key, 0) + 1

    def _count_block_code(self, code: str) -> None:
        if not code:
            return
        self._diag_block_codes[code] = self._diag_block_codes.get(code, 0) + 1

    def _fail(self, code: str, detail: str = "") -> Tuple[bool, str, str]:
        self._count_block_code(code)
        return False, code, detail

    def _add_trading_days(self, start_date: str, days: int) -> str:
        base = datetime.strptime(start_date, "%Y-%m-%d").date()
        remaining = max(1, int(days))
        candidate = base
        while remaining > 0:
            candidate += timedelta(days=1)
            if candidate.weekday() < 5:  # Mon..Fri
                remaining -= 1
        return candidate.isoformat()

    def _parse_trade_date(
        self, current_time: Optional[str], algorithm: Any = None
    ) -> Optional[str]:
        if current_time:
            try:
                return str(datetime.strptime(current_time[:10], "%Y-%m-%d").date())
            except Exception:
                pass
        if algorithm is not None and hasattr(algorithm, "Time"):
            try:
                return str(algorithm.Time.date())
            except Exception:
                return None
        return None

    def _update_equity(self, trade_date: str, equity: float) -> None:
        if not trade_date or equity <= 0:
            return
        if not self._equity_history or self._equity_history[-1][0] != trade_date:
            self._equity_history.append((trade_date, float(equity)))
        else:
            self._equity_history[-1] = (trade_date, float(equity))
        max_keep = max(90, int(getattr(config, "ITM_V2_DD_LOOKBACK_DAYS", 60)) * 3)
        if len(self._equity_history) > max_keep:
            self._equity_history = self._equity_history[-max_keep:]

    def _drawdown_allows(self, trade_date: str, equity: float) -> Tuple[bool, str]:
        self._update_equity(trade_date, equity)
        lookback = max(5, int(getattr(config, "ITM_V2_DD_LOOKBACK_DAYS", 60)))
        block_thr = float(getattr(config, "ITM_V2_DD_BLOCK_THRESHOLD", 0.90))
        recover_thr = float(getattr(config, "ITM_V2_DD_RECOVER_THRESHOLD", 0.95))
        recent = self._equity_history[-lookback:]
        if not recent:
            return True, "NO_HISTORY"
        max_equity = max(v for _, v in recent if v > 0)
        if max_equity <= 0:
            return True, "NO_PEAK"
        ratio = float(equity) / float(max_equity)
        if self._dd_blocked:
            if ratio >= recover_thr:
                self._dd_blocked = False
                self._log(
                    f"ITM_V2_DD_GATE_RECOVER|Ratio={ratio:.1%} >= {recover_thr:.0%}",
                    trades_only=True,
                )
            else:
                return False, f"Ratio={ratio:.1%}<{recover_thr:.0%}"
        if ratio < block_thr:
            self._dd_blocked = True
            return False, f"Ratio={ratio:.1%}<{block_thr:.0%}"
        return True, f"Ratio={ratio:.1%}"

    def evaluate_entry(
        self,
        *,
        direction: OptionDirection,
        current_time: Optional[str],
        current_hour: int,
        current_minute: int,
        trace_id: Optional[str],
        qqq_current: float,
        sma20_value: Optional[float],
        adx_value: Optional[float],
        vix_current: float,
        vix20_change: Optional[float],
        portfolio_value: float,
        current_itm_positions: int = 0,
        algorithm: Any = None,
    ) -> Tuple[bool, str, str]:
        """Return (allowed, code, detail)."""
        if not self.enabled():
            return True, "ITM_V2_DISABLED", ""
        self._count("ITM_V2_Candidate")

        trade_date = self._parse_trade_date(current_time, algorithm=algorithm)
        if not trade_date:
            self._count("ITM_V2_Blocked_Other")
            return self._fail("E_ITM_V2_NO_DATE", "DATE_PARSE")

        dec_h = int(getattr(config, "ITM_V2_DECISION_HOUR", 10))
        dec_m = int(getattr(config, "ITM_V2_DECISION_MINUTE", 30))
        if (current_hour * 60 + current_minute) < (dec_h * 60 + dec_m):
            self._count("ITM_V2_Blocked_Other")
            return self._fail("E_ITM_V2_DECISION_TIME", f"{current_hour:02d}:{current_minute:02d}")

        entry_end = str(getattr(config, "ITM_V2_ENTRY_END", "13:30") or "13:30")
        try:
            end_h, end_m = [int(x) for x in entry_end.split(":")]
        except Exception:
            end_h, end_m = 13, 30
        if (current_hour * 60 + current_minute) > (end_h * 60 + end_m):
            self._count("ITM_V2_Blocked_Other")
            return self._fail(
                "E_ITM_V2_ENTRY_WINDOW_CLOSED",
                f"{current_hour:02d}:{current_minute:02d}>{end_h:02d}:{end_m:02d}",
            )

        max_positions = int(getattr(config, "ITM_V2_MAX_CONCURRENT_POSITIONS", 1))
        if int(current_itm_positions) >= max(1, max_positions):
            self._count("ITM_V2_Blocked_Breaker")
            return self._fail(
                "E_ITM_V2_POSITION_LIMIT",
                f"Open={current_itm_positions} Max={max_positions}",
            )

        if sma20_value is None or sma20_value <= 0:
            self._count("ITM_V2_Blocked_Trend")
            return self._fail("E_ITM_V2_NO_SMA20", "")

        band = float(getattr(config, "ITM_V2_SMA_BAND_PCT", 0.003))
        upper = sma20_value * (1.0 + band)
        lower = sma20_value * (1.0 - band)
        if lower <= qqq_current <= upper:
            self._count("ITM_V2_Blocked_Trend")
            return self._fail("E_ITM_V2_TREND_NEUTRAL", f"Band={band:.2%}")
        if direction == OptionDirection.CALL and qqq_current <= upper:
            self._count("ITM_V2_Blocked_Trend")
            return self._fail(
                "E_ITM_V2_TREND_CALL_BELOW_SMA20",
                f"QQQ={qqq_current:.2f}<=SMA20+band={upper:.2f}",
            )
        if direction == OptionDirection.PUT and qqq_current >= lower:
            self._count("ITM_V2_Blocked_Trend")
            return self._fail(
                "E_ITM_V2_TREND_PUT_ABOVE_SMA20",
                f"QQQ={qqq_current:.2f}>=SMA20-band={lower:.2f}",
            )

        adx_min = float(getattr(config, "ITM_V2_ADX_MIN", 15.0))
        if adx_value is None or adx_value < adx_min:
            self._count("ITM_V2_Blocked_Trend")
            return self._fail(
                "E_ITM_V2_ADX_WEAK",
                f"ADX={adx_value if adx_value is not None else 'NA'}<{adx_min:.1f}",
            )

        call_max_vix = float(getattr(config, "ITM_V2_CALL_MAX_VIX", 22.0))
        call_low_pref = float(getattr(config, "ITM_V2_CALL_LOW_VIX_PREFERRED", 18.0))
        put_min_vix = float(getattr(config, "ITM_V2_PUT_MIN_VIX", 14.0))
        put_max_vix = float(getattr(config, "ITM_V2_PUT_MAX_VIX", 35.0))
        req_vix20_falling = bool(
            getattr(config, "ITM_V2_REQUIRE_VIX20D_FALLING_FOR_CALL_WHEN_VIX_ABOVE_LOW", True)
        )
        if direction == OptionDirection.CALL:
            if vix_current >= call_max_vix:
                self._count("ITM_V2_Blocked_VIX")
                return self._fail(
                    "E_ITM_V2_VIX_CALL_MAX", f"VIX={vix_current:.1f}>={call_max_vix:.1f}"
                )
            if req_vix20_falling and vix_current >= call_low_pref:
                if vix20_change is None:
                    self._count("ITM_V2_Blocked_VIX")
                    return self._fail("E_ITM_V2_VIX20_MISSING", "")
                if vix20_change > 0:
                    self._count("ITM_V2_Blocked_VIX")
                    return self._fail("E_ITM_V2_VIX20_RISING_CALL", f"VIX20d={vix20_change:+.1%}")
        else:
            if vix_current < put_min_vix or vix_current >= put_max_vix:
                self._count("ITM_V2_Blocked_VIX")
                return self._fail("E_ITM_V2_VIX_PUT_BOUNDS", f"VIX={vix_current:.1f}")

        if bool(getattr(config, "ITM_V2_BLOCK_SAME_DAY_SAME_DIRECTION_REENTRY", True)):
            dir_key = direction.value.upper()
            if self._last_exit_date_by_direction.get(dir_key) == trade_date:
                self._count("ITM_V2_Blocked_Reentry")
                return self._fail("E_ITM_V2_REENTRY_SAME_DAY", dir_key)

        if self._pause_until and trade_date <= str(self._pause_until):
            self._count("ITM_V2_Blocked_Breaker")
            return self._fail("E_ITM_V2_BREAKER_GLOBAL", f"Until={self._pause_until}")
        dir_pause_until = (
            self._call_pause_until if direction == OptionDirection.CALL else self._put_pause_until
        )
        if dir_pause_until and trade_date <= str(dir_pause_until):
            self._count("ITM_V2_Blocked_Breaker")
            return self._fail("E_ITM_V2_BREAKER_DIRECTIONAL", f"Until={dir_pause_until}")

        dd_ok, dd_detail = self._drawdown_allows(trade_date, float(portfolio_value))
        if not dd_ok:
            self._count("ITM_V2_Blocked_Drawdown")
            return self._fail("E_ITM_V2_DRAWDOWN", dd_detail)

        self._count("ITM_V2_Pass")
        if trace_id:
            self._log(f"ITM_V2_TRACE_PASS|Trace={trace_id}|Dir={direction.value}", trades_only=True)
        return True, "ITM_V2_PASS", "PASS"

    def on_trade_closed(
        self,
        *,
        symbol: str,
        is_win: bool,
        current_time: Optional[str],
        strategy: Optional[str],
        algorithm: Any = None,
    ) -> None:
        if not self.enabled():
            return
        if str(strategy or "") not in {"ITM_MOMENTUM", "DEBIT_MOMENTUM"}:
            return

        trade_date = self._parse_trade_date(current_time, algorithm=algorithm)
        if trade_date is None:
            return

        is_call = re.search(r"\d{6}C\d{8}", str(symbol)) is not None
        is_put = re.search(r"\d{6}P\d{8}", str(symbol)) is not None
        direction_key = "CALL" if is_call else "PUT" if is_put else None
        if direction_key is None:
            return

        self._last_exit_date_by_direction[direction_key] = trade_date
        self._count("ITM_V2_Closed")

        if is_win:
            self._count("ITM_V2_Closed_Win")
            self._consecutive_losses = 0
            if direction_key == "CALL":
                self._call_consecutive_losses = 0
            else:
                self._put_consecutive_losses = 0
            return

        self._count("ITM_V2_Closed_Loss")
        self._consecutive_losses += 1
        if direction_key == "CALL":
            self._call_consecutive_losses += 1
        else:
            self._put_consecutive_losses += 1

        b3 = int(getattr(config, "ITM_V2_BREAKER_3_LOSSES_PAUSE_DAYS", 2))
        b5 = int(getattr(config, "ITM_V2_BREAKER_5_LOSSES_PAUSE_DAYS", 5))
        if self._consecutive_losses >= 5:
            self._pause_until = self._add_trading_days(trade_date, b5)
            self._log(
                f"ITM_V2_BREAKER_GLOBAL|Losses={self._consecutive_losses}|Until={self._pause_until}",
                trades_only=True,
            )
            self._consecutive_losses = 0
        elif self._consecutive_losses >= 3:
            self._pause_until = self._add_trading_days(trade_date, b3)
            self._log(
                f"ITM_V2_BREAKER_GLOBAL|Losses={self._consecutive_losses}|Until={self._pause_until}",
                trades_only=True,
            )

        if bool(getattr(config, "ITM_V2_DIRECTIONAL_BREAKER_ENABLED", True)):
            dir_days = int(getattr(config, "ITM_V2_DIRECTIONAL_BREAKER_3_LOSSES_PAUSE_DAYS", 2))
            if direction_key == "CALL" and self._call_consecutive_losses >= 3:
                self._call_pause_until = self._add_trading_days(trade_date, dir_days)
                self._log(
                    f"ITM_V2_BREAKER_CALL|Losses={self._call_consecutive_losses}|Until={self._call_pause_until}",
                    trades_only=True,
                )
                self._call_consecutive_losses = 0
            if direction_key == "PUT" and self._put_consecutive_losses >= 3:
                self._put_pause_until = self._add_trading_days(trade_date, dir_days)
                self._log(
                    f"ITM_V2_BREAKER_PUT|Losses={self._put_consecutive_losses}|Until={self._put_pause_until}",
                    trades_only=True,
                )
                self._put_consecutive_losses = 0

    def should_hold_overnight(self, *, entry_dte: int, live_dte: Optional[int]) -> bool:
        if not self.enabled():
            return False
        if not bool(getattr(config, "ITM_V2_HOLD_OVERNIGHT_ENABLED", True)):
            return False
        min_entry_dte = int(getattr(config, "ITM_V2_DTE_MIN", 5))
        if entry_dte < min_entry_dte:
            return False
        if live_dte is None:
            return False
        force_exit_dte = int(getattr(config, "ITM_V2_FORCE_EXIT_DTE", 1))
        return live_dte > force_exit_dte

    def get_exit_profile(self) -> Tuple[float, float, float, float, int]:
        return (
            float(getattr(config, "ITM_V2_TARGET_PCT", 0.35)),
            float(getattr(config, "ITM_V2_STOP_PCT", 0.22)),
            float(getattr(config, "ITM_V2_TRAIL_TRIGGER", 0.15)),
            float(getattr(config, "ITM_V2_TRAIL_PCT", 0.40)),
            int(getattr(config, "ITM_V2_FORCE_EXIT_DTE", 1)),
        )

    def get_max_hold_days(self) -> int:
        return int(getattr(config, "ITM_V2_MAX_HOLD_DAYS", 4))

    def emit_daily_summary(self, current_date: str) -> None:
        if self._diag_counts:
            parts = [f"{k}={v}" for k, v in sorted(self._diag_counts.items())]
            self._log(f"ITM_V2_DAILY_SUMMARY|{current_date}|" + "|".join(parts), trades_only=True)
        if self._diag_block_codes:
            parts = [f"{k}={v}" for k, v in sorted(self._diag_block_codes.items())]
            self._log(
                f"ITM_V2_DAILY_BLOCK_CODES|{current_date}|" + "|".join(parts),
                trades_only=True,
            )
        self._diag_counts = {}
        self._diag_block_codes = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consecutive_losses": self._consecutive_losses,
            "call_consecutive_losses": self._call_consecutive_losses,
            "put_consecutive_losses": self._put_consecutive_losses,
            "pause_until": self._pause_until,
            "call_pause_until": self._call_pause_until,
            "put_pause_until": self._put_pause_until,
            "last_exit_date_by_direction": dict(self._last_exit_date_by_direction),
            "equity_history": list(self._equity_history),
            "dd_blocked": self._dd_blocked,
            "diag_counts": dict(self._diag_counts),
            "diag_block_codes": dict(self._diag_block_codes),
        }

    def from_dict(self, state: Dict[str, Any]) -> None:
        self._consecutive_losses = int(state.get("consecutive_losses", 0) or 0)
        self._call_consecutive_losses = int(state.get("call_consecutive_losses", 0) or 0)
        self._put_consecutive_losses = int(state.get("put_consecutive_losses", 0) or 0)
        self._pause_until = str(state.get("pause_until") or "")[:10] or None
        self._call_pause_until = str(state.get("call_pause_until") or "")[:10] or None
        self._put_pause_until = str(state.get("put_pause_until") or "")[:10] or None
        self._last_exit_date_by_direction = {
            str(k).upper(): str(v)[:10]
            for k, v in (state.get("last_exit_date_by_direction", {}) or {}).items()
            if str(k).upper() in {"CALL", "PUT"} and str(v)
        }
        self._equity_history = []
        for row in state.get("equity_history", []) or []:
            try:
                dt = str(row[0])[:10]
                val = float(row[1])
                if dt and val > 0:
                    self._equity_history.append((dt, val))
            except Exception:
                continue
        self._dd_blocked = bool(state.get("dd_blocked", False))
        self._diag_counts = {
            str(k): int(v) for k, v in (state.get("diag_counts", {}) or {}).items() if str(k)
        }
        self._diag_block_codes = {
            str(k): int(v) for k, v in (state.get("diag_block_codes", {}) or {}).items() if str(k)
        }

    def reset(self) -> None:
        self._consecutive_losses = 0
        self._call_consecutive_losses = 0
        self._put_consecutive_losses = 0
        self._pause_until = None
        self._call_pause_until = None
        self._put_pause_until = None
        self._last_exit_date_by_direction = {}
        self._equity_history = []
        self._dd_blocked = False
        self._diag_counts = {}
        self._diag_block_codes = {}
