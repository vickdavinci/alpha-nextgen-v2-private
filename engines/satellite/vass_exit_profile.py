"""VASS exit-profile helpers extracted from options_engine."""

from __future__ import annotations

import config


def record_vass_mfe_diag_impl(self, spread: SpreadPosition, prev_tier: int) -> None:
    """Record VASS MFE telemetry into algorithm-level daily counters."""
    algo = self.algorithm
    if algo is None:
        return
    try:
        peak = float(getattr(spread, "highest_pnl_max_profit_pct", 0.0) or 0.0)
        if hasattr(algo, "_diag_vass_mfe_peak_max_profit_pct"):
            algo._diag_vass_mfe_peak_max_profit_pct = max(
                float(getattr(algo, "_diag_vass_mfe_peak_max_profit_pct", 0.0) or 0.0),
                peak,
            )
        curr_tier = int(getattr(spread, "mfe_lock_tier", 0) or 0)
        if curr_tier > int(prev_tier):
            if curr_tier >= 1 and hasattr(algo, "_diag_vass_mfe_t1_hits"):
                algo._diag_vass_mfe_t1_hits = (
                    int(getattr(algo, "_diag_vass_mfe_t1_hits", 0) or 0) + 1
                )
            if curr_tier >= 2 and hasattr(algo, "_diag_vass_mfe_t2_hits"):
                algo._diag_vass_mfe_t2_hits = (
                    int(getattr(algo, "_diag_vass_mfe_t2_hits", 0) or 0) + 1
                )
    except Exception:
        return


def get_vass_exit_profile_impl(
    self, spread: SpreadPosition, vix_current: Optional[float]
) -> Dict[str, Any]:
    """
    Resolve VASS debit exit profile using a frozen tier.

    Tier is anchored to entry VIX when available to avoid intra-trade profile
    flipping during volatility spikes/drops.
    """
    base_profile: Dict[str, Any] = {
        "tier": "MED",
        "ref_vix": float(vix_current) if vix_current is not None else None,
        "target_pct": float(getattr(config, "SPREAD_PROFIT_TARGET_PCT", 0.40)),
        "stop_pct": float(getattr(config, "SPREAD_STOP_LOSS_PCT", 0.35)),
        "trail_activate_pct": float(getattr(config, "SPREAD_TRAIL_ACTIVATE_PCT", 0.22)),
        "trail_offset_pct": float(getattr(config, "SPREAD_TRAIL_OFFSET_PCT", 0.15)),
        "mfe_t2_floor_pct": float(getattr(config, "VASS_MFE_T2_FLOOR_PCT", 0.15)),
        "hard_stop_pct": float(getattr(config, "SPREAD_HARD_STOP_LOSS_PCT", 0.40)),
        "eod_gate_pct": float(getattr(config, "SPREAD_EOD_HOLD_RISK_GATE_PCT", -0.25)),
    }
    if not bool(getattr(config, "VASS_EXIT_TIERED_ENABLED", False)):
        return base_profile

    use_entry_tier = bool(getattr(config, "VASS_EXIT_USE_ENTRY_VIX_TIER", True))
    entry_vix = getattr(spread, "entry_vix", None)
    ref_vix = (
        float(entry_vix)
        if use_entry_tier and entry_vix is not None
        else float(vix_current)
        if vix_current is not None
        else float(entry_vix)
        if entry_vix is not None
        else None
    )
    if ref_vix is None:
        return base_profile

    low_max = float(getattr(config, "VASS_EXIT_VIX_LOW_MAX", 18.0))
    high_min = float(getattr(config, "VASS_EXIT_VIX_HIGH_MIN", 25.0))
    profile = dict(base_profile)
    profile["ref_vix"] = ref_vix

    if ref_vix < low_max:
        profile.update(
            {
                "tier": "LOW",
                "target_pct": float(getattr(config, "VASS_TARGET_PCT_LOW_VIX", 0.35)),
                "stop_pct": float(getattr(config, "VASS_STOP_PCT_LOW_VIX", 0.25)),
                "trail_activate_pct": float(getattr(config, "VASS_TRAIL_ACTIVATE_LOW_VIX", 0.18)),
                "trail_offset_pct": float(getattr(config, "VASS_TRAIL_OFFSET_LOW_VIX", 0.12)),
                "mfe_t2_floor_pct": float(getattr(config, "VASS_MFE_T2_FLOOR_LOW_VIX", 0.12)),
                "hard_stop_pct": float(getattr(config, "VASS_HARD_STOP_LOW_VIX", 0.35)),
                "eod_gate_pct": float(getattr(config, "VASS_EOD_GATE_LOW_VIX", -0.20)),
            }
        )
        return profile

    if ref_vix >= high_min:
        profile.update(
            {
                "tier": "HIGH",
                "target_pct": float(getattr(config, "VASS_TARGET_PCT_HIGH_VIX", 0.50)),
                "stop_pct": float(getattr(config, "VASS_STOP_PCT_HIGH_VIX", 0.40)),
                "trail_activate_pct": float(getattr(config, "VASS_TRAIL_ACTIVATE_HIGH_VIX", 0.28)),
                "trail_offset_pct": float(getattr(config, "VASS_TRAIL_OFFSET_HIGH_VIX", 0.20)),
                "mfe_t2_floor_pct": float(getattr(config, "VASS_MFE_T2_FLOOR_HIGH_VIX", 0.25)),
                "hard_stop_pct": float(getattr(config, "VASS_HARD_STOP_HIGH_VIX", 0.45)),
                "eod_gate_pct": float(getattr(config, "VASS_EOD_GATE_HIGH_VIX", -0.35)),
            }
        )
        return profile

    profile.update(
        {
            "tier": "MED",
            "target_pct": float(getattr(config, "VASS_TARGET_PCT_MED_VIX", 0.40)),
            "stop_pct": float(getattr(config, "VASS_STOP_PCT_MED_VIX", 0.35)),
            "trail_activate_pct": float(getattr(config, "VASS_TRAIL_ACTIVATE_MED_VIX", 0.22)),
            "trail_offset_pct": float(getattr(config, "VASS_TRAIL_OFFSET_MED_VIX", 0.15)),
            "mfe_t2_floor_pct": float(getattr(config, "VASS_MFE_T2_FLOOR_MED_VIX", 0.18)),
            "hard_stop_pct": float(getattr(config, "VASS_HARD_STOP_MED_VIX", 0.40)),
            "eod_gate_pct": float(getattr(config, "VASS_EOD_GATE_MED_VIX", -0.25)),
        }
    )
    return profile


def resolve_qqq_atr_pct_impl(self, underlying_price: Optional[float]) -> Optional[float]:
    """Return QQQ ATR% (ATR/price) when indicator context is available."""
    if self.algorithm is None or underlying_price is None or float(underlying_price) <= 0:
        return None
    qqq_atr = getattr(self.algorithm, "qqq_atr", None)
    if qqq_atr is None or not bool(getattr(qqq_atr, "IsReady", False)):
        return None
    try:
        atr_value = float(qqq_atr.Current.Value)
        if atr_value <= 0:
            return None
        return atr_value / float(underlying_price)
    except Exception:
        return None


# =========================================================================
# V2.3 SPREAD EXIT SIGNALS
# =========================================================================
