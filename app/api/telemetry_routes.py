"""Telemetry API (pilot metrics)."""

from __future__ import annotations

from typing import Any, Dict

try:
    from fastapi import APIRouter, Depends
except Exception:
    APIRouter = None
    Depends = None

from app.logging import get_logger
from app.services.telemetry import summarize

log = get_logger(__name__)

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"]) if APIRouter else None

if router:
    # SECURITY-FIX 2026-10-15: previously imported app.auth.get_tenant_id, which
    # does not exist (app.auth only has password/session helpers) — this router
    # has been silently failing to mount since it was added. Fixed to use the
    # real session-derived tenant, same as every other router.
    from app.db.models import User
    from app.web_auth import get_current_user

    @router.get("/summary")
    def get_summary(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
        return summarize(str(current_user.tenant_id))
