from __future__ import annotations
from typing import Dict, Any, List, Optional
from pathlib import Path
import os, json, time, uuid

BASE = Path(os.getenv("ASGL_DI_REVIEW_STORE","data/di_review"))
BASE.mkdir(parents=True, exist_ok=True)

def _path(tenant_id:str)->Path:
    p = BASE / tenant_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "queue.json"

def enqueue(tenant_id:str, job_id:str, items: List[Dict[str,Any]]):
    p=_path(tenant_id)
    existing=[]
    if p.exists():
        existing=json.loads(p.read_text(encoding="utf-8"))
    ts=int(time.time())
    for it in items:
        it.setdefault("id", str(uuid.uuid4()))
        it.setdefault("status","open")
        it.setdefault("created_at", ts)
        it.setdefault("job_id", job_id)
        existing.append(it)
    p.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return {"ok": True, "count": len(items)}

def list_items(tenant_id:str, status:Optional[str]=None, limit:int=200)->List[Dict[str,Any]]:
    p=_path(tenant_id)
    if not p.exists(): return []
    items=json.loads(p.read_text(encoding="utf-8"))
    if status:
        items=[i for i in items if i.get("status")==status]
    return items[:limit]

def resolve(tenant_id:str, item_id:str, resolution:Dict[str,Any]):
    p=_path(tenant_id)
    if not p.exists(): return {"ok": False, "error":"no_queue"}
    items=json.loads(p.read_text(encoding="utf-8"))
    for it in items:
        if it.get("id")==item_id:
            it["status"]="resolved"
            it["resolution"]=resolution
            it["resolved_at"]=int(time.time())
            try:
                from app.di.review_queue_v5_patch import on_resolve_learning
                on_resolve_learning(tenant_id, it, resolution)
            except Exception:
                pass
            p.write_text(json.dumps(items, indent=2), encoding="utf-8")
            return {"ok": True}
    return {"ok": False, "error":"not_found"}