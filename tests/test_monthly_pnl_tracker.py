from unittest.mock import MagicMock

from utils.monthly_pnl_tracker import MonthlyPnLTracker


def test_options_engines_tracked_independently():
    tracker = MonthlyPnLTracker()
    tracker.record_trade(
        symbol="QQQ 260130C00500000",
        engine="ITM",
        entry_date="2026-01-03",
        exit_date="2026-01-03",
        entry_price=4.0,
        exit_price=4.8,
        quantity=2,
    )
    tracker.record_trade(
        symbol="QQQ 260130P00490000",
        engine="MICRO",
        entry_date="2026-01-03",
        exit_date="2026-01-03",
        entry_price=2.0,
        exit_price=1.5,
        quantity=1,
    )
    tracker.record_trade(
        symbol="SPREAD:BULL_CALL_DEBIT",
        engine="VASS",
        entry_date="2026-01-03",
        exit_date="2026-01-03",
        entry_price=1.2,
        exit_price=1.6,
        quantity=1,
    )

    stats = tracker.get_month_stats("2026-01")
    assert stats.engine_trades.get("ITM") == 1
    assert stats.engine_trades.get("MICRO") == 1
    assert stats.engine_trades.get("VASS") == 1


def test_options_rollup_includes_lane_and_legacy_categories():
    tracker = MonthlyPnLTracker()
    for engine in ("OPT", "OPT_INTRADAY", "OPT_SPREAD", "ITM", "MICRO", "VASS"):
        tracker.record_trade(
            symbol="QQQ 260130C00500000",
            engine=engine,
            entry_date="2026-01-03",
            exit_date="2026-01-03",
            entry_price=2.0,
            exit_price=2.2,
            quantity=1,
        )

    stats = tracker.get_month_stats("2026-01")
    rolled = tracker._rollup_options_stats(stats)
    assert int(rolled["trades"]) == 6
    assert int(rolled["wins"]) == 6


def test_optimization_summary_uses_options_rollup():
    algo = MagicMock()
    tracker = MonthlyPnLTracker(algorithm=algo)
    tracker.record_trade(
        symbol="QQQ 260130C00500000",
        engine="ITM",
        entry_date="2026-01-03",
        exit_date="2026-01-03",
        entry_price=3.0,
        exit_price=3.5,
        quantity=1,
    )
    tracker.record_trade(
        symbol="QQQ 260130P00490000",
        engine="MICRO",
        entry_date="2026-01-03",
        exit_date="2026-01-03",
        entry_price=1.8,
        exit_price=1.1,
        quantity=1,
    )
    tracker.log_optimization_summary("2026-01-03")

    last_log = str(algo.Log.call_args_list[-1][0][0])
    assert "OPT Trades=2" in last_log
