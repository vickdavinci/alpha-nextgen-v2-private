"""
Monthly P&L Tracker - Tracks realized P&L by engine and month.

Provides visibility into:
- Monthly P&L per engine (TREND, MR, OPT, HEDGE, YIELD)
- Win/loss counts per engine per month
- Total portfolio P&L aggregation
- Persists via ObjectStore for survival across restarts

V6.12: Initial implementation for portfolio-wide P&L visibility.
"""

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from AlgorithmImports import QCAlgorithm

# Engine source categories
ENGINE_SOURCES = {
    "TREND": "TREND",
    "MR": "MR",
    "OPT": "OPT",
    "OPT_INTRADAY": "OPT",  # Roll up intraday options into OPT
    "OPT_SPREAD": "OPT",  # Roll up spread options into OPT
    "HEDGE": "HEDGE",
    "YIELD": "YIELD",
    "COLD_START": "TREND",  # Cold start is part of trend
    "RISK": "RISK",  # Kill switch liquidations
    "ROUTER": "OTHER",  # Router-initiated trades
}

# ObjectStore key
PNL_TRACKER_KEY = "ALPHA_NEXTGEN_MONTHLY_PNL"


@dataclass
class TradeRecord:
    """Single trade record for P&L tracking."""

    symbol: str
    engine: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    realized_pnl: float
    fees: float
    is_win: bool

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "symbol": self.symbol,
            "engine": self.engine,
            "entry_date": self.entry_date,
            "exit_date": self.exit_date,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "realized_pnl": self.realized_pnl,
            "fees": self.fees,
            "is_win": self.is_win,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeRecord":
        """Deserialize from dictionary."""
        return cls(
            symbol=data["symbol"],
            engine=data["engine"],
            entry_date=data["entry_date"],
            exit_date=data["exit_date"],
            entry_price=data["entry_price"],
            exit_price=data["exit_price"],
            quantity=data["quantity"],
            realized_pnl=data["realized_pnl"],
            fees=data.get("fees", 0.0),
            is_win=data["is_win"],
        )


@dataclass
class MonthlyStats:
    """Aggregated stats for a single month."""

    month: str  # Format: "YYYY-MM"
    trades: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl: float = 0.0
    fees: float = 0.0

    # Per-engine breakdown
    engine_pnl: Dict[str, float] = field(default_factory=dict)
    engine_trades: Dict[str, int] = field(default_factory=dict)
    engine_wins: Dict[str, int] = field(default_factory=dict)

    def win_rate(self) -> float:
        """Calculate win rate percentage."""
        if self.trades == 0:
            return 0.0
        return (self.wins / self.trades) * 100

    def net_pnl(self) -> float:
        """Net P&L after fees."""
        return self.realized_pnl - self.fees

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "month": self.month,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "realized_pnl": self.realized_pnl,
            "fees": self.fees,
            "engine_pnl": self.engine_pnl,
            "engine_trades": self.engine_trades,
            "engine_wins": self.engine_wins,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MonthlyStats":
        """Deserialize from dictionary."""
        stats = cls(month=data["month"])
        stats.trades = data.get("trades", 0)
        stats.wins = data.get("wins", 0)
        stats.losses = data.get("losses", 0)
        stats.realized_pnl = data.get("realized_pnl", 0.0)
        stats.fees = data.get("fees", 0.0)
        stats.engine_pnl = data.get("engine_pnl", {})
        stats.engine_trades = data.get("engine_trades", {})
        stats.engine_wins = data.get("engine_wins", {})
        return stats


class MonthlyPnLTracker:
    """
    Tracks realized P&L by engine and month.

    Features:
    - Records each closed trade with engine source
    - Aggregates by month and engine
    - Persists via ObjectStore
    - Provides summary logging at EOD/EOM

    Usage:
        tracker = MonthlyPnLTracker(algorithm)

        # On trade close
        tracker.record_trade(
            symbol="QLD",
            engine="TREND",
            entry_date="2024-01-15",
            exit_date="2024-01-20",
            entry_price=50.0,
            exit_price=55.0,
            quantity=100,
            fees=2.0
        )

        # Get monthly summary
        stats = tracker.get_month_stats("2024-01")

        # Log summary
        tracker.log_monthly_summary("2024-01")
    """

    def __init__(self, algorithm: Optional["QCAlgorithm"] = None):
        """
        Initialize tracker.

        Args:
            algorithm: QCAlgorithm instance (None for testing)
        """
        self.algorithm = algorithm

        # Trade records by month: {"YYYY-MM": [TradeRecord, ...]}
        self._trades_by_month: Dict[str, List[TradeRecord]] = {}

        # Cached monthly stats
        self._monthly_stats: Dict[str, MonthlyStats] = {}

        # Running totals for current session
        self._session_trades = 0
        self._session_pnl = 0.0

    def log(self, message: str, trades_only: bool = True) -> None:
        """Log via algorithm.

        Note: trades_only parameter is kept for API compatibility but
        QuantConnect's Log method doesn't support it directly.
        """
        if self.algorithm:
            self.algorithm.Log(message)

    # =========================================================================
    # Trade Recording
    # =========================================================================

    def record_trade(
        self,
        symbol: str,
        engine: str,
        entry_date: str,
        exit_date: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        fees: float = 0.0,
        realized_pnl: Optional[float] = None,
    ) -> TradeRecord:
        """
        Record a completed trade.

        Args:
            symbol: Ticker symbol
            engine: Source engine (TREND, MR, OPT, etc.)
            entry_date: Entry date (YYYY-MM-DD)
            exit_date: Exit date (YYYY-MM-DD)
            entry_price: Entry fill price
            exit_price: Exit fill price
            quantity: Number of shares/contracts (absolute value)
            fees: Total fees for the trade
            realized_pnl: Optional explicit realized P&L override. When provided,
                this value is used directly instead of derived (exit-entry)*qty.

        Returns:
            TradeRecord created
        """
        # Normalize engine to category
        engine_category = ENGINE_SOURCES.get(engine, "OTHER")

        # Calculate P&L (or use explicit override for complex products like spreads).
        if realized_pnl is None:
            # For options: quantity is contracts, price is per-share (×100 multiplier)
            is_option = len(symbol) > 10 or "C0" in symbol or "P0" in symbol
            multiplier = 100 if is_option else 1
            realized_pnl_calc = (exit_price - entry_price) * abs(quantity) * multiplier
        else:
            realized_pnl_calc = float(realized_pnl)
        is_win = realized_pnl_calc > 0

        # Create record
        record = TradeRecord(
            symbol=symbol,
            engine=engine_category,
            entry_date=entry_date,
            exit_date=exit_date,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=abs(quantity),
            realized_pnl=realized_pnl_calc,
            fees=fees,
            is_win=is_win,
        )

        # Extract month from exit date
        month = exit_date[:7]  # "YYYY-MM"

        # Store record
        if month not in self._trades_by_month:
            self._trades_by_month[month] = []
        self._trades_by_month[month].append(record)

        # Invalidate cached stats for this month
        if month in self._monthly_stats:
            del self._monthly_stats[month]

        # Update session counters
        self._session_trades += 1
        self._session_pnl += realized_pnl_calc - fees

        # Log trade
        win_str = "WIN" if is_win else "LOSS"
        display_symbol = symbol if str(symbol).startswith("SPREAD:") else str(symbol)[-15:]
        self.log(
            f"PNL_TRACK: {win_str} | {engine_category} | {display_symbol} | "
            f"P&L=${realized_pnl_calc:+,.0f} | Entry=${entry_price:.2f} Exit=${exit_price:.2f} | "
            f"Qty={quantity} | Month={month}"
        )

        return record

    def record_trade_from_fill(
        self,
        symbol: str,
        source: str,
        entry_date: str,
        exit_date: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        direction: str = "BUY",
        fees: float = 0.0,
    ) -> TradeRecord:
        """
        Record trade from fill data (handles direction).

        Args:
            symbol: Ticker symbol
            source: Source from TargetWeight
            entry_date: Entry date
            exit_date: Exit date
            entry_price: Entry price
            exit_price: Exit price
            quantity: Quantity (positive)
            direction: "BUY" or "SELL" (for short positions)
            fees: Fees paid

        Returns:
            TradeRecord created
        """
        # Adjust P&L direction for shorts
        if direction == "SELL":
            # Short position: profit when price goes down
            adjusted_entry = exit_price
            adjusted_exit = entry_price
        else:
            adjusted_entry = entry_price
            adjusted_exit = exit_price

        return self.record_trade(
            symbol=symbol,
            engine=source,
            entry_date=entry_date,
            exit_date=exit_date,
            entry_price=adjusted_entry,
            exit_price=adjusted_exit,
            quantity=quantity,
            fees=fees,
        )

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_month_stats(self, month: str) -> MonthlyStats:
        """
        Get aggregated stats for a month.

        Args:
            month: Month in "YYYY-MM" format

        Returns:
            MonthlyStats for the month
        """
        # Return cached if available
        if month in self._monthly_stats:
            return self._monthly_stats[month]

        # Calculate from trades
        stats = MonthlyStats(month=month)

        trades = self._trades_by_month.get(month, [])
        for trade in trades:
            stats.trades += 1
            stats.realized_pnl += trade.realized_pnl
            stats.fees += trade.fees

            if trade.is_win:
                stats.wins += 1
            else:
                stats.losses += 1

            # Engine breakdown
            eng = trade.engine
            stats.engine_pnl[eng] = stats.engine_pnl.get(eng, 0.0) + trade.realized_pnl
            stats.engine_trades[eng] = stats.engine_trades.get(eng, 0) + 1
            if trade.is_win:
                stats.engine_wins[eng] = stats.engine_wins.get(eng, 0) + 1

        # Cache result
        self._monthly_stats[month] = stats
        return stats

    def get_all_months(self) -> List[str]:
        """Get list of all months with trades."""
        return sorted(self._trades_by_month.keys())

    def get_ytd_stats(self, year: str) -> MonthlyStats:
        """
        Get year-to-date aggregated stats.

        Args:
            year: Year in "YYYY" format

        Returns:
            Aggregated MonthlyStats for the year
        """
        ytd = MonthlyStats(month=f"{year}-YTD")

        for month in self._trades_by_month.keys():
            if month.startswith(year):
                monthly = self.get_month_stats(month)
                ytd.trades += monthly.trades
                ytd.wins += monthly.wins
                ytd.losses += monthly.losses
                ytd.realized_pnl += monthly.realized_pnl
                ytd.fees += monthly.fees

                # Merge engine stats
                for eng, pnl in monthly.engine_pnl.items():
                    ytd.engine_pnl[eng] = ytd.engine_pnl.get(eng, 0.0) + pnl
                for eng, cnt in monthly.engine_trades.items():
                    ytd.engine_trades[eng] = ytd.engine_trades.get(eng, 0) + cnt
                for eng, wins in monthly.engine_wins.items():
                    ytd.engine_wins[eng] = ytd.engine_wins.get(eng, 0) + wins

        return ytd

    def get_session_stats(self) -> Dict[str, Any]:
        """Get current session stats."""
        return {
            "trades": self._session_trades,
            "pnl": self._session_pnl,
        }

    def log_optimization_summary(self, current_date: str) -> None:
        """
        Log one compact line for optimization decisions.

        Includes MTD totals and options-only diagnostics so tuning can be
        evaluated quickly without parsing multiple sections.
        """
        month = current_date[:7]
        stats = self.get_month_stats(month)

        opt_trades = stats.engine_trades.get("OPT", 0)
        opt_wins = stats.engine_wins.get("OPT", 0)
        opt_win_rate = (opt_wins / opt_trades * 100.0) if opt_trades > 0 else 0.0
        opt_gross = stats.engine_pnl.get("OPT", 0.0)
        opt_fee_share = (
            (stats.fees / abs(stats.realized_pnl) * 100.0) if stats.realized_pnl else 0.0
        )

        self.log(
            f"OPTIMIZATION_SUMMARY: {current_date} | "
            f"SessionTrades={self._session_trades} SessionPnL=${self._session_pnl:+,.0f} | "
            f"MTD Trades={stats.trades} Win%={stats.win_rate():.1f}% Gross=${stats.realized_pnl:+,.0f} "
            f"Fees=${stats.fees:,.0f} Net=${stats.net_pnl():+,.0f} | "
            f"OPT Trades={opt_trades} Win%={opt_win_rate:.1f}% Gross=${opt_gross:+,.0f} "
            f"FeeLoad={opt_fee_share:.1f}%"
        )

    # =========================================================================
    # Logging
    # =========================================================================

    def log_monthly_summary(self, month: str) -> None:
        """
        Log summary for a month.

        Args:
            month: Month in "YYYY-MM" format
        """
        stats = self.get_month_stats(month)

        if stats.trades == 0:
            self.log(f"MONTHLY_PNL: {month} | No trades recorded")
            return

        # Header
        self.log(
            f"MONTHLY_PNL: {month} | "
            f"Trades={stats.trades} | Win%={stats.win_rate():.1f}% | "
            f"Gross=${stats.realized_pnl:+,.0f} | Fees=${stats.fees:,.0f} | "
            f"Net=${stats.net_pnl():+,.0f}"
        )

        # Per-engine breakdown
        for eng in sorted(stats.engine_pnl.keys()):
            eng_pnl = stats.engine_pnl.get(eng, 0)
            eng_trades = stats.engine_trades.get(eng, 0)
            eng_wins = stats.engine_wins.get(eng, 0)
            eng_win_rate = (eng_wins / eng_trades * 100) if eng_trades > 0 else 0

            self.log(
                f"  {eng:8s}: P&L=${eng_pnl:+8,.0f} | "
                f"Trades={eng_trades:3d} | Win%={eng_win_rate:5.1f}%"
            )

    def log_ytd_summary(self, year: str) -> None:
        """
        Log year-to-date summary.

        Args:
            year: Year in "YYYY" format
        """
        stats = self.get_ytd_stats(year)

        if stats.trades == 0:
            self.log(f"YTD_PNL: {year} | No trades recorded")
            return

        self.log(
            f"YTD_PNL: {year} | "
            f"Trades={stats.trades} | Win%={stats.win_rate():.1f}% | "
            f"Gross=${stats.realized_pnl:+,.0f} | Net=${stats.net_pnl():+,.0f}"
        )

        # Per-engine breakdown
        for eng in sorted(stats.engine_pnl.keys()):
            eng_pnl = stats.engine_pnl.get(eng, 0)
            eng_trades = stats.engine_trades.get(eng, 0)
            eng_wins = stats.engine_wins.get(eng, 0)
            eng_win_rate = (eng_wins / eng_trades * 100) if eng_trades > 0 else 0

            self.log(
                f"  {eng:8s}: P&L=${eng_pnl:+10,.0f} | "
                f"Trades={eng_trades:4d} | Win%={eng_win_rate:5.1f}%"
            )

    def log_eod_summary(self, current_date: str) -> None:
        """
        Log end-of-day summary.

        Args:
            current_date: Current date in "YYYY-MM-DD" format
        """
        month = current_date[:7]
        stats = self.get_month_stats(month)

        self.log(
            f"EOD_PNL: {current_date} | "
            f"Session: {self._session_trades} trades, ${self._session_pnl:+,.0f} | "
            f"MTD: {stats.trades} trades, ${stats.net_pnl():+,.0f}"
        )

    # =========================================================================
    # Persistence
    # =========================================================================

    def get_state_for_persistence(self) -> Dict[str, Any]:
        """Get state for ObjectStore persistence."""
        trades_data = {}
        for month, trades in self._trades_by_month.items():
            trades_data[month] = [t.to_dict() for t in trades]

        return {
            "version": "1.0",
            "trades_by_month": trades_data,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        """Restore state from ObjectStore."""
        version = data.get("version", "1.0")

        trades_data = data.get("trades_by_month", {})
        self._trades_by_month = {}

        for month, trades in trades_data.items():
            self._trades_by_month[month] = [TradeRecord.from_dict(t) for t in trades]

        # Clear cached stats
        self._monthly_stats = {}

        # Count total trades loaded
        total = sum(len(t) for t in self._trades_by_month.values())
        self.log(f"PNL_TRACKER: Restored {total} trades across {len(self._trades_by_month)} months")

    def save(self) -> bool:
        """Save state to ObjectStore."""
        if not self.algorithm:
            return False

        try:
            data = self.get_state_for_persistence()
            self.algorithm.ObjectStore.Save(PNL_TRACKER_KEY, json.dumps(data))
            self.log(f"PNL_TRACKER: Saved state")
            return True
        except Exception as e:
            self.log(f"PNL_TRACKER: Save failed - {e}")
            return False

    def load(self) -> bool:
        """Load state from ObjectStore."""
        if not self.algorithm:
            return False

        try:
            if not self.algorithm.ObjectStore.ContainsKey(PNL_TRACKER_KEY):
                self.log("PNL_TRACKER: No saved state found")
                return False

            raw = self.algorithm.ObjectStore.Read(PNL_TRACKER_KEY)
            data = json.loads(raw)
            self.restore_state(data)
            return True
        except Exception as e:
            self.log(f"PNL_TRACKER: Load failed - {e}")
            return False

    # =========================================================================
    # Reset
    # =========================================================================

    def reset_session(self) -> None:
        """Reset session counters (call at start of day)."""
        self._session_trades = 0
        self._session_pnl = 0.0

    def reset_all(self) -> None:
        """Reset all data (for testing)."""
        self._trades_by_month = {}
        self._monthly_stats = {}
        self._session_trades = 0
        self._session_pnl = 0.0
