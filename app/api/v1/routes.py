"""Oversight Labs — v1 REST API Routes.

This module exposes every major engine subsystem as REST endpoints:

    GET  /api/v1/health                       Health check + engine status
    POST /api/v1/pipeline/run                 Run full pipeline on uploaded files
    GET  /api/v1/pipeline/jobs/{job_id}       Get job status
    GET  /api/v1/pipeline/jobs/{job_id}/results  Get job results
    POST /api/v1/normalise                    Phase 1+2 schema mapping (sync)
    POST /api/v1/compare                      Compare two uploaded files
    POST /api/v1/columns/encode               Column signatures + embeddings
    GET  /api/v1/flywheel/health              Flywheel state
    POST /api/v1/flywheel/capture             Log a flywheel event
    POST /api/v1/flywheel/confirm             Confirm a mapping (active learning)
    GET  /api/v1/governance/audit/{run_id}    Get audit events for a run
    GET  /api/v1/governance/audit/{run_id}/summary  Get audit summary
    POST /api/v1/di/run                       Run DI v3 orchestrator (DAG)

Every route is fault-tolerant: if an engine module fails to import or run,
the route returns a clear error rather than crashing the application.
"""
from __future__ import annotations

import os
import io
import uuid
import json
import time
import logging
import tempfile
import traceback
import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter, UploadFile, File, Form, HTTPException,
    BackgroundTasks, Depends, Query, Body
)
from fastapi.responses import JSONResponse, FileResponse

from app.api.v1.schemas import (
    HealthResponse,
    JobStatus, JobRunResponse, JobResultsResponse,
    NormaliseRequest, NormaliseResponse, ColumnSignatureOut, TemplateMatch,
    CompareRequest, CompareResponse,
    ColumnEncodeRequest, ColumnEncodeResponse,
    FlywheelCaptureRequest, FlywheelConfirmRequest, FlywheelHealthResponse,
    AuditEventOut, AuditSummaryResponse,
    DIRunRequest, DIRunResponse,
    ErrorResponse,
)
from app.db.models import User
from app.web_auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v1"])


# ═══════════════════════════════════════════════════════════════════════════
# In-memory job store
# ═══════════════════════════════════════════════════════════════════════════
#
# Lightweight job tracker for sync/async pipeline runs.  For production, swap
# this for the SQLAlchemy Job model in app/db/models.py — but the in-memory
# version is enough to demo end-to-end and prove the pipeline works through
# the API.  Each entry is keyed by job_id (uuid4).
#

from app import jobstore  # noqa: E402  (jobs live in the database so every worker sees them)

_AUDIT_LOGS: Dict[str, Any] = {}  # run_id -> AuditLog instance


class _JobsView:
    """Read only dict like view over the shared job store, for modules that still import _JOBS."""

    def get(self, job_id, default=None):
        return jobstore.get(str(job_id)) or default

    def __getitem__(self, job_id):
        j = jobstore.get(str(job_id))
        if j is None:
            raise KeyError(job_id)
        return j

    def __contains__(self, job_id):
        return jobstore.get(str(job_id)) is not None


_JOBS = _JobsView()


def _new_job(tenant_id: str = "default") -> str:
    # SECURITY-FIX 2026-10-15: ownership (tenant_id) is stored with the job for status/results/list scoping
    return jobstore.create(tenant_id)


def _update_job(job_id: str, **kwargs: Any) -> None:
    jobstore.update(job_id, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# Engine import guards
# ═══════════════════════════════════════════════════════════════════════════
#
# Each engine is imported lazily inside the route handler.  If an import fails
# we degrade gracefully and return a useful error message rather than 500.
#

def _check_engines() -> Dict[str, str]:
    """Return availability status for every major engine.  Used by /health."""
    engines: Dict[str, str] = {}

    try:
        from app.asgl_engine.pipeline import run_pipeline  # noqa: F401
        engines["pipeline"] = "available"
    except Exception as e:
        engines["pipeline"] = f"unavailable: {type(e).__name__}: {str(e)[:80]}"

    try:
        from app.services.phase1_semantic_mapper import phase1_select_template_and_mapping  # noqa: F401
        engines["phase1_mapper"] = "available"
    except Exception as e:
        engines["phase1_mapper"] = f"unavailable: {type(e).__name__}: {str(e)[:80]}"

    try:
        from app.services.phase2_column_encoder import extract_column_signatures_from_preview  # noqa: F401
        engines["phase2_encoder"] = "available"
    except Exception as e:
        engines["phase2_encoder"] = f"unavailable: {type(e).__name__}: {str(e)[:80]}"

    try:
        from app.flywheel.engine import FlywheelEngine  # noqa: F401
        engines["flywheel"] = "available"
    except Exception as e:
        engines["flywheel"] = f"unavailable: {type(e).__name__}: {str(e)[:80]}"

    try:
        from app.governance.audit import AuditLog  # noqa: F401
        engines["governance"] = "available"
    except Exception as e:
        engines["governance"] = f"unavailable: {type(e).__name__}: {str(e)[:80]}"

    try:
        from app.di.orchestrator_v3 import run_di_v3  # noqa: F401
        engines["di_orchestrator"] = "available"
    except Exception as e:
        engines["di_orchestrator"] = f"unavailable: {type(e).__name__}: {str(e)[:80]}"

    try:
        from oversight_c import OversightCNormaliser  # noqa: F401
        engines["oversight_c"] = "available"
    except Exception as e:
        engines["oversight_c"] = "not installed (ASGL-C fallback in use)"

    return engines


# ═══════════════════════════════════════════════════════════════════════════
# /health
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/health", response_model=HealthResponse, summary="Health check")
@router.head("/health", include_in_schema=False)
async def health() -> HealthResponse:
    """Returns API health and engine availability.

    Supports both GET and HEAD — the desktop renderer's startup probe uses
    HEAD which was previously returning 405.
    """
    return HealthResponse(
        ok=True,
        service="oversight-labs-api",
        version="13.5.0",
        timestamp=dt.datetime.utcnow().isoformat() + "Z",
        engines=_check_engines(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# /pipeline/run  — main entry point for end-to-end analysis
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/pipeline/run", response_model=JobRunResponse, summary="Run full pipeline on uploaded files")
async def pipeline_run(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(..., description="One or more files (xlsx, xls, xlsm, pdf)"),
    vertical: Optional[str] = Form(None, description="financial | accountancy | pensions | medical | defence"),
    pack_id: Optional[str] = Form(None),
    template_id: Optional[int] = Form(None),
    enable_shap: bool = Form(True),
    enable_llm_summary: bool = Form(False),
    sync: bool = Form(False, description="If true, run synchronously and return full results"),
    client_id: str = Form("default"),
    current_user: User = Depends(get_current_user),
) -> JobRunResponse:
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never accepted from the client.
    tenant_id = str(current_user.tenant_id)
    """Accept one or more uploaded files and run the full Oversight pipeline:

      1. Spreadsheet ingestion (AdvancedSpreadsheetIngestor)
      2. Template-based mapping (Phase 1 + 2)
      3. Canonical normalisation (ASGL-C)
      4. Validation pack execution
      5. ASGL-A analysis engine
      6. Decision intelligence (DI v5)
      7. PDF/spreadsheet comparison (if both supplied)
      8. SHAP explainability + LLM summary
      9. PDF evidence pack assembly

    Returns a job_id immediately.  In sync mode (sync=true) blocks until
    complete and returns the full results.  In async mode, poll
    GET /pipeline/jobs/{job_id} for status.
    """
    # ── Read files into memory ────────────────────────────────────────────
    from app.config import settings as _st
    if len(files) > 20:
        raise HTTPException(status_code=413, detail="Too many files (maximum 20)")
    _ok = _st.allowed_upload_exts_set
    for f in files:
        _ext = os.path.splitext(f.filename or "")[1].lower()
        if _ok and _ext not in _ok:
            raise HTTPException(status_code=415, detail=f"File type {_ext or '(none)'} is not allowed")
    file_payloads: List = []
    for f in files:
        content = await f.read()
        file_payloads.append((f.filename or "unnamed", content))
        await f.seek(0)

    if not file_payloads:
        raise HTTPException(status_code=400, detail="No files provided")

    # ── Create job ────────────────────────────────────────────────────────
    job_id = _new_job(tenant_id=tenant_id)
    run_id = job_id  # use same id for audit/governance correlation

    # ── Initialise audit log ──────────────────────────────────────────────
    audit_log = None
    try:
        from app.governance.audit import AuditLog
        audit_log = AuditLog(tenant_id=tenant_id, client_id=client_id, run_id=run_id, logger=logger)
        audit_log.emit(
            "pipeline.run.requested",
            f"Pipeline run requested for {len(file_payloads)} file(s)",
            actor="api_user",
            meta={
                "filenames": [n for n, _ in file_payloads],
                "vertical": vertical,
                "pack_id": pack_id,
                "template_id": template_id,
            },
        )
        _AUDIT_LOGS[run_id] = audit_log
    except Exception as e:
        logger.warning("Audit log init failed: %s", e)

    # ── Initialise flywheel ───────────────────────────────────────────────
    flywheel = None
    try:
        from app.flywheel.engine import FlywheelEngine
        flywheel = FlywheelEngine(
            tenant_id=tenant_id,
            client_id=client_id,
            run_id=run_id,
            vertical=vertical or "accountancy",
            logger=logger,
        )
    except Exception as e:
        logger.warning("Flywheel init failed: %s", e)

    # ── Run pipeline ──────────────────────────────────────────────────────
    options: Dict[str, Any] = {
        "vertical": vertical,
        "pack_id": pack_id,
        "enable_shap": enable_shap,
        "enable_llm_summary": enable_llm_summary,
        "max_tables": 3,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "run_id": run_id,
    }

    template_payload = None
    if template_id is not None:
        try:
            from app.db.session import SessionLocal
            from app.db.models import Template
            db = SessionLocal()
            try:
                t = db.query(Template).filter(Template.id == template_id).first()
                if t and getattr(t, "payload", None):
                    template_payload = t.payload if isinstance(t.payload, dict) else json.loads(t.payload)
            finally:
                db.close()
        except Exception as e:
            logger.warning("Template fetch failed: %s", e)

    def progress_cb(event: Dict[str, Any]) -> None:
        _update_job(
            job_id,
            stage=event.get("stage"),
            progress=event.get("progress"),
            message=event.get("message"),
        )
        if audit_log:
            try:
                audit_log.emit(
                    f"pipeline.{event.get('stage', 'progress')}",
                    str(event.get("message", "")),
                    actor="pipeline",
                    severity="INFO",
                    meta=event.get("meta", {}),
                )
            except Exception:
                pass

    def execute_pipeline() -> None:
        _update_job(
            job_id,
            status="running",
            started_at=dt.datetime.utcnow().isoformat() + "Z",
        )
        try:
            from app.asgl_engine.pipeline import run_pipeline
            result = run_pipeline(
                files=file_payloads,
                options=options,
                selection=None,
                template_payload=template_payload,
                progress_cb=progress_cb,
            )
            _update_job(
                job_id,
                status="completed",
                completed_at=dt.datetime.utcnow().isoformat() + "Z",
                progress=100.0,
                stage="complete",
                message="Pipeline completed successfully",
                results=result,
            )
            if audit_log:
                audit_log.emit(
                    "pipeline.run.completed",
                    "Pipeline run completed successfully",
                    actor="pipeline",
                )
            if flywheel:
                try:
                    flywheel.capture(
                        type="pipeline_completed",  # type: ignore[arg-type]
                        actor="pipeline",
                        confidence=1.0,
                        payload={"job_id": job_id, "filenames": [n for n, _ in file_payloads]},
                    )
                except Exception:
                    pass
        except Exception as e:
            tb = traceback.format_exc()
            logger.exception("Pipeline run failed for job %s", job_id)
            _update_job(
                job_id,
                status="failed",
                completed_at=dt.datetime.utcnow().isoformat() + "Z",
                error=f"{type(e).__name__}: {e}",
                message="Pipeline failed",
            )
            if audit_log:
                audit_log.emit(
                    "pipeline.run.failed",
                    f"Pipeline failed: {e}",
                    severity="ERROR",
                    actor="pipeline",
                    meta={"traceback": tb[-2000:]},
                )

    if sync:
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(execute_pipeline)
        job = jobstore.get(job_id) or {}
        return JobRunResponse(
            job_id=job_id,
            status=job["status"],
            message=job.get("message") or "Pipeline run complete",
            artifacts=(job.get("results") or {}).get("artifacts"),
        )
    else:
        background_tasks.add_task(execute_pipeline)
        return JobRunResponse(
            job_id=job_id,
            status="queued",
            message="Pipeline queued for background execution. Poll /api/v1/pipeline/jobs/{job_id}",
        )


@router.get("/pipeline/jobs/{job_id}", response_model=JobStatus, summary="Get job status")
async def pipeline_job_status(job_id: str, current_user: User = Depends(get_current_user)) -> JobStatus:
    job = jobstore.get(job_id)
    # SECURITY-FIX 2026-10-15: a job belonging to another tenant looks like "not found".
    if not job or job.get("tenant_id") != str(current_user.tenant_id):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobStatus(
        job_id=job["job_id"],
        status=job["status"],
        stage=job.get("stage"),
        progress=job.get("progress"),
        message=job.get("message"),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        error=job.get("error"),
    )


@router.get("/pipeline/jobs/{job_id}/results", response_model=JobResultsResponse, summary="Get job results")
async def pipeline_job_results(job_id: str, current_user: User = Depends(get_current_user)) -> JobResultsResponse:
    job = jobstore.get(job_id)
    # SECURITY-FIX 2026-10-15: a job belonging to another tenant looks like "not found".
    if not job or job.get("tenant_id") != str(current_user.tenant_id):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job["status"] in ("queued", "running"):
        raise HTTPException(status_code=425, detail=f"Job {job_id} still running")
    result = job.get("results") or {}

    duration: Optional[float] = None
    if job.get("started_at") and job.get("completed_at"):
        try:
            t0 = dt.datetime.fromisoformat(job["started_at"].rstrip("Z"))
            t1 = dt.datetime.fromisoformat(job["completed_at"].rstrip("Z"))
            duration = (t1 - t0).total_seconds()
        except Exception:
            pass

    return JobResultsResponse(
        job_id=job_id,
        status=job["status"],
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        duration_seconds=duration,
        tables=result.get("tables"),
        validations=result.get("validations") or result.get("validation"),
        asgl_a=result.get("asgl_a"),
        asgl_c=result.get("asgl_c"),
        di=result.get("di") or result.get("decision_intelligence"),
        artifacts=result.get("artifacts"),
        report_path=result.get("report_path"),
        error=job.get("error"),
    )


@router.get("/pipeline/jobs", summary="List recent jobs")
async def pipeline_list_jobs(
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    # SECURITY-FIX 2026-10-15: previously returned every tenant's jobs unfiltered.
    items = jobstore.list_for_tenant(str(current_user.tenant_id), limit=limit)
    return {"count": len(items), "items": items}


# ═══════════════════════════════════════════════════════════════════════════
# /normalise — Phase 1 + Phase 2 mapping (sync)
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/normalise", response_model=NormaliseResponse, summary="Schema mapping (Phase 1 + Phase 2)")
async def normalise(req: NormaliseRequest) -> NormaliseResponse:
    """Run schema mapping on a column preview WITHOUT executing the full
    pipeline.  Useful for:

      - "What canonical fields does this dataset contain?"
      - Pre-mapping confidence assessment before triggering a pipeline run
      - Building review UI workflows

    Returns column signatures, embeddings dimension, retrieved templates,
    and (if enabled) an LLM-proposed field selection mapping.
    """
    t0 = time.time()

    # ── Phase 2: column signatures ─────────────────────────────────────────
    try:
        from app.services.phase2_column_encoder import (
            extract_column_signatures_from_preview,
            embed_column_signatures,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"phase2_encoder unavailable: {e}")

    preview_dict = {
        "headers": req.preview.headers,
        "sample_values": req.preview.sample_values,
        "sheet_names": req.preview.sheet_names or [],
    }
    try:
        sigs = extract_column_signatures_from_preview(preview_dict, workbook_name=req.workbook_name)
        embs = embed_column_signatures(sigs)
        emb_dim = len(embs[0]) if embs else 0
    except Exception as e:
        logger.exception("Phase 2 encode failed")
        raise HTTPException(status_code=500, detail=f"Column encoding failed: {e}")

    column_out = [
        ColumnSignatureOut(
            header=s.header,
            inferred_type=s.inferred_type or "unknown",
            position=s.position or 0,
            sample_values=s.sample_values or [],
        )
        for s in sigs
    ]

    # ── Phase 1: template retrieval ────────────────────────────────────────
    retrieved: List[TemplateMatch] = []
    selected_template_id: Optional[int] = None
    field_selections: Optional[Dict[str, Any]] = None

    try:
        from app.services.phase1_semantic_mapper import (
            phase1_select_template_and_mapping,
            TemplateDoc,
        )

        # Load available templates from DB
        templates: List[TemplateDoc] = []
        try:
            from app.db.session import SessionLocal
            from app.db.models import Template
            db = SessionLocal()
            try:
                rows = db.query(Template).limit(500).all()
                for r in rows:
                    payload = r.payload if isinstance(r.payload, dict) else (
                        json.loads(r.payload) if r.payload else {}
                    )
                    templates.append(TemplateDoc(
                        id=getattr(r, "id", 0),
                        name=getattr(r, "name", "") or "",
                        description=getattr(r, "description", "") or "",
                        payload=payload,
                    ))
            finally:
                db.close()
        except Exception as e:
            logger.warning("Template DB fetch failed (continuing with empty list): %s", e)

        # Enable LLM if requested
        if req.use_llm:
            os.environ["ASGL_ENABLE_LLM"] = "1"

        phase1_out = phase1_select_template_and_mapping(
            templates=templates,
            meta={"filename": req.workbook_name, "vertical": req.vertical},
            preview=preview_dict,
            top_k=req.top_k,
        )

        for t in phase1_out.get("retrieved", []):
            retrieved.append(TemplateMatch(id=t["id"], name=t["name"], score=t["score"]))
        selected_template_id = phase1_out.get("selected_template_id")
        field_selections = phase1_out.get("field_selections")
    except Exception as e:
        logger.warning("Phase 1 retrieval failed: %s", e)

    # ── Confidence summary ────────────────────────────────────────────────
    type_counts: Dict[str, int] = {}
    for s in sigs:
        t = s.inferred_type or "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1

    confidence_summary = {
        "column_count": len(sigs),
        "type_distribution": type_counts,
        "best_template_score": (retrieved[0].score if retrieved else None),
        "llm_proposed_mapping": field_selections is not None,
    }

    return NormaliseResponse(
        workbook_name=req.workbook_name,
        columns=column_out,
        embeddings_dim=emb_dim,
        retrieved_templates=retrieved,
        selected_template_id=selected_template_id,
        field_selections=field_selections,
        confidence_summary=confidence_summary,
        latency_ms=int((time.time() - t0) * 1000),
    )


# ═══════════════════════════════════════════════════════════════════════════
# /compare — REMOVED FROM THIS ROUTER
# Handled exclusively by app/api/v1/compare_routes.py
# mounted at /api/v1/compare via main.py _mount(compare_router)
# ═══════════════════════════════════════════════════════════════════════════
# (endpoint deleted — compare_routes.py is canonical)

# ═══════════════════════════════════════════════════════════════════════════
# /columns/encode — column signatures + embeddings
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/columns/encode", response_model=ColumnEncodeResponse, summary="Encode columns to signatures + embeddings")
async def columns_encode(req: ColumnEncodeRequest) -> ColumnEncodeResponse:
    """Extract column signatures and (optionally) embedding vectors.

    This is the lower-level mapping primitive — useful for building custom
    mapping UIs, debugging Phase 2, or training column rankers.
    """
    try:
        from app.services.phase2_column_encoder import (
            extract_column_signatures_from_preview,
            embed_column_signatures,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"phase2_encoder unavailable: {e}")

    preview_dict = {
        "headers": req.preview.headers,
        "sample_values": req.preview.sample_values,
        "sheet_names": req.preview.sheet_names or [],
    }
    sigs = extract_column_signatures_from_preview(preview_dict, workbook_name=req.workbook_name)
    embs = embed_column_signatures(sigs) if req.return_embeddings else None
    emb_dim = (len(embs[0]) if embs else 0) if req.return_embeddings else 0

    return ColumnEncodeResponse(
        workbook_name=req.workbook_name,
        signatures=[
            ColumnSignatureOut(
                header=s.header,
                inferred_type=s.inferred_type or "unknown",
                position=s.position or 0,
                sample_values=s.sample_values or [],
            )
            for s in sigs
        ],
        embeddings=embs,
        embedding_dim=emb_dim,
        inferred_types={s.header: (s.inferred_type or "unknown") for s in sigs},
    )


# ═══════════════════════════════════════════════════════════════════════════
# /flywheel/* — active learning
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/flywheel/health", response_model=FlywheelHealthResponse, summary="Flywheel health and stats")
async def flywheel_health(
    client_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    vertical: str = Query("accountancy"),
    current_user: User = Depends(get_current_user),
) -> FlywheelHealthResponse:
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never accepted from the client.
    tenant_id = str(current_user.tenant_id)
    try:
        from app.flywheel.engine import FlywheelEngine
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"flywheel unavailable: {e}")

    fw = FlywheelEngine(
        tenant_id=tenant_id,
        client_id=client_id or "default",
        run_id=run_id or "health-probe",
        vertical=vertical,
        logger=logger,
    )
    h = fw.health()
    return FlywheelHealthResponse(
        tenant_id=tenant_id,
        client_id=client_id,
        run_id=run_id,
        vertical=vertical,
        dataset=h.get("dataset") or {},
        queue=h.get("queue") or {},
        recent_training=h.get("recent_training") or [],
    )


@router.post("/flywheel/capture", summary="Log a flywheel event")
async def flywheel_capture(
    req: FlywheelCaptureRequest,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    try:
        from app.flywheel.engine import FlywheelEngine
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"flywheel unavailable: {e}")

    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never from the request body.
    fw = FlywheelEngine(
        tenant_id=str(current_user.tenant_id),
        client_id=req.client_id,
        run_id=req.run_id,
        vertical=req.vertical,
        logger=logger,
    )
    evt = fw.capture(
        type=req.event_type,  # type: ignore[arg-type]
        actor=req.actor,
        confidence=req.confidence,
        payload=req.payload,
        meta=req.meta,
    )

    # Maybe queue active learning
    queued = fw.maybe_queue_active_learning(
        kind=req.event_type,
        confidence=req.confidence,
        payload=req.payload,
    )

    return {
        "event": evt,
        "queued_for_review": queued is not None,
        "queue_item": queued,
    }


@router.post("/flywheel/confirm", summary="Confirm a mapping (active learning)")
async def flywheel_confirm(
    req: FlywheelConfirmRequest,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Confirm a canonical field mapping decision.

    This logs a supervised (column_signature → canonical_field) pair via the
    Phase 2 dataset logger and emits a flywheel event.  The accumulated
    confirmed mappings are what the Phase 2 ranker trains on.
    """
    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never from the request body.
    tenant_id = str(current_user.tenant_id)
    # Phase 2 dataset log
    try:
        from app.services.phase2_column_encoder import log_mapping_example
        preview_dict = {
            "headers": req.preview.headers,
            "sample_values": req.preview.sample_values,
            "sheet_names": req.preview.sheet_names or [],
        }
        log_mapping_example(
            tenant_id=tenant_id,
            workbook_name=req.workbook_name,
            preview=preview_dict,
            field_selections=req.field_selections,
            template_id=req.template_id,
            job_id=req.job_id,
        )
    except Exception as e:
        logger.warning("log_mapping_example failed: %s", e)

    # Flywheel capture
    try:
        from app.flywheel.engine import FlywheelEngine
        fw = FlywheelEngine(
            tenant_id=tenant_id,
            client_id=req.client_id,
            run_id=req.run_id,
            vertical=req.vertical,
            logger=logger,
        )
        evt = fw.capture(
            type="mapping_confirmed",  # type: ignore[arg-type]
            actor="reviewer",
            confidence=1.0,
            payload={
                "workbook_name": req.workbook_name,
                "field_selections": req.field_selections,
                "template_id": req.template_id,
            },
        )
        training_job = fw.maybe_trigger_training()
        return {
            "ok": True,
            "confirmed_count": len(req.field_selections),
            "event": evt,
            "training_triggered": training_job is not None,
            "training_job": training_job,
        }
    except Exception as e:
        return {"ok": True, "confirmed_count": len(req.field_selections), "warning": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# /governance/audit
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/governance/audit/{run_id}", summary="Audit events for a run")
async def audit_events(run_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    al = _AUDIT_LOGS.get(run_id)
    # SECURITY-FIX 2026-10-15: an audit log belonging to another tenant looks like "not found".
    if not al or str(getattr(al, "tenant_id", None)) != str(current_user.tenant_id):
        raise HTTPException(status_code=404, detail=f"No audit log for run_id {run_id}")
    return {"run_id": run_id, "event_count": len(al.events), "events": al.events}


@router.get(
    "/governance/audit/{run_id}/summary",
    response_model=AuditSummaryResponse,
    summary="Audit summary for a run",
)
async def audit_summary(run_id: str, current_user: User = Depends(get_current_user)) -> AuditSummaryResponse:
    al = _AUDIT_LOGS.get(run_id)
    # SECURITY-FIX 2026-10-15: an audit log belonging to another tenant looks like "not found".
    if not al or str(getattr(al, "tenant_id", None)) != str(current_user.tenant_id):
        raise HTTPException(status_code=404, detail=f"No audit log for run_id {run_id}")
    summary = al.summary()
    return AuditSummaryResponse(
        schema=summary.get("schema", "asgl.governance.audit_log.v1"),
        tenant_id=summary["tenant_id"],
        client_id=summary["client_id"],
        run_id=summary["run_id"],
        generated_at=summary["generated_at"],
        event_count=summary["event_count"],
        severity_counts=summary["severity_counts"],
        events_sha256=summary["events_sha256"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# /di/run — Decision Intelligence v3 DAG
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/di/run", response_model=DIRunResponse, summary="Run DI v3 DAG orchestrator")
async def di_run(req: DIRunRequest, current_user: User = Depends(get_current_user)) -> DIRunResponse:
    try:
        from app.di.orchestrator_v3 import run_di_v3
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"di orchestrator unavailable: {e}")

    # SECURITY-FIX 2026-10-15: tenant_id is derived from the session, never from the request body.
    tenant_id = str(current_user.tenant_id)
    ctx: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "job_id": req.job_id,
        "schema_domain": req.schema_domain or "",
        "schema_id": req.schema_id or "",
        "schema_version": req.schema_version or "",
        "spreadsheet_path": req.spreadsheet_path,
        "pdf_path": req.pdf_path,
        "mapping": req.mapping or {},
        "previous_mapping_fp": req.previous_mapping_fp or "",
    }
    try:
        result = run_di_v3(ctx)
    except Exception as e:
        logger.exception("DI v3 failed")
        raise HTTPException(status_code=500, detail=f"DI v3 execution failed: {e}")

    factbundle: Dict[str, Any] = {}
    drift: Dict[str, Any] = {}
    tables: Dict[str, Any] = {}
    node_results: List[Dict[str, Any]] = []
    warnings: List[str] = []

    if isinstance(result, dict):
        for k, v in result.items():
            if isinstance(v, dict) and "factbundle" in v:
                factbundle = v["factbundle"]
            elif isinstance(v, dict) and "drift" in v:
                drift = v.get("drift") or {}
            elif isinstance(v, dict) and "tables" in v:
                tables = v.get("tables") or {}
            node_results.append({"node": k, "result": v if isinstance(v, dict) else {"value": v}})

    return DIRunResponse(
        tenant_id=tenant_id,
        job_id=req.job_id,
        factbundle=factbundle,
        drift=drift,
        tables=tables,
        node_results=node_results,
        warnings=warnings,
    )


# ═══════════════════════════════════════════════════════════════════════════
# /ping — simple liveness probe (no engine load, no DB)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/ping", summary="Simple liveness probe")
@router.head("/ping", include_in_schema=False)
async def ping() -> Dict[str, Any]:
    return {"ok": True, "ts": dt.datetime.utcnow().isoformat() + "Z"}