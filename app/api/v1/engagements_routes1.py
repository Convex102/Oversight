"""
Oversight ASGL v13.5 Ã¢â‚¬â€ Engagements API
========================================
Full lifecycle: create Ã¢â€ â€™ submit (pipeline) Ã¢â€ â€™ review points Ã¢â€ â€™ corrections
             Ã¢â€ â€™ re-assess Ã¢â€ â€™ partner sign-off Ã¢â€ â€™ immutable audit trail

Routes (all under /api/v1/):
  POST   /engagements                          Create engagement
  GET    /engagements                          List engagements
  GET    /engagements/{id}                     Detail + review points + runs
  PUT    /engagements/{id}                     Update metadata
  POST   /engagements/{id}/submit              Submit files Ã¢â€ â€™ run pipeline Ã¢â€ â€™ raise points
  POST   /engagements/{id}/sign-off            Partner sign-off
  GET    /engagements/{id}/audit-trail         Immutable audit log
  POST   /review-points/{id}/acknowledge       Acknowledge a point
  POST   /review-points/{id}/correct           Submit correction (file or notes)
  POST   /review-points/{id}/waive             Waive a point with reason
  POST   /review-points/{id}/escalate          Escalate to partner
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import (
    Engagement, ReviewPoint, EngagementRun, AuditEvent, Notification
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["engagements"])

def _flywheel_capture_point(pt, outcome: str, actor: str) -> None:
    """Capture a confirmed review-point outcome into the flywheel dataset.
    
    Stores anonymised field-name mapping and outcome Ã¢â‚¬â€ no client data values.
    GDPR compliant: only field labels and correction outcome are stored.
    """
    try:
        from app.flywheel.engine import FlywheelEngine
        from app.flywheel.events import FlywheelEventType
        engine = FlywheelEngine(
            tenant_id=str(getattr(pt, "tenant_id", 1)),
            client_id=str(pt.engagement_id),
            run_id=str(pt.run_number or 0),
            vertical="accountancy",
        )
        engine.capture(
            type=FlywheelEventType.LABEL_CONFIRMED,
            actor=actor,
            confidence=0.95 if outcome == "corrected" else 0.80,
            payload={
                "field_name": pt.field_name or pt.title or "",
                "section": pt.section or "",
                "category": pt.category or "",
                "severity": pt.severity or "",
                "outcome": outcome,
                # Store delta magnitude (not raw values) for ML signal
                "delta_magnitude": abs(float(pt.delta or 0)),
            },
        )
        # Trigger retraining check
        from app.flywheel.trainer import TrainingScheduler
        scheduler = TrainingScheduler()
        ds_meta = engine.dataset.snapshot_meta(
            tenant_id=str(getattr(pt, "tenant_id", 1)),
            vertical="accountancy",
        )
        if ds_meta.get("rows", 0) >= 20 and ds_meta.get("rows", 0) % 20 == 0:
            scheduler.trigger(
                tenant_id=str(getattr(pt, "tenant_id", 1)),
                vertical="accountancy",
                model_id="risk_model",
                base_version="v1",
                reason=f"flywheel_threshold_{ds_meta['rows']}_rows",
            )
    except Exception as e:
        logger.debug("Flywheel capture failed (non-critical): %s", e)




# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# PYDANTIC MODELS
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

class EngagementCreate(BaseModel):
    client_name: str = Field(..., min_length=1, max_length=300)
    period: Optional[str] = Field(None, max_length=100)
    regulatory_pack: str = Field("frs102", max_length=80)
    preparer_name: Optional[str] = Field(None, max_length=200)
    engagement_ref: Optional[str] = Field(None, max_length=100)
    job_type: str = Field("comparison", max_length=50)
    tenant_id: int = Field(1, ge=1)


class EngagementUpdate(BaseModel):
    client_name: Optional[str] = Field(None, max_length=300)
    period: Optional[str] = Field(None, max_length=100)
    regulatory_pack: Optional[str] = Field(None, max_length=80)
    preparer_name: Optional[str] = Field(None, max_length=200)
    engagement_ref: Optional[str] = Field(None, max_length=100)
    status: Optional[str] = None


class SignOffRequest(BaseModel):
    partner_name: str = Field(..., min_length=1, max_length=200)
    notes: Optional[str] = None
    tenant_id: int = Field(1, ge=1)


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# HELPERS
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

def _engagement_to_dict(e: Engagement) -> Dict[str, Any]:
    return {
        "id": e.id,
        "tenant_id": e.tenant_id,
        "client_name": e.client_name,
        "period": e.period,
        "engagement_ref": e.engagement_ref,
        "regulatory_pack": e.regulatory_pack,
        "preparer_name": e.preparer_name,
        "job_type": e.job_type,
        "status": e.status,
        "open_points_count": e.open_points_count,
        "resolved_points_count": e.resolved_points_count,
        "run_count": e.run_count,
        "signed_off_by": e.signed_off_by,
        "signed_off_at": e.signed_off_at.isoformat() if e.signed_off_at else None,
        "sign_off_notes": e.sign_off_notes,
        "latest_run_id": e.latest_run_id,
        "latest_run_summary": e.latest_run_summary,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


def _point_to_dict(p: ReviewPoint) -> Dict[str, Any]:
    return {
        "id": p.id,
        "engagement_id": p.engagement_id,
        "run_number": p.run_number,
        "severity": p.severity,
        "category": p.category,
        "title": p.title,
        "description": p.description,
        "audit_ref": p.audit_ref,
        "section": p.section,
        "field_name": p.field_name,
        "value_a": p.value_a,
        "value_b": p.value_b,
        "delta": p.delta,
        "status": p.status,
        "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
        "resolved_by": p.resolved_by,
        "resolution_notes": p.resolution_notes,
        "waiver_reason": p.waiver_reason,
        "escalated_to": p.escalated_to,
        "escalation_note": p.escalation_note,
        "correction_notes": p.correction_notes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _run_to_dict(r: EngagementRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "engagement_id": r.engagement_id,
        "run_number": r.run_number,
        "job_id": r.job_id,
        "status": r.status,
        "review_points_raised": r.review_points_raised,
        "llm_narrative": r.llm_narrative,
        "file_a_name": r.file_a_name,
        "file_b_name": r.file_b_name,
        "label_a": r.label_a,
        "label_b": r.label_b,
        "pipeline_result": r.pipeline_result,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _emit_notification(
    db: Session,
    tenant_id: int,
    ntype: str,
    title: str,
    message: str,
    severity: str = "info",
    engagement_id: Optional[int] = None,
    client_name: Optional[str] = None,
) -> None:
    try:
        n = Notification(
            tenant_id=tenant_id,
            type=ntype,
            title=title,
            message=message,
            severity=severity,
            engagement_id=engagement_id,
            client_name=client_name,
        )
        db.add(n)
        db.flush()
    except Exception as exc:
        logger.warning("Failed to emit notification: %s", exc)


def _audit(
    db: Session,
    engagement_id: int,
    action: str,
    actor: str = "system",
    detail: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        ev = AuditEvent(
            engagement_id=engagement_id,
            action=action,
            actor=actor,
            detail=detail,
            payload=payload,
        )
        db.add(ev)
        db.flush()
    except Exception as exc:
        logger.warning("Failed to write audit event: %s", exc)


def _recalc_point_counts(db: Session, engagement_id: int) -> None:
    pts = db.query(ReviewPoint).filter(ReviewPoint.engagement_id == engagement_id).all()
    open_c = sum(1 for p in pts if p.status in ("open", "acknowledged", "in_progress", "escalated"))
    res_c = sum(1 for p in pts if p.status in ("resolved", "corrected", "waived"))
    eng = db.query(Engagement).filter(Engagement.id == engagement_id).first()
    if eng:
        eng.open_points_count = open_c
        eng.resolved_points_count = res_c
        db.flush()


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# ENGAGEMENT CRUD
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

@router.get("/engagements", summary="List engagements for a tenant")
async def list_engagements(
    tenant_id: int = 1,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> JSONResponse:
    q = db.query(Engagement).filter(Engagement.tenant_id == tenant_id)
    if status:
        q = q.filter(Engagement.status == status)
    total = q.count()
    engs = q.order_by(Engagement.created_at.desc()).offset(offset).limit(limit).all()
    return JSONResponse({
        "engagements": [_engagement_to_dict(e) for e in engs],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@router.post("/engagements", status_code=status.HTTP_201_CREATED, summary="Create engagement")
async def create_engagement(
    req: EngagementCreate,
    db: Session = Depends(get_db),
) -> JSONResponse:
    eng = Engagement(
        tenant_id=req.tenant_id,
        client_name=req.client_name.strip(),
        period=req.period.strip(),
        regulatory_pack=req.regulatory_pack,
        preparer_name=(req.preparer_name or "").strip() or None,
        engagement_ref=(req.engagement_ref or "").strip() or None,
        job_type=req.job_type,
        status="draft",
    )
    db.add(eng)
    db.flush()
    _audit(db, eng.id, "created", actor="user",
           detail=f"Engagement created for {eng.client_name} / {eng.period}")
    _emit_notification(db, req.tenant_id, "info", "Engagement Created",
                       f"{eng.client_name} Ã¢â‚¬â€ {eng.period} engagement created.",
                       engagement_id=eng.id, client_name=eng.client_name)
    db.commit()
    db.refresh(eng)
    return JSONResponse(_engagement_to_dict(eng), status_code=201)


@router.get("/engagements/{eng_id}", summary="Get engagement detail")
async def get_engagement(
    eng_id: int,
    db: Session = Depends(get_db),
) -> JSONResponse:
    eng = db.query(Engagement).filter(Engagement.id == eng_id).first()
    if not eng:
        raise HTTPException(404, f"Engagement {eng_id} not found")

    points = (
        db.query(ReviewPoint)
        .filter(ReviewPoint.engagement_id == eng_id)
        .order_by(ReviewPoint.created_at.desc())
        .all()
    )
    runs = (
        db.query(EngagementRun)
        .filter(EngagementRun.engagement_id == eng_id)
        .order_by(EngagementRun.run_number)
        .all()
    )

    statuses = [p.status for p in points]
    summary = {
        "open":         statuses.count("open"),
        "acknowledged": statuses.count("acknowledged"),
        "in_progress":  statuses.count("in_progress"),
        "corrected":    statuses.count("corrected"),
        "waived":       statuses.count("waived"),
        "resolved":     statuses.count("resolved"),
        "escalated":    statuses.count("escalated"),
    }

    return JSONResponse({
        "engagement": _engagement_to_dict(eng),
        "review_points": [_point_to_dict(p) for p in points],
        "runs": [_run_to_dict(r) for r in runs],
        "summary": summary,
    })


@router.put("/engagements/{eng_id}", summary="Update engagement metadata")
async def update_engagement(
    eng_id: int,
    req: EngagementUpdate,
    db: Session = Depends(get_db),
) -> JSONResponse:
    eng = db.query(Engagement).filter(Engagement.id == eng_id).first()
    if not eng:
        raise HTTPException(404, f"Engagement {eng_id} not found")
    if eng.status == "signed_off":
        raise HTTPException(409, "Signed-off engagements cannot be modified")

    if req.client_name is not None:
        eng.client_name = req.client_name.strip()
    if req.period is not None:
        eng.period = req.period.strip()
    if req.regulatory_pack is not None:
        eng.regulatory_pack = req.regulatory_pack
    if req.preparer_name is not None:
        eng.preparer_name = req.preparer_name.strip() or None
    if req.engagement_ref is not None:
        eng.engagement_ref = req.engagement_ref.strip() or None
    if req.status is not None:
        eng.status = req.status

    _audit(db, eng.id, "updated", actor="user", detail="Engagement metadata updated")
    db.commit()
    db.refresh(eng)
    return JSONResponse(_engagement_to_dict(eng))


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# SUBMIT Ã¢â‚¬â€ run pipeline, generate review points
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

@router.post("/engagements/{eng_id}/submit", summary="Submit files for AI analysis")
async def submit_engagement(
    eng_id: int,
    files: List[UploadFile] = File(default=[]),
    # Legacy 2-file params kept for backwards compatibility
    file_a: Optional[UploadFile] = File(default=None),
    file_b: Optional[UploadFile] = File(default=None),
    label_a: str = Form("Working Papers"),
    label_b: str = Form("Final Accounts"),
    preparer_name: str = Form("Preparer"),
    materiality_pct: float = Form(5.0),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Submit N documents for AI analysis.

    Accepts either:
    - `files[]` Ã¢â‚¬â€ multiple files (new multi-document interface)
    - `file_a` + `file_b` Ã¢â‚¬â€ two files (legacy interface, still supported)

    Documents are classified, fields extracted, cross-document reconciliation
    rules run, and a structured set of review points returned.
    """
    eng = db.query(Engagement).filter(Engagement.id == eng_id).first()
    if not eng:
        raise HTTPException(404, f"Engagement {eng_id} not found")
    if eng.status == "signed_off":
        raise HTTPException(409, "Signed-off engagement cannot be resubmitted")

    # Normalise to list of (filename, bytes) Ã¢â‚¬â€ merge both interfaces
    all_files: List[tuple] = []
    if files:
        for f in files:
            data = await f.read()
            if data:
                all_files.append((f.filename or f"document_{len(all_files)+1}", data))
    if not all_files and file_a:
        data_a = await file_a.read()
        if data_a:
            all_files.append((file_a.filename or "document_a", data_a))
        if file_b:
            data_b = await file_b.read()
            if data_b:
                all_files.append((file_b.filename or "document_b", data_b))

    if not all_files:
        raise HTTPException(400, "At least one file is required")

    eng.run_count += 1
    eng.status = "submitted"
    if preparer_name:
        eng.preparer_name = preparer_name

    filenames = [f[0] for f in all_files]
    run = EngagementRun(
        engagement_id=eng_id,
        run_number=eng.run_count,
        status="running",
        file_a_name=filenames[0] if filenames else None,
        file_b_name=filenames[1] if len(filenames) > 1 else None,
        label_a=label_a,
        label_b=label_b,
    )
    db.add(run)
    db.flush()
    run_id = run.id

    _audit(db, eng_id, "submitted", actor=preparer_name,
           detail=f"Run #{eng.run_count} submitted: {len(all_files)} document(s): {', '.join(filenames[:5])}")
    db.commit()

    # Run pipeline
    review_points, narrative, pipeline_result = _run_pipeline_and_raise_points_multi(
        eng_id=eng_id,
        run_id=run_id,
        files=all_files,
        label_a=label_a,
        label_b=label_b,
        materiality_pct=materiality_pct,
        run_number=eng.run_count,
    )

    # Persist review points and update run
    db2 = db
    run_obj = db2.query(EngagementRun).filter(EngagementRun.id == run_id).first()
    if run_obj:
        run_obj.status = "completed"
        run_obj.review_points_raised = len(review_points)
        run_obj.llm_narrative = narrative
        run_obj.pipeline_result = pipeline_result
        db2.flush()

    # Delete previous OPEN/ACKNOWLEDGED/IN_PROGRESS points from this engagement
    # before saving the new run's points. Human decisions (corrected/waived/resolved)
    # are preserved â€” they represent reviewer actions and must survive re-runs.
    _REPLACEABLE_STATUSES = ('open', 'acknowledged', 'in_progress')
    old_open = (
        db2.query(ReviewPoint)
        .filter(
            ReviewPoint.engagement_id == eng_id,
            ReviewPoint.status.in_(_REPLACEABLE_STATUSES),
        )
        .all()
    )
    for old_pt in old_open:
        db2.delete(old_pt)
    db2.flush()
    if old_open:
        logger.info(
            "Deleted %d open review points from previous run before saving new points",
            len(old_open),
        )

    _RP_COLS = {
        'severity', 'category', 'title', 'description', 'audit_ref',
        'section', 'field_name', 'value_a', 'value_b', 'delta', 'status',
        'correction_notes', 'waiver_reason', 'escalation_note',
    }
    for pt_data in review_points:
        desc = str(pt_data.get("description", ""))
        reg_ref = pt_data.get("regulation_ref") or pt_data.get("regulation_refs")
        suggestion = pt_data.get("suggested_action") or pt_data.get("recommended_action")
        doc_srcs = pt_data.get("document_sources", [])
        if reg_ref and str(reg_ref) not in desc:
            desc = desc + "\n\nðŸ“– " + str(reg_ref)
        if suggestion and str(suggestion) not in desc:
            desc = desc + "\n\nâ†’ " + str(suggestion)
        if doc_srcs and isinstance(doc_srcs, list):
            desc = desc + "\n\nðŸ“„ Documents: " + " â†” ".join(doc_srcs)
        clean = {k: v for k, v in pt_data.items() if k in _RP_COLS}
        clean['description'] = desc.strip()
        clean.setdefault('severity', 'medium')
        clean.setdefault('category', 'difference')
        clean.setdefault('status', 'open')
        pt = ReviewPoint(
            engagement_id=eng_id,
            run_number=eng.run_count,
            **clean,
        )
        db2.add(pt)

    eng2 = db2.query(Engagement).filter(Engagement.id == eng_id).first()
    if eng2:
        eng2.status = "ai_review"
        eng2.latest_run_id = str(run_id)
        eng2.latest_run_summary = {
            "run_number": eng.run_count,
            "points_raised": len(review_points),
            "narrative": (narrative or "")[:300],
        }
        _recalc_point_counts(db2, eng_id)

    _audit(db2, eng_id, "analysis_complete", actor="system",
           detail=f"{len(review_points)} review points raised")
    _emit_notification(
        db2, eng2.tenant_id if eng2 else 1,
        "review_point_raised" if review_points else "job_complete",
        f"Analysis Complete Ã¢â‚¬â€ {eng2.client_name if eng2 else ''}",
        f"{len(review_points)} review point(s) raised for {label_a} vs {label_b}.",
        severity="warning" if any(p.get("severity") == "high" for p in review_points) else "info",
        engagement_id=eng_id,
        client_name=eng2.client_name if eng2 else None,
    )
    db2.commit()

    return JSONResponse({
        "engagement_id": eng_id,
        "run_number": eng.run_count,
        "status": "ai_review",
        "review_points": [_point_to_dict(p) for p in
                          db2.query(ReviewPoint)
                          .filter(ReviewPoint.engagement_id == eng_id,
                                  ReviewPoint.run_number == eng.run_count)
                          .all()],
        "narrative": narrative,
    })


def _run_pipeline_and_raise_points_multi(
    eng_id: int,
    run_id: int,
    files: List[tuple],  # List of (filename, bytes)
    label_a: str,
    label_b: str,
    materiality_pct: float,
    run_number: int,
) -> tuple[List[Dict], Optional[str], Dict]:
    """Multi-document pipeline.

    Stage 1: Classify all N documents
    Stage 2: Route pairs for comparison (WP vs accounts, CT600 vs computation, etc.)
    Stage 3: Run cross-document reconciliation rules across all extracted fields
    Stage 4: Merge all review points, deduplicate, rank by severity
    """
    from app.services.document_classifier import classify_document

    review_points: List[Dict] = []
    narrative: Optional[str] = None
    all_classifications = []
    recon_fields: Dict = {}
    pipeline_result: Dict = {"files": len(files), "status": "ok"}

    try:
        with tempfile.TemporaryDirectory(prefix=f"oversight_eng{eng_id}_multi_") as tmp:
            tmp_path = Path(tmp)

            # Ã¢â€â‚¬Ã¢â€â‚¬ Stage 1: Classify all documents Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            classified = []
            for filename, data in files:
                try:
                    cls = classify_document(filename, data)
                    classified.append((filename, data, cls))
                    logger.info(
                        "Classified: %s Ã¢â€ â€™ %s (%.0f%%)",
                        filename, cls.label, cls.confidence * 100
                    )
                    # Extract values into recon_fields
                    cfg = cls.extraction_config or {}
                    if cls.doc_type == "ct600":
                        for k, v in (cfg.get("boxes") or {}).items():
                            if v is not None: recon_fields[f"ct600.{k}"] = v
                    elif cls.doc_type == "ct_computation":
                        for k, v in (cfg.get("values") or {}).items():
                            if v is not None: recon_fields[f"ct_computation.{k}"] = v
                except Exception as e:
                    logger.warning("Classification failed for %s: %s", filename, e)
                    classified.append((filename, data, None))

            all_classifications = [c for _, _, c in classified]
            pipeline_result["classifications"] = [
                {"filename": fn, "doc_type": c.doc_type if c else "unknown",
                 "label": c.label if c else "Unknown", "confidence": c.confidence if c else 0.0}
                for fn, _, c in classified
            ]

            # Ã¢â€â‚¬Ã¢â€â‚¬ Stage 2: Smart comparison pairing Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            # Group by doc_type, then compare logical pairs
            by_type = {}
            for fn, data, cls in classified:
                dt = cls.doc_type if cls else "unknown"
                by_type.setdefault(dt, []).append((fn, data, cls))

            # Priority: find best Excel source and best PDF target
            # Document types excluded from comparison (used for reconciliation only)
            NON_COMPARABLE = {
                "audit_trail", "nominal_ledger", "ct600", "ct_computation",
                "vat_return", "payroll_summary",
            }

            # Best Excel source: WP > Sage TB > software TB > Xero TB
            excel_source = None
            for preferred in ("excel_working_papers", "sage_trial_balance",
                              "software_trial_balance", "xero_trial_balance"):
                if preferred in by_type:
                    excel_source = by_type[preferred][0]
                    break

            # Best PDF target: statutory accounts first
            pdf_target = None
            for preferred in ("statutory_accounts",):
                if preferred in by_type:
                    pdf_target = by_type[preferred][0]
                    break
            # Fallback: any PDF not in NON_COMPARABLE
            if pdf_target is None:
                for fn, data, cls in classified:
                    if fn.lower().endswith(".pdf") and (cls.doc_type if cls else "") not in NON_COMPARABLE:
                        pdf_target = (fn, data, cls)
                        break

            pairs_run = set()

            def _sanitise_fn(fn):
                import re as _re
                return _re.sub(r'[/\:*?"<>|]', '_', str(fn))[:200]

            def _run_pair(fn_a, data_a, fn_b, data_b, la, lb):
                pair_key = f"{fn_a}|{fn_b}"
                if pair_key in pairs_run:
                    return []
                pairs_run.add(pair_key)
                p_a = tmp_path / _sanitise_fn(fn_a)
                p_b = tmp_path / ("b_" + _sanitise_fn(fn_b))
                p_a.write_bytes(data_a)
                p_b.write_bytes(data_b)
                logger.info("Comparing: %s vs %s", fn_a, fn_b)
                pts, result = _compare_and_raise(p_a, p_b, la, lb, materiality_pct, tmp_path)
                for anomaly in result.get("anomalies", []):
                    d = anomaly.get("data", {})
                    field = d.get("field", "")
                    if field:
                        f_key = field.lower().replace(" ", "_").replace("/", "_")
                        if d.get("value_a") is not None:
                            try: recon_fields[f"working_papers.{f_key}"] = float(d["value_a"])
                            except (TypeError, ValueError): pass
                        if d.get("value_b") is not None:
                            try: recon_fields[f"accounts.{f_key}"] = float(d["value_b"])
                            except (TypeError, ValueError): pass
                return pts

            if excel_source and pdf_target:
                fn_a, data_a, cls_a = excel_source
                fn_b, data_b, cls_b = pdf_target
                la = cls_a.label if cls_a else label_a
                lb = cls_b.label if cls_b else label_b
                logger.info("Primary pair: [%s] %s vs [%s] %s",
                    cls_a.doc_type if cls_a else "?", fn_a,
                    cls_b.doc_type if cls_b else "?", fn_b)
                review_points.extend(_run_pair(fn_a, data_a, fn_b, data_b, la, lb))

                # Also populate recon_fields directly from extracted KV pairs
                # so regulatory rules have correct values even if comparison finds no anomalies
                try:
                    import tempfile as _tmp, pathlib as _pl
                    with _tmp.TemporaryDirectory() as _td:
                        from asgla_compare.ingest.excel_ingest import extract_excel_kv
                        from asgla_compare.ingest.pdf_ingest import extract_pdf_kv
                        _pa = _pl.Path(_td) / "wp_kv.xlsx"
                        _pb = _pl.Path(_td) / "fs_kv.pdf"
                        _pa.write_bytes(data_a)
                        _pb.write_bytes(data_b)
                        _ex = extract_excel_kv(str(_pa))
                        _pdf = extract_pdf_kv(str(_pb))
                        # Map FS PDF values to accounts.* namespace
                        _FS_MAP = {
                            "turnover": "accounts.turnover",
                            "gross profit": "accounts.gross_profit",
                            "operating profit": "accounts.operating_profit",
                            "profit before taxation": "accounts.profit_before_tax",
                            "loss before taxation": "accounts.loss_before_tax",
                            "profit after taxation": "accounts.profit_after_tax",
                            "loss after taxation": "accounts.loss_after_tax",
                            "net assets": "accounts.net_assets",
                            "shareholders funds": "accounts.net_assets",
                            "retained earnings": "accounts.retained_earnings",
                            "profit and loss account": "accounts.retained_earnings",
                            "tangible assets": "accounts.fixed_assets",
                            "fixed assets": "accounts.fixed_assets",
                            "debtors": "accounts.debtors",
                            "cash at bank and in hand": "accounts.cash",
                            "cash at bank": "accounts.cash",
                            "creditors amounts falling due within one year": "accounts.current_liabilities",
                            "net current assets": "accounts.net_current_assets",
                            "net current liabilities": "accounts.net_current_liabilities",
                            "corporation tax": "accounts.corporation_tax",
                            "deferred taxation": "accounts.deferred_tax",
                            "dividends": "accounts.dividends",
                        }
                        for pdf_key, pdf_val in _pdf.items.items():
                            for match_key, field_name in _FS_MAP.items():
                                if match_key in pdf_key and field_name not in recon_fields:
                                    recon_fields[field_name] = pdf_val
                                    break
                        # Map WP Excel totals to working_papers.* namespace
                        for ex_key, ex_val in _ex.items.items():
                            if ex_key not in recon_fields:
                                recon_fields[f"working_papers.{ex_key}"] = ex_val
                        logger.info(
                            "Recon fields populated: %d total (%d from FS, %d from WP)",
                            len(recon_fields),
                            sum(1 for k in recon_fields if k.startswith("accounts.")),
                            sum(1 for k in recon_fields if k.startswith("working_papers.")),
                        )
                except Exception as _e:
                    logger.warning("Recon field population failed (non-critical): %s", _e)
                excl = [fn for fn, _, c in classified if (c.doc_type if c else "unknown") in NON_COMPARABLE]
                if excl: logger.info("Excluded (reconciliation only): %s", excl)
            else:
                logger.warning("No valid pair found. types=%s", list(by_type.keys()))

            try:
                from app.packs.accountancy.reconciliation_rules import (
                    run_reconciliation_rules, results_to_review_points
                )
                recon_results = run_reconciliation_rules(recon_fields, materiality_pct=materiality_pct)
                recon_points = results_to_review_points(recon_results, include_pass=False)
                if recon_points:
                    logger.info("Reconciliation rules: %d findings", len(recon_points))
                    review_points.extend(recon_points)
                pipeline_result["reconciliation"] = {
                    "rules_run": len(recon_results),
                    "findings": len(recon_points),
                    "field_count": len(recon_fields),
                }
            except Exception as e:
                logger.warning("Reconciliation rules failed: %s", e)

            # â”€â”€ Stage 3b: Analytical review rules â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # These detect Kate/Paul-style review points:
            # Dividends vs reserves, payroll inconsistency, negative cash,
            # DTA consideration, depreciation timing, aged creditors, WP completion
            try:
                from app.packs.accountancy.analytical_rules import (
                    run_analytical_review, findings_to_review_points
                )
                # Inject TB nominal codes from classified documents
                for fn, data, cls in classified:
                    if cls and cls.doc_type in ("software_trial_balance", "sage_trial_balance",
                                                 "excel_working_papers"):
                        ec = cls.extraction_config or {}
                        sheets = ec.get("sheets", [])
                        if sheets:
                            recon_fields["wp.sheets"] = sheets

                analytical_findings = run_analytical_review(recon_fields, materiality_pct=materiality_pct)
                analytical_points = findings_to_review_points(analytical_findings)
                if analytical_points:
                    logger.info("Analytical review: %d findings", len(analytical_points))
                    review_points.extend(analytical_points)
                pipeline_result["analytical_review"] = {
                    "rules_run": 9,
                    "findings": len(analytical_points),
                }
            except Exception as e:
                logger.warning("Analytical review failed (non-critical): %s", e)

            # â”€â”€ Stage 3c: FRS 102 Regulatory Pack â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # 19 rules across FRS102, CA2006, CTA2010, CAA2001, ITEPA2003
            # Works across TB + WP + FS + CT simultaneously
            # Fires on ANY engagement where relevant fields are present
            try:
                from app.packs.accountancy.regulatory_pack_frs102 import (
                    run_frs102_pack, frs102_findings_to_review_points
                )
                frs102_findings = run_frs102_pack(recon_fields, materiality_pct=materiality_pct)
                frs102_points = frs102_findings_to_review_points(frs102_findings)
                if frs102_points:
                    logger.info("FRS102 pack: %d findings", len(frs102_points))
                    review_points.extend(frs102_points)
                pipeline_result["frs102_pack"] = {
                    "rules_run": 19,
                    "findings": len(frs102_points),
                }
            except Exception as e:
                logger.warning("FRS102 pack failed (non-critical): %s", e)

            pipeline_result["comparison_pairs"] = len(pairs_run)
            pipeline_result["total_review_points"] = len(review_points)
            pipeline_result["classification"] = {
                "documents": pipeline_result["classifications"]
            }

    except Exception as exc:
        logger.exception("Multi-file pipeline failed for engagement %s", eng_id)
        pipeline_result = {"error": str(exc), "status": "failed", "files": len(files)}

    return review_points, narrative, pipeline_result


def _run_pipeline_and_raise_points(
    eng_id: int,
    run_id: int,
    data_a: bytes,
    filename_a: str,
    data_b: Optional[bytes],
    filename_b: Optional[str],
    label_a: str,
    label_b: str,
    materiality_pct: float,
    run_number: int,
) -> tuple[List[Dict], Optional[str], Dict]:
    """Run the full pipeline:
    Stage 1 Ã¢â‚¬â€ Classify each document (Sage TB, CT600, working papers, etc.)
    Stage 2 Ã¢â‚¬â€ Extract named fields from each document type
    Stage 3 Ã¢â‚¬â€ Run cross-document reconciliation rules (CT vs CT600, etc.)
    Stage 4 Ã¢â‚¬â€ Run field-level comparison (existing compare engine)
    Merge all findings into review points.
    """
    review_points: List[Dict] = []
    narrative: Optional[str] = None
    pipeline_result: Dict = {}

    try:
        with tempfile.TemporaryDirectory(prefix=f"oversight_eng{eng_id}_") as tmp:
            tmp_path = Path(tmp)
            path_a = tmp_path / filename_a
            path_a.write_bytes(data_a)

            path_b = None
            if data_b and filename_b:
                path_b = tmp_path / filename_b
                path_b.write_bytes(data_b)

            # Ã¢â€â‚¬Ã¢â€â‚¬ Stage 1: Classify documents Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            classification_a = None
            classification_b = None
            try:
                from app.services.document_classifier import classify_document
                classification_a = classify_document(filename_a, data_a)
                logger.info(
                    "Document A classified: %s (%.0f%% confidence) Ã¢â‚¬â€ signals: %s",
                    classification_a.label,
                    classification_a.confidence * 100,
                    classification_a.signals[:2],
                )
                if data_b and filename_b:
                    classification_b = classify_document(filename_b, data_b)
                    logger.info(
                        "Document B classified: %s (%.0f%% confidence) Ã¢â‚¬â€ signals: %s",
                        classification_b.label,
                        classification_b.confidence * 100,
                        classification_b.signals[:2],
                    )
            except Exception as e:
                logger.warning("Document classification failed (non-critical): %s", e)

            # Ã¢â€â‚¬Ã¢â€â‚¬ Stage 2 + Stage 4: Field-level comparison Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            if path_b is not None:
                compare_points, pipeline_result = _compare_and_raise(
                    path_a, path_b, label_a, label_b,
                    materiality_pct, tmp_path
                )
                review_points.extend(compare_points)
            else:
                single_points, pipeline_result = _pipeline_and_raise(
                    path_a, filename_a, tmp_path
                )
                review_points.extend(single_points)

            # Ã¢â€â‚¬Ã¢â€â‚¬ Stage 3: Cross-document reconciliation rules Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            try:
                from app.packs.accountancy.reconciliation_rules import (
                    run_reconciliation_rules, results_to_review_points
                )
                # Build fields dict from compare result + classification metadata
                recon_fields: Dict = {}
                # Inject extracted values from classified documents
                def _inject_doc(cls):
                    if cls is None:
                        return
                    cfg = cls.extraction_config or {}
                    doc_type = cls.doc_type
                    if doc_type == "ct600":
                        for k, v in (cfg.get("boxes") or {}).items():
                            if v is not None:
                                recon_fields[f"ct600.{k}"] = v
                    elif doc_type == "ct_computation":
                        for k, v in (cfg.get("values") or {}).items():
                            if v is not None:
                                recon_fields[f"ct_computation.{k}"] = v
                    elif doc_type in ("software_trial_balance", "sage_trial_balance"):
                        recon_fields["doc.trial_balance"] = True
                    elif doc_type == "statutory_accounts":
                        recon_fields["doc.statutory_accounts"] = True
                    elif doc_type == "nominal_ledger":
                        recon_fields["doc.nominal_ledger"] = True
                    elif doc_type == "audit_trail":
                        recon_fields["doc.audit_trail"] = True

                _inject_doc(classification_a)
                _inject_doc(classification_b)
                # Pull values from compare result anomalies
                for anomaly in pipeline_result.get("anomalies", []):
                    data = anomaly.get("data", {})
                    field = data.get("field", "")
                    if field and data.get("value_a") is not None:
                        try:
                            recon_fields[f"accounts.{field.lower().replace(' ', '_')}"] = float(data["value_a"])
                        except (TypeError, ValueError):
                            pass
                    if field and data.get("value_b") is not None:
                        try:
                            recon_fields[f"working_papers.{field.lower().replace(' ', '_')}"] = float(data["value_b"])
                        except (TypeError, ValueError):
                            pass

                recon_results = run_reconciliation_rules(recon_fields, materiality_pct=materiality_pct)
                recon_points = results_to_review_points(recon_results, include_pass=False)
                if recon_points:
                    logger.info("Reconciliation rules produced %d findings", len(recon_points))
                    review_points.extend(recon_points)
            except Exception as e:
                logger.warning("Reconciliation rules failed (non-critical): %s", e)

            # Ã¢â€â‚¬Ã¢â€â‚¬ Attach classification metadata to pipeline result Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
            pipeline_result["classification"] = {
                "document_a": {
                    "doc_type": classification_a.doc_type if classification_a else "unknown",
                    "label": classification_a.label if classification_a else label_a,
                    "confidence": classification_a.confidence if classification_a else 0.0,
                    "signals": classification_a.signals[:3] if classification_a else [],
                } if classification_a else None,
                "document_b": {
                    "doc_type": classification_b.doc_type if classification_b else "unknown",
                    "label": classification_b.label if classification_b else label_b,
                    "confidence": classification_b.confidence if classification_b else 0.0,
                    "signals": classification_b.signals[:3] if classification_b else [],
                } if classification_b else None,
            }

    except Exception as exc:
        logger.exception("Pipeline/compare failed for engagement %s", eng_id)
        pipeline_result = {"error": str(exc), "status": "failed"}

    return review_points, narrative, pipeline_result


def _compare_and_raise(
    path_a: Path,
    path_b: Path,
    label_a: str,
    label_b: str,
    materiality_pct: float,
    tmp_path: Path,
) -> tuple[List[Dict], Dict]:
    """Compare two documents using the real asgla_compare pipeline.

    Uses extract_excel_kv / extract_pdf_kv for field extraction, then
    compare_kv (RapidFuzz + CrossEncoder reranking) for semantic matching.
    Falls back to _compare_sheets only if the asgla_compare pipeline
    cannot extract any key-value pairs (e.g. scanned image PDFs).

    For post-review clearance: values agree -> MATCH, not MISMATCH.
    Structural sections present in one doc only -> MISSING_, not MISMATCH.
    Zero MISMATCH rows expected on a clean job set.
    """
    import math

    ext_a = path_a.suffix.lower()
    ext_b = path_b.suffix.lower()

    # â”€â”€ PRIMARY: asgla_compare pipeline (real semantic extraction) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        from asgla_compare.ingest.excel_ingest import extract_excel_kv
        from asgla_compare.ingest.pdf_ingest import extract_pdf_kv
        from asgla_compare.compare.diff_engine import compare_kv

        if not path_a.exists():
            raise FileNotFoundError(f"path_a missing: {path_a}")
        if not path_b.exists():
            raise FileNotFoundError(f"path_b missing: {path_b}")

        # Extract key-value pairs
        if ext_a in (".xlsx", ".xlsm", ".xls", ".csv"):
            result_a = extract_excel_kv(str(path_a), logger=logger)
            items_a = result_a.items
        elif ext_a == ".pdf":
            result_a = extract_pdf_kv(str(path_a), logger=logger)
            items_a = result_a.items
        else:
            items_a = {}

        if ext_b == ".pdf":
            result_b = extract_pdf_kv(str(path_b), logger=logger)
            items_b = result_b.items
        elif ext_b in (".xlsx", ".xlsm", ".xls", ".csv"):
            result_b = extract_excel_kv(str(path_b), logger=logger)
            items_b = result_b.items
        else:
            items_b = {}

        if items_a and items_b:
            diff_rows = compare_kv(
                items_a, items_b,
                abs_tolerance=0.5,
                pct_tolerance=float(materiality_pct),
                min_match_score=75.0,
            )

            def _clean(v):
                if v is None:
                    return None
                try:
                    return None if (math.isnan(v) or math.isinf(v)) else v
                except Exception:
                    return None

            all_anomalies = []
            for row in diff_rows:
                if row.status == "MATCH":
                    continue
                va = _clean(row.value_excel)
                vb = _clean(row.value_pdf)
                delta = _clean(row.delta)
                pct = _clean(row.pct_delta)
                sev = "high" if row.status == "MISMATCH" else "low"
                if row.status == "MISSING_IN_PDF":
                    desc = f"'{row.key_excel}': present in {label_a} only"
                elif row.status == "MISSING_IN_EXCEL":
                    desc = f"'{row.key_pdf}': present in {label_b} only"
                else:
                    if va is not None and vb is not None and delta is not None and pct is not None:
                        desc = (f"'{row.key_excel}' â†” '{row.key_pdf}': "
                                f"{label_a} Â£{va:,.2f} vs {label_b} Â£{vb:,.2f} "
                                f"(Î” Â£{delta:+,.2f}, {pct:.1f}%)")
                    else:
                        desc = f"'{row.key_excel}' â†” '{row.key_pdf}': value mismatch"
                all_anomalies.append({
                    "severity": sev,
                    "description": desc,
                    "sheet": "Field Comparison",
                    "type": row.status,
                    "data": {
                        "field": row.key_excel or row.key_pdf,
                        "value_a": va,
                        "value_b": vb,
                        "delta": delta,
                        "pct": pct,
                    },
                })

            material = sum(1 for a in all_anomalies if a["severity"] == "high")
            compare_result = {
                "summary": {
                    "status": "DIFFERENCES NOTED" if all_anomalies else "CLEAN",
                    "label_a": label_a, "label_b": label_b,
                    "total_deltas": len(diff_rows),
                    "material_deltas": material,
                    "anomaly_count": len(all_anomalies),
                    "excel_items": len(items_a),
                    "pdf_items": len(items_b),
                    "method": "asgla_compare",
                },
                "anomalies": all_anomalies,
            }
            logger.info(
                "_compare_and_raise [asgla_compare]: %d/%d items -> "
                "%d rows (%d MISMATCH, %d MISSING, %d MATCH)",
                len(items_a), len(items_b), len(diff_rows),
                sum(1 for r in diff_rows if r.status == "MISMATCH"),
                sum(1 for r in diff_rows if "MISSING" in r.status),
                sum(1 for r in diff_rows if r.status == "MATCH"),
            )

            # Build review points
            review_points: List[Dict] = []
            for anomaly in all_anomalies:
                sev = anomaly.get("severity", "medium")
                review_points.append({
                    "severity": sev,
                    "category": "difference",
                    "title": anomaly.get("description", "Difference detected")[:300],
                    "description": anomaly.get("description", ""),
                    "section": anomaly.get("sheet", "Field Comparison"),
                    "audit_ref": None,
                    "field_name": anomaly.get("data", {}).get("field"),
                    "value_a": str(anomaly.get("data", {}).get("value_a") or ""),
                    "value_b": str(anomaly.get("data", {}).get("value_b") or ""),
                    "delta": anomaly.get("data", {}).get("delta"),
                    "status": "open",
                    "severity": sev,
                })

            # Post-review clearance: no differences = one clean pass point
            if not review_points:
                review_points.append({
                    "severity": "low",
                    "category": "difference",
                    "title": "No material differences detected",
                    "description": (
                        f"Oversight analysis of {label_a} vs {label_b} found no "
                        f"material differences above the {materiality_pct}% materiality threshold."
                    ),
                    "section": "Overall",
                    "audit_ref": None,
                    "field_name": None,
                    "value_a": None,
                    "value_b": None,
                    "delta": None,
                    "status": "open",
                })

            return review_points, compare_result

    except Exception as e:
        logger.warning(
            "_compare_and_raise: asgla_compare pipeline failed (%s) "
            "â€” falling back to sheet comparison", e
        )

    # â”€â”€ FALLBACK: sheet-by-sheet comparison â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Used when asgla_compare cannot extract KV pairs (scanned PDFs, unusual formats)
    from app.api.v1.compare_routes import _parse_excel, _parse_pdf, _parse_csv, _compare_sheets

    def _parse(path: Path):
        ext = path.suffix.lower()
        data = path.read_bytes()
        if ext in (".xlsx", ".xlsm", ".xls"):
            return _parse_excel(data, path.name)
        elif ext == ".pdf":
            return _parse_pdf(data, path.name)
        elif ext == ".csv":
            return _parse_csv(data, path.name)
        return {}

    sheets_a = _parse(path_a)
    sheets_b = _parse(path_b)
    all_anomalies: List[Dict] = []
    all_names = list(dict.fromkeys(list(sheets_a.keys()) + list(sheets_b.keys())))

    for sheet_name in all_names:
        rows_a = sheets_a.get(sheet_name, [])
        rows_b = sheets_b.get(sheet_name, [])
        if not rows_a and not rows_b:
            continue
        try:
            result = _compare_sheets(
                name=sheet_name, rows_a=rows_a, rows_b=rows_b,
                label_a=label_a, label_b=label_b, materiality=materiality_pct,
            )
            all_anomalies.extend(result.get("anomalies", []))
        except Exception as exc:
            logger.warning("Sheet compare failed [%s]: %s", sheet_name, exc)

    material = sum(1 for a in all_anomalies if a.get("severity") == "high")
    compare_result = {
        "summary": {"status": "DIFFERENCES NOTED" if all_anomalies else "CLEAN",
                    "label_a": label_a, "label_b": label_b,
                    "total_deltas": len(all_anomalies), "material_deltas": material,
                    "method": "sheet_fallback"},
        "anomalies": all_anomalies,
    }

    review_points: List[Dict] = []
    for anomaly in all_anomalies:
        sev = anomaly.get("severity", "medium")
        review_points.append({
            "severity": sev,
            "category": "difference",
            "title": anomaly.get("description", "Difference")[:300],
            "description": anomaly.get("description", ""),
            "section": anomaly.get("sheet", "General"),
            "audit_ref": None,
            "field_name": anomaly.get("data", {}).get("field"),
            "value_a": str(anomaly.get("data", {}).get("value_a") or ""),
            "value_b": str(anomaly.get("data", {}).get("value_b") or ""),
            "delta": anomaly.get("data", {}).get("delta"),
            "status": "open",
        })

    if not review_points:
        review_points.append({
            "severity": "low", "category": "difference",
            "title": "No material differences detected",
            "description": f"No differences above {materiality_pct}% threshold.",
            "section": "Overall", "audit_ref": None,
            "field_name": None, "value_a": None, "value_b": None,
            "delta": None, "status": "open",
        })

    return review_points, compare_result

def _pipeline_and_raise(
    path_a: Path,
    filename_a: str,
    tmp_path: Path,
) -> tuple[List[Dict], Dict]:
    """Run full pipeline and extract review points from DI output."""
    pipeline_result: Dict = {}
    review_points: List[Dict] = []

    try:
        from app.asgl_engine.pipeline import run_pipeline
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                run_pipeline(
                    file_path=str(path_a),
                    out_dir=str(tmp_path / "pipeline_out"),
                )
            )
        except TypeError:
            # run_pipeline may be sync
            result = run_pipeline(
                file_path=str(path_a),
                out_dir=str(tmp_path / "pipeline_out"),
            )
        finally:
            loop.close()

        pipeline_result = result or {}

        # Convert DI violations to review points
        for v in pipeline_result.get("di", {}).get("violations", []):
            review_points.append({
                "severity": v.get("severity", "medium"),
                "category": "policy",
                "title": v.get("rule_id", "Policy Violation")[:300],
                "description": v.get("message", str(v))[:2000],
                "section": v.get("field", "General"),
                "audit_ref": v.get("regulation_ref"),
                "field_name": v.get("field"),
                "value_a": str(v.get("value", ""))[:500],
                "value_b": None,
                "delta": None,
                "status": "open",
            })

    except Exception as exc:
        logger.warning("Full pipeline failed, returning empty: %s", exc)
        pipeline_result = {"error": str(exc)}

    return review_points, pipeline_result


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# SIGN-OFF
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

@router.post("/engagements/{eng_id}/sign-off", summary="Partner sign-off")
async def sign_off(
    eng_id: int,
    req: SignOffRequest,
    db: Session = Depends(get_db),
) -> JSONResponse:
    eng = db.query(Engagement).filter(Engagement.id == eng_id).first()
    if not eng:
        raise HTTPException(404, f"Engagement {eng_id} not found")
    if eng.status == "signed_off":
        raise HTTPException(409, "Already signed off")

    open_pts = (
        db.query(ReviewPoint)
        .filter(
            ReviewPoint.engagement_id == eng_id,
            ReviewPoint.status.in_(["open", "acknowledged", "in_progress"])
        )
        .count()
    )
    if open_pts > 0:
        raise HTTPException(
            422,
            f"Cannot sign off: {open_pts} review point(s) are still open. "
            "Resolve, waive, or escalate all points first."
        )

    eng.status = "signed_off"
    eng.signed_off_by = req.partner_name.strip()
    eng.signed_off_at = dt.datetime.utcnow()
    eng.sign_off_notes = (req.notes or "").strip() or None

    _audit(db, eng_id, "signed_off", actor=req.partner_name,
           detail=f"Partner sign-off by {req.partner_name}",
           payload={"notes": req.notes})
    _emit_notification(
        db, eng.tenant_id, "info", "Engagement Signed Off",
        f"{eng.client_name} Ã¢â‚¬â€ {eng.period} has been signed off by {req.partner_name}.",
        severity="success", engagement_id=eng_id, client_name=eng.client_name,
    )
    db.commit()
    db.refresh(eng)
    return JSONResponse({
        "engagement_id": eng_id,
        "status": "signed_off",
        "signed_off_by": eng.signed_off_by,
        "signed_off_at": eng.signed_off_at.isoformat(),
    })


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# AUDIT TRAIL
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

@router.get("/engagements/{eng_id}/audit-trail", summary="Immutable audit trail")
async def audit_trail(
    eng_id: int,
    db: Session = Depends(get_db),
) -> JSONResponse:
    eng = db.query(Engagement).filter(Engagement.id == eng_id).first()
    if not eng:
        raise HTTPException(404, f"Engagement {eng_id} not found")

    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.engagement_id == eng_id)
        .order_by(AuditEvent.ts)
        .all()
    )
    return JSONResponse({
        "engagement_id": eng_id,
        "client_name": eng.client_name,
        "period": eng.period,
        "events": [
            {
                "id": ev.id,
                "ts": ev.ts.isoformat(),
                "actor": ev.actor,
                "action": ev.action,
                "detail": ev.detail,
                "payload": ev.payload,
            }
            for ev in events
        ],
    })


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# REVIEW POINTS Ã¢â‚¬â€ status transitions
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

def _get_point_or_404(pt_id: int, db: Session) -> ReviewPoint:
    pt = db.query(ReviewPoint).filter(ReviewPoint.id == pt_id).first()
    if not pt:
        raise HTTPException(404, f"Review point {pt_id} not found")
    return pt


def _check_not_resolved(pt: ReviewPoint) -> None:
    if pt.status in ("resolved", "waived"):
        raise HTTPException(409, f"Review point is already {pt.status}")


@router.post("/review-points/{pt_id}/acknowledge", summary="Acknowledge a review point")
async def acknowledge_point(
    pt_id: int,
    acknowledged_by: str = Form("Reviewer"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    pt = _get_point_or_404(pt_id, db)
    _check_not_resolved(pt)
    pt.status = "acknowledged"
    _audit(db, pt.engagement_id, "point_acknowledged", actor=acknowledged_by,
           detail=f"Point #{pt_id} acknowledged")
    _recalc_point_counts(db, pt.engagement_id)
    db.commit()
    db.refresh(pt)
    return JSONResponse(_point_to_dict(pt))


@router.post("/review-points/{pt_id}/correct", summary="Submit a correction for a review point")
async def correct_point(
    pt_id: int,
    correction_notes: str = Form(""),
    corrected_by: str = Form("Preparer"),
    correction_file: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    pt = _get_point_or_404(pt_id, db)
    _check_not_resolved(pt)

    pt.status = "corrected"
    pt.resolved_at = dt.datetime.utcnow()
    pt.resolved_by = corrected_by
    pt.correction_notes = correction_notes.strip() or None

    if correction_file:
        # Store correction file bytes (path-based storage)
        try:
            from app.storage import storage
            data = await correction_file.read()
            uri = storage.put(
                f"engagements/{pt.engagement_id}/corrections/{pt_id}_{correction_file.filename}",
                data,
                correction_file.content_type or "application/octet-stream",
            )
            pt.correction_file_uri = uri
        except Exception as exc:
            logger.warning("Correction file storage failed: %s", exc)

    _audit(db, pt.engagement_id, "point_corrected", actor=corrected_by,
           detail=f"Point #{pt_id} marked corrected")
    _recalc_point_counts(db, pt.engagement_id)
    # Move engagement to in_correction if still in ai_review
    eng = db.query(Engagement).filter(Engagement.id == pt.engagement_id).first()
    if eng and eng.status == "ai_review":
        eng.status = "in_correction"
    db.commit()
    _flywheel_capture_point(pt, "corrected", corrected_by)
    db.refresh(pt)
    return JSONResponse(_point_to_dict(pt))


@router.post("/review-points/{pt_id}/waive", summary="Waive a review point")
async def waive_point(
    pt_id: int,
    waiver_reason: str = Form(...),
    waived_by: str = Form("Partner"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    pt = _get_point_or_404(pt_id, db)
    _check_not_resolved(pt)

    if not waiver_reason.strip():
        raise HTTPException(422, "waiver_reason is required")

    pt.status = "waived"
    pt.resolved_at = dt.datetime.utcnow()
    pt.resolved_by = waived_by
    pt.waiver_reason = waiver_reason.strip()

    _audit(db, pt.engagement_id, "point_waived", actor=waived_by,
           detail=f"Point #{pt_id} waived: {waiver_reason[:100]}")
    _recalc_point_counts(db, pt.engagement_id)
    eng = db.query(Engagement).filter(Engagement.id == pt.engagement_id).first()
    if eng and eng.status == "ai_review":
        eng.status = "in_correction"
    db.commit()
    _flywheel_capture_point(pt, "waived", waived_by)
    db.refresh(pt)
    return JSONResponse(_point_to_dict(pt))


@router.post("/review-points/{pt_id}/escalate", summary="Escalate a review point to partner")
async def escalate_point(
    pt_id: int,
    escalation_note: str = Form(...),
    escalated_to: str = Form("Partner"),
    escalated_by: str = Form("Reviewer"),
    db: Session = Depends(get_db),
) -> JSONResponse:
    pt = _get_point_or_404(pt_id, db)
    if pt.status == "waived":
        raise HTTPException(409, "Waived points cannot be escalated")

    pt.status = "escalated"
    pt.escalated_to = escalated_to
    pt.escalation_note = escalation_note.strip()

    _audit(db, pt.engagement_id, "point_escalated", actor=escalated_by,
           detail=f"Point #{pt_id} escalated to {escalated_to}")
    _recalc_point_counts(db, pt.engagement_id)
    eng = db.query(Engagement).filter(Engagement.id == pt.engagement_id).first()
    if eng:
        _emit_notification(
            db, eng.tenant_id, "sign_off_required",
            f"Escalation Ã¢â‚¬â€ {eng.client_name}",
            f"Review point escalated to {escalated_to}: {escalation_note[:100]}",
            severity="warning", engagement_id=pt.engagement_id,
            client_name=eng.client_name,
        )
    db.commit()
    db.refresh(pt)
    return JSONResponse(_point_to_dict(pt))


