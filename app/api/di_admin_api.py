from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, Optional, List

from app.auth.deps import get_principal
from app.auth.rbac import require
from app.di.persistence import list_jobs, load_factbundle
from app.di.review_queue import list_items, resolve

router = APIRouter(prefix="/api/v1/di", tags=["di-admin"])

@router.get("/jobs")
async def di_jobs(limit: int = 50, principal=Depends(get_principal)):
    require(principal, "jobs:read")
    return {"ok": True, "jobs": list_jobs(principal.tenant_id, limit=limit)}

@router.get("/jobs/{job_id}/factbundle")
async def factbundle(job_id: str, principal=Depends(get_principal)):
    require(principal, "jobs:read")
    fb = load_factbundle(principal.tenant_id, job_id)
    if not fb:
        raise HTTPException(status_code=404, detail="factbundle not found")
    return {"ok": True, "factbundle": fb}

@router.get("/review-queue")
async def review_queue(status: Optional[str] = None, limit: int = 200, principal=Depends(get_principal)):
    require(principal, "jobs:read")
    return {"ok": True, "items": list_items(principal.tenant_id, status=status, limit=limit)}

class ResolveReq(BaseModel):
    resolution: Dict[str,Any] = {}

@router.post("/review-queue/{item_id}/resolve")
async def resolve_item(item_id: str, req: ResolveReq, principal=Depends(get_principal)):
    require(principal, "jobs:write")
    return resolve(principal.tenant_id, item_id, req.resolution)