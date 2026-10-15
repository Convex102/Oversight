"""Decision Intelligence learning signals and priors for Oversight-A.

Mounted in app/main.py with group_guard("di"): login, Oversight-A licence and role are enforced there.
The company comes from the session or API key, never from the request.
"""
from fastapi import APIRouter, Depends

from app.di.active_learning import load_priors, load_signals
from app.rbac import Principal, get_principal

router = APIRouter(prefix="/api/v1/di-learning", tags=["di-learning"])


@router.get("/signals")
async def signals(limit: int = 500, p: Principal = Depends(get_principal)):
    return {"ok": True, "signals": load_signals(str(p.tenant_id), limit=max(1, min(limit, 5000)))}


@router.get("/priors")
async def priors(p: Principal = Depends(get_principal)):
    return {"ok": True, "priors": load_priors(str(p.tenant_id))}
