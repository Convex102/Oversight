from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.pipeline.pensions_pipeline import run_pensions_pack, TEMPLATE_STORE
from app.telemetry.client import track

logger = logging.getLogger("asgl.api.pensions")

router = APIRouter(prefix="/api/v1/pensions", tags=["pensions"])


class PensionsRunRequest(BaseModel):
    tenant_id: str = Field(..., description="Tenant/customer identifier")
    scheme_id: str = Field(..., description="Scheme identifier")
    template_name: str = "unknown"
    sheet_name: Optional[str] = None
    headers: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list, description="DI-mapped row dicts")
    meta: Dict[str, Any] = Field(default_factory=dict)


@router.post("/run")
def pensions_run(req: PensionsRunRequest):
    """
    Phase 7: API entrypoint to run ASGL-P pack end-to-end.
    Intended for UI runner to call after ingestion + DI mapping.
    """
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
def pensions_templates(tenant_id: str, limit: int = 100):
    """
    List known template fingerprints for the tenant (Phase 7).
    """
    try:
        return {"tenant_id": tenant_id, "templates": TEMPLATE_STORE.list(tenant_id, limit=limit), "count": TEMPLATE_STORE.count(tenant_id)}
    except Exception as e:
        logger.exception("pensions_templates_failed", extra={"tenant_id": tenant_id})
        raise HTTPException(status_code=500, detail="Template listing failed")
