from datetime import datetime, timedelta
from types import SimpleNamespace

import config
from engines.satellite.options_primitives import SpreadStrategy
from engines.satellite.vass_entry_engine import VASSEntryEngine
from models.enums import OptionDirection


def test_select_strategy_routing_matrix_credit_medium(monkeypatch):
    monkeypatch.setattr(config, "VASS_MEDIUM_IV_PREFER_CREDIT", True)
    monkeypatch.setattr(config, "VASS_LOW_IV_DTE_MIN", 30)
    monkeypatch.setattr(config, "VASS_LOW_IV_DTE_MAX", 45)
    monkeypatch.setattr(config, "VASS_MEDIUM_IV_DTE_MIN", 14)
    monkeypatch.setattr(config, "VASS_MEDIUM_IV_DTE_MAX", 30)
    monkeypatch.setattr(config, "VASS_HIGH_IV_DTE_MIN", 5)
    monkeypatch.setattr(config, "VASS_HIGH_IV_DTE_MAX", 21)

    engine = VASSEntryEngine()

    strategy, dte_min, dte_max = engine.select_strategy(
        direction="BULLISH",
        iv_environment="LOW",
        is_intraday=False,
        spread_strategy_enum=SpreadStrategy,
    )
    assert strategy == SpreadStrategy.BULL_CALL_DEBIT
    assert (dte_min, dte_max) == (30, 45)

    strategy, dte_min, dte_max = engine.select_strategy(
        direction="BULLISH",
        iv_environment="MEDIUM",
        is_intraday=False,
        spread_strategy_enum=SpreadStrategy,
    )
    assert strategy == SpreadStrategy.BULL_PUT_CREDIT
    assert (dte_min, dte_max) == (14, 30)

    strategy, dte_min, dte_max = engine.select_strategy(
        direction="BEARISH",
        iv_environment="HIGH",
        is_intraday=False,
        spread_strategy_enum=SpreadStrategy,
    )
    assert strategy == SpreadStrategy.BEAR_CALL_CREDIT
    assert (dte_min, dte_max) == (5, 21)


def test_check_similar_entry_guard_blocks_burst_and_cooldown(monkeypatch):
    monkeypatch.setattr(config, "VASS_SIMILAR_ENTRY_MIN_GAP_MINUTES", 15)
    monkeypatch.setattr(config, "VASS_SIMILAR_ENTRY_COOLDOWN_DAYS", 3)

    engine = VASSEntryEngine()
    now_dt = datetime(2024, 1, 2, 10, 0, 0)
    sig = "BULL_CALL_DEBIT|CALL|DTE:15"

    assert engine.check_similar_entry_guard(signature=sig, now_dt=now_dt) is None
    engine.record_signature_entry(signature=sig, entry_dt=now_dt)

    burst_block = engine.check_similar_entry_guard(
        signature=sig, now_dt=now_dt + timedelta(minutes=5)
    )
    assert burst_block == "E_VASS_SIMILAR_15M_BLOCK"

    cooldown_block = engine.check_similar_entry_guard(
        signature=sig,
        now_dt=now_dt + timedelta(minutes=20),
    )
    assert cooldown_block == "E_VASS_SIMILAR_3D_COOLDOWN"


def test_record_spread_result_arms_loss_breaker(monkeypatch):
    monkeypatch.setattr(config, "VASS_LOSS_BREAKER_ENABLED", True)
    monkeypatch.setattr(config, "VASS_LOSS_BREAKER_THRESHOLD", 3)
    monkeypatch.setattr(config, "VASS_LOSS_BREAKER_PAUSE_DAYS", 1)

    engine = VASSEntryEngine()
    now_dt = datetime(2024, 1, 5, 12, 0, 0)

    assert engine.record_spread_result(is_win=False, now_dt=now_dt) is None
    assert engine.record_spread_result(is_win=False, now_dt=now_dt) is None
    pause_until = engine.record_spread_result(is_win=False, now_dt=now_dt)
    assert pause_until == "2024-01-08"

    assert engine.should_block_for_loss_breaker("2024-01-08") is True
    assert engine.should_block_for_loss_breaker("2024-01-09") is False


def test_check_direction_day_gap_and_state_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "VASS_DIRECTION_MIN_GAP_ENABLED", True)
    monkeypatch.setattr(config, "VASS_DIRECTION_MIN_GAP_MINUTES", 30)
    monkeypatch.setattr(config, "VASS_DIRECTION_DAY_GAP_ENABLED", True)

    engine = VASSEntryEngine()
    algo = SimpleNamespace(Time=datetime(2024, 1, 10, 10, 0, 0))

    assert (
        engine.check_direction_day_gap(
            direction=OptionDirection.CALL,
            current_date="2024-01-10",
            algorithm=algo,
        )
        is None
    )

    engine.record_direction_day_entry(direction=OptionDirection.CALL, entry_dt=algo.Time)

    min_gap_reason = engine.check_direction_day_gap(
        direction=OptionDirection.CALL,
        current_date="2024-01-10",
        algorithm=SimpleNamespace(Time=datetime(2024, 1, 10, 10, 15, 0)),
    )
    assert str(min_gap_reason).startswith("R_DIRECTION_MIN_GAP")

    state = engine.to_dict()
    restored = VASSEntryEngine()
    restored.from_dict(state)
    assert restored.to_dict()["last_entry_date_by_direction"]["BULLISH"] == "2024-01-10"


def test_reset_daily_only_clears_slot_backoff(monkeypatch):
    monkeypatch.setattr(config, "VASS_SLOT_BACKOFF_ENABLED", True)
    monkeypatch.setattr(config, "VASS_SLOT_BACKOFF_MINUTES", 20)

    engine = VASSEntryEngine()
    key = engine._slot_backoff_key(direction=OptionDirection.CALL, overlay_state="NORMAL")
    engine._arm_slot_backoff(now=datetime(2024, 1, 10, 10, 0, 0), key=key)
    engine.record_direction_day_entry(
        direction=OptionDirection.CALL,
        entry_dt=datetime(2024, 1, 10, 10, 0, 0),
    )

    assert engine._slot_backoff_until_by_key
    assert engine._last_entry_date_by_direction

    engine.reset_daily()

    assert engine._slot_backoff_until_by_key == {}
    assert engine._last_entry_date_by_direction


def test_build_spread_signal_blocks_bear_call_credit_in_risk_on(monkeypatch):
    monkeypatch.setattr(config, "VASS_EV_PRE_GATE_ENABLED", False)
    monkeypatch.setattr(config, "REGIME_RISK_ON", 70)

    engine = VASSEntryEngine()

    class _Host:
        def __init__(self):
            self.algorithm = SimpleNamespace(Time=datetime(2024, 6, 3, 10, 0, 0))
            self.last_failure = None

        def set_last_entry_validation_failure(self, reason):
            self.last_failure = reason

    host = _Host()
    signal, reason = engine.build_spread_signal(
        host=host,
        chain=None,
        candidate_contracts=[],
        direction=OptionDirection.PUT,
        regime_score=75.0,
        qqq_price=450.0,
        adx_value=20.0,
        ma200_value=440.0,
        ma50_value=445.0,
        iv_rank=50.0,
        size_multiplier=1.0,
        portfolio_value=100000.0,
        margin_remaining=50000.0,
        strategy=SpreadStrategy.BEAR_CALL_CREDIT,
        vass_dte_min=5,
        vass_dte_max=21,
        dte_ranges=[(5, 21)],
        is_credit=True,
        is_eod_scan=False,
        fallback_log_prefix="TEST",
    )

    assert signal is None
    assert str(reason).startswith("R_BEAR_CALL_RISK_ON_BLOCK")
    assert host.last_failure == reason


def test_high_iv_sanity_guard_blocks_bull_call_debit_route():
    engine = VASSEntryEngine()
    logs = []
    decisions = []

    class _Host:
        def _record_regime_decision(self, **kwargs):
            decisions.append(kwargs)

    algorithm = SimpleNamespace(Log=lambda msg: logs.append(msg))
    blocked = engine._should_block_high_iv_bull_debit_route(
        strategy=SpreadStrategy.BULL_CALL_DEBIT,
        iv_environment="HIGH",
        algorithm=algorithm,
        host=_Host(),
    )

    assert blocked is True
    assert any("R_ROUTE_SANITY_HIGH_IV_BULL_DEBIT" in msg for msg in logs)
    assert any(d.get("gate_name") == "VASS_ROUTE_SANITY_HIGH_IV_BULL_DEBIT" for d in decisions)
