"""Telemetry API (pilot metrics)."""

from __future__ import annotations

from typing import Any, Dict

try:
    from fastapi import APIRouter, Depends
except Exception:
    APIRouter = None
    Depends = None

from app.logging import get_logger
from app.auth import get_tenant_id as _get_tenant_id
from app.services.telemetry import summarize

log = get_logger(__name__)

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"]) if APIRouter else None

if router:
    @router.get("/summary")
    def get_summary(tenant_id: str = Depends(_get_tenant_id())) -> Dict[str, Any]:
        return summarize(tenant_id)
