from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, Any, Optional

from app.auth.deps import get_principal
from app.auth.rbac import require
from app.di.active_learning import load_signals, load_priors

router = APIRouter(prefix="/api/v1/di-learning", tags=["di-learning"])

@router.get("/signals")
async def signals(limit: int = 500, principal=Depends(get_principal)):
    require(principal, "jobs:read")
    return {"ok": True, "signals": load_signals(principal.tenant_id, limit=limit)}

@router.get("/priors")
async def priors(principal=Depends(get_principal)):
    require(principal, "jobs:read")
    return {"ok": True, "priors": load_priors(principal.tenant_id)}