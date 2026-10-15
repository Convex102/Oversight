"""API endpoints for Active Learning Queue (Phase 2 v5)."""

from __future__ import annotations

from typing import Any, Dict

try:
    from fastapi import APIRouter, Depends, Body
except Exception:
    APIRouter = None

from app.services.active_learning import list_open_items, resolve_item

router = APIRouter(prefix="/api/active-learning", tags=["active-learning"]) if APIRouter else None

if router:
    # SECURITY-FIX 2026-10-15: tenant_id was previously taken from a client-set
    # X-Tenant-Id header (trivially spoofable) with no authentication at all.
    # Derived from the real session instead, same as every other router.
    from app.db.models import User
    from app.web_auth import get_current_user

    @router.get("/open")
    def get_open(limit: int = 50, current_user: User = Depends(get_current_user)):
        return {"items": list_open_items(str(current_user.tenant_id), limit=limit)}

    @router.post("/resolve")
    def post_resolve(payload: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user)):
        ok = resolve_item(
            tenant_id=str(current_user.tenant_id),
            item_id=str(payload.get("item_id")),
            chosen_canonical_field=str(payload.get("chosen_canonical_field")),
            note=payload.get("note"),
            reviewer=current_user.email,  # from the session, never from the request body
        )
        return {"ok": ok}
