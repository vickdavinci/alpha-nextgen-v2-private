from __future__ import annotations

import csv
import gzip
import io
import json
import re
from base64 import b64encode
from typing import Any, Dict, List, Optional

from AlgorithmImports import QCAlgorithm

import config


class MainObservabilityMixin:
    @staticmethod
    def _safe_objectstore_key_component(raw: Any, default: str = "default") -> str:
        text = str(raw or "").strip()
        if not text:
            return default
        # LocalObjectStore rejects some punctuation in key segments (notably dots inside run labels).
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", text)
        safe = re.sub(r"_+", "_", safe).strip("_")
        return safe or default

    def OnEndOfAlgorithm(self) -> None:
        """Flush end-of-run observability artifacts."""
        self._flush_regime_decision_artifact()
        self._flush_regime_timeline_artifact()
        self._flush_signal_lifecycle_artifact()
        self._flush_router_rejection_artifact()
        self._flush_order_lifecycle_artifact()

    def _ensure_log_budget_state(self) -> None:
        if not hasattr(self, "_log_budget_bytes_used"):
            self._log_budget_bytes_used = 0
        if not hasattr(self, "_log_budget_survival_mode"):
            self._log_budget_survival_mode = False
        if not hasattr(self, "_log_budget_extreme_mode"):
            self._log_budget_extreme_mode = False
        if not hasattr(self, "_log_budget_suppressed_total"):
            self._log_budget_suppressed_total = 0
        if not hasattr(self, "_log_budget_suppressed_by_priority"):
            self._log_budget_suppressed_by_priority = {"P1": 0, "P2": 0, "P3": 0}

    def _is_log_budget_enforced(self) -> bool:
        if not bool(getattr(config, "LOG_BUDGET_GUARD_ENABLED", True)):
            return False
        is_live = bool(hasattr(self, "LiveMode") and self.LiveMode)
        if is_live:
            return bool(getattr(config, "LOG_BUDGET_GUARD_LIVE_ENABLED", False))
        return bool(getattr(config, "LOG_BUDGET_GUARD_BACKTEST_ENABLED", True))

    def _estimate_log_bytes(self, message: Any) -> int:
        text = str(message or "")
        try:
            payload = len(text.encode("utf-8"))
        except Exception:
            payload = len(text)
        overhead = max(0, int(getattr(config, "LOG_BUDGET_ESTIMATED_OVERHEAD_BYTES_PER_LINE", 50)))
        return payload + overhead

    def _emit_log_raw(self, message: Any) -> None:
        text = str(message or "")
        QCAlgorithm.Log(self, text)
        self._log_budget_bytes_used = int(getattr(self, "_log_budget_bytes_used", 0)) + int(
            self._estimate_log_bytes(text)
        )

    def _budget_log(self, message: Any, priority: int = 2) -> bool:
        """Priority-based log gate.

        Priority 1: always keep (fills/exits/errors/daily summaries)
        Priority 2: normal diagnostics, suppressed only in extreme mode
        Priority 3: high-frequency diagnostics, suppressed in survival mode
        """
        self._ensure_log_budget_state()
        p = max(1, min(3, int(priority)))
        enforce = self._is_log_budget_enforced()
        used = int(getattr(self, "_log_budget_bytes_used", 0))
        soft_limit = max(0, int(getattr(config, "LOG_BUDGET_SOFT_LIMIT_BYTES", 4_000_000)))
        extreme_limit = max(
            soft_limit,
            int(getattr(config, "LOG_BUDGET_EXTREME_LIMIT_BYTES", 4_500_000)),
        )

        if enforce and used >= soft_limit and not bool(self._log_budget_survival_mode):
            self._log_budget_survival_mode = True
            self._emit_log_raw(
                "LOG_BUDGET_SURVIVAL: Entered survival mode "
                f"at {used / 1024 / 1024:.2f}MB | suppressing P3 logs"
            )
        if enforce and used >= extreme_limit and not bool(self._log_budget_extreme_mode):
            self._log_budget_extreme_mode = True
            self._emit_log_raw(
                "LOG_BUDGET_EXTREME: Entered extreme survival mode "
                f"at {used / 1024 / 1024:.2f}MB | suppressing P2/P3 logs"
            )

        should_suppress = False
        if enforce and p >= 3 and used >= soft_limit:
            should_suppress = True
        if enforce and p >= 2 and used >= extreme_limit:
            should_suppress = True
        if should_suppress:
            self._log_budget_suppressed_total = (
                int(getattr(self, "_log_budget_suppressed_total", 0)) + 1
            )
            bucket = f"P{p}"
            suppressed_by_priority = dict(
                getattr(self, "_log_budget_suppressed_by_priority", {}) or {}
            )
            suppressed_by_priority[bucket] = int(suppressed_by_priority.get(bucket, 0)) + 1
            self._log_budget_suppressed_by_priority = suppressed_by_priority
            return False

        self._emit_log_raw(message)
        return True

    def _save_observability_csv_artifact(
        self,
        key: str,
        fields: List[str],
        rows: List[Dict[str, Any]],
        error_prefix: str,
    ) -> None:
        """Common CSV artifact serializer for observability channels."""
        if not key or not rows:
            return
        retries = max(1, int(getattr(config, "OBSERVABILITY_OBJECTSTORE_SAVE_RETRIES", 2)))

        def _render_csv(payload_rows: List[Dict[str, Any]]) -> str:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(payload_rows)
            return output.getvalue()

        def _save_text(target_key: str, payload: str) -> bool:
            for attempt in range(1, retries + 1):
                try:
                    save_result = self.ObjectStore.Save(target_key, payload)
                    if save_result is False:
                        raise RuntimeError("ObjectStore.Save returned False")
                    return True
                except Exception as e:
                    if attempt >= retries:
                        self.Log(f"{error_prefix}: key={target_key} | attempt={attempt} | {e}")
                        return False
            return False

        def _should_fallback_to_logs() -> bool:
            if not bool(getattr(config, "OBSERVABILITY_LOG_FALLBACK_ENABLED", False)):
                return False
            key_norm = str(key or "")
            if key_norm == str(getattr(self, "_regime_observability_key", "")):
                return bool(getattr(config, "REGIME_OBSERVABILITY_LOG_FALLBACK_ENABLED", True))
            if key_norm == str(getattr(self, "_regime_timeline_observability_key", "")):
                return bool(getattr(config, "REGIME_TIMELINE_LOG_FALLBACK_ENABLED", True))
            if key_norm == str(getattr(self, "_signal_lifecycle_observability_key", "")):
                return bool(getattr(config, "SIGNAL_LIFECYCLE_LOG_FALLBACK_ENABLED", False))
            if key_norm == str(getattr(self, "_router_rejection_observability_key", "")):
                return bool(getattr(config, "ROUTER_REJECTION_LOG_FALLBACK_ENABLED", False))
            if key_norm == str(getattr(self, "_order_lifecycle_observability_key", "")):
                return bool(getattr(config, "ORDER_LIFECYCLE_LOG_FALLBACK_ENABLED", False))
            return False

        def _emit_log_fallback(payload_csv: str) -> None:
            if not payload_csv or not _should_fallback_to_logs():
                return
            signature = f"{len(rows)}|{len(payload_csv)}"
            last_signature = self._observability_log_fallback_signature_by_key.get(key)
            if signature == last_signature:
                return
            try:
                compressed = gzip.compress(payload_csv.encode("utf-8"))
                encoded = b64encode(compressed).decode("ascii")
            except Exception as e:
                self.Log(f"OBS_FALLBACK_ENCODE_ERROR: Key={key} | {e}")
                return

            chunk_size = max(
                512,
                int(getattr(config, "OBSERVABILITY_LOG_FALLBACK_CHUNK_SIZE", 3400)),
            )
            total_parts = (len(encoded) + chunk_size - 1) // chunk_size
            self.Log(
                f"OBS_FALLBACK_BEGIN: Key={key} | Rows={len(rows)} | "
                f"Bytes={len(payload_csv)} | Parts={total_parts} | Encoding=gzip+base64"
            )
            for idx in range(total_parts):
                start = idx * chunk_size
                end = min((idx + 1) * chunk_size, len(encoded))
                self.Log(
                    f"OBS_FALLBACK_PART: Key={key} | Part={idx + 1}/{total_parts} | Data={encoded[start:end]}"
                )
            self.Log(f"OBS_FALLBACK_END: Key={key} | Rows={len(rows)} | Parts={total_parts}")
            self._observability_log_fallback_signature_by_key[key] = signature

        fallback_csv: Optional[str] = None
        shard_enabled = bool(getattr(config, "OBSERVABILITY_OBJECTSTORE_SHARD_ENABLED", True))
        shard_max_rows = int(getattr(config, "OBSERVABILITY_OBJECTSTORE_SHARD_MAX_ROWS", 12000))
        max_shards = max(1, int(getattr(config, "OBSERVABILITY_OBJECTSTORE_MAX_SHARDS", 32)))
        if not shard_enabled or shard_max_rows <= 0 or len(rows) <= shard_max_rows:
            fallback_csv = _render_csv(rows)
            saved = _save_text(key, fallback_csv)
            if not saved:
                _emit_log_fallback(fallback_csv)
            return

        shard_total = (len(rows) + shard_max_rows - 1) // shard_max_rows
        shard_total = min(shard_total, max_shards)
        if shard_total <= 1:
            fallback_csv = _render_csv(rows)
            saved = _save_text(key, fallback_csv)
            if not saved:
                _emit_log_fallback(fallback_csv)
            return

        adjusted_rows_per_shard = (len(rows) + shard_total - 1) // shard_total
        key_root = key[:-4] if key.endswith(".csv") else key
        manifest_key = f"{key_root}__manifest.json"
        wrote_all = True
        for shard_idx in range(shard_total):
            start = shard_idx * adjusted_rows_per_shard
            end = min((shard_idx + 1) * adjusted_rows_per_shard, len(rows))
            shard_rows = rows[start:end]
            if not shard_rows:
                continue
            part_csv = _render_csv(shard_rows)
            part_key = f"{key_root}__part{shard_idx + 1:03d}.csv"
            if not _save_text(part_key, part_csv):
                wrote_all = False
        if wrote_all:
            manifest = {
                "base_key": key,
                "parts": shard_total,
                "rows": len(rows),
                "fields": fields,
                "timestamp": self.Time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if not _save_text(manifest_key, json.dumps(manifest, separators=(",", ":"))):
                wrote_all = False
        if not wrote_all:
            if fallback_csv is None:
                fallback_csv = _render_csv(rows)
            _emit_log_fallback(fallback_csv)

    def _on_observability_checkpoint(self) -> None:
        """Periodic telemetry checkpoint to persist RCA artifacts mid-session."""
        if self.IsWarmingUp:
            return
        self._record_regime_timeline_event(source="PERIODIC_CHECKPOINT")
        self._flush_regime_decision_artifact()
        self._flush_regime_timeline_artifact()
        self._flush_signal_lifecycle_artifact()
        self._flush_router_rejection_artifact()
        self._flush_order_lifecycle_artifact()

    def _build_regime_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="REGIME_OBSERVABILITY_OBJECTSTORE_KEY_PREFIX",
            default_prefix="regime_observability",
        )

    def _build_observability_key(self, prefix_config_name: str, default_prefix: str) -> str:
        prefix = self._safe_objectstore_key_component(
            getattr(config, prefix_config_name, default_prefix),
            default=default_prefix,
        )
        run_suffix_raw = self._run_label or f"year_{self._backtest_year}"
        run_suffix = self._safe_objectstore_key_component(run_suffix_raw, default="run")
        year = self._safe_objectstore_key_component(self._backtest_year, default="year")
        # LocalObjectStore does not support path-style keys ("/"), so keep this flat.
        return f"{prefix}__{run_suffix}_{year}.csv"

    def _build_regime_timeline_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="REGIME_TIMELINE_OBJECTSTORE_KEY_PREFIX",
            default_prefix="regime_timeline_observability",
        )

    def _build_signal_lifecycle_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="SIGNAL_LIFECYCLE_OBJECTSTORE_KEY_PREFIX",
            default_prefix="signal_lifecycle_observability",
        )

    def _build_router_rejection_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="ROUTER_REJECTION_OBJECTSTORE_KEY_PREFIX",
            default_prefix="router_rejection_observability",
        )

    def _build_order_lifecycle_observability_key(self) -> str:
        return self._build_observability_key(
            prefix_config_name="ORDER_LIFECYCLE_OBJECTSTORE_KEY_PREFIX",
            default_prefix="order_lifecycle_observability",
        )

    def _should_log_backtest_category(self, config_flag: str, default: bool = True) -> bool:
        """Return whether a log category is enabled for current run mode."""
        is_live = bool(hasattr(self, "LiveMode") and self.LiveMode)
        if is_live:
            return True
        return bool(getattr(config, config_flag, default))

    def _ensure_daily_proxy_windows_snapshot(self) -> None:
        """Backfill daily proxy windows from latest closes when intraday feed missed close bar."""
        day_key = self.Time.date()
        symbols = (
            (self.spy, self.spy_closes, "SPY"),
            (self.rsp, self.rsp_closes, "RSP"),
            (self.hyg, self.hyg_closes, "HYG"),
            (self.ief, self.ief_closes, "IEF"),
        )
        for symbol, window, key in symbols:
            if self._daily_proxy_window_last_update.get(key) == day_key:
                continue
            try:
                close_px = float(self.Securities[symbol].Close)
            except Exception:
                continue
            if close_px <= 0:
                continue
            window.Add(close_px)
            self._daily_proxy_window_last_update[key] = day_key
