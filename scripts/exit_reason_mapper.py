#!/usr/bin/env python3
"""Shared exit-reason normalization for report tooling."""

from __future__ import annotations

import re
from typing import Optional


def is_reconciled_close_marker(text: str) -> bool:
    """Match reconciled-close reason variants with optional suffix payloads."""
    upper = str(text or "").upper()
    if not upper:
        return False
    return bool(
        re.search(r"\b(FILL_CLOSE_RECONCILED|RECONCILED_CLOSE(?:[:_|A-Z0-9-].*)?)\b", upper)
    )


def classify_exit_reason(text: str) -> Optional[str]:
    """Normalize raw exit text to canonical reason buckets."""
    upper = str(text or "").upper()
    if not upper:
        return None
    if "QQQ_INVALIDATION_INTRADAY" in upper:
        return "QQQ_INVALIDATION_INTRADAY"
    if "OVERNIGHT_GAP_PROTECTION" in upper:
        return "OVERNIGHT_GAP_PROTECTION"
    if "OVERLAY_STRESS_EXIT" in upper:
        return "OVERLAY_STRESS_EXIT"
    if "REGIME_DETERIORATION" in upper:
        return "REGIME_DETERIORATION"
    if "TRANSITION_DERISK_" in upper:
        return "TRANSITION_DERISK"
    if "MFE_LOCK_T2" in upper:
        return "MFE_LOCK_T2"
    if "MFE_LOCK_T1" in upper:
        return "MFE_LOCK_T1"
    if "MFE_LOCK" in upper:
        return "MFE_LOCK"
    if "CONVICTION_FLOOR" in upper:
        return "CONVICTION_FLOOR"
    if "PREMARKET_ITM_CLOSE" in upper:
        return "PREMARKET_ITM_CLOSE"
    if "PARTIAL_ASSIGNMENT_RECOVERY" in upper:
        return "PARTIAL_ASSIGNMENT_RECOVERY"
    if "MARGIN_BUFFER_INSUFFICIENT" in upper:
        return "MARGIN_BUFFER_INSUFFICIENT"
    if "HARD_STOP_TRIGGERED_WIDTH" in upper:
        return "HARD_STOP_WIDTH"
    if "SPREAD_HARD_STOP" in upper or "HARD_STOP" in upper:
        return "HARD_STOP"
    if "CREDIT_THETA_STOP" in upper:
        return "CREDIT_THETA_STOP"
    if "CREDIT_STOP_2X" in upper or "CREDIT_STOP_LOSS" in upper:
        return "CREDIT_STOP_LOSS"
    if "STOP_LOSS" in upper:
        return "STOP_LOSS"
    if "CREDIT_PROFIT_TARGET" in upper or "PROFIT_TARGET" in upper:
        return "PROFIT_TARGET"
    if "TRAIL_STOP" in upper:
        return "TRAIL_STOP"
    if "DTE_EXIT" in upper:
        return "DTE_EXIT"
    if "DAY4_EOD_CLOSE" in upper:
        return "DAY4_EOD_CLOSE"
    if "PREMARKET_ITM_GUARDED_SKIP" in upper:
        return "PREMARKET_ITM_GUARDED_SKIP"
    if "FRIDAY_FIREWALL_SKIPPED_DTE" in upper:
        return "FRIDAY_FIREWALL_SKIPPED_DTE"
    if "FRIDAY_FIREWALL" in upper:
        return "FRIDAY_FIREWALL"
    if is_reconciled_close_marker(upper):
        return "RECONCILED"
    if "ASSIGNMENT_RISK" in upper:
        return "ASSIGNMENT_RISK"
    if "SPREAD_CLOSE_RETRY" in upper:
        return "CLOSE_RETRY"
    if "SPREAD_OVERLAY_EXIT" in upper and "STRESS" in upper:
        return "STRESS_EXIT"
    return None
