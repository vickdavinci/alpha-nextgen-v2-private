from __future__ import annotations

import sys
import types

import config

if "AlgorithmImports" not in sys.modules:
    algo_imports = types.ModuleType("AlgorithmImports")

    class _QCAlgorithm:
        @staticmethod
        def Log(_self, _message):
            return None

    algo_imports.QCAlgorithm = _QCAlgorithm
    sys.modules["AlgorithmImports"] = algo_imports

from main_observability_mixin import MainObservabilityMixin


class _Harness(MainObservabilityMixin):
    def __init__(self) -> None:
        self.LiveMode = False
        self.raw_logs = []
        self._log_budget_bytes_used = 0

    def _emit_log_raw(self, message):  # type: ignore[override]
        text = str(message or "")
        self.raw_logs.append(text)
        self._log_budget_bytes_used = int(getattr(self, "_log_budget_bytes_used", 0)) + int(
            self._estimate_log_bytes(text)
        )


def test_budget_checkpoint_emits_p1_summary_in_extreme_mode(monkeypatch):
    monkeypatch.setattr(config, "LOG_BUDGET_GUARD_ENABLED", True)
    monkeypatch.setattr(config, "LOG_BUDGET_GUARD_BACKTEST_ENABLED", True)
    monkeypatch.setattr(config, "LOG_BUDGET_GUARD_LIVE_ENABLED", False)
    monkeypatch.setattr(config, "LOG_BUDGET_SOFT_LIMIT_BYTES", 100)
    monkeypatch.setattr(config, "LOG_BUDGET_EXTREME_LIMIT_BYTES", 120)
    monkeypatch.setattr(config, "LOG_BUDGET_ESTIMATED_OVERHEAD_BYTES_PER_LINE", 0)
    monkeypatch.setattr(config, "LOG_BUDGET_SUPPRESSION_CHECKPOINT_ENABLED", True)
    monkeypatch.setattr(config, "LOG_BUDGET_SUPPRESSION_CHECKPOINT_EVERY_N", 2)
    monkeypatch.setattr(config, "LOG_BUDGET_SUPPRESSION_CHECKPOINT_PREVIEW_CHARS", 24)

    h = _Harness()
    h._log_budget_bytes_used = 200  # Force immediate extreme-mode suppression for P2/P3 logs.

    assert h._budget_log("P3 first suppressed event detail", priority=3) is False
    assert h._budget_log("P3 second suppressed event detail", priority=3) is False

    checkpoints = [line for line in h.raw_logs if line.startswith("LOG_BUDGET_CHECKPOINT:")]
    assert checkpoints, "Expected checkpoint summary after configured suppression interval"
    assert "Mode=EXTREME" in checkpoints[-1]
    assert "Suppressed=2" in checkpoints[-1]
    assert "P3=2" in checkpoints[-1]
    assert "LastP3=P3 second suppressed" in checkpoints[-1]
