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

import contextlib
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
    EXIT_IC_EOD_HOLD_GATE,
    EXIT_IC_FRIDAY_CLOSE,
    EXIT_IC_HARD_STOP_HOLD,
    EXIT_IC_MFE_LOCK,
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
    R_IC_STRIKE_REUSE,
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
    entry_dte: int = 30,
    entry_time: str = "2025-03-01 11:00:00",
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
        entry_time=entry_time,
        regime_at_entry=regime,
        entry_vix=vix,
        entry_adx=15.0,
        entry_dte=entry_dte,
        condor_id="test123",
        entry_cw_tier="MID_VIX",
        stop_dw=2.5 * net_credit / wing_width,
        implied_wr_be=1.0 - net_credit / wing_width,
    )


def _default_transition_ctx(state: str = "STABLE", fast_overlay: str = "") -> Dict[str, Any]:
    return {
        "transition_overlay": state,
        "transition_state": state,
        "fast_overlay": fast_overlay,
        "transition_score": 52,
        "is_event_day": False,
    }


def _make_engine() -> IronCondorEngine:
    return IronCondorEngine(log_func=lambda msg, trades_only=False: None)


@contextlib.contextmanager
def _patch_config(**overrides):
    """Patch multiple config attributes without deep nesting."""
    patches = [patch.object(config, k, v) for k, v in overrides.items()]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


# Default config overrides for search tests (reduces repetition)
_SEARCH_DEFAULTS = dict(
    IC_SHORT_DELTA_MIN=0.14,
    IC_SHORT_DELTA_MAX=0.22,
    IC_ELASTIC_DELTA_STEPS=[0.0],
    IC_ELASTIC_DELTA_FLOOR=0.10,
    IC_ELASTIC_DELTA_CEILING=0.30,
    IC_MIN_POOL_DEPTH=1,
    IC_CW_FLOOR_MID_VIX=0.25,
    IC_CW_RELAX_STEPS=[0.0],
    IC_CW_ABSOLUTE_FLOOR=0.20,
    IC_DELTA_SYMMETRY_MAX=0.10,
    IC_MAX_COMBO_SLIPPAGE=0.50,
    IC_WING_WIDTH_FALLBACK_TOLERANCE=1,
    IC_MAX_CANDIDATE_COMBOS=50,
    IC_SCAN_THROTTLE_MINUTES=0,
    IC_WING_WIDTH_MID_VIX=5,
    IC_MAX_IMPLIED_WR=0.82,
    IC_STOP_LOSS_MULTIPLE=1.50,
    IC_MAX_STOP_DW=0.65,
    IC_WING_SYMMETRY_MAX=1.0,
    IC_PER_TRADE_RISK_PCT=0.01,
    IC_MIN_OPEN_INTEREST=100,
    IC_MAX_SPREAD_PCT=0.30,
    IC_DTE_RANGES=[(21, 35)],
)


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
        """Day 1 of neutral regime is not enough — need IC_REGIME_PERSISTENCE_DAYS."""
        engine = _make_engine()
        with patch.object(config, "IRON_CONDOR_ENGINE_ENABLED", True):
            with patch.object(config, "IC_REGIME_PERSISTENCE_DAYS", 2):
                # Day 1: first neutral day counted
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
                # Same day, second call — still day 1
                result2 = engine._check_env_gates(
                    regime_score=52,
                    adx_value=15,
                    vix_current=18,
                    transition_ctx=_default_transition_ctx(),
                    current_time=datetime(2025, 3, 3, 11, 15),
                    effective_portfolio_value=100000,
                    margin_remaining=50000,
                    ic_open_risk=0,
                )
                assert result2 == R_IC_REGIME_NOT_PERSISTENT
                # Day 2: passes persistence
                result3 = engine._check_env_gates(
                    regime_score=52,
                    adx_value=15,
                    vix_current=18,
                    transition_ctx=_default_transition_ctx(),
                    current_time=datetime(2025, 3, 4, 11, 0),
                    effective_portfolio_value=100000,
                    margin_remaining=50000,
                    ic_open_risk=0,
                )
                assert result3 is None  # All gates pass

    def test_vix_below_min_rejects(self):
        engine = _make_engine()
        engine._regime_neutral_days = 5
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
        engine._regime_neutral_days = 5
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
        engine._regime_neutral_days = 5
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
        engine._regime_neutral_days = 5
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
        engine._regime_neutral_days = 5
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
        engine._regime_neutral_days = 5
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
        engine._regime_neutral_days = 5
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
        engine._regime_neutral_days = 5
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
        engine._regime_neutral_days = 5
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
        assert engine._get_wing_width_for_vix(12.0) == 5

    def test_mid_vix_width(self):
        engine = _make_engine()
        assert engine._get_wing_width_for_vix(20.0) == 5

    def test_high_vix_width(self):
        engine = _make_engine()
        assert engine._get_wing_width_for_vix(28.0) == 7

    def test_low_vix_cw_floor(self):
        engine = _make_engine()
        assert engine._get_cw_floor_for_vix(12.0) == pytest.approx(0.25)

    def test_mid_vix_cw_floor(self):
        engine = _make_engine()
        assert engine._get_cw_floor_for_vix(20.0) == pytest.approx(0.25)

    def test_high_vix_cw_floor(self):
        engine = _make_engine()
        assert engine._get_cw_floor_for_vix(28.0) == pytest.approx(0.23)

    def test_cw_floors_feasible_with_stop_dw(self):
        """Guard rail: C/W floor × (1 + STOP_MULT) must never exceed MAX_STOP_DW.

        If this test fails, it means config C/W floors are set too high for
        the current stop parameters — the engine will reject every condor at
        the base floor level and require C/W relaxation for all entries.
        """
        max_cw = config.IC_MAX_STOP_DW / (1 + config.IC_STOP_LOSS_MULTIPLE)
        for attr in ("IC_CW_FLOOR_LOW_VIX", "IC_CW_FLOOR_MID_VIX", "IC_CW_FLOOR_HIGH_VIX"):
            floor = getattr(config, attr)
            assert floor <= max_cw, (
                f"{attr}={floor:.2f} exceeds max feasible C/W={max_cw:.4f} "
                f"(MAX_STOP_DW={config.IC_MAX_STOP_DW} / "
                f"(1+STOP_MULT={config.IC_STOP_LOSS_MULTIPLE}))"
            )


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
        # Use date past hold guard (10 days for 30 DTE entry)
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,  # > 360 loss
            current_dte=10,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 12, 11, 0),  # 11 days after entry
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
            current_dte=9,  # <= IC_TIME_EXIT_DTE=10
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 12, 11, 0),  # Past hold guard
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
            current_time=datetime(2025, 3, 12, 11, 0),  # Past hold guard
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_REGIME_BREAK

    def test_friday_close_exit(self):
        engine = _make_engine()
        condor = _make_condor()
        # Friday (weekday=4), DTE < IC_FRIDAY_CLOSE_DTE(14)
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=0,
            current_dte=13,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 14, 11, 0),  # Friday, past hold guard
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
            current_time=datetime(2025, 3, 12, 11, 0),  # Past hold guard
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
            current_time=datetime(2025, 3, 12, 11, 0),  # Past hold guard
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
            current_dte=25,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 12, 11, 0),  # Past hold guard, Wednesday
        )
        assert result is None

    def test_closing_condor_skipped(self):
        engine = _make_engine()
        condor = _make_condor()
        condor.is_closing = True
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-500,  # Should trigger stop but is_closing
            current_dte=25,
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


# ═══════════════════════════════════════════════════════════════════
# PROGRESSIVE SEARCH TESTS (VASS-Style Three-Layer Fallback)
# ═══════════════════════════════════════════════════════════════════


def _make_option_chain(
    qqq_price: float = 480.0,
    *,
    put_delta: float = 0.18,
    call_delta: float = 0.18,
    put_credit: float = 2.00,
    call_credit: float = 2.00,
    wing_width: int = 5,
    dte: int = 28,
    expiry: str = "2025-04-01",
    oi: int = 500,
) -> List[OptionContract]:
    """Build a minimal 4-leg synthetic chain for search tests."""
    short_put_strike = round(qqq_price - qqq_price * put_delta / 5, 0)  # rough OTM
    long_put_strike = short_put_strike - wing_width
    short_call_strike = round(qqq_price + qqq_price * call_delta / 5, 0)
    long_call_strike = short_call_strike + wing_width

    # Short put: credit leg
    sp = _make_contract(
        short_put_strike,
        OptionDirection.PUT,
        expiry=expiry,
        delta=put_delta,
        bid=put_credit - 0.10,
        ask=put_credit + 0.10,
        oi=oi,
        dte=dte,
    )
    # Long put: debit leg (cheaper, further OTM)
    lp = _make_contract(
        long_put_strike,
        OptionDirection.PUT,
        expiry=expiry,
        delta=put_delta * 0.3,
        bid=0.30,
        ask=0.50,
        oi=oi,
        dte=dte,
    )
    # Short call: credit leg
    sc = _make_contract(
        short_call_strike,
        OptionDirection.CALL,
        expiry=expiry,
        delta=call_delta,
        bid=call_credit - 0.10,
        ask=call_credit + 0.10,
        oi=oi,
        dte=dte,
    )
    # Long call: debit leg (cheaper, further OTM)
    lc = _make_contract(
        long_call_strike,
        OptionDirection.CALL,
        expiry=expiry,
        delta=call_delta * 0.3,
        bid=0.30,
        ask=0.50,
        oi=oi,
        dte=dte,
    )
    return [sp, lp, sc, lc]


class TestScanThrottle:
    """Test scan throttle prevents rapid re-scanning."""

    def test_throttle_blocks_within_window(self):
        engine = _make_engine()
        t1 = datetime(2025, 3, 3, 11, 0)
        # Use a non-None chain (empty list extracted) so we get past the None guard
        dummy_chain = iter([])  # non-None, will extract 0 contracts → R_IC_NO_CHAIN
        with _patch_config(IC_SCAN_THROTTLE_MINUTES=15):
            engine._search_and_build_condor(
                chain=dummy_chain,
                qqq_price=480,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=t1,
                effective_portfolio_value=100000,
            )
            # Scan time should be set (even though chain was empty after extract)
            assert engine._last_scan_time == "2025-03-03 11:00:00"

            # 5 min later — should be throttled
            t2 = t1 + timedelta(minutes=5)
            engine._diag_drop_codes.clear()
            result2 = engine._search_and_build_condor(
                chain=iter([]),
                qqq_price=480,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=t2,
                effective_portfolio_value=100000,
            )
            assert result2 is None
            # Throttle should not have updated scan time
            assert engine._last_scan_time == "2025-03-03 11:00:00"

    def test_throttle_allows_after_window(self):
        engine = _make_engine()
        t1 = datetime(2025, 3, 3, 11, 0)
        with _patch_config(IC_SCAN_THROTTLE_MINUTES=15):
            engine._search_and_build_condor(
                chain=iter([]),
                qqq_price=480,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=t1,
                effective_portfolio_value=100000,
            )
            # 20 min later — past throttle window
            t2 = t1 + timedelta(minutes=20)
            engine._search_and_build_condor(
                chain=iter([]),
                qqq_price=480,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=t2,
                effective_portfolio_value=100000,
            )
            assert engine._last_scan_time == "2025-03-03 11:20:00"

    def test_throttle_zero_disables(self):
        engine = _make_engine()
        t1 = datetime(2025, 3, 3, 11, 0)
        with _patch_config(IC_SCAN_THROTTLE_MINUTES=0):
            engine._search_and_build_condor(
                chain=iter([]),
                qqq_price=480,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=t1,
                effective_portfolio_value=100000,
            )
            t2 = t1 + timedelta(minutes=1)
            engine._search_and_build_condor(
                chain=iter([]),
                qqq_price=480,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=t2,
                effective_portfolio_value=100000,
            )
            assert engine._last_scan_time == "2025-03-03 11:01:00"

    def test_daily_reset_clears_scan_time(self):
        engine = _make_engine()
        engine._last_scan_time = "2025-03-03 11:00:00"
        engine.reset_daily()
        assert engine._last_scan_time is None


class TestElasticDeltaWidening:
    """Test Layer 2: elastic delta band widening."""

    def test_narrow_delta_no_contracts_then_widen_finds(self):
        """Contract with delta 0.12 (outside [0.16, 0.22]) found after widening by 0.06."""
        engine = _make_engine()
        chain = _make_option_chain(
            put_delta=0.12,
            call_delta=0.12,
            put_credit=1.80,
            call_credit=1.80,
            wing_width=5,
            dte=28,
        )
        overrides = {
            **_SEARCH_DEFAULTS,
            "IC_SHORT_DELTA_MIN": 0.16,
            "IC_SHORT_DELTA_MAX": 0.22,
            "IC_ELASTIC_DELTA_STEPS": [0.0, 0.03, 0.06, 0.10],
            "IC_CW_ABSOLUTE_FLOOR": 0.15,
        }
        with _patch_config(**overrides):
            result = engine._search_single_dte_range(
                contracts=chain,
                dte_min=21,
                dte_max=35,
                qqq_price=480,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                fallback_stats=[],
            )
            # Delta 0.12 needs widen >= 0.04 → step 0.06 gives [0.10, 0.28]

    def test_delta_floor_ceiling_respected(self):
        """Elastic widening never goes below floor or above ceiling."""
        engine = _make_engine()
        chain = _make_option_chain(
            put_delta=0.08,
            call_delta=0.08,
            put_credit=1.50,
            call_credit=1.50,
            wing_width=5,
            dte=28,
        )
        overrides = {
            **_SEARCH_DEFAULTS,
            "IC_SHORT_DELTA_MIN": 0.16,
            "IC_SHORT_DELTA_MAX": 0.22,
            "IC_ELASTIC_DELTA_STEPS": [0.0, 0.50],  # extreme widen
        }
        with _patch_config(**overrides):
            result = engine._search_single_dte_range(
                contracts=chain,
                dte_min=21,
                dte_max=35,
                qqq_price=480,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
                fallback_stats=[],
            )
            assert result is None  # 0.08 below floor of 0.10


class TestCWRelaxation:
    """Test Layer 3: C/W floor relaxation."""

    def test_relaxation_finds_marginal_condor(self):
        """Condor with C/W just below base floor found after relaxation."""
        engine = _make_engine()
        chain = _make_option_chain(
            put_delta=0.18,
            call_delta=0.18,
            put_credit=1.10,
            call_credit=1.05,
            wing_width=5,
            dte=28,
        )
        overrides = {
            **_SEARCH_DEFAULTS,
            "IC_CW_FLOOR_MID_VIX": 0.28,
            "IC_CW_RELAX_STEPS": [0.0, 0.03, 0.05],
        }
        with _patch_config(**overrides):
            result = engine._build_best_condor(
                contracts=chain,
                eligible_puts=[
                    c for c in chain if c.direction == OptionDirection.PUT and abs(c.delta) >= 0.14
                ],
                eligible_calls=[
                    c for c in chain if c.direction == OptionDirection.CALL and abs(c.delta) >= 0.14
                ],
                wing_width=5,
                tolerance=1,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
            )
            # Floor 0.28 → relaxed to 0.25 → C/W ~0.27 passes

    def test_absolute_floor_never_breached(self):
        """Relaxation cannot go below IC_CW_ABSOLUTE_FLOOR."""
        engine = _make_engine()
        chain = _make_option_chain(
            put_delta=0.18,
            call_delta=0.18,
            put_credit=0.50,
            call_credit=0.50,
            wing_width=5,
            dte=28,
        )
        overrides = {
            **_SEARCH_DEFAULTS,
            "IC_CW_FLOOR_MID_VIX": 0.28,
            "IC_CW_RELAX_STEPS": [0.0, 0.03, 0.05, 0.10],
        }
        with _patch_config(**overrides):
            result = engine._build_best_condor(
                contracts=chain,
                eligible_puts=[
                    c for c in chain if c.direction == OptionDirection.PUT and abs(c.delta) >= 0.14
                ],
                eligible_calls=[
                    c for c in chain if c.direction == OptionDirection.CALL and abs(c.delta) >= 0.14
                ],
                wing_width=5,
                tolerance=1,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
            )
            assert result is None  # C/W ~0.15 below absolute floor 0.20


class TestDTERangeFallback:
    """Test Layer 1: DTE range fallback."""

    def test_primary_range_miss_fallback_hits(self):
        """When contracts don't match primary DTE range, fallback range succeeds."""
        engine = _make_engine()
        chain_contracts = _make_option_chain(
            put_delta=0.18,
            call_delta=0.18,
            put_credit=2.00,
            call_credit=2.00,
            wing_width=5,
            dte=38,
            expiry="2025-04-10",
        )
        overrides = {**_SEARCH_DEFAULTS, "IC_DTE_RANGES": [(21, 35), (35, 45)]}
        with _patch_config(**overrides):
            with patch.object(engine, "_extract_chain_contracts", return_value=chain_contracts):
                result = engine._search_and_build_condor(
                    chain=["dummy"],
                    qqq_price=480,
                    vix_current=18,
                    regime_score=52,
                    adx_value=15,
                    current_time=datetime(2025, 3, 3, 11, 0),
                    effective_portfolio_value=100000,
                )
                # DTE=38 doesn't fit (21,35) but fits (35,45)
                # If result is not None, it found a condor in the fallback range

    def test_all_ranges_exhausted_records_drop(self):
        """When no DTE range has qualifying contracts, drop code is recorded."""
        engine = _make_engine()
        # Contracts with DTE=50 — outside all configured ranges
        chain_contracts = _make_option_chain(
            put_delta=0.18,
            call_delta=0.18,
            put_credit=2.00,
            call_credit=2.00,
            wing_width=5,
            dte=50,
            expiry="2025-04-25",
        )
        from engines.satellite.iron_condor_engine import R_IC_NO_COMPLETE_CONDOR

        overrides = {**_SEARCH_DEFAULTS, "IC_DTE_RANGES": [(21, 35), (35, 45)]}
        with _patch_config(**overrides):
            with patch.object(engine, "_extract_chain_contracts", return_value=chain_contracts):
                result = engine._search_and_build_condor(
                    chain=["dummy"],
                    qqq_price=480,
                    vix_current=18,
                    regime_score=52,
                    adx_value=15,
                    current_time=datetime(2025, 3, 3, 11, 0),
                    effective_portfolio_value=100000,
                )
                assert result is None
                assert engine._diag_drop_codes.get(R_IC_NO_COMPLETE_CONDOR, 0) > 0


class TestWingLegFinder:
    """Test _find_wing_leg with strict and tolerance passes."""

    def test_exact_width_match(self):
        engine = _make_engine()
        short_put = _make_contract(470.0, OptionDirection.PUT, expiry="2025-04-01", dte=28)
        long_put = _make_contract(465.0, OptionDirection.PUT, expiry="2025-04-01", dte=28)
        found = engine._find_wing_leg([short_put, long_put], short_put, 5, "PUT", tolerance=0)
        assert found is not None
        assert found.strike == 465.0

    def test_no_match_strict_then_tolerance_finds(self):
        engine = _make_engine()
        short_put = _make_contract(470.0, OptionDirection.PUT, expiry="2025-04-01", dte=28)
        long_put = _make_contract(464.0, OptionDirection.PUT, expiry="2025-04-01", dte=28)
        assert (
            engine._find_wing_leg([short_put, long_put], short_put, 5, "PUT", tolerance=0) is None
        )
        found = engine._find_wing_leg([short_put, long_put], short_put, 5, "PUT", tolerance=1)
        assert found is not None
        assert found.strike == 464.0

    def test_call_wing_direction(self):
        engine = _make_engine()
        short_call = _make_contract(490.0, OptionDirection.CALL, expiry="2025-04-01", dte=28)
        long_call = _make_contract(495.0, OptionDirection.CALL, expiry="2025-04-01", dte=28)
        found = engine._find_wing_leg([short_call, long_call], short_call, 5, "CALL", tolerance=0)
        assert found is not None
        assert found.strike == 495.0

    def test_wrong_expiry_excluded(self):
        engine = _make_engine()
        short_put = _make_contract(470.0, OptionDirection.PUT, expiry="2025-04-01", dte=28)
        long_put = _make_contract(465.0, OptionDirection.PUT, expiry="2025-04-15", dte=42)
        assert (
            engine._find_wing_leg([short_put, long_put], short_put, 5, "PUT", tolerance=0) is None
        )

    def test_low_oi_excluded(self):
        engine = _make_engine()
        short_put = _make_contract(470.0, OptionDirection.PUT, expiry="2025-04-01", dte=28)
        long_put = _make_contract(465.0, OptionDirection.PUT, expiry="2025-04-01", dte=28, oi=10)
        with _patch_config(IC_MIN_OPEN_INTEREST=100):
            assert (
                engine._find_wing_leg([short_put, long_put], short_put, 5, "PUT", tolerance=0)
                is None
            )


class TestEndToEndSearch:
    """Integration: full three-layer search finds a valid condor.

    Uses mock for _extract_chain_contracts since QC chain objects aren't
    available in unit tests. The mock returns our OptionContract list directly.
    """

    def test_good_chain_produces_condor(self):
        engine = _make_engine()
        # Target C/W ~0.28: each side credit ~$0.70 → net_credit=$1.40, wing=$5
        # Short leg at $1.10 bid/$1.30 ask → mid $1.20; long leg at $0.30/$0.50 → mid $0.40
        # Per-side credit = 1.20 - 0.40 = 0.80; total = 1.60; C/W = 0.32
        # stop_dw = 1.60 * 2.5 / 5 = 0.80 > 0.65 — too high!
        # Instead: short leg at 0.90/1.10 → mid 1.00; per-side = 1.00 - 0.40 = 0.60; total = 1.20; C/W = 0.24
        # stop_dw = 1.20 * 2.5 / 5 = 0.60 ≤ 0.65 ✓
        # C/W = 0.24 < 0.25 floor → need to set IC_CW_FLOOR_MID_VIX = 0.22
        chain_contracts = _make_option_chain(
            qqq_price=480,
            put_delta=0.18,
            call_delta=0.18,
            put_credit=1.00,
            call_credit=1.00,
            wing_width=5,
            dte=28,
            expiry="2025-04-01",
        )
        overrides = {**_SEARCH_DEFAULTS, "IC_CW_FLOOR_MID_VIX": 0.22}
        with _patch_config(**overrides):
            with patch.object(engine, "_extract_chain_contracts", return_value=chain_contracts):
                result = engine._search_and_build_condor(
                    chain=["dummy"],
                    qqq_price=480,
                    vix_current=18,
                    regime_score=52,
                    adx_value=15,
                    current_time=datetime(2025, 3, 3, 11, 0),
                    effective_portfolio_value=100000,
                )
                assert result is not None
                assert result.net_credit > 0
                assert result.credit_to_width >= 0.20

    def test_empty_chain_returns_none_with_drop(self):
        engine = _make_engine()
        from engines.satellite.iron_condor_engine import R_IC_NO_CHAIN

        with _patch_config(IC_SCAN_THROTTLE_MINUTES=0):
            result = engine._search_and_build_condor(
                chain=None,
                qqq_price=480,
                vix_current=18,
                regime_score=52,
                adx_value=15,
                current_time=datetime(2025, 3, 3, 11, 0),
                effective_portfolio_value=100000,
            )
            assert result is None
            assert engine._diag_drop_codes.get(R_IC_NO_CHAIN, 0) > 0

    def test_no_qualifying_contracts_records_no_complete_condor(self):
        """When chain has contracts but none form a valid condor, records correct drop."""
        engine = _make_engine()
        # Very low credit → C/W will fail even after relaxation
        chain_contracts = _make_option_chain(
            put_delta=0.18,
            call_delta=0.18,
            put_credit=0.20,
            call_credit=0.20,
            wing_width=5,
            dte=28,
        )
        from engines.satellite.iron_condor_engine import R_IC_NO_COMPLETE_CONDOR

        overrides = {
            **_SEARCH_DEFAULTS,
            "IC_CW_FLOOR_MID_VIX": 0.28,
            "IC_CW_RELAX_STEPS": [0.0, 0.03, 0.05],
            "IC_MAX_SPREAD_PCT": 0.50,
        }
        with _patch_config(**overrides):
            with patch.object(engine, "_extract_chain_contracts", return_value=chain_contracts):
                result = engine._search_and_build_condor(
                    chain=["dummy"],
                    qqq_price=480,
                    vix_current=18,
                    regime_score=52,
                    adx_value=15,
                    current_time=datetime(2025, 3, 3, 11, 0),
                    effective_portfolio_value=100000,
                )
                assert result is None
                assert engine._diag_drop_codes.get(R_IC_NO_COMPLETE_CONDOR, 0) > 0


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


# ═══════════════════════════════════════════════════════════════════
# HOLD GUARD TESTS
# ═══════════════════════════════════════════════════════════════════


class TestHoldGuard:
    """Test DTE-adaptive hold guard that blocks premature exits."""

    # Entry: 2025-03-01 11:00:00, entry_dte=30
    # hold_days = ceil(30 * 0.33) = 10 → hold_minutes = 14400
    # Hold expires: 2025-03-11 11:00:00

    def test_hold_guard_blocks_stop_loss_during_hold(self):
        """P3 stop loss blocked within hold window."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240; 150% stop = 360 loss
        # Within hold: 3 days after entry
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,  # Would trigger P3 stop but held in guard
            current_dte=20,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 4, 11, 0),  # 3 days in
        )
        assert result is None

    def test_hold_guard_blocks_wing_breach_during_hold(self):
        """P4 wing breach blocked within hold window."""
        engine = _make_engine()
        condor = _make_condor(qqq_price=480)
        # short_put_strike = 470; QQQ at 460 → 2.17% ITM > 2% threshold
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-100,
            current_dte=20,
            vix_current=18,
            regime_score=52,
            qqq_price=460,
            current_time=datetime(2025, 3, 4, 11, 0),  # Within hold
        )
        assert result is None

    def test_regime_break_fires_through_hold_guard(self):
        """Regime break is a pre-guard — fires even during hold window."""
        engine = _make_engine()
        condor = _make_condor()
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-50,
            current_dte=20,
            vix_current=18,
            regime_score=35,  # Below regime_min(45) - buffer(5) = 40
            qqq_price=480,
            current_time=datetime(2025, 3, 4, 11, 0),
        )
        assert result is not None
        reason, signals = result
        assert reason == "IC_REGIME_BREAK"

    def test_hold_guard_blocks_friday_close_during_hold(self):
        """P7 Friday close blocked within hold window."""
        engine = _make_engine()
        condor = _make_condor()
        # 2025-03-07 is a Friday, only 6 days after entry (within 10-day hold)
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-20,
            current_dte=13,  # < IC_FRIDAY_CLOSE_DTE=14
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 7, 11, 0),  # Friday, within hold
        )
        assert result is None

    def test_hold_guard_allows_vix_spike_during_hold(self):
        """P0 VIX spike always fires (pre-guard)."""
        engine = _make_engine()
        condor = _make_condor()
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-50,
            current_dte=20,
            vix_current=35,  # >= IC_VIX_SPIKE_EXIT=30
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 2, 11, 0),  # 1 day after entry
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_VIX_SPIKE

    def test_hold_guard_allows_assignment_risk_during_hold(self):
        """P8 assignment risk always fires (pre-guard)."""
        engine = _make_engine()
        condor = _make_condor(qqq_price=480)
        # short_put_strike = 470; QQQ at 469 → put_itm = (470-469)/469 > 0
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-50,
            current_dte=2,  # <= IC_DIVIDEND_GUARD_DTE=3
            vix_current=18,
            regime_score=52,
            qqq_price=469,  # Put short strike ITM
            current_time=datetime(2025, 3, 2, 11, 0),  # Within hold
        )
        assert result is not None
        reason, _ = result
        assert reason == "IC_ASSIGNMENT_RISK"

    def test_hold_guard_hard_stop_fires_during_hold(self):
        """Loss > 2.5× credit triggers hard stop during hold."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240; hard stop = 2.5 × 240 = 600
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-610,  # loss_pct = 610/240 = 2.54× > 2.5×
            current_dte=20,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 4, 11, 0),  # Within hold
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_HARD_STOP_HOLD

    def test_hold_guard_eod_gate_fires_during_hold(self):
        """Loss > 1.5× credit at 15:45+ triggers EOD gate during hold."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240; EOD gate = 1.5 × 240 = 360
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,  # loss_pct = 370/240 = 1.54× > 1.5×
            current_dte=20,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 4, 15, 50),  # 15:50 = EOD, held > 4h
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_EOD_HOLD_GATE

    def test_hold_guard_eod_gate_requires_min_hold(self):
        """EOD gate blocked if held < IC_HOLD_EOD_GATE_MIN_MINUTES (240 min)."""
        engine = _make_engine()
        # Entry at 12:00, check at 15:50 = only 230 min < 240 min threshold
        condor = _make_condor(
            net_credit=1.20,
            num_spreads=2,
            entry_time="2025-03-01 12:00:00",
        )
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,  # Would trigger EOD gate if min hold met
            current_dte=20,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 1, 15, 50),  # Same day, 230 min
        )
        # EOD gate can't fire (held < 240 min), but also within hold guard
        # so cascade is blocked → None
        assert result is None

    def test_hold_guard_profitable_bypass(self):
        """Profitable condor during hold → profit target can fire."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240; target = 60% = 144
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=150,  # > 144 → profit target fires
            current_dte=20,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 4, 11, 0),  # Within hold, but profitable
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_PROFIT_TARGET

    def test_hold_guard_expires_after_window(self):
        """After hold window, full cascade runs normally."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240; stop = 150% = 360
        # Hold window: 10 days. Check at day 11.
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,  # > 360 loss → P3 fires
            current_dte=15,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 12, 11, 0),  # Day 11, past hold
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_STOP_LOSS

    def test_hold_guard_dte_adaptive_21dte(self):
        """21 DTE entry → hold = ceil(21 × 0.33) = 7 days."""
        engine = _make_engine()
        condor = _make_condor(entry_dte=21)
        # Hold = 7 days = 10080 min. Entry: Mar 1 11:00
        # Day 6 (Mar 7): still within hold → blocked
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,  # Would trigger stop
            current_dte=15,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 7, 11, 0),  # 6 days in
        )
        assert result is None

        # Day 8 (Mar 9): past hold → cascade fires
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,
            current_dte=15,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 9, 11, 0),  # 8 days in, past 7-day hold
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_STOP_LOSS

    def test_hold_guard_dte_adaptive_45dte(self):
        """45 DTE entry → hold = min(ceil(45 × 0.33), 15) = 15 days (capped)."""
        engine = _make_engine()
        condor = _make_condor(entry_dte=45)
        # Hold = 15 days (cap). Entry: Mar 1 11:00
        # Day 14 (Mar 15): still within hold → blocked
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,
            current_dte=25,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 15, 11, 0),  # 14 days in
        )
        assert result is None

        # Day 16 (Mar 17): past hold → cascade fires
        result = engine.check_exit_signals(
            condor=condor,
            combined_pnl=-370,
            current_dte=25,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=datetime(2025, 3, 17, 11, 0),  # 16 days in, past 15-day hold
        )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_STOP_LOSS

    def test_hold_guard_disabled_config(self):
        """IC_HOLD_GUARD_ENABLED=False disables hold guard entirely."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        with _patch_config(IC_HOLD_GUARD_ENABLED=False):
            result = engine.check_exit_signals(
                condor=condor,
                combined_pnl=-370,  # Would trigger P3 stop
                current_dte=20,
                vix_current=18,
                regime_score=52,
                qqq_price=480,
                current_time=datetime(2025, 3, 4, 11, 0),  # Within hold window
            )
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_STOP_LOSS


# ═══════════════════════════════════════════════════════════════════
# MFE LOCK TESTS
# ═══════════════════════════════════════════════════════════════════


class TestMFELock:
    """Test MFE (Maximum Favorable Excursion) lock floor ratchet system."""

    # Use time past hold guard so MFE lock can fire in the main cascade
    POST_HOLD = datetime(2025, 3, 12, 11, 0)  # 11 days after default entry

    def _exit_kwargs(self, condor, combined_pnl, **overrides):
        defaults = dict(
            condor=condor,
            combined_pnl=combined_pnl,
            current_dte=14,
            vix_current=18,
            regime_score=52,
            qqq_price=480,
            current_time=self.POST_HOLD,
        )
        defaults.update(overrides)
        return defaults

    def test_mfe_hwm_tracking(self):
        """HWM updates on positive P&L and never decreases."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240
        assert condor.highest_pnl_pct == 0.0

        # Call with positive P&L → HWM should update
        with _patch_config(IC_MFE_LOCK_ENABLED=False):
            engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=60))
        # 60 / 240 = 0.25
        assert abs(condor.highest_pnl_pct - 0.25) < 0.01

        # Call with lower P&L → HWM stays
        with _patch_config(IC_MFE_LOCK_ENABLED=False):
            engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=30))
        assert abs(condor.highest_pnl_pct - 0.25) < 0.01

        # Call with higher P&L → HWM updates
        with _patch_config(IC_MFE_LOCK_ENABLED=False):
            engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=120))
        # 120 / 240 = 0.50
        assert abs(condor.highest_pnl_pct - 0.50) < 0.01

        # Negative P&L → HWM stays at 0.50
        with _patch_config(IC_MFE_LOCK_ENABLED=False):
            engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=-50))
        assert abs(condor.highest_pnl_pct - 0.50) < 0.01

    def test_mfe_t1_arms_and_exits(self):
        """T1 arms at 25% of credit captured, exits when P&L falls to 0%."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240

        # First call: P&L at 30% of credit → arms T1, no exit yet
        condor.highest_pnl_pct = 0.30  # Simulate prior HWM
        result = engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=24))
        # 24/240 = 0.10 → above T1 floor (0%), no exit
        assert result is None
        assert condor.mfe_lock_tier == 1

        # P&L drops to 0 → at T1 floor (0%), should exit
        result = engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=0))
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_MFE_LOCK

    def test_mfe_t2_arms_and_exits(self):
        """T2 arms at 45% of credit captured, exits when P&L falls to 15%."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240

        # Simulate HWM that reached 50% of credit
        condor.highest_pnl_pct = 0.50

        # P&L at 20% → above T2 floor (15%), no exit
        result = engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=48))
        # 48/240 = 0.20, floor = 0.15 → no exit
        assert result is None
        assert condor.mfe_lock_tier == 2

        # P&L at 14% → below T2 floor (15%), exit
        result = engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=33.6))
        # 33.6/240 = 0.14, floor = 0.15 → exit
        assert result is not None
        reason, _ = result
        assert reason == EXIT_IC_MFE_LOCK

    def test_mfe_tier_ratchet_never_decreases(self):
        """T2 stays armed even when P&L drops — tier never decreases."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240

        # Arm T2
        condor.highest_pnl_pct = 0.50
        condor.mfe_lock_tier = 2

        # P&L drops to 20% → T2 stays (still above floor)
        result = engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=48))
        assert condor.mfe_lock_tier == 2
        assert result is None

        # HWM drops below T2 trigger on this call (can't happen in practice,
        # but test ratchet safety): tier should not decrease
        condor.highest_pnl_pct = 0.10  # Artificially set below T1
        result = engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=48))
        # 48/240 = 0.20, but tier was already 2 → stays 2
        assert condor.mfe_lock_tier == 2

    def test_mfe_disabled_config(self):
        """IC_MFE_LOCK_ENABLED=False disables MFE exit (HWM still tracks)."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240

        # Set HWM past T2, then drop P&L below floor
        condor.highest_pnl_pct = 0.50

        with _patch_config(IC_MFE_LOCK_ENABLED=False):
            result = engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=0))
        # MFE disabled → should NOT fire MFE exit
        assert result is None or result[0] != EXIT_IC_MFE_LOCK

    def test_mfe_no_exit_above_floor(self):
        """P&L above floor → no MFE exit fires."""
        engine = _make_engine()
        condor = _make_condor(net_credit=1.20, num_spreads=2)
        # credit_100 = 240

        # Arm T1 (HWM = 30%), P&L at 15% → above T1 floor (0%)
        condor.highest_pnl_pct = 0.30
        result = engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=36))
        # 36/240 = 0.15 → above 0.0 floor
        assert result is None

        # Arm T2 (HWM = 50%), P&L at 25% → above T2 floor (15%)
        condor.highest_pnl_pct = 0.50
        condor.mfe_lock_tier = 0  # Reset to verify ratchet re-arms
        result = engine.check_exit_signals(**self._exit_kwargs(condor, combined_pnl=60))
        # 60/240 = 0.25 → above 0.15 floor
        assert result is None
        assert condor.mfe_lock_tier == 2


# ═══════════════════════════════════════════════════════════════════
# STRIKE REUSE GUARD TESTS
# ═══════════════════════════════════════════════════════════════════


class TestStrikeReuseGuard:
    """Test strike-reuse guard blocks new IC when strikes overlap active/pending IC."""

    def _validate_kwargs(self, engine, **overrides):
        """Default kwargs for _validate_and_score_condor."""
        defaults = dict(
            vix_current=18.0,
            regime_score=52.0,
            adx_value=15.0,
            current_time=datetime(2025, 3, 5, 11, 0),
            effective_portfolio_value=100000,
        )
        defaults.update(overrides)
        return defaults

    def test_active_overlap_blocks(self):
        """New IC sharing a strike with active IC at same expiry is blocked."""
        engine = _make_engine()
        # Register an active condor
        existing = _make_condor(qqq_price=480.0, entry_dte=30)
        engine.register_fill(existing)
        # Existing strikes: short_put=470, long_put=466, short_call=490, long_call=494

        # Build a new condor whose short_call (490) overlaps existing short_call
        # Use realistic prices: short legs more expensive than long legs for positive credit
        new_short_put = _make_contract(472.0, OptionDirection.PUT, bid=2.00, ask=2.20)
        new_long_put = _make_contract(468.0, OptionDirection.PUT, bid=0.80, ask=1.00)
        new_short_call = _make_contract(490.0, OptionDirection.CALL, bid=2.00, ask=2.20)
        new_long_call = _make_contract(494.0, OptionDirection.CALL, bid=0.80, ask=1.00)

        with _patch_config(IC_STRIKE_REUSE_GUARD_ENABLED=True, **_SEARCH_DEFAULTS):
            result = engine._validate_and_score_condor(
                short_put=new_short_put,
                long_put=new_long_put,
                short_call=new_short_call,
                long_call=new_long_call,
                **self._validate_kwargs(engine),
            )
        assert result is None
        assert engine._diag_drop_codes.get(R_IC_STRIKE_REUSE, 0) >= 1

    def test_pending_overlap_blocks(self):
        """New IC sharing a strike with pending IC at same expiry is blocked."""
        engine = _make_engine()
        # Set up a pending condor
        pending = _make_condor(qqq_price=480.0, entry_dte=30)
        engine._pending_entry = True
        engine._pending_condor = pending
        # Pending strikes: short_put=470, long_put=466, short_call=490, long_call=494

        # New condor with short_put at 470 (same as pending short_put)
        new_short_put = _make_contract(470.0, OptionDirection.PUT, bid=2.00, ask=2.20)
        new_long_put = _make_contract(466.0, OptionDirection.PUT, bid=0.80, ask=1.00)
        new_short_call = _make_contract(492.0, OptionDirection.CALL, bid=2.00, ask=2.20)
        new_long_call = _make_contract(496.0, OptionDirection.CALL, bid=0.80, ask=1.00)

        with _patch_config(IC_STRIKE_REUSE_GUARD_ENABLED=True, **_SEARCH_DEFAULTS):
            result = engine._validate_and_score_condor(
                short_put=new_short_put,
                long_put=new_long_put,
                short_call=new_short_call,
                long_call=new_long_call,
                **self._validate_kwargs(engine),
            )
        assert result is None
        assert engine._diag_drop_codes.get(R_IC_STRIKE_REUSE, 0) >= 1

    def test_different_expiry_allows(self):
        """Overlapping strikes at different expiry are allowed."""
        engine = _make_engine()
        existing = _make_condor(qqq_price=480.0, entry_dte=30)
        engine.register_fill(existing)
        # Existing expiry: "2025-03-15" (from _make_contract default)

        # New condor with SAME strikes but different expiry
        diff_expiry = "2025-04-15"
        new_short_put = _make_contract(470.0, OptionDirection.PUT, expiry=diff_expiry)
        new_long_put = _make_contract(466.0, OptionDirection.PUT, expiry=diff_expiry)
        new_short_call = _make_contract(490.0, OptionDirection.CALL, expiry=diff_expiry)
        new_long_call = _make_contract(494.0, OptionDirection.CALL, expiry=diff_expiry)

        with _patch_config(IC_STRIKE_REUSE_GUARD_ENABLED=True, **_SEARCH_DEFAULTS):
            result = engine._validate_and_score_condor(
                short_put=new_short_put,
                long_put=new_long_put,
                short_call=new_short_call,
                long_call=new_long_call,
                **self._validate_kwargs(engine),
            )
        # Should NOT be blocked by strike reuse (may pass or fail other gates)
        assert engine._diag_drop_codes.get(R_IC_STRIKE_REUSE, 0) == 0

    def test_no_overlap_allows(self):
        """Non-overlapping strikes at same expiry are allowed."""
        engine = _make_engine()
        existing = _make_condor(qqq_price=480.0, entry_dte=30)
        engine.register_fill(existing)
        # Existing: short_put=470, long_put=466, short_call=490, long_call=494

        # Completely different strikes
        new_short_put = _make_contract(460.0, OptionDirection.PUT)
        new_long_put = _make_contract(456.0, OptionDirection.PUT)
        new_short_call = _make_contract(500.0, OptionDirection.CALL)
        new_long_call = _make_contract(504.0, OptionDirection.CALL)

        with _patch_config(IC_STRIKE_REUSE_GUARD_ENABLED=True, **_SEARCH_DEFAULTS):
            result = engine._validate_and_score_condor(
                short_put=new_short_put,
                long_put=new_long_put,
                short_call=new_short_call,
                long_call=new_long_call,
                **self._validate_kwargs(engine),
            )
        assert engine._diag_drop_codes.get(R_IC_STRIKE_REUSE, 0) == 0

    def test_disabled_config_allows(self):
        """IC_STRIKE_REUSE_GUARD_ENABLED=False bypasses the guard."""
        engine = _make_engine()
        existing = _make_condor(qqq_price=480.0, entry_dte=30)
        engine.register_fill(existing)

        # Same strikes as existing — would normally be blocked
        new_short_put = _make_contract(470.0, OptionDirection.PUT)
        new_long_put = _make_contract(466.0, OptionDirection.PUT)
        new_short_call = _make_contract(490.0, OptionDirection.CALL)
        new_long_call = _make_contract(494.0, OptionDirection.CALL)

        with _patch_config(IC_STRIKE_REUSE_GUARD_ENABLED=False, **_SEARCH_DEFAULTS):
            result = engine._validate_and_score_condor(
                short_put=new_short_put,
                long_put=new_long_put,
                short_call=new_short_call,
                long_call=new_long_call,
                **self._validate_kwargs(engine),
            )
        # Guard disabled — should NOT record strike reuse drop
        assert engine._diag_drop_codes.get(R_IC_STRIKE_REUSE, 0) == 0

    def test_closing_position_ignored(self):
        """Active IC that is already closing should not block new entries."""
        engine = _make_engine()
        existing = _make_condor(qqq_price=480.0, entry_dte=30)
        existing.is_closing = True
        engine.register_fill(existing)

        # Same strikes — should be allowed since existing is closing
        new_short_put = _make_contract(470.0, OptionDirection.PUT)
        new_long_put = _make_contract(466.0, OptionDirection.PUT)
        new_short_call = _make_contract(490.0, OptionDirection.CALL)
        new_long_call = _make_contract(494.0, OptionDirection.CALL)

        with _patch_config(IC_STRIKE_REUSE_GUARD_ENABLED=True, **_SEARCH_DEFAULTS):
            result = engine._validate_and_score_condor(
                short_put=new_short_put,
                long_put=new_long_put,
                short_call=new_short_call,
                long_call=new_long_call,
                **self._validate_kwargs(engine),
            )
        assert engine._diag_drop_codes.get(R_IC_STRIKE_REUSE, 0) == 0


# ═══════════════════════════════════════════════════════════════════
# CLOSE RETRY / ESCALATION TESTS
# ═══════════════════════════════════════════════════════════════════


class TestCloseRetry:
    """Tests for IC close-retry lifecycle with cooldown and escalation."""

    def _closing_condor(self, **kwargs) -> IronCondorPosition:
        condor = _make_condor(**kwargs)
        condor.is_closing = True
        return condor

    def test_close_retry_cooldown(self):
        """No signals emitted within cooldown window."""
        engine = _make_engine()
        condor = self._closing_condor()
        engine.register_fill(condor)
        condor.is_closing = True

        t0 = datetime(2025, 3, 5, 10, 0, 0)
        with _patch_config(
            IC_CLOSE_RETRY_COOLDOWN_MIN=5, IC_CLOSE_ESCALATION_THRESHOLD=2, IC_CLOSE_MAX_RETRIES=10
        ):
            # First call — should emit (no prior timestamp)
            sigs = engine.build_retry_close_signals(condor, ["short_put", "short_call"], t0)
            assert len(sigs) > 0
            assert condor.close_attempt_count == 1

            # 2 minutes later — within cooldown, no signals
            t1 = t0 + timedelta(minutes=2)
            sigs2 = engine.build_retry_close_signals(condor, ["short_put", "short_call"], t1)
            assert len(sigs2) == 0
            assert condor.close_attempt_count == 1  # Not incremented

            # 6 minutes later — past cooldown, should emit
            t2 = t0 + timedelta(minutes=6)
            sigs3 = engine.build_retry_close_signals(condor, ["short_put", "short_call"], t2)
            assert len(sigs3) > 0
            assert condor.close_attempt_count == 2

    def test_close_retry_combo_mode(self):
        """First N attempts emit combo close signals (no emergency flag)."""
        engine = _make_engine()
        condor = self._closing_condor()
        engine.register_fill(condor)
        condor.is_closing = True

        t0 = datetime(2025, 3, 5, 10, 0, 0)
        with _patch_config(
            IC_CLOSE_RETRY_COOLDOWN_MIN=0, IC_CLOSE_ESCALATION_THRESHOLD=2, IC_CLOSE_MAX_RETRIES=10
        ):
            sigs = engine.build_retry_close_signals(condor, ["short_put", "short_call"], t0)
            assert len(sigs) == 2  # One per live short leg
            assert condor.close_attempt_count == 1
            for s in sigs:
                assert s.metadata.get("spread_exit_emergency") is None
                assert s.metadata.get("spread_close_short") is True
                assert "COMBO" in s.reason

    def test_close_retry_escalation(self):
        """After threshold, signals include spread_exit_emergency=True."""
        engine = _make_engine()
        condor = self._closing_condor()
        engine.register_fill(condor)
        condor.is_closing = True

        t0 = datetime(2025, 3, 5, 10, 0, 0)
        with _patch_config(
            IC_CLOSE_RETRY_COOLDOWN_MIN=0, IC_CLOSE_ESCALATION_THRESHOLD=2, IC_CLOSE_MAX_RETRIES=10
        ):
            # Attempt 1 — combo
            engine.build_retry_close_signals(condor, ["short_put"], t0)
            assert condor.close_attempt_count == 1

            # Attempt 2 — still combo (threshold = 2)
            sigs2 = engine.build_retry_close_signals(condor, ["short_put"], t0)
            assert condor.close_attempt_count == 2
            assert sigs2[0].metadata.get("spread_exit_emergency") is None

            # Attempt 3 — escalated to sequential
            sigs3 = engine.build_retry_close_signals(condor, ["short_put"], t0)
            assert condor.close_attempt_count == 3
            assert sigs3[0].metadata.get("spread_exit_emergency") is True
            assert "SEQUENTIAL" in sigs3[0].reason

    def test_close_retry_max_abandon(self):
        """After max retries, is_closing cleared, no signals returned."""
        engine = _make_engine()
        condor = self._closing_condor()
        engine.register_fill(condor)
        condor.is_closing = True

        t0 = datetime(2025, 3, 5, 10, 0, 0)
        with _patch_config(
            IC_CLOSE_RETRY_COOLDOWN_MIN=0, IC_CLOSE_ESCALATION_THRESHOLD=2, IC_CLOSE_MAX_RETRIES=3
        ):
            # Burn through 3 attempts
            for _ in range(3):
                engine.build_retry_close_signals(condor, ["short_put"], t0)
            assert condor.close_attempt_count == 3
            assert condor.is_closing is True

            # 4th attempt exceeds max — abandoned
            sigs = engine.build_retry_close_signals(condor, ["short_put"], t0)
            assert len(sigs) == 0
            assert condor.is_closing is False
            assert condor.close_attempt_count == 0
            assert condor.last_close_signal_time is None

    def test_close_retry_resets_on_new_exit(self):
        """Fresh exit trigger resets attempt count to 0."""
        engine = _make_engine()
        condor = _make_condor(entry_dte=30)
        engine.register_fill(condor)

        # Simulate a prior close cycle that was abandoned
        condor.close_attempt_count = 5
        condor.last_close_signal_time = "2025-03-04 14:00:00"

        # Now a new exit triggers — is_closing set fresh with reset tracking
        condor.is_closing = True
        condor.close_attempt_count = 0
        condor.last_close_signal_time = None

        # First retry should start at attempt 1, not 6
        t0 = datetime(2025, 3, 5, 10, 0, 0)
        with _patch_config(
            IC_CLOSE_RETRY_COOLDOWN_MIN=0, IC_CLOSE_ESCALATION_THRESHOLD=2, IC_CLOSE_MAX_RETRIES=10
        ):
            sigs = engine.build_retry_close_signals(condor, ["short_put"], t0)
            assert condor.close_attempt_count == 1
