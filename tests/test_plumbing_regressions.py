"""Regression tests for core plumbing isolation fixes."""

from engines.satellite.options_engine import OptionContract, OptionsEngine, OptionsPosition


def test_options_engine_reset_clears_intraday_pending_maps() -> None:
    engine = OptionsEngine()
    engine._pending_intraday_entries = {
        "MICRO|QQQ 260127C00455000": {
            "symbol": "QQQ 260127C00455000",
            "lane": "MICRO",
        }
    }
    engine._pending_intraday_exit_lanes = {"MICRO"}
    engine._pending_intraday_exit_symbols = {"QQQ 260127C00455000"}

    engine.reset()

    assert engine._pending_intraday_entries == {}
    assert engine._pending_intraday_exit_lanes == set()
    assert engine._pending_intraday_exit_symbols == set()


def test_options_engine_restore_clears_non_hold_intraday_lane_payloads() -> None:
    contract = OptionContract(
        symbol="QQQ 260127C00455000",
        strike=455.0,
        expiry="2026-01-27",
        days_to_expiry=1,
    )
    position = OptionsPosition(
        contract=contract,
        entry_price=1.00,
        entry_time="10:45:00",
        entry_score=3.2,
        num_contracts=20,
        stop_price=0.80,
        target_price=1.50,
        stop_pct=0.20,
        entry_strategy="MICRO_DEBIT_FADE",
    )
    state = {"intraday_positions": {"MICRO": [position.to_dict()], "ITM": []}}

    engine = OptionsEngine()
    engine.restore_state(state)

    assert engine._intraday_positions["MICRO"] == []
    assert engine._intraday_position is None
