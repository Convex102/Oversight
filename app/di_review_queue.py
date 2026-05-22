from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Dict, List, Optional


def build_items(
    suggestions: List[Dict[str, Any]],
    *,
    tenant_id: int,
    job_id: int,
    schema_name: Optional[str] = None,
    schema_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in (suggestions or []):
        out.append({
            "item_id": uuid.uuid4().hex,
            "status": "open",
            "created_at": dt.datetime.utcnow().isoformat() + "Z",
            "resolved_at": None,
            "tenant_id": int(tenant_id),
            "job_id": int(job_id),
            "schema": {"name": schema_name, "version": schema_version},
            "field": s.get("field"),
            "proposed_value": s.get("proposed_value"),
            "confidence": float(s.get("confidence", 0.0) or 0.0),
            "evidence": s.get("evidence") or [],
            "reason": s.get("reason") or "review_required",
            "resolution": None,
        })
    return out


def resolve_item(
    items: List[Dict[str, Any]],
    *,
    item_id: str,
    resolution: Dict[str, Any],
) -> List[Dict[str, Any]]:
    now = dt.datetime.utcnow().isoformat() + "Z"
    updated = []
    for it in (items or []):
        if str(it.get("item_id")) == str(item_id):
            it = dict(it)
            it["status"] = "resolved"
            it["resolved_at"] = now
            it["resolution"] = resolution or {}
        updated.append(it)
    return updated
