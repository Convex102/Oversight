from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.db.models import ApiKey, Tenant
from app.security import hash_api_key


def _extract_api_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if settings.AUTH_ACCEPT_X_API_KEY and x_api_key:
        return x_api_key.strip()

    if settings.AUTH_ACCEPT_BEARER and authorization:
        a = authorization.strip()
        if a.lower().startswith("bearer "):
            return a.split(" ", 1)[1].strip()
    return None


def get_tenant(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> Tenant:
    """Resolve tenant from API key.

    Supported (configurable):
      - X-API-Key: <api_key>
      - Authorization: Bearer <api_key>
    """
    raw = _extract_api_key(x_api_key, authorization)
    if not raw:
        raise HTTPException(401, "Missing API key")

    kh = hash_api_key(raw)
    row = db.query(ApiKey).filter(ApiKey.key_hash == kh, ApiKey.is_active == True).first()
    if not row:
        raise HTTPException(401, "Invalid API key")

    row.last_used_at = dt.datetime.utcnow()
    db.commit()

    tenant = db.query(Tenant).filter(Tenant.id == row.tenant_id).first()
    if not tenant or not tenant.is_active:
        raise HTTPException(403, "Tenant inactive")
    return tenant
