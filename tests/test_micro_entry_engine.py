from datetime import date
from types import SimpleNamespace

import config
from engines.satellite.micro_entry_engine import MicroEntryEngine
from models.enums import IntradayStrategy, MicroRegime, OptionDirection


def _lane_resolver(strategy_value: str) -> str:
    return "ITM" if strategy_value == IntradayStrategy.ITM_MOMENTUM.value else "MICRO"


def test_validate_lane_caps_itm_and_micro_are_independent(monkeypatch):
    monkeypatch.setattr(config, "ITM_MAX_TRADES_PER_DAY", 4)
    monkeypatch.setattr(config, "MICRO_MAX_TRADES_PER_DAY", 6)

    engine = MicroEntryEngine()

    ok, code, detail, lane = engine.validate_lane_caps(
        entry_strategy=IntradayStrategy.ITM_MOMENTUM,
        engine_positions={"ITM": [object()], "MICRO": []},
        has_pending_engine_entry=lambda _lane: False,
        itm_trades_today=1,
        micro_trades_today=0,
        lane_resolver=_lane_resolver,
        lane_caps={"ITM": 1, "MICRO": 2},
        daily_caps={"ITM": 4, "MICRO": 6},
    )
    assert not ok
    assert code == "R_ITM_CONCURRENT_CAP"
    assert lane == "ITM"
    assert detail == "ITM=1/1"

    ok, code, detail, lane = engine.validate_lane_caps(
        entry_strategy=IntradayStrategy.MICRO_DEBIT_FADE,
        engine_positions={"ITM": [object()], "MICRO": [object(), object()]},
        has_pending_engine_entry=lambda _lane: False,
        itm_trades_today=1,
        micro_trades_today=1,
        lane_resolver=_lane_resolver,
        lane_caps={"ITM": 1, "MICRO": 2},
        daily_caps={"ITM": 4, "MICRO": 6},
    )
    assert not ok
    assert code == "R_MICRO_CONCURRENT_CAP"
    assert lane == "MICRO"
    assert detail == "MICRO=2/2"


def test_validate_contract_friction_strategy_specific_limit(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_ITM_MAX_BID_ASK_SPREAD_PCT", 0.12)

    engine = MicroEntryEngine()
    ok, code, detail = engine.validate_contract_friction(
        strategy_value=IntradayStrategy.ITM_MOMENTUM.value,
        contract_spread_pct=0.13,
    )

    assert not ok
    assert code == "E_INTRADAY_FRICTION_CAP"
    assert "ITM_MOMENTUM" in str(detail)


def test_validate_time_window_boundaries(monkeypatch):
    monkeypatch.setattr(config, "MICRO_DEBIT_FADE_START", "10:00", raising=False)
    monkeypatch.setattr(config, "MICRO_DEBIT_FADE_END", "14:00", raising=False)

    engine = MicroEntryEngine()
    state = SimpleNamespace(micro_regime=MicroRegime.NORMAL)

    ok, code = engine.validate_time_window(
        entry_strategy=IntradayStrategy.MICRO_DEBIT_FADE,
        itm_engine_mode=False,
        state=state,
        current_hour=10,
        current_minute=0,
    )
    assert ok
    assert code is None

    ok, code = engine.validate_time_window(
        entry_strategy=IntradayStrategy.MICRO_DEBIT_FADE,
        itm_engine_mode=False,
        state=state,
        current_hour=9,
        current_minute=59,
    )
    assert not ok
    assert code == "E_INTRADAY_TIME_WINDOW"


def test_apply_pre_contract_gates_call_and_put_specific_rejections(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_CALL_BLOCK_VIX_MIN", 25.0)
    monkeypatch.setattr(config, "MICRO_OTM_STRESS_SOFT_GATE_ENABLED", False)
    monkeypatch.setattr(config, "PUT_ENTRY_VIX_MAX", 36.0)

    engine = MicroEntryEngine()
    state = SimpleNamespace(
        micro_regime=MicroRegime.NORMAL,
        put_cooldown_until_date=None,
        put_consecutive_losses=0,
    )
    algo = SimpleNamespace(qqq_sma20=None)
    iv_sensor = SimpleNamespace(
        is_conviction_ready=lambda: False,
        get_vix_5d_change=lambda: None,
    )

    size, code, _ = engine.apply_pre_contract_gates(
        state=state,
        entry_strategy=IntradayStrategy.MICRO_OTM_MOMENTUM,
        direction=OptionDirection.CALL,
        itm_engine_mode=False,
        current_time="2024-01-10 10:00:00",
        size_multiplier=1.0,
        macro_regime_score=60.0,
        qqq_current=450.0,
        vix_current=26.0,
        vix_level_override=None,
        algorithm=algo,
        iv_sensor=iv_sensor,
        call_cooldown_until_date=None,
        call_consecutive_losses=0,
        transition_ctx={"transition_overlay": "STABLE", "overlay_bars_since_flip": 50},
    )
    assert size == 1.0
    assert code == "E_CALL_GATE_STRESS"

    size, code, _ = engine.apply_pre_contract_gates(
        state=state,
        entry_strategy=IntradayStrategy.MICRO_OTM_MOMENTUM,
        direction=OptionDirection.PUT,
        itm_engine_mode=False,
        current_time="2024-01-10 10:00:00",
        size_multiplier=1.0,
        macro_regime_score=60.0,
        qqq_current=450.0,
        vix_current=40.0,
        vix_level_override=None,
        algorithm=algo,
        iv_sensor=iv_sensor,
        call_cooldown_until_date=date(2024, 1, 1),
        call_consecutive_losses=3,
        transition_ctx={"transition_overlay": "STABLE", "overlay_bars_since_flip": 50},
    )
    assert size == 1.0
    assert code == "E_PUT_GATE_VIX_MAX"
