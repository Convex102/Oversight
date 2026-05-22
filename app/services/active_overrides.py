"""Phase 2 v5.1: Active Overrides Store (tenant-scoped).

Purpose
-------
When a reviewer resolves an Active Learning item, we want the correction to take effect
immediately (without waiting for model retraining). This store maintains a tenant-local
mapping from source_column -> canonical_field, optionally scoped by workbook fingerprint.

Storage keys:
  ml/overrides/<tenant>/column_overrides.json

Env:
  ASGL_OVERRIDES_ENABLE=1
"""

from __future__ import annotations

import os, json, time
from typing import Any, Dict, List, Optional

from app.logging import get_logger
from app.storage import storage

log = get_logger(__name__)

def _safe(x: str) -> str:
    return str(x).replace(":","_").replace("/","_")

def _key(tenant_id: str) -> str:
    return f"ml/overrides/{_safe(tenant_id)}/column_overrides.json"

def enabled() -> bool:
    return os.getenv("ASGL_OVERRIDES_ENABLE", "1") not in ("0","false","FALSE","no","NO")

def _load_doc(tenant_id: str) -> Dict[str, Any]:
    try:
        b = storage.read(_key(tenant_id))
        if not b:
            return {"version": 1, "updated_ts": 0, "overrides": []}
        return json.loads(b.decode("utf-8"))
    except Exception:
        return {"version": 1, "updated_ts": 0, "overrides": []}

def _save_doc(tenant_id: str, doc: Dict[str, Any]) -> None:
    storage.write(_key(tenant_id), json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"))

def add_override(
    *,
    tenant_id: str,
    source_column: str,
    canonical_field: str,
    workbook: Optional[str] = None,
    sheet: Optional[str] = None,
) -> bool:
    if not enabled():
        return False
    try:
        doc = _load_doc(tenant_id)
        src = str(source_column).strip()
        cf = str(canonical_field).strip()
        scope = {"workbook": workbook, "sheet": sheet}
        new_overrides = []
        replaced = False
        for o in doc.get("overrides", []):
            if str(o.get("source_column","")) .strip().lower() == src.lower():
                sc = o.get("scope") or {}
                if (sc.get("workbook") or None) == (workbook or None) and (sc.get("sheet") or None) == (sheet or None):
                    new_overrides.append({"source_column": src, "canonical_field": cf, "scope": scope, "ts": int(time.time())})
                    replaced = True
                else:
                    new_overrides.append(o)
            else:
                new_overrides.append(o)
        if not replaced:
            new_overrides.append({"source_column": src, "canonical_field": cf, "scope": scope, "ts": int(time.time())})
        doc["overrides"] = new_overrides[-5000:]
        doc["updated_ts"] = int(time.time())
        _save_doc(tenant_id, doc)
        log.info("override_added", tenant_id=str(tenant_id), source_column=src, canonical_field=cf)
        return True
    except Exception as e:
        log.exception("override_add_failed", error=str(e))
        return False

def apply_overrides(
    *,
    tenant_id: str,
    headers: List[str],
    workbook: Optional[str] = None,
    sheet: Optional[str] = None,
) -> Dict[str, str]:
    if not enabled():
        return {}
    doc = _load_doc(tenant_id)
    hs = [str(h).strip() for h in (headers or [])]
    hset = {h.lower(): h for h in hs}
    out: Dict[str, str] = {}
    for o in doc.get("overrides", []):
        src = str(o.get("source_column","")) .strip()
        if not src:
            continue
        if src.lower() not in hset:
            continue
        sc = o.get("scope") or {}
        if sc.get("workbook") and (sc.get("workbook") != workbook):
            continue
        if sc.get("sheet") and (sc.get("sheet") != sheet):
            continue
        cf = str(o.get("canonical_field","")) .strip()
        if cf:
            out[cf] = hset[src.lower()]
    return out
