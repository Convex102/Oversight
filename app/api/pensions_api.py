from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from app.pipeline.pensions_pipeline import run_pensions_pack, TEMPLATE_STORE
from app.telemetry.client import track
from app.db.models import User
from app.web_auth import get_current_user

# SECURITY-FIX 2026-10-15: every route here ran with no session check and took tenant_id from the
# client, so any caller could run the pack or list templates for any tenant. Same fix as commit
# 02df4bb: require app.web_auth.get_current_user and derive the tenant from the session. Pensions
# stores key tenants by string, so the key is str(current_user.tenant_id).

logger = logging.getLogger("asgl.api.pensions")

router = APIRouter(prefix="/api/v1/pensions", tags=["pensions"])


class PensionsRunRequest(BaseModel):
    tenant_id: Optional[str] = Field(None, description="Ignored, the tenant comes from the session")
    scheme_id: str = Field(..., description="Scheme identifier")
    template_name: str = "unknown"
    sheet_name: Optional[str] = None
    headers: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list, description="DI-mapped row dicts")
    meta: Dict[str, Any] = Field(default_factory=dict)


@router.post("/run")
def pensions_run(req: PensionsRunRequest, current_user: User = Depends(get_current_user)):
    """
    Phase 7: API entrypoint to run ASGL-P pack end-to-end.
    Intended for UI runner to call after ingestion + DI mapping.
    """
    req.tenant_id = str(current_user.tenant_id)
    try:
        out = run_pensions_pack(
            tenant_id=req.tenant_id,
            scheme_id=req.scheme_id,
            rows=req.rows,
            meta=req.meta,
            template_name=req.template_name,
            sheet_name=req.sheet_name,
            headers=req.headers,
        )
        return out
    except Exception as e:
        logger.exception("pensions_run_failed", extra={"tenant_id": req.tenant_id, "scheme_id": req.scheme_id})
        track("api.pensions.run.error", tenant_id=req.tenant_id, pack_id="ASGL-P", scheme_id=req.scheme_id, meta={"error": str(e)})
        raise HTTPException(status_code=500, detail="Pensions run failed")


@router.get("/templates")
def pensions_templates(limit: int = 100, current_user: User = Depends(get_current_user)):
    """
    List known template fingerprints for the tenant (Phase 7).
    """
    tenant_id = str(current_user.tenant_id)
    try:
        return {"tenant_id": tenant_id, "templates": TEMPLATE_STORE.list(tenant_id, limit=limit), "count": TEMPLATE_STORE.count(tenant_id)}
    except Exception as e:
        logger.exception("pensions_templates_failed", extra={"tenant_id": tenant_id})
        raise HTTPException(status_code=500, detail="Template listing failed")


# ════════════════════════════════════════════════════════════════════════════
# Oversight-P batch endpoints (mass ingestion, anomaly detection, evidence pack)
# Wired via app/packs/pensions/pension_batch_task.py
# ════════════════════════════════════════════════════════════════════════════
try:
    from app.packs.pensions.pension_batch_task import pension_batch_router
    if pension_batch_router is not None:
        # The batch router has its own prefix `/api/v1/pensions` — mount with empty prefix
        # so its full path stays intact. The endpoints become siblings of /run and /templates.
        from fastapi import APIRouter
        # Re-export the router so main.py picks it up under the same module.
        # Endpoints: POST /api/v1/pensions/batch-upload, /batch-run-sync, /ingest-record
        #            GET  /api/v1/pensions/batch/{run_id}, /batch/{run_id}/pdf
        # Note: the batch_router itself has prefix=/api/v1/pensions so we attach it
        # to a no-prefix wrapper via app.include_router in main.py — that already happens
        # for `router` so we re-assign for clarity.
        _batch_endpoints_loaded = True
    else:
        _batch_endpoints_loaded = False
except Exception as _batch_err:
    logger.warning("Pension batch endpoints not loaded: %s", _batch_err)
    pension_batch_router = None
    _batch_endpoints_loaded = False
