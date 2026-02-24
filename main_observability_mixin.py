from __future__ import annotations

import csv
import gzip
import io
import json
from base64 import b64encode
from typing import Any, Dict, List, Optional

import config


class MainObservabilityMixin:
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
