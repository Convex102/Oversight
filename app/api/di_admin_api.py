"""Decision Intelligence admin routes (jobs, fact bundles) for Oversight-A. The review queue routes live in routes_addon.

Mounted in app/main.py with group_guard("di"): login, Oversight-A licence and role are enforced there.
The company comes from the session or API key, never from the request.
"""
import re

from fastapi import APIRouter, Depends, HTTPException

from app.di.persistence import BASE as _JOBS_BASE, list_jobs, load_factbundle
from app.rbac import Principal, get_principal

router = APIRouter(prefix="/api/v1/di", tags=["di-admin"])

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _tenant(p: Principal) -> str:
    return str(p.tenant_id)


@router.get("/jobs")
async def di_jobs(limit: int = 50, p: Principal = Depends(get_principal)):
    return {"ok": True, "jobs": list_jobs(_tenant(p), limit=max(1, min(limit, 500)))}


@router.get("/jobs/{job_id}/factbundle")
async def factbundle(job_id: str, p: Principal = Depends(get_principal)):
    if not _SAFE_ID.match(job_id):
        raise HTTPException(status_code=400, detail="Invalid job id")
    # Do not create directories on a read: only load when the job folder already exists.
    if not (_JOBS_BASE / _tenant(p) / "jobs" / job_id).is_dir():
        raise HTTPException(status_code=404, detail="factbundle not found")
    fb = load_factbundle(_tenant(p), job_id)
    if not fb:
        raise HTTPException(status_code=404, detail="factbundle not found")
    return {"ok": True, "factbundle": fb}
