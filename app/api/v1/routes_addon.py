"""Oversight Labs — v1 API missing routes addon.

Adds the endpoints the HTML dashboard calls that weren't in the initial
routes.py build. Drop this file alongside routes.py and add one line
to app/api/v1/__init__.py:

    from app.api.v1.routes_addon import router as addon_router

Then in main.py:
    app.include_router(addon_router, prefix="/api/v1")
"""
from __future__ import annotations

import datetime as dt
import uuid
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel

# Reuse the job store from routes.py
from app.api.v1.routes import _JOBS, _AUDIT_LOGS, _new_job, _update_job
from app.db.models import User
from app.web_auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v1-addon"])


# ── /version ────────────────────────────────────────────────────────────────

@router.get("/version", summary="API version info")
async def version() -> Dict[str, Any]:
    return {
        "service": "oversight-labs-api",
        "version": "1.0.0",
        "api": "v1",
        "build": "2026-05",
        "engines": ["pipeline", "phase1", "phase2", "flywheel", "governance", "di", "oade"],
        "timestamp": dt.datetime.utcnow().isoformat() + "Z",
    }


# ── /pipeline/jobs/{job_id}/result  (alias for /results) ─────────────────────

@router.get("/pipeline/jobs/{job_id}/result", summary="Get job result (alias)")
async def pipeline_job_result_alias(job_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Alias for /pipeline/jobs/{job_id}/results — supports both spellings."""
    job = _JOBS.get(job_id)
    # SECURITY-FIX 2026-10-15: a job belonging to another tenant looks like "not found".
    if not job or job.get("tenant_id") != str(current_user.tenant_id):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job["status"] in ("queued", "running"):
        raise HTTPException(status_code=425, detail=f"Job {job_id} still running ({job['status']})")
    result = job.get("results") or {}

    duration: Optional[float] = None
    if job.get("started_at") and job.get("completed_at"):
        try:
            t0 = dt.datetime.fromisoformat(job["started_at"].rstrip("Z"))
            t1 = dt.datetime.fromisoformat(job["completed_at"].rstrip("Z"))
            duration = (t1 - t0).total_seconds()
        except Exception:
            pass

    return {
        "job_id": job_id,
        "status": job["status"],
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "duration_seconds": duration,
        "tables": result.get("tables"),
        "validations": result.get("validations") or result.get("validation"),
        "asgl_a": result.get("asgl_a"),
        "asgl_c": result.get("asgl_c"),
        "di": result.get("di"),
        "artifacts": result.get("artifacts"),
        "report_path": result.get("report_path"),
        "error": job.get("error"),
    }


# ── /flywheel/train ──────────────────────────────────────────────────────────

class TrainRequest(BaseModel):
    vertical: str = "accountancy"
    model_id: str = "di_mapper"
    base_version: str = "v0"
    force: bool = False
    min_new_rows: int = 50
    # tenant_id is never accepted from the client — derived from the
    # authenticated session. SECURITY-FIX 2026-10-15.


@router.post("/flywheel/train", summary="Trigger flywheel training")
async def flywheel_train(req: TrainRequest, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    tenant_id = str(current_user.tenant_id)
    try:
        from app.flywheel.engine import FlywheelEngine
        fw = FlywheelEngine(
            tenant_id=tenant_id,
            client_id="default",
            run_id=str(uuid.uuid4()),
            vertical=req.vertical,
            logger=logger,
        )
        if req.force:
            job = fw.trainer.trigger(
                tenant_id=tenant_id,
                vertical=req.vertical,
                model_id=req.model_id,
                base_version=req.base_version,
                reason="forced_via_api",
                meta={"forced": True},
            )
            return {"triggered": True, "job": job.to_dict() if hasattr(job, "to_dict") else str(job)}
        else:
            result = fw.maybe_trigger_training(
                min_new_rows=req.min_new_rows,
                model_id=req.model_id,
                base_version=req.base_version,
            )
            return {
                "triggered": result is not None,
                "job": result,
                "message": "Training triggered" if result else f"Below threshold ({req.min_new_rows} rows required)",
            }
    except Exception as e:
        logger.exception("flywheel/train failed")
        raise HTTPException(status_code=500, detail=f"Training trigger failed: {e}")


# ── /flywheel/queue ──────────────────────────────────────────────────────────

@router.get("/flywheel/queue", summary="Get active learning review queue")
async def flywheel_queue(
    vertical: str = Query("accountancy"),
    kind: Optional[str] = Query(None, description="Filter by kind e.g. ANOMALY_REVIEW"),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never accepted from the client.
    tenant_id = str(current_user.tenant_id)
    try:
        from app.flywheel.queue import ActiveLearningQueue
        q = ActiveLearningQueue(logger=logger)
        # Real queue has stats() not list() - get all items from internal dict
        all_items = list(q._items.values())
        if tenant_id:
            all_items = [i for i in all_items if i.tenant_id == tenant_id]
        if kind:
            all_items = [i for i in all_items if i.kind == kind]
        items = [i.to_dict() for i in all_items[:limit]]
        stats = q.stats(tenant_id=tenant_id, vertical=vertical)
        return {"count": len(items), "items": items, "stats": stats}
    except Exception as e:
        logger.warning("flywheel/queue failed: %s", e)
        return {"count": 0, "items": [], "warning": str(e)}


# ── /di/review-queue ────────────────────────────────────────────────────────

@router.get("/di/review-queue", summary="Get DI review queue")
async def di_review_queue(
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Returns items in the DI review queue — mapping overrides, anomalies,
    rule violations — pending human review."""
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never accepted from the client.
    tenant_id = str(current_user.tenant_id)
    try:
        # Try the v5 review queue first
        from app.di.review_queue import get_review_queue
        items = get_review_queue(tenant_id=tenant_id, limit=limit)
        return {"count": len(items), "items": items, "tenant_id": tenant_id}
    except ImportError:
        pass

    try:
        # Fallback: use flywheel queue filtered for DI items
        from app.flywheel.queue import ActiveLearningQueue
        q = ActiveLearningQueue(logger=logger)
        all_items = q.list(tenant_id=tenant_id, vertical="accountancy")
        items = []
        for item in all_items[:limit]:
            d = item.to_dict() if hasattr(item, "to_dict") else item
            items.append(d)
        return {"count": len(items), "items": items, "tenant_id": tenant_id}
    except Exception as e:
        return {"count": 0, "items": [], "warning": str(e), "tenant_id": tenant_id}


@router.post("/di/review-queue/{item_id}/resolve", summary="Resolve a review queue item")
async def di_review_queue_resolve(
    item_id: str,
    confirmed: bool = Body(..., embed=True),
    notes: Optional[str] = Body(None, embed=True),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Approve or reject a review queue item. Records the decision in the
    flywheel dataset for active learning."""
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never accepted from the client.
    tenant_id = str(current_user.tenant_id)
    try:
        from app.di.review_queue import resolve_review_item
        result = resolve_review_item(
            item_id=item_id,
            confirmed=confirmed,
            tenant_id=tenant_id,
            notes=notes,
        )
        return {"ok": True, "item_id": item_id, "confirmed": confirmed, "result": result}
    except ImportError:
        pass

    # Fallback: log to flywheel
    try:
        from app.flywheel.engine import FlywheelEngine
        fw = FlywheelEngine(
            tenant_id=tenant_id,
            client_id="default",
            run_id=item_id,
            vertical="accountancy",
            logger=logger,
        )
        evt = fw.capture(
            type="reviewer_override",  # type: ignore[arg-type]
            actor="reviewer",
            confidence=1.0 if confirmed else 0.0,
            payload={"item_id": item_id, "confirmed": confirmed, "notes": notes},
        )
        return {"ok": True, "item_id": item_id, "confirmed": confirmed, "event": evt}
    except Exception as e:
        return {"ok": True, "item_id": item_id, "confirmed": confirmed, "warning": str(e)}


# ── /di/ml/status ────────────────────────────────────────────────────────────

@router.get("/di/ml/status", summary="DI ML model status")
async def di_ml_status(
    vertical: str = Query("accountancy"),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Status of trained ML models in the DI stack."""
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never accepted from the client.
    tenant_id = str(current_user.tenant_id)
    try:
        from app.flywheel.engine import FlywheelEngine
        fw = FlywheelEngine(
            tenant_id=tenant_id,
            client_id="default",
            run_id="status-probe",
            vertical=vertical,
            logger=logger,
        )
        h = fw.health()
        dataset = h.get("dataset") or {}
        training = h.get("recent_training") or []
        latest = training[0] if training else {}
        return {
            "tenant_id": tenant_id,
            "vertical": vertical,
            "model_ready": bool(latest),
            "dataset_rows": dataset.get("rows", 0),
            "latest_training_job": latest,
            "status": "trained" if latest else "untrained",
        }
    except Exception as e:
        return {
            "tenant_id": tenant_id,
            "vertical": vertical,
            "model_ready": False,
            "status": "unavailable",
            "warning": str(e),
        }


# ── /governance/export ────────────────────────────────────────────────────────

class ExportRequest(BaseModel):
    run_id: str
    format: str = "json"
    # tenant_id is never accepted from the client — derived from the
    # authenticated session. SECURITY-FIX 2026-10-15.


@router.post("/governance/export", summary="Export audit log as evidence pack")
async def governance_export(req: ExportRequest, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Export the audit log for a run as a JSON evidence pack.

    For PDF evidence packs, the full pipeline run generates these automatically
    via reportlab. This endpoint provides the structured JSON export for
    programmatic access and downstream reporting.
    """
    tenant_id = str(current_user.tenant_id)
    al = _AUDIT_LOGS.get(req.run_id)
    # SECURITY-FIX 2026-10-15: an audit log/job belonging to another tenant looks like "not found".
    if al is not None and str(getattr(al, "tenant_id", None)) != tenant_id:
        al = None
    if not al:
        # Try to find from job results
        job = _JOBS.get(req.run_id)
        if job and job.get("tenant_id") != tenant_id:
            job = None
        if job and job.get("results"):
            results = job["results"]
            return {
                "run_id": req.run_id,
                "tenant_id": tenant_id,
                "format": req.format,
                "exported_at": dt.datetime.utcnow().isoformat() + "Z",
                "event_count": 0,
                "artifacts": results.get("artifacts"),
                "report_path": results.get("report_path"),
                "warning": "Audit log not found — returning job artifacts only",
            }
        raise HTTPException(status_code=404, detail=f"No audit log for run_id {req.run_id}")

    summary = al.summary()
    return {
        "run_id": req.run_id,
        "tenant_id": tenant_id,
        "format": req.format,
        "exported_at": dt.datetime.utcnow().isoformat() + "Z",
        "event_count": len(al.events),
        "summary": summary,
        "events": al.events,
        "sha256": summary.get("events_sha256"),
    }
