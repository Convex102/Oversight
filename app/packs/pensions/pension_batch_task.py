"""
OVERSIGHT-P — Batch Task + API Patch
======================================
Two components in one file:

PART A: Celery task for async batch processing
  pension_batch_run — Celery task wrapping the full pipeline
  Progress events feed the existing job_events system.

PART B: API endpoint patch
  Additional endpoints to add to app/api/pensions_api.py:
    POST /api/v1/pensions/batch-upload  — accepts files, queues Celery task
    GET  /api/v1/pensions/batch/{run_id} — poll job status + results
    POST /api/v1/pensions/ingest-record  — sync single-record ingestion

These patch into the existing pensions_api.py router.
Wire by importing and including in app/main.py alongside existing pensions router.
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════════
# PART A: CELERY TASK
# ══════════════════════════════════════════════════════════════════════════════

import logging
import os
import sys
import json
import time
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def run_pension_batch_pipeline(
    scheme_id: str,
    file_paths: List[str],
    run_id: str,
    scheme_rules_dict: Optional[Dict] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full Oversight-P pipeline for a batch of pension documents.

    Can be called directly (sync) or wrapped in a Celery task.
    Returns the complete PensionReport as a dict.

    Args:
        scheme_id:         Scheme identifier
        file_paths:        List of paths to uploaded files
        run_id:            Unique run identifier
        scheme_rules_dict: Optional override of SchemeRules defaults
        output_dir:        Where to write PDF evidence pack

    Pipeline:
        1. Mass ingest all files → List[IngestedRecord]
        2. Build canonical member list
        3. Run anomaly detection (12 checks)
        4. Build report (SHAP + LLM narrative + PDF)
        5. Return JSON-serialisable report dict
    """
    t0 = time.time()
    logger.info("pension_batch_pipeline START: scheme=%s run=%s files=%d",
                scheme_id, run_id, len(file_paths))

    # ── Add paths ─────────────────────────────────────────────────────────────
    repo_root = str(Path(__file__).parent.parent.parent.parent)
    for p in [repo_root, str(Path(__file__).parent)]:
        if p not in sys.path:
            sys.path.insert(0, p)

    # ── Stage 1: Mass ingestion ───────────────────────────────────────────────
    try:
        try:
            from app.packs.pensions.pension_mass_ingestor import PensionMassIngestor
        except ImportError:
            from pension_mass_ingestor import PensionMassIngestor
        ingestor = PensionMassIngestor()
        batch_result = ingestor.ingest_batch(file_paths, scheme_id=scheme_id, run_id=run_id)
        logger.info("Ingestion: %d records (%d ok, %d failed)",
                    batch_result.total_records,
                    batch_result.successful_records,
                    batch_result.failed_records)
    except Exception as e:
        logger.exception("Mass ingestion failed: %s", e)
        return {"error": f"Ingestion failed: {e}", "run_id": run_id}

    # ── Stage 2: Build canonical member list ──────────────────────────────────
    members = []
    for record in batch_result.records:
        if record.error:
            continue
        # Derive age and service years if dates available
        member_dict = dict(record.canonical)
        member_dict["member_id"] = record.member_id or member_dict.get("member_id", "UNKNOWN")
        member_dict["source_document"] = record.source_file
        member_dict["extraction_strategy"] = record.extraction_strategy
        member_dict["mapping_confidence"] = record.mapping_confidence

        # Derive age from DOB
        if member_dict.get("date_of_birth") and not member_dict.get("age"):
            try:
                import datetime
                dob_str = str(member_dict["date_of_birth"])
                for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                    try:
                        dob = datetime.datetime.strptime(dob_str, fmt)
                        age = (datetime.datetime.now() - dob).days / 365.25
                        member_dict["age"] = round(age, 1)
                        break
                    except ValueError:
                        pass
            except Exception:
                pass

        # Derive service years from join date
        if member_dict.get("scheme_join_date") and not member_dict.get("service_years"):
            try:
                import datetime
                join_str = str(member_dict["scheme_join_date"])
                for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                    try:
                        join = datetime.datetime.strptime(join_str, fmt)
                        svc = (datetime.datetime.now() - join).days / 365.25
                        member_dict["service_years"] = round(svc, 1)
                        break
                    except ValueError:
                        pass
            except Exception:
                pass

        members.append(member_dict)

    logger.info("Canonical members built: %d", len(members))

    # ── Stage 3: Anomaly detection ────────────────────────────────────────────
    try:
        try:
            from app.packs.pensions.pension_anomaly_engine import PensionAnomalyEngine, SchemeRules
        except ImportError:
            from pension_anomaly_engine import PensionAnomalyEngine, SchemeRules
        rules = SchemeRules(**scheme_rules_dict) if scheme_rules_dict else None
        engine = PensionAnomalyEngine(scheme_rules=rules)
        flags, population_analytics = engine.detect_batch(members)
        logger.info("Anomaly detection: %d flags", len(flags))
    except Exception as e:
        logger.exception("Anomaly detection failed: %s", e)
        flags, population_analytics = [], {}

    # ── Stage 4: Report ───────────────────────────────────────────────────────
    try:
        try:
            from app.packs.pensions.pension_report_builder import PensionReportBuilder
        except ImportError:
            from pension_report_builder import PensionReportBuilder
        builder = PensionReportBuilder()
        report = builder.build(
            scheme_id=scheme_id,
            run_id=run_id,
            members=members,
            flags=flags,
            population_analytics=population_analytics,
            output_dir=output_dir,
        )
        result = builder.to_dict(report)
    except Exception as e:
        logger.exception("Report build failed: %s", e)
        result = {
            "scheme_id": scheme_id,
            "run_id": run_id,
            "error": f"Report build failed: {e}",
            "total_members": len(members),
            "total_flags": len(flags),
        }

    elapsed = time.time() - t0
    result["duration_seconds"] = round(elapsed, 3)
    result["ingestion_summary"] = {
        "total_files": len(file_paths),
        "total_records": batch_result.total_records,
        "successful": batch_result.successful_records,
        "failed": batch_result.failed_records,
        "low_confidence": batch_result.low_confidence_records,
        "strategy_distribution": batch_result.strategy_distribution,
    }

    logger.info("pension_batch_pipeline DONE: %d members, %d flags, %.2fs",
                len(members), len(flags), elapsed)
    return result


# ── Optional Celery wrapper ────────────────────────────────────────────────────

def get_celery_task():
    """Returns the Celery task if Celery is available. Safe to call if not."""
    try:
        from app.worker import celery_app

        @celery_app.task(bind=True, name="oversight.pensions.batch_run", max_retries=2)
        def pension_batch_run(
            self,
            scheme_id: str,
            file_paths: List[str],
            run_id: str,
            scheme_rules_dict: Optional[Dict] = None,
            output_dir: Optional[str] = None,
        ):
            try:
                return run_pension_batch_pipeline(
                    scheme_id=scheme_id,
                    file_paths=file_paths,
                    run_id=run_id,
                    scheme_rules_dict=scheme_rules_dict,
                    output_dir=output_dir,
                )
            except Exception as exc:
                logger.exception("Celery pension_batch_run failed: %s", exc)
                raise self.retry(exc=exc, countdown=60)

        return pension_batch_run
    except ImportError:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# PART B: API ENDPOINT ADDITIONS
# ══════════════════════════════════════════════════════════════════════════════

"""
Add these routes to app/api/pensions_api.py by importing and including:

  from app.packs.pensions.pension_batch_task import pension_batch_router
  app.include_router(pension_batch_router)

Or paste the router directly into pensions_api.py.
"""

try:
    from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field

    pension_batch_router = APIRouter(prefix="/api/v1/pensions", tags=["pensions-batch"])

    # In-memory job store (replace with DB/Redis in production)
    _job_store: Dict[str, Dict] = {}

    class BatchRunRequest(BaseModel):
        scheme_id: str
        run_id: Optional[str] = None
        scheme_rules: Optional[Dict] = None

    @pension_batch_router.post("/batch-upload")
    async def batch_upload(
        background_tasks: BackgroundTasks,
        scheme_id: str = Form(...),
        run_id: Optional[str] = Form(None),
        files: List[UploadFile] = File(...),
    ):
        """
        Upload batch of pension member documents for async processing.
        Accepts PDF, images (JPEG/PNG), Excel, CSV, or ZIP archives.
        Returns run_id for polling.
        """
        import uuid
        _run_id = run_id or f"P-{uuid.uuid4().hex[:8].upper()}"
        _job_store[_run_id] = {"status": "queued", "scheme_id": scheme_id}

        # Save uploaded files to temp dir
        tmp_dir = tempfile.mkdtemp(prefix=f"oversight_p_{_run_id}_")
        file_paths = []
        for uf in files:
            dest = os.path.join(tmp_dir, uf.filename or f"file_{len(file_paths)}")
            with open(dest, "wb") as f:
                f.write(await uf.read())
            file_paths.append(dest)

        async def run_async():
            _job_store[_run_id]["status"] = "running"
            try:
                output_dir = os.path.join(tmp_dir, "output")
                result = run_pension_batch_pipeline(
                    scheme_id=scheme_id,
                    file_paths=file_paths,
                    run_id=_run_id,
                    output_dir=output_dir,
                )
                _job_store[_run_id] = {"status": "complete", "result": result}
            except Exception as e:
                _job_store[_run_id] = {"status": "error", "error": str(e)}

        background_tasks.add_task(run_async)

        return {"run_id": _run_id, "status": "queued", "file_count": len(file_paths)}

    @pension_batch_router.post("/batch-run-sync")
    async def batch_run_sync(
        scheme_id: str = Form(...),
        run_id: Optional[str] = Form(None),
        scheme_rules_json: Optional[str] = Form(None),
        files: List[UploadFile] = File(...),
    ):
        """
        Synchronous batch run (small batches only — use /batch-upload for >100 records).
        """
        import uuid, json as _json
        _run_id = run_id or f"P-{uuid.uuid4().hex[:8].upper()}"
        tmp_dir = tempfile.mkdtemp(prefix=f"oversight_p_{_run_id}_")
        file_paths = []
        for uf in files:
            dest = os.path.join(tmp_dir, uf.filename or f"file_{len(file_paths)}")
            with open(dest, "wb") as f:
                f.write(await uf.read())
            file_paths.append(dest)

        rules_dict = None
        if scheme_rules_json:
            try:
                rules_dict = _json.loads(scheme_rules_json)
            except Exception:
                rules_dict = None

        result = run_pension_batch_pipeline(
            scheme_id=scheme_id,
            file_paths=file_paths,
            run_id=_run_id,
            scheme_rules_dict=rules_dict,
            output_dir=os.path.join(tmp_dir, "output"),
        )
        return result

    @pension_batch_router.get("/batch/{run_id}")
    def batch_status(run_id: str):
        """Poll batch job status."""
        job = _job_store.get(run_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        return job

    @pension_batch_router.get("/batch/{run_id}/pdf")
    def batch_pdf(run_id: str):
        """Download PDF evidence pack for completed batch run."""
        job = _job_store.get(run_id)
        if not job or job.get("status") != "complete":
            raise HTTPException(status_code=404, detail="Job not complete or not found")
        pdf_path = job.get("result", {}).get("pdf_path")
        if not pdf_path or not os.path.exists(pdf_path):
            raise HTTPException(status_code=404, detail="PDF not available")
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"oversight_p_{run_id}.pdf",
        )

    @pension_batch_router.post("/ingest-record")
    async def ingest_single_record(file: UploadFile = File(...), scheme_id: str = Form("unknown")):
        """
        Sync single-record ingestion — for testing and UI demo.
        Returns canonical member record + any anomaly flags.
        """
        tmp = tempfile.NamedTemporaryFile(
            suffix=Path(file.filename or "record.pdf").suffix,
            delete=False
        )
        try:
            tmp.write(await file.read())
            tmp.close()

            result = run_pension_batch_pipeline(
                scheme_id=scheme_id,
                file_paths=[tmp.name],
                run_id="single",
            )
            return result
        finally:
            os.unlink(tmp.name)

except ImportError:
    # FastAPI not available (running outside API context)
    pension_batch_router = None
