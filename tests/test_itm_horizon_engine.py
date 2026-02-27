from types import SimpleNamespace

import config
from engines.satellite.itm_horizon_engine import ITMHorizonEngine
from models.enums import OptionDirection


def _make_host(regime_score: float = 70.0):
    decisions = []
    sma = SimpleNamespace(IsReady=True, Current=SimpleNamespace(Value=100.0))
    algo = SimpleNamespace(qqq_sma20=sma, _get_vix_level=lambda: 17.0)

    def _record_regime_decision(**kwargs):
        decisions.append(kwargs)

    host = SimpleNamespace(
        algorithm=algo,
        _get_regime_transition_context=lambda: {"transition_score": regime_score},
        evaluate_transition_policy_block=lambda **kwargs: (None, ""),
        _record_regime_decision=_record_regime_decision,
        decisions=decisions,
    )
    return host


def test_get_direction_proposal_sma20_band_and_regime(monkeypatch):
    monkeypatch.setattr(config, "ITM_CALL_MIN_REGIME", 35.0)
    monkeypatch.setattr(config, "ITM_PUT_MAX_REGIME", 70.0)
    monkeypatch.setattr(config, "ITM_SMA_BAND_PCT_LOW_VIX", 0.015)

    engine = ITMHorizonEngine()

    host = _make_host(regime_score=72.0)
    direction, reason = engine.get_direction_proposal(
        host=host,
        qqq_current=103.0,
        transition_ctx={"transition_score": 72.0},
    )
    assert direction == OptionDirection.CALL
    assert "SMA20+band" in reason

    host = _make_host(regime_score=30.0)
    direction, reason = engine.get_direction_proposal(
        host=host,
        qqq_current=96.0,
        transition_ctx={"transition_score": 30.0},
    )
    assert direction == OptionDirection.PUT
    assert "SMA20-band" in reason


def test_on_trade_closed_tracks_directional_losses(monkeypatch):
    monkeypatch.setattr(config, "ITM_ENGINE_ENABLED", True)
    monkeypatch.setattr(config, "ITM_DIRECTIONAL_BREAKER_ENABLED", True)
    monkeypatch.setattr(config, "ITM_DIRECTIONAL_BREAKER_3_LOSSES_PAUSE_DAYS", 2)
    monkeypatch.setattr(config, "ITM_BREAKER_3_LOSSES_PAUSE_DAYS", 2)
    monkeypatch.setattr(config, "ITM_BREAKER_5_LOSSES_PAUSE_DAYS", 5)

    engine = ITMHorizonEngine()

    for _ in range(3):
        engine.on_trade_closed(
            symbol="QQQ240119C00450000",
            is_win=False,
            current_time="2024-01-10 11:00:00",
            strategy="ITM_MOMENTUM",
            algorithm=None,
        )

    assert engine._call_pause_until is not None
    assert engine._call_consecutive_losses == 0


def test_should_hold_overnight_uses_dte_policy(monkeypatch):
    monkeypatch.setattr(config, "ITM_ENGINE_ENABLED", True)
    monkeypatch.setattr(config, "ITM_HOLD_OVERNIGHT_ENABLED", True)
    monkeypatch.setattr(config, "ITM_DTE_MIN", 5)
    monkeypatch.setattr(config, "ITM_FORCE_EXIT_DTE", 1)

    engine = ITMHorizonEngine()

    assert engine.should_hold_overnight(entry_dte=6, live_dte=4) is True
    assert engine.should_hold_overnight(entry_dte=4, live_dte=4) is False
    assert engine.should_hold_overnight(entry_dte=6, live_dte=1) is False


def test_get_exit_profile_vix_tiers(monkeypatch):
    monkeypatch.setattr(config, "ITM_TIERED_EXIT_ENABLED", True)
    monkeypatch.setattr(config, "ITM_MED_VIX_THRESHOLD", 18.0)
    monkeypatch.setattr(config, "ITM_HIGH_VIX_THRESHOLD", 25.0)
    monkeypatch.setattr(config, "ITM_TARGET_PCT_LOW_VIX", 0.35)
    monkeypatch.setattr(config, "ITM_TARGET_PCT_MED_VIX", 0.40)
    monkeypatch.setattr(config, "ITM_TARGET_PCT_HIGH_VIX", 0.45)
    monkeypatch.setattr(config, "ITM_STOP_PCT_LOW_VIX", 0.22)
    monkeypatch.setattr(config, "ITM_STOP_PCT_MED_VIX", 0.25)
    monkeypatch.setattr(config, "ITM_STOP_PCT_HIGH_VIX", 0.28)

    engine = ITMHorizonEngine()

    low_profile = engine.get_exit_profile(vix_current=15.0)
    med_profile = engine.get_exit_profile(vix_current=20.0)
    high_profile = engine.get_exit_profile(vix_current=30.0)

    assert low_profile[0] < med_profile[0] < high_profile[0]
    assert high_profile[0] == 0.45
    assert high_profile[1] == 0.28


def test_state_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "ITM_ENGINE_ENABLED", True)

    engine = ITMHorizonEngine()
    engine.on_trade_closed(
        symbol="QQQ240119P00450000",
        is_win=False,
        current_time="2024-01-10 11:00:00",
        strategy="ITM_MOMENTUM",
        algorithm=None,
    )

    state = engine.to_dict()

    restored = ITMHorizonEngine()
    restored.from_dict(state)

    assert restored.to_dict()["put_consecutive_losses"] == state["put_consecutive_losses"]
    assert restored.to_dict()["last_exit_date_by_direction"] == state["last_exit_date_by_direction"]


def test_reset_daily_clears_only_diagnostics(monkeypatch):
    monkeypatch.setattr(config, "ITM_ENGINE_ENABLED", True)

    engine = ITMHorizonEngine()
    engine._diag_counts = {"ITM_ENGINE_Pass": 4}
    engine._diag_block_codes = {"E_ITM_ENGINE_ADX_WEAK": 2}
    engine._consecutive_losses = 3
    engine._pause_until = "2024-01-12"

    engine.reset_daily()

    assert engine._diag_counts == {}
    assert engine._diag_block_codes == {}
    assert engine._consecutive_losses == 3
    assert engine._pause_until == "2024-01-12"
