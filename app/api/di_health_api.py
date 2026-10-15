from __future__ import annotations

from fastapi import APIRouter, Depends

from app.db.models import User
from app.di.ml.health import get_status
from app.web_auth import get_current_user

router = APIRouter(prefix="/api/v1/di", tags=["di"])


# SECURITY-FIX 2026-10-15: this route was public and returned the model name, device and the last
# raw error string (up to 4000 chars) to any caller. It now needs a session, same as commit 02df4bb.
@router.get("/ml/status")
def di_ml_status(current_user: User = Depends(get_current_user)):
    """Returns whether DI ML embeddings are enabled/loaded and last error."""
    return get_status()
