from __future__ import annotations

from fastapi import APIRouter
from app.pipeline.pack_router import list_packs

router = APIRouter(prefix="/api/v1/packs", tags=["packs"])

@router.get("")
def packs_list():
    return {"packs": list_packs()}
