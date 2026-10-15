"""
Oversight ASGL v13.5 — Engagements API
========================================
Full lifecycle: create → submit (pipeline) → review points → corrections
             → re-assess → partner sign-off → immutable audit trail

Routes (all under /api/v1/):
  POST   /engagements                          Create engagement
  GET    /engagements                          List engagements
  GET    /engagements/{id}                     Detail + review points + runs
  PUT    /engagements/{id}                     Update metadata
  POST   /engagements/{id}/submit              Submit files → run pipeline → raise points
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
import math
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
    Engagement, ReviewPoint, EngagementRun, AuditEvent, Notification, User
)
from app.web_auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["engagements"])

def _flywheel_capture_point(pt, outcome: str, actor: str) -> None:
    """Capture a confirmed review-point outcome into the flywheel dataset.
    
    Stores anonymised field-name mapping and outcome — no client data values.
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




# ══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════════

class EngagementCreate(BaseModel):
    client_name: str = Field(..., min_length=1, max_length=300)
    period: Optional[str] = Field(None, max_length=100)
    regulatory_pack: str = Field("frs102", max_length=80)
    preparer_name: Optional[str] = Field(None, max_length=200)
    engagement_ref: Optional[str] = Field(None, max_length=100)
    job_type: str = Field("comparison", max_length=50)
    # tenant_id is never accepted from the client — derived from the
    # authenticated session (current_user.tenant_id). SECURITY-FIX 2026-10-15.


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
    # tenant_id is never accepted from the client — derived from the
    # authenticated session (current_user.tenant_id). SECURITY-FIX 2026-10-15.


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# ENGAGEMENT CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/engagements", summary="List engagements for a tenant")
async def list_engagements(
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: tenant_id no longer accepted from the client.
    q = db.query(Engagement).filter(Engagement.tenant_id == current_user.tenant_id)
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
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: tenant_id derived from the session, never from the request body.
    eng = Engagement(
        tenant_id=current_user.tenant_id,
        client_name=req.client_name.strip(),
        period=(req.period or "").strip() or None,
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
    _emit_notification(db, current_user.tenant_id, "info", "Engagement Created",
                       f"{eng.client_name} — {eng.period} engagement created.",
                       engagement_id=eng.id, client_name=eng.client_name)
    db.commit()
    db.refresh(eng)
    return JSONResponse(_engagement_to_dict(eng), status_code=201)


@router.get("/engagements/{eng_id}", summary="Get engagement detail")
async def get_engagement(
    eng_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: scoped to the caller's tenant so eng_id cannot be enumerated cross-tenant.
    eng = db.query(Engagement).filter(Engagement.id == eng_id, Engagement.tenant_id == current_user.tenant_id).first()
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
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: scoped to the caller's tenant.
    eng = db.query(Engagement).filter(Engagement.id == eng_id, Engagement.tenant_id == current_user.tenant_id).first()
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


# ══════════════════════════════════════════════════════════════════════════════
# SUBMIT — run pipeline, generate review points
# ══════════════════════════════════════════════════════════════════════════════

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
    # Mapping gate — if True, skip the gate even on first run (mapping already confirmed)
    mapping_confirmed: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Submit N documents for AI analysis.

    First-run mapping gate
    ----------------------
    On the first submission for an engagement (run_count == 0) where no
    confirmed field mapping exists, this endpoint returns HTTP 202 with
    status "mapping_required" instead of running the comparison.  The
    response includes an auto-proposed mapping (via Phase 1/2 template
    retrieval) which the user confirms or edits in the Mapping Editor.

    Once the user confirms the mapping (POST /engagements/{id}/confirm-mapping),
    the engagement moves to "mapping_confirmed" and the full analysis runs
    automatically.

    On subsequent runs (run_count > 0), the confirmed mapping is used
    directly and the gate is bypassed unless the file structure has changed
    significantly (fingerprint mismatch triggers re-confirmation prompt).

    Accepts either:
    - `files[]` — multiple files (new multi-document interface)
    - `file_a` + `file_b` — two files (legacy interface, still supported)
    """
    # SECURITY-FIX 2026-10-15: scoped to the caller's tenant.
    eng = db.query(Engagement).filter(Engagement.id == eng_id, Engagement.tenant_id == current_user.tenant_id).first()
    if not eng:
        raise HTTPException(404, f"Engagement {eng_id} not found")
    if eng.status == "signed_off":
        raise HTTPException(409, "Signed-off engagement cannot be resubmitted")

    # Normalise to list of (filename, bytes) — merge both interfaces
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

    # ── MAPPING GATE ──────────────────────────────────────────────────────────
    # On the first run for any engagement, gate at field mapping confirmation
    # unless the caller has already confirmed a mapping.
    #
    # Logic:
    #  1. First run (run_count == 0) with no saved mapping → ALWAYS gate
    #  2. Subsequent runs with a saved mapping → bypass gate (use saved mapping)
    #  3. Subsequent runs where file structure has changed (fingerprint drift)
    #     → soft re-confirm (202 with status "mapping_review_suggested")
    #  4. mapping_confirmed=True in the form → bypass gate regardless
    #
    # The gate response includes an auto-proposed mapping so the Mapping Editor
    # has something to show — the user confirms, edits, or ignores it.

    saved_mapping = eng.latest_run_summary and eng.latest_run_summary.get("confirmed_mapping")
    is_first_run = eng.run_count == 0

    if is_first_run and not saved_mapping and not mapping_confirmed:
        # Attempt auto-propose via Phase 1/2
        proposed = _propose_mapping(all_files, eng.regulatory_pack or "frs102", label_a, label_b)

        # Store uploaded file bytes in engagement for when mapping is confirmed
        # (we don't re-upload — stored as a pending submission on the engagement)
        import json as _json
        pending = {
            "files": [{"filename": fn, "size": len(data)} for fn, data in all_files],
            "label_a": label_a,
            "label_b": label_b,
            "preparer_name": preparer_name,
            "materiality_pct": materiality_pct,
            "proposed_mapping": proposed,
        }
        eng.status = "mapping_required"
        if not eng.latest_run_summary:
            eng.latest_run_summary = {}
        eng.latest_run_summary["pending_submission"] = pending

        _audit(db, eng_id, "mapping_required", actor=preparer_name,
               detail="First run — mapping confirmation required before analysis")
        db.commit()

        return JSONResponse(
            status_code=202,
            content={
                "engagement_id": eng_id,
                "status": "mapping_required",
                "message": (
                    "This is the first analysis run for this engagement. "
                    "Please confirm the field mappings in the Mapping Editor, "
                    "then resubmit. The proposed auto-mapping is included below."
                ),
                "proposed_mapping": proposed,
                "action_required": "confirm_mapping",
                "confirm_url": f"/api/v1/engagements/{eng_id}/confirm-mapping",
            },
        )

    # Fingerprint drift check on subsequent runs (non-blocking — advisory only)
    # If the file headers differ materially from the saved mapping's fingerprint,
    # return a soft warning alongside the result (don't block, just flag).
    mapping_drift_warning = None
    if saved_mapping and not is_first_run:
        saved_fingerprint = saved_mapping.get("fingerprint", {})
        current_fingerprint = _fingerprint_files(all_files)
        drift_score = _mapping_drift(saved_fingerprint, current_fingerprint)
        if drift_score > 0.4:  # >40% header change → suggest re-confirm
            mapping_drift_warning = (
                f"File structure has changed significantly from the saved mapping "
                f"(drift score {drift_score:.0%}). Consider re-confirming mappings "
                f"in the Mapping Editor."
            )

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

    # Extract key_fields from confirmed mapping (if any)
    confirmed_key_fields = None
    if saved_mapping and saved_mapping.get("key_fields_only"):
        confirmed_key_fields = saved_mapping["key_fields_only"]
        logger.info(
            "submit_engagement: using confirmed mapping with %d key fields",
            len(confirmed_key_fields),
        )

    # Run pipeline
    review_points, narrative, pipeline_result = _run_pipeline_and_raise_points_multi(
        eng_id=eng_id,
        run_id=run_id,
        files=all_files,
        label_a=label_a,
        label_b=label_b,
        materiality_pct=materiality_pct,
        run_number=eng.run_count,
        key_fields=confirmed_key_fields,
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

    for pt_data in review_points:
        pt = ReviewPoint(
            engagement_id=eng_id,
            run_number=eng.run_count,
            **pt_data,
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
        f"Analysis Complete — {eng2.client_name if eng2 else ''}",
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
        "mapping_drift_warning": mapping_drift_warning,
    })



# ══════════════════════════════════════════════════════════════════════════════
# CONFIRM MAPPING — user has reviewed/edited the proposed mapping in the
# Mapping Editor and is confirming it. This saves the mapping and triggers
# the deferred analysis run.
# ══════════════════════════════════════════════════════════════════════════════

class MappingField(BaseModel):
    """Single field mapping: source column → canonical role."""
    source_label: str          # e.g. "Net profit / (loss)" from the working papers
    canonical_role: str        # e.g. "profit_loss" (Oversight-C canonical name)
    source_doc: str = "a"      # "a" = doc A, "b" = doc B
    value_type: str = "numeric"  # "numeric" | "text" | "date"
    is_key_field: bool = True  # whether this field is checked in comparison


class ConfirmMappingRequest(BaseModel):
    """User-confirmed field mapping for this engagement type."""
    fields: List[MappingField]
    template_name: Optional[str] = None   # optional: save as reusable template
    materiality_pct: Optional[float] = None
    notes: Optional[str] = None
    confirmed_by: Optional[str] = "User"
    regulatory_pack: Optional[str] = None


@router.post(
    "/engagements/{eng_id}/confirm-mapping",
    summary="Confirm field mappings and trigger deferred analysis",
)
async def confirm_mapping(
    eng_id: int,
    req: ConfirmMappingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """
    Confirm the field mapping for this engagement and run the deferred analysis.

    Called after the user has reviewed (and optionally edited) the proposed
    mapping in the Mapping Editor.  The confirmed mapping is:
    1. Saved to the engagement record (used for all subsequent runs)
    2. Fed into the flywheel as a label-confirmed signal
    3. Optionally saved as a reusable template for this firm/client type
    4. Used immediately to run the deferred analysis (files are re-submitted)

    The mapping controls which fields the comparison engine checks.  Fields
    not in the mapping are NOT raised as review points — this is how you
    avoid the 461-noise-point problem without hardcoding any accountancy rules.
    """
    # SECURITY-FIX 2026-10-15: scoped to the caller's tenant.
    eng = db.query(Engagement).filter(Engagement.id == eng_id, Engagement.tenant_id == current_user.tenant_id).first()
    if not eng:
        raise HTTPException(404, f"Engagement {eng_id} not found")
    if eng.status not in ("draft", "mapping_required"):
        raise HTTPException(409, f"Cannot confirm mapping — engagement status is '{eng.status}'")

    if not req.fields:
        raise HTTPException(400, "At least one field mapping is required")

    # Build fingerprint from confirmed fields (used for drift detection on re-runs)
    fingerprint = {
        "field_count": len(req.fields),
        "canonical_roles": sorted({f.canonical_role for f in req.fields}),
        "key_fields": sorted(f.source_label for f in req.fields if f.is_key_field),
    }

    confirmed_mapping = {
        "confirmed_by": req.confirmed_by,
        "confirmed_at": dt.datetime.utcnow().isoformat() + "Z",
        "template_name": req.template_name,
        "notes": req.notes,
        "fingerprint": fingerprint,
        "fields": [f.model_dump() for f in req.fields],
        "key_fields_only": [f.source_label for f in req.fields if f.is_key_field],
    }

    # Save mapping to engagement
    summary = eng.latest_run_summary or {}
    summary["confirmed_mapping"] = confirmed_mapping
    eng.latest_run_summary = summary
    eng.status = "mapping_confirmed"

    _audit(db, eng_id, "mapping_confirmed", actor=req.confirmed_by,
           detail=f"{len(req.fields)} fields confirmed ({sum(1 for f in req.fields if f.is_key_field)} key fields)")
    db.commit()

    # Optionally save as reusable template via flywheel
    if req.template_name:
        try:
            from app.flywheel.engine import FlywheelEngine
            from app.flywheel.events import FlywheelEventType
            engine = FlywheelEngine(
                tenant_id=str(eng.tenant_id),
                client_id=str(eng_id),
                run_id="mapping",
                vertical=eng.regulatory_pack or "accountancy",
            )
            engine.capture(
                type=FlywheelEventType.LABEL_CONFIRMED,
                actor=req.confirmed_by,
                confidence=1.0,
                payload={
                    "event": "mapping_template_saved",
                    "template_name": req.template_name,
                    "field_count": len(req.fields),
                    "canonical_roles": fingerprint["canonical_roles"],
                },
            )
        except Exception as exc:
            logger.warning("Flywheel capture for mapping failed: %s", exc)

    # If there's a pending submission (files uploaded at submit time), run it now
    pending = summary.get("pending_submission")
    if pending:
        logger.info(
            "confirm_mapping: no pending file bytes to re-run automatically — "
            "client must resubmit (files not stored server-side for security). "
            "Returning mapping_confirmed status so UI can trigger resubmit."
        )
        # Note: we intentionally do NOT store raw file bytes — they must be
        # resubmitted by the client with mapping_confirmed=True in the form.
        # The UI should detect status=mapping_confirmed and auto-resubmit.

    _emit_notification(
        db, eng.tenant_id,
        "mapping_confirmed",
        f"Mapping Confirmed — {eng.client_name}",
        f"Field mappings confirmed by {req.confirmed_by}. "
        f"Ready to run analysis — resubmit to continue.",
        severity="info",
        engagement_id=eng_id,
        client_name=eng.client_name,
    )
    db.commit()

    return JSONResponse({
        "engagement_id": eng_id,
        "status": "mapping_confirmed",
        "confirmed_mapping": confirmed_mapping,
        "message": (
            "Mapping confirmed. Resubmit files with mapping_confirmed=true "
            "to run the full analysis using these field mappings."
        ),
        "key_fields": confirmed_mapping["key_fields_only"],
        "next_action": "resubmit",
        "resubmit_url": f"/api/v1/engagements/{eng_id}/submit",
    })


# ══════════════════════════════════════════════════════════════════════════════
# GET MAPPING — retrieve current confirmed mapping for the Mapping Editor UI
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/engagements/{eng_id}/mapping", summary="Get current field mapping")
async def get_mapping(
    eng_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Return the current confirmed mapping and auto-propose if none exists."""
    # SECURITY-FIX 2026-10-15: scoped to the caller's tenant.
    eng = db.query(Engagement).filter(Engagement.id == eng_id, Engagement.tenant_id == current_user.tenant_id).first()
    if not eng:
        raise HTTPException(404, f"Engagement {eng_id} not found")

    summary = eng.latest_run_summary or {}
    confirmed = summary.get("confirmed_mapping")
    pending = summary.get("pending_submission", {})
    proposed = pending.get("proposed_mapping") if pending else None

    return JSONResponse({
        "engagement_id": eng_id,
        "status": eng.status,
        "has_confirmed_mapping": bool(confirmed),
        "confirmed_mapping": confirmed,
        "proposed_mapping": proposed,
        "instructions": (
            "Edit the proposed_mapping fields, set is_key_field=true for each "
            "field you want compared, then POST to /confirm-mapping."
            if not confirmed else
            "Mapping already confirmed. POST to /confirm-mapping to update it."
        ),
    })


# ══════════════════════════════════════════════════════════════════════════════
# MAPPING HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _propose_mapping(
    files: List[tuple],
    regulatory_pack: str,
    label_a: str,
    label_b: str,
) -> Dict[str, Any]:
    """Attempt auto-mapping proposal via Phase 1/2 (template retrieval +
    column signature embeddings).  Falls back to a structural scan if the
    Phase 1/2 machinery is unavailable.

    Returns a dict the Mapping Editor can display immediately:
    {
        "method": "phase1_template" | "structural_scan" | "fallback",
        "confidence": 0.0–1.0,
        "fields": [{"source_label", "canonical_role", "source_doc", ...}],
        "template_match": {...} | None,
        "instructions": "...",
    }
    """
    proposed_fields: List[Dict] = []
    method = "fallback"
    confidence = 0.0
    template_match = None

    # ── Try Phase 1/2 machinery first ────────────────────────────────────────
    try:
        from app.services.phase2_column_encoder import (
            extract_column_signatures_from_preview,
            embed_column_signatures,
        )
        from app.services.phase1_semantic_mapper import phase1_select_template_and_map

        for doc_label, (filename, data) in zip(["a", "b"], [
            (files[0][0], files[0][1]) if files else ("", b""),
            (files[1][0], files[1][1]) if len(files) > 1 else ("", b""),
        ]):
            if not data:
                continue
            # Extract column signatures from Excel/CSV preview
            try:
                import openpyxl, io as _io
                wb = openpyxl.load_workbook(_io.BytesIO(data), read_only=True, data_only=True)
                headers = []
                sample_values: Dict[str, List] = {}
                ws = wb.worksheets[0]
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i == 0:
                        headers = [str(c or "").strip() for c in row]
                    elif i < 6:
                        for h, val in zip(headers, row):
                            sample_values.setdefault(h, []).append(val)
                    else:
                        break

                preview = {
                    "headers": headers,
                    "sample_values": sample_values,
                    "sheet_names": wb.sheetnames,
                }
                sigs = extract_column_signatures_from_preview(preview, workbook_name=filename)
                # Phase 1: retrieve template
                result = phase1_select_template_and_map(
                    sigs,
                    vertical=regulatory_pack,
                    top_k=3,
                )
                if result and result.get("selected_template"):
                    template_match = result["selected_template"]
                    method = "phase1_template"
                    confidence = result.get("confidence", 0.7)

                # Convert column signatures to proposed fields
                for sig in sigs:
                    if sig.header and sig.inferred_type not in ("row_number", "empty"):
                        proposed_fields.append({
                            "source_label": sig.header,
                            "canonical_role": _infer_canonical_role(sig.header, sig.inferred_type),
                            "source_doc": doc_label,
                            "value_type": "numeric" if sig.inferred_type == "numeric" else "text",
                            "is_key_field": _is_likely_key_field(sig.header),
                            "confidence": confidence,
                        })
            except Exception as inner_exc:
                logger.debug("Phase 2 preview extraction failed for %s: %s", filename, inner_exc)

        if proposed_fields:
            method = method if method != "fallback" else "structural_scan"

    except ImportError:
        logger.debug("Phase 1/2 machinery not available — falling back to structural scan")

    # ── Structural scan fallback ──────────────────────────────────────────────
    if not proposed_fields:
        method = "structural_scan"
        confidence = 0.4
        for doc_idx, (filename, data) in enumerate(files[:2]):
            doc_label = "a" if doc_idx == 0 else "b"
            try:
                import openpyxl, io as _io
                wb = openpyxl.load_workbook(_io.BytesIO(data), read_only=True, data_only=True)
                for ws in wb.worksheets[:3]:
                    for row in ws.iter_rows(min_row=1, max_row=20, values_only=True):
                        for cell in row:
                            if cell and isinstance(cell, str) and len(cell.strip()) > 2:
                                proposed_fields.append({
                                    "source_label": cell.strip(),
                                    "canonical_role": _infer_canonical_role(cell.strip(), "text"),
                                    "source_doc": doc_label,
                                    "value_type": "numeric",
                                    "is_key_field": _is_likely_key_field(cell.strip()),
                                    "confidence": 0.4,
                                })
            except Exception:
                pass

    # ── Hard fallback — empty mapping for user to fill ───────────────────────
    if not proposed_fields:
        method = "fallback"
        confidence = 0.0
        proposed_fields = [
            {
                "source_label": "< select from document >",
                "canonical_role": "turnover",
                "source_doc": "a",
                "value_type": "numeric",
                "is_key_field": True,
                "confidence": 0.0,
            }
        ]

    # De-duplicate by source_label + source_doc
    seen = set()
    unique_fields = []
    for f in proposed_fields:
        key = (f["source_label"], f["source_doc"])
        if key not in seen:
            seen.add(key)
            unique_fields.append(f)

    return {
        "method": method,
        "confidence": round(confidence, 2),
        "fields": unique_fields,
        "template_match": template_match,
        "instructions": (
            "Review the proposed mappings below. Mark is_key_field=true for each "
            "field you want Oversight to compare between the two documents. "
            "Fields not marked as key fields will not appear as review points. "
            "When done, confirm to run the analysis."
        ),
    }


# Known canonical roles for accountancy — maps common label patterns
# to the Oversight-C accountancy canonical schema.
_CANONICAL_ROLE_PATTERNS: List[tuple] = [
    # Revenue
    ({"turnover", "revenue", "sales", "income"}, "turnover"),
    # P&L
    ({"profit", "loss", "ebit", "ebitda", "net profit", "operating profit"}, "profit_loss"),
    # Cost
    ({"cost of sales", "cos", "direct cost", "cogs"}, "cost_of_sales"),
    # Tax
    ({"corporation tax", "deferred tax", "tax charge", "tax on"}, "tax_charge"),
    # Balance sheet — assets
    ({"fixed asset", "tangible", "intangible", "ppe", "freehold"}, "fixed_assets"),
    ({"debtor", "trade debtor", "receivable"}, "debtors"),
    ({"stock", "inventory", "wip"}, "stock"),
    ({"cash", "bank", "santander", "hsbc", "lloyds", "natwest", "barclays"}, "cash"),
    # Balance sheet — liabilities
    ({"creditor", "trade creditor", "payable", "accrual"}, "creditors"),
    ({"loan", "mortgage", "borrowing", "overdraft"}, "loans"),
    ({"dla", "director loan", "director current account"}, "dla"),
    # Equity
    ({"share capital", "capital"}, "share_capital"),
    ({"reserves", "retained", "profit and loss account"}, "reserves"),
    # Payroll
    ({"wages", "salary", "salaries", "payroll", "paye", "national insurance", "ni "}, "wages"),
    ({"pension", "auto enrolment"}, "pension"),
]


def _infer_canonical_role(label: str, inferred_type: str) -> str:
    """Map a source label to the closest Oversight-C canonical role."""
    lower = label.lower()
    for keywords, role in _CANONICAL_ROLE_PATTERNS:
        if any(kw in lower for kw in keywords):
            return role
    return "other"


def _is_likely_key_field(label: str) -> bool:
    """Heuristic: is this label likely a key comparison field (not a sub-analysis row)?

    Key fields are summary-level: turnover, profit, tax, main balance sheet lines.
    Non-key fields are granular: vendor names, individual journal entries, sub-totals.
    """
    lower = label.lower()
    # Definitely key
    KEY_PATTERNS = {
        "turnover", "revenue", "profit", "loss", "tax", "total", "net assets",
        "share capital", "reserves", "debtors", "creditors", "cash", "stock",
        "fixed asset", "loans", "wages", "salaries",
    }
    # Definitely not key (granular working paper rows)
    NOISE_PATTERNS = {
        "ltd", "limited", "plc", " - ", "invoice", "receipt", "slip",
        "various", "sundry", "miscellaneous", "see below", "per client",
        "summary", "totals", "reclassif",
    }
    if any(p in lower for p in NOISE_PATTERNS):
        return False
    if any(p in lower for p in KEY_PATTERNS):
        return True
    # Default: short labels are more likely to be key fields
    return len(label) < 40


def _fingerprint_files(files: List[tuple]) -> Dict:
    """Create a lightweight structural fingerprint from uploaded files
    (header names only — no values, no PII).  Used for drift detection."""
    fingerprint: Dict = {"headers_by_doc": {}}
    for idx, (filename, data) in enumerate(files[:2]):
        doc_key = "a" if idx == 0 else "b"
        try:
            import openpyxl, io as _io
            wb = openpyxl.load_workbook(_io.BytesIO(data), read_only=True, data_only=True)
            headers = []
            for ws in wb.worksheets[:3]:
                for row in ws.iter_rows(min_row=1, max_row=5, values_only=True):
                    for cell in row:
                        if cell and isinstance(cell, str) and len(cell.strip()) > 1:
                            headers.append(cell.strip().lower())
            fingerprint["headers_by_doc"][doc_key] = sorted(set(headers))[:100]
        except Exception:
            fingerprint["headers_by_doc"][doc_key] = []
    return fingerprint


def _mapping_drift(saved: Dict, current: Dict) -> float:
    """Return a 0–1 drift score between two file fingerprints.
    0 = identical structure, 1 = completely different."""
    try:
        saved_h = set(h for hs in saved.get("headers_by_doc", {}).values() for h in hs)
        current_h = set(h for hs in current.get("headers_by_doc", {}).values() for h in hs)
        if not saved_h and not current_h:
            return 0.0
        if not saved_h or not current_h:
            return 1.0
        intersection = saved_h & current_h
        union = saved_h | current_h
        jaccard = len(intersection) / len(union)
        return round(1.0 - jaccard, 3)
    except Exception:
        return 0.0


def _run_pipeline_and_raise_points_multi(
    eng_id: int,
    run_id: int,
    files: List[tuple],  # List of (filename, bytes)
    label_a: str,
    label_b: str,
    materiality_pct: float,
    run_number: int,
    key_fields: Optional[List[str]] = None,
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

            # ── Stage 1: Classify all documents ──────────────────────────────
            classified = []
            for filename, data in files:
                try:
                    cls = classify_document(filename, data)
                    classified.append((filename, data, cls))
                    logger.info(
                        "Classified: %s → %s (%.0f%%)",
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

            # ── Stage 2: Smart comparison pairing ────────────────────────────
            # Group documents by type
            by_type = {}
            for fn, data, cls in classified:
                dt = cls.doc_type if cls else "unknown"
                by_type.setdefault(dt, []).append((fn, data, cls))

            # Document types that contain comparable financial values
            # (exclude AT, NL, CT600, CT computation — these feed reconciliation only)
            COMPARABLE_EXCEL = {"excel_working_papers", "sage_trial_balance",
                                 "software_trial_balance", "xero_trial_balance"}
            COMPARABLE_PDF   = {"statutory_accounts", "software_trial_balance"}
            # Types that should NOT be compared against each other
            NON_COMPARABLE   = {"audit_trail", "nominal_ledger", "ct600",
                                 "ct_computation", "vat_return", "payroll_summary"}

            # Find the best Excel source (working papers > trial balance)
            excel_source = None
            for preferred in ("excel_working_papers", "sage_trial_balance",
                              "software_trial_balance", "xero_trial_balance"):
                if preferred in by_type:
                    excel_source = by_type[preferred][0]  # (fn, data, cls)
                    break

            # Find the best PDF target (statutory accounts > software TB)
            pdf_target = None
            for preferred in ("statutory_accounts", "software_trial_balance"):
                if preferred in by_type:
                    pdf_target = by_type[preferred][0]
                    break

            # If we didn't find a PDF, look for any PDF that's not in NON_COMPARABLE
            if pdf_target is None:
                for fn, data, cls in classified:
                    dt = cls.doc_type if cls else "unknown"
                    ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                    if ext == "pdf" and dt not in NON_COMPARABLE:
                        pdf_target = (fn, data, cls)
                        break

            # If still no pair, fall back to first Excel + first PDF
            # but NEVER compare two of the same type or non-comparable types
            if excel_source is None or pdf_target is None:
                excel_candidates = [
                    (fn, data, cls) for fn, data, cls in classified
                    if fn.lower().endswith((".xlsx", ".xlsm", ".xls", ".csv"))
                ]
                pdf_candidates = [
                    (fn, data, cls) for fn, data, cls in classified
                    if fn.lower().endswith(".pdf") and
                    (cls.doc_type if cls else "unknown") not in NON_COMPARABLE
                ]
                if excel_source is None and excel_candidates:
                    excel_source = excel_candidates[0]
                if pdf_target is None and pdf_candidates:
                    pdf_target = pdf_candidates[0]

            pairs_run = set()

            def _sanitise_fname(fn: str) -> str:
                """Make filename safe for use as tmp path - strip path separators."""
                import re as _re
                return _re.sub(r'[/\\:*?"<>|]', '_', fn)[:200]

            def _run_pair(fn_a, data_a, fn_b, data_b, la, lb):
                pair_key = f"{fn_a}|{fn_b}"
                if pair_key in pairs_run:
                    return []
                pairs_run.add(pair_key)
                # Sanitise filenames - prevent path traversal and collisions
                safe_a = _sanitise_fname(fn_a)
                safe_b = _sanitise_fname(f"b_{fn_b}")  # prefix b_ to prevent collision if same name
                p_a = tmp_path / safe_a
                p_b = tmp_path / safe_b
                p_a.write_bytes(data_a)
                p_b.write_bytes(data_b)
                logger.info("Comparing: %s vs %s", fn_a, fn_b)
                pts, result = _compare_and_raise(p_a, p_b, la, lb, materiality_pct, tmp_path, key_fields=key_fields)
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
                logger.info(
                    "Primary comparison pair: [%s] %s vs [%s] %s",
                    cls_a.doc_type if cls_a else "?", fn_a,
                    cls_b.doc_type if cls_b else "?", fn_b,
                )
                pts = _run_pair(fn_a, data_a, fn_b, data_b, la, lb)
                review_points.extend(pts)
            else:
                logger.warning(
                    "No valid comparison pair found. excel_source=%s pdf_target=%s. "
                    "Classified types: %s",
                    excel_source[0] if excel_source else None,
                    pdf_target[0] if pdf_target else None,
                    {dt: [f for f, _, _ in docs] for dt, docs in by_type.items()},
                )
                # No comparison pair: analyse the first spreadsheet or PDF on its own
                # so a single upload still produces validation and analytic findings.
                single = next(((fn, data) for fn, data, cls in classified
                               if fn.lower().endswith((".xlsx", ".xlsm", ".xls", ".csv", ".pdf"))), None)
                if single is not None:
                    fn_s, data_s = single
                    p_s = tmp_path / _sanitise_fname(fn_s)
                    p_s.write_bytes(data_s)
                    s_pts, s_res = _pipeline_and_raise(p_s, fn_s, tmp_path)
                    review_points.extend(s_pts)
                    pipeline_result["single_document"] = {
                        "filename": fn_s,
                        "status": "failed" if s_res.get("error") else "ok",
                        "error": s_res.get("error"),
                    }

            # ── Stage 3: Reconciliation rules across all documents ────────────
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
    key_fields: Optional[List[str]] = None,
) -> tuple[List[Dict], Optional[str], Dict]:
    """Run the full pipeline:
    Stage 1 — Classify each document (Sage TB, CT600, working papers, etc.)
    Stage 2 — Extract named fields from each document type
    Stage 3 — Run cross-document reconciliation rules (CT vs CT600, etc.)
    Stage 4 — Run field-level comparison (existing compare engine)
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

            # ── Stage 1: Classify documents ────────────────────────────────
            classification_a = None
            classification_b = None
            try:
                from app.services.document_classifier import classify_document
                classification_a = classify_document(filename_a, data_a)
                logger.info(
                    "Document A classified: %s (%.0f%% confidence) — signals: %s",
                    classification_a.label,
                    classification_a.confidence * 100,
                    classification_a.signals[:2],
                )
                if data_b and filename_b:
                    classification_b = classify_document(filename_b, data_b)
                    logger.info(
                        "Document B classified: %s (%.0f%% confidence) — signals: %s",
                        classification_b.label,
                        classification_b.confidence * 100,
                        classification_b.signals[:2],
                    )
            except Exception as e:
                logger.warning("Document classification failed (non-critical): %s", e)

            # ── Stage 2 + Stage 4: Field-level comparison ──────────────────
            if path_b is not None:
                compare_points, pipeline_result = _compare_and_raise(
                    path_a, path_b, label_a, label_b,
                    materiality_pct, tmp_path,
                    key_fields=key_fields,
                )
                review_points.extend(compare_points)
            else:
                single_points, pipeline_result = _pipeline_and_raise(
                    path_a, filename_a, tmp_path
                )
                review_points.extend(single_points)

            # ── Stage 3: Cross-document reconciliation rules ───────────────
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

            # ── Attach classification metadata to pipeline result ──────────
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
    key_fields: Optional[List[str]] = None,
) -> tuple[List[Dict], Dict]:
    """Run field-level semantic comparison using FinBERT + CrossEncoder pipeline.

    key_fields: if supplied (from a confirmed mapping), only rows whose
    key_excel or key_pdf label matches one of these field labels are raised
    as review points.  This is the primary noise-suppression mechanism —
    the user tells us what to check, and we check exactly that.

    When key_fields is None (no mapping confirmed), falls back to materiality-
    floor filtering to suppress granular working paper rows.

    Routes:
    - Excel vs PDF  → asgla_compare.pipeline (extract_excel_kv + extract_pdf_kv + compare_kv)
    - Excel vs Excel → same pipeline via pdfplumber text fallback
    - Any vs Any    → graceful fallback to sheet-row comparison

    The asgla_compare pipeline uses RapidFuzz retrieval + CrossEncoder reranking
    to semantically match field names across documents regardless of label differences.
    """
    # ── ORCHESTRATOR PATH (preferred) ───────────────────────────────────────
    # If oversight_enhancements is installed, delegate to ReviewPointOrchestrator
    # which adds anchor-field filtering, ratio anomaly detection, and field
    # dependency graph chained-point generation on top of the base pipeline.
    # If anything fails, falls through to the legacy implementation below.
    try:
        from oversight_enhancements.review_point_orchestrator import ReviewPointOrchestrator
        # Build a confirmed_mapping dict from key_fields if supplied
        _confirmed = None
        if key_fields:
            _confirmed = {"fields": [
                {"canonical_role": k, "is_key_field": True} for k in key_fields
            ]}
        _orch = ReviewPointOrchestrator(vertical="accountancy")
        return _orch.run(
            path_a=path_a, path_b=path_b,
            label_a=label_a, label_b=label_b,
            materiality_pct=materiality_pct,
            tmp_path=tmp_path,
            engagement_id=0,
            confirmed_mapping=_confirmed,
            regulatory_pack="frs102",
        )
    except ImportError:
        pass
    except Exception as _orch_err:
        logger.warning("Orchestrator path failed, using legacy compare: %s", _orch_err)

    ext_a = path_a.suffix.lower()
    ext_b = path_b.suffix.lower()

    # Normalise key_fields for fuzzy matching
    key_fields_lower = {k.lower().strip() for k in key_fields} if key_fields else None

    # ── PRIMARY PATH: use asgla_compare pipeline (FinBERT + CrossEncoder) ────
    # This is the real SOTA path — extract KV pairs then semantically match them
    try:
        from asgla_compare.ingest.excel_ingest import extract_excel_kv
        from asgla_compare.ingest.pdf_ingest import extract_pdf_kv
        from asgla_compare.compare.diff_engine import compare_kv

        # Verify files exist before reading
        if not path_a.exists():
            raise FileNotFoundError(f"path_a does not exist: {path_a}")
        if not path_b.exists():
            raise FileNotFoundError(f"path_b does not exist: {path_b}")

        # Extract key-value pairs from each document
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
                if v is None: return None
                try: return None if (math.isnan(v) or math.isinf(v)) else v
                except: return None

            def _is_in_key_fields(row) -> bool:
                """True if this diff row matches a confirmed key field (or no filter is active)."""
                if key_fields_lower is None:
                    return True  # no mapping confirmed — use materiality-floor fallback below
                label_a_lower = (row.key_excel or "").lower().strip()
                label_b_lower = (row.key_pdf or "").lower().strip()
                # Exact match first
                if label_a_lower in key_fields_lower or label_b_lower in key_fields_lower:
                    return True
                # Substring match for partial labels (e.g. "profit" matches "net profit")
                return any(
                    kf in label_a_lower or kf in label_b_lower
                    or label_a_lower in kf or label_b_lower in kf
                    for kf in key_fields_lower
                )

            # Materiality floor (used only when no confirmed mapping exists)
            MISSING_MATERIALITY_FLOOR = max(1000.0, (materiality_pct / 100.0) * 50000.0)

            all_anomalies = []
            for row in diff_rows:
                if row.status == "MATCH":
                    continue

                va = _clean(row.value_excel)
                vb = _clean(row.value_pdf)
                delta = _clean(row.delta)
                pct = _clean(row.pct_delta)

                # ── Key-fields gate (mapping confirmed) ─────────────────────
                # When a confirmed mapping exists, only raise points for fields
                # the user has explicitly marked as key fields. Everything else
                # is suppressed — the user told us what to check.
                if key_fields_lower is not None:
                    if not _is_in_key_fields(row):
                        continue  # not a key field — skip entirely

                # ── Materiality-floor gate (no mapping confirmed) ────────────
                # When running without a confirmed mapping (e.g. mapping_confirmed
                # bypass flag used), suppress MISSING_IN_PDF/EXCEL noise below
                # the materiality floor.  This prevents 400+ granular working
                # paper rows flooding the review queue.
                elif row.status in ("MISSING_IN_PDF", "MISSING_IN_EXCEL"):
                    raw_val = (va if row.status == "MISSING_IN_PDF" else vb) or 0.0
                    if abs(raw_val) < MISSING_MATERIALITY_FLOOR:
                        continue

                sev = "high" if row.status == "MISMATCH" else "medium"
                if row.status == "MISSING_IN_PDF":
                    desc = f"'{row.key_excel}': present in {label_a} only"
                    sev = "low"
                elif row.status == "MISSING_IN_EXCEL":
                    desc = f"'{row.key_pdf}': present in {label_b} only"
                    sev = "low"
                else:
                    desc = (f"'{row.key_excel}' ↔ '{row.key_pdf}': {label_a} £{va:,.2f} vs "
                            f"{label_b} £{vb:,.2f} "
                            f"(Δ £{delta:+,.2f}, {pct:.1f}%)"
                            if va is not None and vb is not None and delta is not None and pct is not None
                            else f"'{row.key_excel}' vs '{row.key_pdf}': value mismatch")
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
                        "match_score": _clean(row.match_score),
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
                    "method": "finbert_crossencoder",
                },
                "anomalies": all_anomalies,
            }
            logger.info(
                "_compare_and_raise [SOTA]: %d items_a, %d items_b → "
                "%d diff rows (%d shown, %d material)",
                len(items_a), len(items_b), len(diff_rows),
                len(all_anomalies), material,
            )
            # Build review points from anomalies
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
                    "value_a": str(anomaly.get("data", {}).get("value_a", "") or ""),
                    "value_b": str(anomaly.get("data", {}).get("value_b", "") or ""),
                    "delta": anomaly.get("data", {}).get("delta"),
                    "status": "open",
                    "severity": sev,
                })
            return review_points, compare_result

    except Exception as e:
        logger.warning("asgla_compare pipeline failed (%s) — falling back to sheet comparison", e)

    # ── FALLBACK PATH: sheet-by-sheet row comparison ──────────────────────────
    # Used when asgla_compare can't extract KV pairs (e.g. scanned PDFs)
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
    all_anomalies = []
    all_sheet_results = []
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
            sheet_anomalies = result.get("anomalies", [])
            all_anomalies.extend(sheet_anomalies)
            all_sheet_results.append({
                "sheet": sheet_name,
                "delta_count": result.get("delta_count", 0),
                "material_delta_count": result.get("material_delta_count", 0),
                "anomalies": sheet_anomalies,
            })
        except Exception as exc:
            logger.warning("Sheet compare failed [%s]: %s", sheet_name, exc)

    material = sum(1 for a in all_anomalies if a.get("severity") == "high")
    compare_result = {
        "summary": {"status": "DIFFERENCES NOTED" if all_anomalies else "CLEAN",
                    "label_a": label_a, "label_b": label_b,
                    "total_deltas": len(all_anomalies), "material_deltas": material,
                    "anomaly_count": len(all_anomalies),
                    "materiality_threshold_pct": materiality_pct,
                    "method": "sheet_fallback"},
        "anomalies": all_anomalies,
        "sheets": all_sheet_results,
    }

    review_points: List[Dict] = []
    for anomaly in compare_result.get("anomalies", []):
        sev_map = {"high": "high", "medium": "medium", "low": "low"}
        sev = sev_map.get(anomaly.get("severity", "medium"), "medium")
        review_points.append({
            "severity": sev,
            "category": "difference",
            "title": anomaly.get("description", "Difference detected")[:300],
            "description": anomaly.get("description", ""),
            "section": anomaly.get("sheet", "General"),
            "audit_ref": None,
            "field_name": anomaly.get("data", {}).get("field"),
            "value_a": str(anomaly.get("data", {}).get("value_a", ""))[:500],
            "value_b": str(anomaly.get("data", {}).get("value_b", ""))[:500],
            "delta": anomaly.get("data", {}).get("delta"),
            "status": "open",
        })

    if not review_points and compare_result.get("summary", {}).get("status") == "CLEAN":
        review_points.append({
            "severity": "low",
            "category": "difference",
            "title": "No material differences detected",
            "description": (
                f"Oversight analysis of {label_a} vs {label_b} found no material differences "
                f"above the {materiality_pct}% materiality threshold."
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

        # run_pipeline(files=[(name, bytes)], ...) is synchronous and returns a dict.
        result = run_pipeline(files=[(filename_a, Path(path_a).read_bytes())])
        pipeline_result = result or {}

        sev_map = {"error": "high", "warn": "medium", "warning": "medium"}

        # Validation rule violations -> review points. In fact mode ASGL-C infers
        # facts from KPIs at low confidence, so those violations are not real findings;
        # only row-mode validation is raised.
        _c_mode = ((pipeline_result.get("asgl_c") or {}).get("canonical") or {}).get("mode")
        _viol = [] if _c_mode == "fact" else (
            (pipeline_result.get("validation") or pipeline_result.get("validations") or {}).get("violations", []) or [])
        for v in _viol:
            detail = v.get("detail") or {}
            review_points.append({
                "severity": sev_map.get(str(v.get("level", "warn")).lower(), "medium"),
                "category": "policy",
                "title": str(v.get("rule", "Validation rule"))[:300],
                "description": str(detail)[:2000],
                "section": "General",
                "audit_ref": None,
                "field_name": None,
                "value_a": None,
                "value_b": None,
                "delta": None,
                "status": "open",
            })

        # ASGL-A analytic flags -> review points
        for f in (pipeline_result.get("asgl_a") or {}).get("flags", []) or []:
            review_points.append({
                "severity": str(f.get("severity", "Medium")).lower(),
                "category": "analytics",
                "title": str(f.get("title", "Flag"))[:300],
                "description": "; ".join(str(x) for x in (f.get("evidence") or []))[:2000],
                "section": "General",
                "audit_ref": None,
                "field_name": None,
                "value_a": None,
                "value_b": None,
                "delta": None,
                "status": "open",
            })

    except Exception as exc:
        logger.warning("Full pipeline failed, returning empty: %s", exc)
        pipeline_result = {"error": str(exc)}

    return review_points, pipeline_result


# ══════════════════════════════════════════════════════════════════════════════
# SIGN-OFF
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/engagements/{eng_id}/sign-off", summary="Partner sign-off")
async def sign_off(
    eng_id: int,
    req: SignOffRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: scoped to the caller's tenant.
    eng = db.query(Engagement).filter(Engagement.id == eng_id, Engagement.tenant_id == current_user.tenant_id).first()
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
        f"{eng.client_name} — {eng.period} has been signed off by {req.partner_name}.",
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


# ══════════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/engagements/{eng_id}/audit-trail", summary="Immutable audit trail")
async def audit_trail(
    eng_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    # SECURITY-FIX 2026-10-15: scoped to the caller's tenant.
    eng = db.query(Engagement).filter(Engagement.id == eng_id, Engagement.tenant_id == current_user.tenant_id).first()
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


# ══════════════════════════════════════════════════════════════════════════════
# REVIEW POINTS — status transitions
# ══════════════════════════════════════════════════════════════════════════════

def _get_point_or_404(pt_id: int, tenant_id: int, db: Session) -> ReviewPoint:
    # SECURITY-FIX 2026-10-15: join to Engagement so a review point can only
    # be resolved by a caller whose session tenant owns the parent engagement.
    pt = (
        db.query(ReviewPoint)
        .join(Engagement, Engagement.id == ReviewPoint.engagement_id)
        .filter(ReviewPoint.id == pt_id, Engagement.tenant_id == tenant_id)
        .first()
    )
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
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    pt = _get_point_or_404(pt_id, current_user.tenant_id, db)
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
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    pt = _get_point_or_404(pt_id, current_user.tenant_id, db)
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
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    pt = _get_point_or_404(pt_id, current_user.tenant_id, db)
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
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    pt = _get_point_or_404(pt_id, current_user.tenant_id, db)
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
            f"Escalation — {eng.client_name}",
            f"Review point escalated to {escalated_to}: {escalation_note[:100]}",
            severity="warning", engagement_id=pt.engagement_id,
            client_name=eng.client_name,
        )
    db.commit()
    db.refresh(pt)
    return JSONResponse(_point_to_dict(pt))
