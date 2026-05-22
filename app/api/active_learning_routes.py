"""API endpoints for Active Learning Queue (Phase 2 v5)."""

from __future__ import annotations

from typing import Any, Dict

try:
    from fastapi import APIRouter, Depends, Body
except Exception:
    APIRouter = None

from app.services.active_learning import list_open_items, resolve_item

router = APIRouter(prefix="/api/active-learning", tags=["active-learning"]) if APIRouter else None

def _get_tenant_id():
    from fastapi import Header
    def dep(x_tenant_id: str = Header("default")):
        return x_tenant_id
    return dep

if router:
    @router.get("/open")
    def get_open(limit: int = 50, tenant_id: str = Depends(_get_tenant_id())):
        return {"items": list_open_items(tenant_id, limit=limit)}

    @router.post("/resolve")
    def post_resolve(payload: Dict[str, Any] = Body(...), tenant_id: str = Depends(_get_tenant_id())):
        ok = resolve_item(
            tenant_id=tenant_id,
            item_id=str(payload.get("item_id")),
            chosen_canonical_field=str(payload.get("chosen_canonical_field")),
            note=payload.get("note"),
            reviewer=payload.get("reviewer"),
        )
        return {"ok": ok}
