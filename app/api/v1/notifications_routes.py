"""
Oversight ASGL v13.5 — Notifications API
==========================================
Routes:
  GET  /notifications              List notifications (with filters)
  POST /notifications/{id}/read   Mark one as read
  POST /notifications/read-all    Mark all as read
  POST /notifications             Create (internal use / admin)
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Notification, User
from app.web_auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationCreate(BaseModel):
    # tenant_id is never accepted from the client — derived from the
    # authenticated session. SECURITY-FIX 2026-10-15.
    type: str = Field("info", max_length=60)
    title: str = Field(..., max_length=300)
    message: str
    severity: str = Field("info", max_length=20)
    engagement_id: Optional[int] = None
    job_id: Optional[str] = None
    client_name: Optional[str] = None
    recipient_role: Optional[str] = None


def _notif_to_dict(n: Notification) -> dict:
    return {
        "id": n.id,
        "tenant_id": n.tenant_id,
        "type": n.type,
        "title": n.title,
        "message": n.message,
        "severity": n.severity,
        "engagement_id": n.engagement_id,
        "job_id": n.job_id,
        "client_name": n.client_name,
        "recipient_role": n.recipient_role,
        "is_read": n.is_read,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("", summary="List notifications")
async def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never accepted from the client.
    q = db.query(Notification).filter(Notification.tenant_id == current_user.tenant_id)
    if unread_only:
        q = q.filter(Notification.is_read == False)  # noqa: E712
    total = q.count()
    notifs = q.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()
    unread_count = (
        db.query(Notification)
        .filter(Notification.tenant_id == current_user.tenant_id, Notification.is_read == False)  # noqa: E712
        .count()
    )
    return JSONResponse({
        "notifications": [_notif_to_dict(n) for n in notifs],
        "total": total,
        "unread_count": unread_count,
        "limit": limit,
        "offset": offset,
    })


@router.post("/{notif_id}/read", summary="Mark notification as read")
async def mark_read(
    notif_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: scoped to the caller's tenant.
    n = db.query(Notification).filter(Notification.id == notif_id, Notification.tenant_id == current_user.tenant_id).first()
    if not n:
        raise HTTPException(404, f"Notification {notif_id} not found")
    if not n.is_read:
        n.is_read = True
        n.read_at = dt.datetime.utcnow()
        db.commit()
    return JSONResponse({"id": notif_id, "is_read": True})


@router.post("/read-all", summary="Mark all notifications as read")
async def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never accepted from the client.
    now = dt.datetime.utcnow()
    updated = (
        db.query(Notification)
        .filter(Notification.tenant_id == current_user.tenant_id, Notification.is_read == False)  # noqa: E712
        .all()
    )
    count = len(updated)
    for n in updated:
        n.is_read = True
        n.read_at = now
    db.commit()
    return JSONResponse({"marked_read": count})


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create notification")
async def create_notification(
    req: NotificationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never from the request body.
    n = Notification(
        tenant_id=current_user.tenant_id,
        type=req.type,
        title=req.title,
        message=req.message,
        severity=req.severity,
        engagement_id=req.engagement_id,
        job_id=req.job_id,
        client_name=req.client_name,
        recipient_role=req.recipient_role,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return JSONResponse(_notif_to_dict(n), status_code=201)
