from __future__ import annotations

import sys
import types
from datetime import datetime

if "AlgorithmImports" not in sys.modules:
    algo_imports = types.ModuleType("AlgorithmImports")

    class _QCAlgorithm:
        @staticmethod
        def Log(_self, _message):
            return None

    algo_imports.QCAlgorithm = _QCAlgorithm
    sys.modules["AlgorithmImports"] = algo_imports

from main_observability_mixin import MainObservabilityMixin


class _ObjectStoreStub:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []

    def Save(self, key: str, payload: str) -> bool:  # QC-style method name
        self.saved.append((key, payload))
        return True


class _Harness(MainObservabilityMixin):
    def __init__(self) -> None:
        self.Time = datetime(2026, 3, 4, 12, 0, 0)
        self.ObjectStore = _ObjectStoreStub()
        self._observability_log_fallback_signature_by_key = {}
        self._regime_observability_key = ""
        self._regime_timeline_observability_key = ""
        self._signal_lifecycle_observability_key = ""
        self._router_rejection_observability_key = ""
        self._order_lifecycle_observability_key = ""
        self.logs: list[str] = []

    def Log(self, message: str) -> None:  # QC-style method name
        self.logs.append(str(message))


def test_save_observability_csv_artifact_skips_empty_when_not_requested() -> None:
    h = _Harness()
    h._save_observability_csv_artifact(
        key="router_rejection_observability__run_2026.csv",
        fields=["time", "stage", "code"],
        rows=[],
        error_prefix="ROUTER_REJECTION_SAVE_ERROR",
        emit_if_empty=False,
    )
    assert h.ObjectStore.saved == []


def test_save_observability_csv_artifact_emits_header_when_empty_requested() -> None:
    h = _Harness()
    h._save_observability_csv_artifact(
        key="router_rejection_observability__run_2026.csv",
        fields=["time", "stage", "code"],
        rows=[],
        error_prefix="ROUTER_REJECTION_SAVE_ERROR",
        emit_if_empty=True,
    )

    assert len(h.ObjectStore.saved) == 1
    saved_key, payload = h.ObjectStore.saved[0]
    assert saved_key == "router_rejection_observability__run_2026.csv"
    lines = payload.splitlines()
    assert lines[0] == "time,stage,code"
    assert len(lines) == 1
