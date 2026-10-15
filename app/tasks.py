from __future__ import annotations

import datetime as dt
import json
import os
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy.orm import Session

from app.worker import celery_app
from app.db.session import SessionLocal
from app.db.models import Job, Upload, Template
from app.storage import storage
from app.hardening import redact_error
from app.di_learning import load_priors
from app.di_review_queue import build_items
from app.asgl_engine.pipeline import run_pipeline, build_pdf
from app.usage import get_or_create_usage, Timer
from app.observability.job_events import append_event


def _now_z() -> str:
    return dt.datetime.utcnow().isoformat() + "Z"


@celery_app.task(name="app.tasks.run_job")
def run_job(job_id: int):
    """Execute an ASGL job in the Celery worker.

    Enhancements (v13.5+)
    - Progress events written to /data/tenants/<tenant>/jobs/<job>/events.jsonl
    - Lightweight progress snapshot stored in Job.result["progress"] for quick polling
    - Error bundle persisted as an artifact on failure
    """
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return

        # Idempotent guard: don't rerun completed jobs
        if job.status in ("succeeded", "failed"):
            return

        tenant_id = int(job.tenant_id)

        # progress snapshot throttling (DB commits are expensive)
        last_snapshot_t = 0.0

        def emit(event: str, message: str = "", progress: Optional[float] = None, meta: Optional[Dict[str, Any]] = None, *, snapshot: bool = False):
            nonlocal last_snapshot_t
            try:
                append_event(tenant_id=tenant_id, job_id=job.id, event=event, message=message, progress=progress, meta=meta or {})
            except Exception:
                pass

            if snapshot:
                now = time.time()
                if (now - last_snapshot_t) > 1.5 or (progress is not None and float(progress) >= 99.0):
                    try:
                        cur = job.result or {}
                        cur["progress"] = {
                            "ts": _now_z(),
                            "event": event,
                            "message": message,
                            "pct": float(progress) if progress is not None else None,
                        }
                        job.result = cur
                        db.commit()
                        last_snapshot_t = now
                    except Exception:
                        db.rollback()

        job.status = "running"
        job.started_at = dt.datetime.utcnow()
        db.commit()

        emit("job_started", "Job started", progress=0.0, snapshot=True)

        req = job.request or {}
        upload_ids: List[int] = list(req.get("upload_ids", []) or [])
        template_id = req.get("template_id")
        template_payload = None

        if template_id:
            t = db.query(Template).filter(Template.id == template_id, Template.tenant_id == tenant_id).first()
            if t:
                template_payload = {"id": t.id, "name": t.name, **(t.payload or {})}

        if not upload_ids:
            raise ValueError("upload_ids required")

        uploads = db.query(Upload).filter(Upload.id.in_(upload_ids), Upload.tenant_id == tenant_id).all()
        if len(uploads) != len(upload_ids):
            raise ValueError("invalid upload_ids for tenant")

        files: List[Tuple[str, bytes]] = []
        for u in uploads:
            files.append((u.filename, storage.get(u.storage_uri)))

        emit("inputs_loaded", f"Loaded {len(files)} file(s)", progress=4.0, meta={"filenames": [u.filename for u in uploads]}, snapshot=True)

        def progress_cb(rec: Dict[str, Any]):
            # Pipeline progress callback
            emit(
                str(rec.get("stage") or "pipeline"),
                str(rec.get("message") or ""),
                progress=rec.get("progress"),
                meta=rec.get("meta") or {},
                snapshot=True,
            )

        # Enrich pipeline options with tenant context + learning priors
        opts = req.get("options", {}) or {}
        try:
            opts = dict(opts)
        except Exception:
            opts = {}
        opts["tenant_id"] = int(tenant_id)
        try:
            opts["confidence_priors"] = load_priors(int(tenant_id))
        except Exception:
            opts["confidence_priors"] = None

        with Timer() as tmr:
            result = run_pipeline(
                files,
                opts,
                req.get("selection"),
                template_payload=template_payload,
                progress_cb=progress_cb,
            )

        emit("pipeline_complete", "Pipeline complete", progress=90.0, snapshot=True)

        pdf = build_pdf(result)
        emit("report_built", "PDF report built", progress=92.0, snapshot=True)

        report_uri = storage.put(
            f"tenants/{tenant_id}/jobs/{job.id}/report.pdf",
            pdf,
            "application/pdf",
        )
        result_uri = storage.put(
            f"tenants/{tenant_id}/jobs/{job.id}/result.json",
            json.dumps(result, ensure_ascii=False).encode("utf-8"),
            "application/json",
        )

        artifacts: Dict[str, Any] = {"report_pdf": report_uri, "result_json": result_uri}

        # Store canonical + review queue artifacts for API/console consumption
        try:
            canonical = (result or {}).get("asgl_c", {}).get("canonical") if isinstance((result or {}).get("asgl_c"), dict) else None
            if canonical:
                artifacts["canonical_json"] = storage.put(
                    f"tenants/{tenant_id}/jobs/{job.id}/canonical.json",
                    json.dumps(canonical, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                )
        except Exception:
            pass

        try:
            rq = (result or {}).get("review_queue") or []
            if rq:
                schema = None
                try:
                    schema = ((result or {}).get("asgl_c") or {}).get("canonical", {}).get("schema")
                except Exception:
                    schema = None
                rq_items = build_items(
                    rq,
                    tenant_id=int(tenant_id),
                    job_id=int(job.id),
                    schema_name=(schema or {}).get("name") if isinstance(schema, dict) else None,
                    schema_version=(schema or {}).get("version") if isinstance(schema, dict) else None,
                )
                artifacts["review_queue_json"] = storage.put(
                    f"tenants/{tenant_id}/jobs/{job.id}/review_queue.json",
                    json.dumps(rq_items, ensure_ascii=False, indent=2).encode("utf-8"),
                    "application/json",
                )
        except Exception:
            pass

        try:
            di_sum = ((result or {}).get("di") or {}).get("summary") if isinstance((result or {}).get("di"), dict) else None
            if di_sum:
                artifacts["di_summary_json"] = storage.put(
                    f"tenants/{tenant_id}/jobs/{job.id}/di_summary.json",
                    json.dumps(di_sum, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                )
        except Exception:
            pass


        # Optional compare artifacts (if produced by pipeline)
        artifacts_local = (result or {}).get("artifacts_local") or {}
        compare_pdf_path = artifacts_local.get("compare_pdf_path")
        compare_csv_path = artifacts_local.get("compare_csv_path")
        if compare_pdf_path and os.path.exists(compare_pdf_path):
            artifacts["compare_pdf"] = storage.put(
                f"tenants/{tenant_id}/jobs/{job.id}/compare.pdf",
                open(compare_pdf_path, "rb").read(),
                "application/pdf",
            )
        if compare_csv_path and os.path.exists(compare_csv_path):
            artifacts["compare_csv"] = storage.put(
                f"tenants/{tenant_id}/jobs/{job.id}/compare.csv",
                open(compare_csv_path, "rb").read(),
                "text/csv",
            )

        job.result = result
        job.artifacts = artifacts
        job.status = "succeeded"
        job.finished_at = dt.datetime.utcnow()
        db.commit()

        emit("job_succeeded", "Job succeeded", progress=100.0, meta={"artifacts": list(artifacts.keys())}, snapshot=True)

        # Usage metering: increment packs processed
        usage = get_or_create_usage(db, tenant_id)
        usage.packs_processed += 1
        usage.compute_seconds += int(getattr(tmr, "seconds", 0) or 0)
        db.commit()

    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass

        # best-effort job lookup again
        job = db.query(Job).filter(Job.id == job_id).first()
        tenant_id = int(job.tenant_id) if job else 0

        tb = redact_error(traceback.format_exc(), 6000)
        logger.exception("job_failed", job_id=job_id)

        err_uri = None
        try:
            err_uri = storage.put(
                f"tenants/{tenant_id}/jobs/{job_id}/error.txt",
                tb.encode("utf-8"),
                "text/plain",
            )
        except Exception:
            pass

        try:
            append_event(
                tenant_id=tenant_id,
                job_id=job_id,
                event="job_failed",
                message=redact_error(str(e)),
                progress=100.0,
                meta={"error_type": type(e).__name__, "error": redact_error(str(e)), "error_artifact": err_uri},
            )
        except Exception:
            pass

        if job:
            job.status = "failed"
            job.error = redact_error(f"{type(e).__name__}: {e}")
            job.finished_at = dt.datetime.utcnow()
            # store a minimal error envelope (avoid breaking clients that expect dict)
            cur = job.result or {}
            cur["progress"] = {"ts": _now_z(), "event": "job_failed", "message": redact_error(str(e)), "pct": 100.0}
            cur["error"] = {"type": type(e).__name__, "message": redact_error(str(e))}
            job.result = cur
            job.artifacts = (job.artifacts or {}) | ({"error_txt": err_uri} if err_uri else {})
            db.commit()
    finally:
        try:
            db.close()
        except Exception:
            pass
