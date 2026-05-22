"""ASGL Telemetry (Pilot Metrics) — v0

Purpose
-------
Track pilot metrics from day 1, in a privacy-safe, audit-friendly way.

This module provides:
- event logging (JSONL) + rollup counters
- per-tenant, per-job metrics
- helper functions to instrument ingestion/mapping/review loops

Storage
-------
Writes to: storage://telemetry/<tenant_id>/<YYYY-MM-DD>.jsonl
And a compact rollup to: storage://telemetry/<tenant_id>/rollup.json

Events
------
Events are append-only, with a stable schema:
  { ts, tenant_id, event, job_id, vertical, meta{...} }

Env
---
ASGL_TELEMETRY_ENABLE=1
ASGL_TELEMETRY_SALT=<random string>  (optional; used for hashing file identifiers)
"""

from __future__ import annotations

import os, json, time, hashlib
from datetime import datetime
from typing import Any, Dict, Optional

from app.logging import get_logger
from app.storage import storage

log = get_logger(__name__)

def enabled() -> bool:
    return os.getenv("ASGL_TELEMETRY_ENABLE", "1") not in ("0","false","FALSE","no","NO")

def _now_ts() -> int:
    return int(time.time())

def _day_key(ts: Optional[int] = None) -> str:
    dt = datetime.utcfromtimestamp(ts or _now_ts())
    return dt.strftime("%Y-%m-%d")

def _tenant_root(tenant_id: str) -> str:
    return f"telemetry/{tenant_id}"

def _event_path(tenant_id: str, day: str) -> str:
    return f"{_tenant_root(tenant_id)}/{day}.jsonl"

def _rollup_path(tenant_id: str) -> str:
    return f"{_tenant_root(tenant_id)}/rollup.json"

def _hash_id(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    salt = os.getenv("ASGL_TELEMETRY_SALT", "asgl")
    h = hashlib.sha256((salt + "::" + raw).encode("utf-8")).hexdigest()
    return h[:16]

def emit(
    *,
    tenant_id: str,
    event: str,
    job_id: Optional[str] = None,
    vertical: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a telemetry event."""
    if not enabled():
        return
    ts = _now_ts()
    day = _day_key(ts)
    payload = {
        "ts": ts,
        "tenant_id": str(tenant_id),
        "event": str(event),
        "job_id": str(job_id) if job_id else None,
        "vertical": str(vertical) if vertical else None,
        "meta": meta or {},
    }
    try:
        storage.append_jsonl(_event_path(tenant_id, day), payload)
    except Exception as e:
        log.exception("telemetry_emit_failed", error=str(e), event=event)
        return
    try:
        _rollup_update(tenant_id, payload)
    except Exception:
        pass

def _rollup_update(tenant_id: str, ev: Dict[str, Any]) -> None:
    """Update counters in a compact rollup."""
    path = _rollup_path(tenant_id)
    roll = storage.read_json(path) or {}
    roll.setdefault("tenant_id", str(tenant_id))
    roll.setdefault("updated_ts", 0)
    roll.setdefault("counters", {})
    roll.setdefault("by_vertical", {})
    roll.setdefault("by_day", {})

    event = ev.get("event") or "unknown"
    vertical = (ev.get("vertical") or "_").upper()
    day = _day_key(ev.get("ts"))

    def inc(d: Dict[str, Any], key: str, amt: int = 1):
        d[key] = int(d.get(key, 0)) + amt

    inc(roll["counters"], event, 1)
    roll["by_vertical"].setdefault(vertical, {})
    inc(roll["by_vertical"][vertical], event, 1)
    roll["by_day"].setdefault(day, {})
    inc(roll["by_day"][day], event, 1)

    # Optional metrics
    m = ev.get("meta") or {}
    if event == "mapping.proposed":
        inc(roll["counters"], "mapping.proposed.total", int(m.get("n_total", 0)))
        inc(roll["counters"], "mapping.proposed.auto", int(m.get("n_auto", 0)))
        inc(roll["counters"], "mapping.proposed.queued", int(m.get("n_queued", 0)))
    if event == "review.resolved":
        inc(roll["counters"], "review.resolved.total", 1)
        if m.get("created_override"):
            inc(roll["counters"], "review.override.created", 1)
        if m.get("updated_template"):
            inc(roll[["counters"]][0], "review.template.updated", 1)
        if m.get("logged_training_example"):
            inc(roll["counters"], "review.training.logged", 1)

    roll["updated_ts"] = _now_ts()
    storage.write_json(path, roll)

def summarize(tenant_id: str) -> Dict[str, Any]:
    if not enabled():
        return {"enabled": False}
    roll = storage.read_json(_rollup_path(tenant_id)) or {}
    roll["enabled"] = True
    return roll

# --------------------------
# Helper instrumentation APIs
# --------------------------

def track_ingestion_start(*, tenant_id: str, job_id: Optional[str], vertical: Optional[str], workbook: Optional[str], sheet: Optional[str]):
    emit(
        tenant_id=tenant_id,
        event="ingestion.start",
        job_id=job_id,
        vertical=vertical,
        meta={
            "workbook_id": _hash_id(workbook),
            "sheet": sheet,
        },
    )

def track_ingestion_complete(*, tenant_id: str, job_id: Optional[str], vertical: Optional[str], n_rows: Optional[int], n_cols: Optional[int], elapsed_ms: Optional[int]):
    emit(
        tenant_id=tenant_id,
        event="ingestion.complete",
        job_id=job_id,
        vertical=vertical,
        meta={
            "n_rows": n_rows,
            "n_cols": n_cols,
            "elapsed_ms": elapsed_ms,
        },
    )

def track_mapping_proposed(*, tenant_id: str, job_id: Optional[str], vertical: Optional[str], n_total: int, n_auto: int, n_queued: int, min_conf: Optional[float] = None):
    emit(
        tenant_id=tenant_id,
        event="mapping.proposed",
        job_id=job_id,
        vertical=vertical,
        meta={
            "n_total": int(n_total),
            "n_auto": int(n_auto),
            "n_queued": int(n_queued),
            "min_conf": float(min_conf) if min_conf is not None else None,
        },
    )

def track_review_opened(*, tenant_id: str, job_id: Optional[str], vertical: Optional[str], item_id: str):
    emit(
        tenant_id=tenant_id,
        event="review.opened",
        job_id=job_id,
        vertical=vertical,
        meta={"item_id": item_id},
    )

def track_review_resolved(*, tenant_id: str, job_id: Optional[str], vertical: Optional[str], item_id: str, created_override: bool, updated_template: bool, logged_training_example: bool):
    emit(
        tenant_id=tenant_id,
        event="review.resolved",
        job_id=job_id,
        vertical=vertical,
        meta={
            "item_id": item_id,
            "created_override": bool(created_override),
            "updated_template": bool(updated_template),
            "logged_training_example": bool(logged_training_example),
        },
    )
