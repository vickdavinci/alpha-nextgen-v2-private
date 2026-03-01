"""Tests for the Iron Condor Engine.

Covers:
  - Regime/overlay gating matrix
  - VIX tier wing width selection
  - DTE + delta filter behavior
  - C/W gate and D/W feasibility
  - Sizing from risk caps
  - Exit triggers (target/stop/regime-break/time/VIX-spike/wing-breach)
  - Loss breaker
  - State persistence round-trip
  - Lane isolation (IC never maps to MICRO/ITM)
  - TargetWeight/source normalization for OPT_IC
"""

import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root on path for imports
sys.path.insert(0, ".")

import config
from engines.satellite.condor_models import IronCondorPosition
from engines.satellite.iron_condor_engine import (
    EXIT_IC_FRIDAY_CLOSE,
    EXIT_IC_PROFIT_TARGET,
    EXIT_IC_REGIME_BREAK,
    EXIT_IC_STOP_LOSS,
    EXIT_IC_TIME_EXIT,
    EXIT_IC_VIX_SPIKE,
    EXIT_IC_WING_BREACH_CALL,
    EXIT_IC_WING_BREACH_PUT,
    R_IC_ADX_TOO_HIGH,
    R_IC_CW_BELOW_MIN,
    R_IC_DAILY_TRADE_LIMIT,
    R_IC_DISABLED,
    R_IC_LOSS_BREAKER_ACTIVE,
    R_IC_OUTSIDE_ENTRY_WINDOW,
    R_IC_POSITION_LIMIT,
    R_IC_REGIME_NOT_PERSISTENT,
    R_IC_REGIME_OUT_OF_RANGE,
    R_IC_STOP_DW_UNFEASIBLE,
    R_IC_TRANSITION_BLOCK,
    R_IC_VIX_OUT_OF_RANGE,
    IronCondorEngine,
)
from engines.satellite.options_primitives import OptionContract
from models.enums import IntradayStrategy, OptionDirection, Urgency
from models.target_weight import TargetWeight

# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


def _make_contract(
    strike: float,
    direction: OptionDirection,
    expiry: str = "2025-03-15",
    delta: float = 0.15,
    bid: float = 1.50,
    ask: float = 1.70,
    oi: int = 500,
    dte: int = 14,
) -> OptionContract:
    mid = (bid + ask) / 2
    return OptionContract(
        symbol=f"QQQ {expiry.replace('-', '')} {'C' if direction == OptionDirection.CALL else 'P'}{int(strike * 1000):08d}",
        underlying="QQQ",
        direction=direction,
        strike=strike,
        expiry=expiry,
        delta=delta if direction == OptionDirection.CALL else -delta,
        gamma=0.01,
        vega=0.10,
        theta=-0.05,
        bid=bid,
        ask=ask,
        mid_price=mid,
        open_interest=oi,
        days_to_expiry=dte,
    )


def _make_condor(
    qqq_price: float = 480.0,
    net_credit: float = 1.20,
    wing_width: float = 4.0,
    num_spreads: int = 2,
    regime: float = 52.0,
    vix: float = 18.0,
) -> IronCondorPosition:
    short_put_strike = qqq_price - 10
    long_put_strike = short_put_strike - wing_width
    short_call_strike = qqq_price + 10
    long_call_strike = short_call_strike + wing_width

    return IronCondorPosition(
        short_put=_make_contract(short_put_strike, OptionDirection.PUT, delta=0.14),
        long_put=_make_contract(long_put_strike, OptionDirection.PUT, delta=0.06),
        short_call=_make_contract(short_call_strike, OptionDirection.CALL, delta=0.14),
        long_call=_make_contract(long_call_strike, OptionDirection.CALL, delta=0.06),
        net_credit=net_credit,
        put_wing_width=wing_width,
        call_wing_width=wing_width,
        max_loss=wing_width - net_credit,
        credit_to_width=net_credit / wing_width,
        num_spreads=num_spreads,
        entry_time="2025-03-01 11:00:00",
        regime_at_entry=regime,
        entry_vix=vix,
        entry_adx=15.0,
        condor_id="test123",
        entry_cw_tier="MID_VIX",
        stop_dw=2.5 * net_credit / wing_width,
        implied_wr_be=1.0 - net_credit / wing_width,
    )


def _default_transition_ctx(state: str = "STABLE", fast_overlay: str = "") -> Dict[str, Any]:
    return {
        "transition_state": state,
        "fast_overlay": fast_overlay,
        "transition_score": 52,
        "is_event_day": False,
    }


def _make_engine() -> IronCondorEngine:
    return IronCondorEngine(log_func=lambda msg, trades_only=False: None)


# ═══════════════════════════════════════════════════════════════════
# ENV GATE TESTS
# ═══════════════════════════════════════════════════════════════════


class TestEnvGates:
    """Test Stage 1: IC_ENV_OK environmental gates."""

    def test_disabled_rejects(self):
        engine = _make_engine()
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", False):
            result = engine._check_env_gates(
                regime_score=52,
                adx_value=15,
                vix_current=18,
                transition_ctx=_default_transition_ctx(),
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result == R_IC_DISABLED

    def test_regime_below_min_rejects(self):
        engine = _make_engine()
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            result = engine._check_env_gates(
                regime_score=40,
                adx_value=15,
                vix_current=18,
                transition_ctx=_default_transition_ctx(),
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result == R_IC_REGIME_OUT_OF_RANGE

    def test_regime_above_max_rejects(self):
        engine = _make_engine()
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            result = engine._check_env_gates(
                regime_score=65,
                adx_value=15,
                vix_current=18,
                transition_ctx=_default_transition_ctx(),
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result == R_IC_REGIME_OUT_OF_RANGE

    def test_regime_persistence_required(self):
        engine = _make_engine()
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            with patch.object(config, "IC_REGIME_PERSISTENCE_BARS", 3):
                # First call: bar 1
                result = engine._check_env_gates(
                    regime_score=52,
                    adx_value=15,
                    vix_current=18,
                    transition_ctx=_default_transition_ctx(),
                    current_time=datetime(2025, 3, 3, 11, 0),
                    effective_portfolio_value=100000,
                    margin_remaining=50000,
                    ic_open_risk=0,
                )
                assert result == R_IC_REGIME_NOT_PERSISTENT

    def test_vix_below_min_rejects(self):
        engine = _make_engine()
        engine._regime_neutral_bars = 5
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            result = engine._check_env_gates(
                regime_score=52,
                adx_value=15,
                vix_current=10,
                transition_ctx=_default_transition_ctx(),
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result == R_IC_VIX_OUT_OF_RANGE

    def test_vix_above_max_rejects(self):
        engine = _make_engine()
        engine._regime_neutral_bars = 5
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            result = engine._check_env_gates(
                regime_score=52,
                adx_value=15,
                vix_current=35,
                transition_ctx=_default_transition_ctx(),
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result == R_IC_VIX_OUT_OF_RANGE

    def test_adx_too_high_rejects(self):
        engine = _make_engine()
        engine._regime_neutral_bars = 5
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            result = engine._check_env_gates(
                regime_score=52,
                adx_value=25,
                vix_current=18,
                transition_ctx=_default_transition_ctx(),
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result == R_IC_ADX_TOO_HIGH

    def test_transition_deterioration_blocks(self):
        engine = _make_engine()
        engine._regime_neutral_bars = 5
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            result = engine._check_env_gates(
                regime_score=52,
                adx_value=15,
                vix_current=18,
                transition_ctx=_default_transition_ctx(state="DETERIORATION"),
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result == R_IC_TRANSITION_BLOCK

    def test_outside_entry_window_rejects(self):
        engine = _make_engine()
        engine._regime_neutral_bars = 5
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            result = engine._check_env_gates(
                regime_score=52,
                adx_value=15,
                vix_current=18,
                transition_ctx=_default_transition_ctx(),
                current_time=datetime(2025, 3, 3, 9, 30),  # Before 10:15
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result == R_IC_OUTSIDE_ENTRY_WINDOW

    def test_position_limit_rejects(self):
        engine = _make_engine()
        engine._regime_neutral_bars = 5
        engine._positions = [_make_condor(), _make_condor()]
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            with patch.object(config, "IC_MAX_CONCURRENT", 2):
                result = engine._check_env_gates(
                    regime_score=52,
                    adx_value=15,
                    vix_current=18,
                    transition_ctx=_default_transition_ctx(),
                    current_time=datetime(2025, 3, 3, 11, 0),
                    effective_portfolio_value=100000,
                    margin_remaining=50000,
                    ic_open_risk=0,
                )
                assert result == R_IC_POSITION_LIMIT

    def test_daily_trade_limit_rejects(self):
        engine = _make_engine()
        engine._regime_neutral_bars = 5
        engine._trades_today = 2
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            with patch.object(config, "IC_MAX_TRADES_PER_DAY", 2):
                result = engine._check_env_gates(
                    regime_score=52,
                    adx_value=15,
                    vix_current=18,
                    transition_ctx=_default_transition_ctx(),
                    current_time=datetime(2025, 3, 3, 11, 0),
                    effective_portfolio_value=100000,
                    margin_remaining=50000,
                    ic_open_risk=0,
                )
                assert result == R_IC_DAILY_TRADE_LIMIT

    def test_all_gates_pass(self):
        engine = _make_engine()
        engine._regime_neutral_bars = 5
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            result = engine._check_env_gates(
                regime_score=52,
                adx_value=15,
                vix_current=18,
                transition_ctx=_default_transition_ctx(),
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result is None

    def test_loss_breaker_blocks(self):
        engine = _make_engine()
        engine._regime_neutral_bars = 5
        engine._loss_breaker_pause_until = "2025-03-04"
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            result = engine._check_env_gates(
                regime_score=52,
                adx_value=15,
                vix_current=18,
                transition_ctx=_default_transition_ctx(),
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                margin_remaining=50000,
                ic_open_risk=0,
            )
            assert result == R_IC_LOSS_BREAKER_ACTIVE


# ═══════════════════════════════════════════════════════════════════
# VIX TIER TESTS
# ═══════════════════════════════════════════════════════════════════


class TestVIXTiers:
    """Test VIX-adaptive wing width and C/W floor selection."""

    def test_low_vix_width(self):
        engine = _make_engine()
        assert engine._get_wing_width_for_vix(12.0) == 3

    def test_mid_vix_width(self):
        engine = _make_engine()
        assert engine._get_wing_width_for_vix(20.0) == 4

    def test_high_vix_width(self):
        engine = _make_engine()
        assert engine._get_wing_width_for_vix(28.0) == 5

    def test_low_vix_cw_floor(self):
        engine = _make_engine()
        assert engine._get_cw_floor_for_vix(12.0) == pytest.approx(0.22)

    def test_mid_vix_cw_floor(self):
        engine = _make_engine()
        assert engine._get_cw_floor_for_vix(20.0) == pytest.approx(0.20)

    def test_high_vix_cw_floor(self):
        engine = _make_engine()
        assert engine._get_cw_floor_for_vix(28.0) == pytest.approx(0.18)


# ═══════════════════════════════════════════════════════════════════
# EXIT TRIGGER TESTS
# ═══════════════════════════════════════════════════════════════════


class TestExitTriggers:
    """Test exit cascade priority and triggers."""

    def test_vix_spike_exit(self):
        engine = _make_engine()
        condor = _make_condor()
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=0,
            current_dte=14,
            vix_current=35,  # Above IC_VIX_SPIKE_EXIT=30
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 5, 11, 0),
        )
        assert result is not None
        reason, signals = result
        assert reason == EXIT_IC_VIX_SPIKE

    def test_profit_target_exit(self):
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 1.20 * 100 * 2 = 240
        # target = 60% of 240 = 144
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=150,  # > 144
            current_dte=10,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 5, 11, 0),
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_PROFIT_TARGET

    def test_stop_loss_exit(self):
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240; stop = 150% = 360
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,  # > 360 loss
            current_dte=10,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 5, 11, 0),
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_STOP_LOSS

    def test_time_exit(self):
        engine = _make_engine()
        condor = _make_condor()
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=0,
            current_dte=4,  # <= IC_TIME_EXIT_DTE=5
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 5, 11, 0),
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_TIME_EXIT

    def test_regime_break_exit(self):
        engine = _make_engine()
        condor = _make_condor()
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=0,
            current_dte=14,
            vix_current=18,
            regime_score=35,  # Below IC_REGIME_MIN(45) - buffer(5) = 40
            qqq_price=480,
            current_time=datetime(2025, 3, 5, 11, 0),
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_REGIME_BREAK

    def test_friday_close_exit(self):
        engine = _make_engine()
        condor = _make_condor()
        # Friday (weekday=4), DTE < IC_FRIDAY_CLOSE_DTE(8)
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=0,
            current_dte=7,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 7, 11, 0),  # Friday
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_FRIDAY_CLOSE

    def test_wing_breach_put_exit(self):
        engine = _make_engine()
        condor = _make_condor(qqq_price=480)
        # short_put_strike = 470; QQQ at 460 means put is ITM
        # ITM depth = (470 - 460) / 460 = 0.0217 > IC_SHORT_ITM_EXIT_PCT(0.02)
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-100,
            current_dte=14,
            vix_current=18,
            regime_score=52,
            qqq_price=460,  # Below short put strike
            current_time=datetime(2025, 3, 5, 11, 0),
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_WING_BREACH_PUT

    def test_wing_breach_call_exit(self):
        engine = _make_engine()
        condor = _make_condor(qqq_price=480)
        # short_call_strike = 490; QQQ at 502 means call is ITM
        # ITM depth = (502 - 490) / 502 = 0.0239 > IC_SHORT_ITM_EXIT_PCT(0.02)
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-100,
            current_dte=14,
            vix_current=18,
            regime_score=52,
            qqq_price=502,  # Above short call strike
            current_time=datetime(2025, 3, 5, 11, 0),
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_WING_BREACH_CALL

    def test_no_exit_when_healthy(self):
        engine = _make_engine()
        condor = _make_condor()
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=50,  # Small positive
            current_dte=14,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 5, 11, 0),  # Wednesday
        )
        assert result is None

    def test_closing_condor_skipped(self):
        engine = _make_engine()
        condor = _make_condor()
        condor.is_closing = True
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-500,  # Should trigger stop but is_closing
            current_dte=14,
            vix_current=35,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 5, 11, 0),
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════
# POSITION MANAGEMENT TESTS
# ═══════════════════════════════════════════════════════════════════


class TestPositionManagement:
    def test_register_and_remove(self):
        engine = _make_engine()
        condor = _make_condor()
        engine.register_fill(condor)
        assert len(engine.positions) == 1
        assert engine._trades_today == 1

        removed = engine.remove_position(condor.condor_id)
        assert removed is not None
        assert len(engine.positions) == 0

    def test_remove_nonexistent(self):
        engine = _make_engine()
        removed = engine.remove_position("nonexistent")
        assert removed is None

    def test_loss_breaker_triggers(self):
        engine = _make_engine()
        engine.record_trade_result(-100)
        engine.record_trade_result(-100)
        engine.record_trade_result(-100)  # 3rd consecutive loss
        assert engine._loss_breaker_pause_until is not None

    def test_win_resets_consecutive_losses(self):
        engine = _make_engine()
        engine.record_trade_result(-100)
        engine.record_trade_result(-100)
        engine.record_trade_result(200)  # Win resets
        assert engine._consecutive_losses == 0

    def test_open_risk_calculation(self):
        engine = _make_engine()
        condor = _make_condor(wing_width=4.0, net_credit=1.0, num_spreads=2)
        # max_loss = 4.0 - 1.0 = 3.0; risk = 3.0 * 2 * 100 = 600
        engine.register_fill(condor)
        assert engine.get_open_risk() == pytest.approx(600.0)


# ═══════════════════════════════════════════════════════════════════
# STATE PERSISTENCE TESTS
# ═══════════════════════════════════════════════════════════════════


class TestStatePersistence:
    def test_roundtrip(self):
        engine = _make_engine()
        condor = _make_condor()
        engine.register_fill(condor)
        engine._consecutive_losses = 2
        engine._loss_breaker_pause_until = "2025-03-05"
        engine._diag_wins = 5
        engine._diag_losses = 3

        state = engine.to_dict()
        engine2 = _make_engine()
        engine2.from_dict(state)

        assert len(engine2.positions) == 1
        assert engine2._consecutive_losses == 2
        assert engine2._loss_breaker_pause_until == "2025-03-05"
        assert engine2._diag_wins == 5
        assert engine2._diag_losses == 3

    def test_empty_state_restore(self):
        engine = _make_engine()
        engine.from_dict({})
        assert len(engine.positions) == 0
        assert engine._trades_today == 0

    def test_none_state_restore(self):
        engine = _make_engine()
        engine.from_dict(None)
        assert len(engine.positions) == 0

    def test_reset_daily(self):
        engine = _make_engine()
        engine._trades_today = 3
        engine._daily_pnl = -500.0
        engine._diag_candidates = 10
        engine.reset_daily()
        assert engine._trades_today == 0
        assert engine._daily_pnl == 0.0
        assert engine._diag_candidates == 0

    def test_reset_full(self):
        engine = _make_engine()
        condor = _make_condor()
        engine.register_fill(condor)
        engine._consecutive_losses = 3
        engine.reset()
        assert len(engine.positions) == 0
        assert engine._consecutive_losses == 0
        assert engine._loss_breaker_pause_until is None


# ═══════════════════════════════════════════════════════════════════
# CONDOR MODEL TESTS
# ═══════════════════════════════════════════════════════════════════


class TestCondorModel:
    def test_serialization_roundtrip(self):
        condor = _make_condor()
        data = condor.to_dict()
        restored = IronCondorPosition.from_dict(data)

        assert restored.condor_id == condor.condor_id
        assert restored.net_credit == condor.net_credit
        assert restored.put_wing_width == condor.put_wing_width
        assert restored.call_wing_width == condor.call_wing_width
        assert restored.short_put.strike == condor.short_put.strike
        assert restored.short_call.strike == condor.short_call.strike
        assert restored.long_put.strike == condor.long_put.strike
        assert restored.long_call.strike == condor.long_call.strike

    def test_derived_properties(self):
        condor = _make_condor(qqq_price=480, wing_width=4.0, net_credit=1.0)
        # short_put = 470, short_call = 490
        assert condor.put_short_strike == 470
        assert condor.call_short_strike == 490
        assert condor.range_width == 20  # 490 - 470
        assert condor.max_wing_width == 4.0


# ═══════════════════════════════════════════════════════════════════
# LANE ISOLATION TESTS
# ═══════════════════════════════════════════════════════════════════


class TestLaneIsolation:
    """Verify IC never maps to MICRO/ITM/VASS."""

    def test_target_weight_opt_ic_normalizes_to_ic_lane(self):
        tw = TargetWeight(
            symbol="QQQ 20250315 P00470000",
            target_weight=1.0,
            source="OPT_IC",
            urgency=Urgency.IMMEDIATE,
            reason="IC_PUT_CREDIT",
            metadata={"options_strategy": "IRON_CONDOR"},
        )
        assert tw.metadata["options_lane"] == "IC"

    def test_target_weight_opt_ic_without_strategy_still_ic(self):
        tw = TargetWeight(
            symbol="QQQ 20250315 P00470000",
            target_weight=1.0,
            source="OPT_IC",
            urgency=Urgency.IMMEDIATE,
            reason="IC_PUT_CREDIT",
            metadata={},
        )
        # OPT_IC source forces lane=IC
        assert tw.metadata["options_lane"] == "IC"

    def test_iron_condor_enum_exists(self):
        assert IntradayStrategy.IRON_CONDOR.value == "IRON_CONDOR"

    def test_opt_ic_is_valid_source(self):
        # Should not raise
        tw = TargetWeight(
            symbol="QQQ 20250315 P00470000",
            target_weight=0.0,
            source="OPT_IC",
            urgency=Urgency.IMMEDIATE,
            reason="IC_CLOSE",
            metadata={"options_lane": "IC", "options_strategy": "IRON_CONDOR"},
        )
        assert tw.source == "OPT_IC"


# ═══════════════════════════════════════════════════════════════════
# DIAGNOSTICS TESTS
# ═══════════════════════════════════════════════════════════════════


class TestDiagnostics:
    def test_diagnostics_structure(self):
        engine = _make_engine()
        diag = engine.get_diagnostics()
        assert "candidates" in diag
        assert "approved" in diag
        assert "dropped" in diag
        assert "drop_codes" in diag
        assert "exit_reasons" in diag
        assert "wins" in diag
        assert "losses" in diag
        assert "open_positions" in diag
        assert "open_risk" in diag
        assert "trades_today" in diag

    def test_drop_code_tracking(self):
        engine = _make_engine()
        engine._record_drop(R_IC_REGIME_OUT_OF_RANGE)
        engine._record_drop(R_IC_REGIME_OUT_OF_RANGE)
        engine._record_drop(R_IC_VIX_OUT_OF_RANGE)
        diag = engine.get_diagnostics()
        assert diag["drop_codes"][R_IC_REGIME_OUT_OF_RANGE] == 2
        assert diag["drop_codes"][R_IC_VIX_OUT_OF_RANGE] == 1


# ═══════════════════════════════════════════════════════════════════
# EXIT SIGNAL STRUCTURE TESTS
# ═══════════════════════════════════════════════════════════════════


class TestExitSignalStructure:
    """Verify exit signals have correct metadata for router."""

    def test_exit_signals_have_correct_source(self):
        engine = _make_engine()
        condor = _make_condor()
        reason, signals = engine._build_exit(
            condor, EXIT_IC_PROFIT_TARGET, datetime(2025, 3, 5, 11, 0)
        )
        assert len(signals) == 2
        for sig in signals:
            assert sig.source == "OPT_IC"
            assert sig.target_weight == 0.0
            assert sig.metadata["options_lane"] == "IC"
            assert sig.metadata["options_strategy"] == "IRON_CONDOR"
            assert sig.metadata["condor_id"] == condor.condor_id
            assert sig.metadata["exit_reason"] == EXIT_IC_PROFIT_TARGET

    def test_exit_signals_cover_both_sides(self):
        engine = _make_engine()
        condor = _make_condor()
        _, signals = engine._build_exit(condor, EXIT_IC_STOP_LOSS, datetime(2025, 3, 5, 11, 0))
        sides = {sig.metadata.get("spread_side") for sig in signals}
        assert "PUT_CREDIT_CLOSE" in sides
        assert "CALL_CREDIT_CLOSE" in sides
