from __future__ import annotations

from fastapi import APIRouter

from app.di.ml.health import get_status

router = APIRouter(prefix="/api/v1/di", tags=["di"])


@router.get("/ml/status")
def di_ml_status():
    """Returns whether DI ML embeddings are enabled/loaded and last error."""
    return get_status()
