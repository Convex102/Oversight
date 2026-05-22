"""Phase 2 v5: Active Learning Queue (tenant-scoped) + Review Workflow hooks.

Goal
----
When the ranker is uncertain (low confidence) we *abstain* and push a review item into
an Active Learning queue. Reviewers confirm mappings, and confirmations are logged back
into the Phase2 dataset for continual training.

Design
------
- Tenant-scoped queue stored in app.storage as JSONL (append-only) + index json.
- Lightweight primitives: enqueue, list, ack/resolve.
- No new DB migrations required (works everywhere).
- Can later be upgraded to DB tables without breaking API.

Storage keys:
  ml/active_learning/<tenant>/queue.jsonl
  ml/active_learning/<tenant>/state.json

Env:
  ASGL_ACTIVE_LEARNING_ENABLE=1
  ASGL_ACTIVE_LEARNING_MAX_OPEN=5000
"""

from __future__ import annotations

import os, json, time, uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.logging import get_logger
from app.storage import storage

log = get_logger(__name__)

def _safe(tenant_id: str) -> str:
    return str(tenant_id).replace(":", "_").replace("/", "_")

def _queue_key(tenant_id: str) -> str:
    return f"ml/active_learning/{_safe(tenant_id)}/queue.jsonl"

def _state_key(tenant_id: str) -> str:
    return f"ml/active_learning/{_safe(tenant_id)}/state.json"

def _enabled() -> bool:
    return os.getenv("ASGL_ACTIVE_LEARNING_ENABLE", "1") not in ("0","false","FALSE","no","NO")

def _load_state(tenant_id: str) -> Dict[str, Any]:
    try:
        b = storage.read(_state_key(tenant_id))
        if not b:
            return {"open": 0, "resolved": 0, "last_ts": 0}
        return json.loads(b.decode("utf-8"))
    except Exception:
        return {"open": 0, "resolved": 0, "last_ts": 0}

def _save_state(tenant_id: str, st: Dict[str, Any]) -> None:
    try:
        storage.write(_state_key(tenant_id), json.dumps(st).encode("utf-8"))
    except Exception:
        pass

@dataclass
class ReviewItem:
    id: str
    tenant_id: str
    ts: int
    job_id: Optional[int]
    template_id: Optional[int]
    vertical: Optional[str]
    workbook: Optional[str]
    sheet: Optional[str]
    source_column: str
    column_text: str
    candidates: List[Dict[str, Any]]  # [{canonical_field, score}]
    status: str = "open"
    resolved_ts: Optional[int] = None
    resolution: Optional[Dict[str, Any]] = None

def enqueue_review_item(
    *,
    tenant_id: str,
    source_column: str,
    column_text: str,
    candidates: List[Dict[str, Any]],
    job_id: Optional[int] = None,
    template_id: Optional[int] = None,
    vertical: Optional[str] = None,
    workbook: Optional[str] = None,
    sheet: Optional[str] = None,
) -> Optional[str]:
    if not _enabled():
        return None
    st = _load_state(tenant_id)
    max_open = int(os.getenv("ASGL_ACTIVE_LEARNING_MAX_OPEN", "5000"))
    if st.get("open", 0) >= max_open:
        log.info("active_learning_queue_full", tenant_id=str(tenant_id), open=st.get("open"), max_open=max_open)
        return None

    item = ReviewItem(
        id=str(uuid.uuid4()),
        tenant_id=str(tenant_id),
        ts=int(time.time()),
        job_id=job_id,
        template_id=template_id,
        vertical=vertical,
        workbook=workbook,
        sheet=sheet,
        source_column=str(source_column),
        column_text=str(column_text),
        candidates=candidates[:10],
    )
    rec = json.dumps(item.__dict__, ensure_ascii=False)
    key = _queue_key(tenant_id)
    blob = (storage.read(key) or b"") + (rec + "\n").encode("utf-8")
    storage.write(key, blob)
    st["open"] = int(st.get("open", 0)) + 1
    st["last_ts"] = int(time.time())
    _save_state(tenant_id, st)
    log.info("active_learning_enqueued", tenant_id=str(tenant_id), item_id=item.id, source_column=item.source_column)
    return item.id

def list_open_items(tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    key = _queue_key(tenant_id)
    blob = storage.read(key) or b""
    items = []
    for ln in blob.decode("utf-8", errors="ignore").splitlines()[::-1]:
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
            if r.get("status") == "open":
                items.append(r)
            if len(items) >= limit:
                break
        except Exception:
            continue
    return items

def resolve_item(
    *,
    tenant_id: str,
    item_id: str,
    chosen_canonical_field: str,
    note: Optional[str] = None,
    reviewer: Optional[str] = None,
) -> bool:
    """Mark as resolved by appending a resolution record."""
    if not _enabled():
        return False
    resolution = {
        "id": str(item_id),
        "tenant_id": str(tenant_id),
        "ts": int(time.time()),
        "status": "resolved",
        "resolved_ts": int(time.time()),
        "resolution": {
            "chosen_canonical_field": str(chosen_canonical_field),
            "note": note,
            "reviewer": reviewer,
        }
    }
    key = _queue_key(tenant_id)
    blob = (storage.read(key) or b"") + (json.dumps(resolution, ensure_ascii=False) + "\n").encode("utf-8")
    storage.write(key, blob)
    st = _load_state(tenant_id)
    st["open"] = max(0, int(st.get("open", 0)) - 1)
    st["resolved"] = int(st.get("resolved", 0)) + 1
    st["last_ts"] = int(time.time())
    _save_state(tenant_id, st)
    log.info("active_learning_resolved", tenant_id=str(tenant_id), item_id=item_id, chosen=chosen_canonical_field)
    return True
