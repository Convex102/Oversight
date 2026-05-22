"""
Celery task wiring patch.

If your repo has `app/tasks.py`, merge the function bodies from this file:
- run_compare_job_wired(job_id, tenant_id, user_id, plan, spreadsheet_path, pdf_path, mapping, ...)

This is the core 'wire everything together' implementation.
"""

import os, time, traceback
from typing import Dict, Any, Optional, List
from app.observability.audit import write_audit
from app.billing.metering import record_run
from app.ingestion.pipeline import Pipeline, StepResult
from app.ingestion.ocr import ocr_pdf
from app.schema.registry import latest_schema
from app.schema.validator import validate as validate_schema
from app.reporting.report_builder import assemble_report_payload

ENABLE_OCR = os.getenv("ASGL_ENABLE_OCR","0") == "1"
DEFAULT_SCHEMA_DOMAIN = os.getenv("ASGL_SCHEMA_DOMAIN","accountancy")
DEFAULT_SCHEMA_ID = os.getenv("ASGL_SCHEMA_ID","asgl_a_core")

def step_ingest(ctx: Dict[str,Any]) -> StepResult:
    # TODO: replace with your actual ingestion functions. This returns a normalized contract.
    return StepResult(name="ingest", ok=True, out={"extracted": ctx.get("extracted", {})})

def step_ocr(ctx: Dict[str,Any]) -> StepResult:
    if not ENABLE_OCR:
        return StepResult(name="ocr", ok=True, out={"ocr": {"ok": True, "skipped": True}})
    pdf_path = ctx.get("pdf_path")
    if not pdf_path:
        return StepResult(name="ocr", ok=True, out={"ocr": {"ok": True, "skipped": True}})
    o = ocr_pdf(pdf_path)
    return StepResult(name="ocr", ok=True, out={"ocr": o}, metrics={"ocr_cached": o.get("cached", False), "ocr_pages": o.get("pages", 0)})

def step_schema_validate(ctx: Dict[str,Any]) -> StepResult:
    schema = latest_schema(DEFAULT_SCHEMA_DOMAIN, DEFAULT_SCHEMA_ID)
    if not schema:
        return StepResult(name="schema_validate", ok=True, out={"validations": {"ok": True, "violations": [], "note":"no schema installed"}})
    extracted = ctx.get("extracted", {})
    v = validate_schema(schema, extracted)
    return StepResult(name="schema_validate", ok=True, out={"validations": v}, metrics={"violation_count": len(v.get("violations", []))})

def step_model_and_shap(ctx: Dict[str,Any]) -> StepResult:
    # Wire into your ML model + SHAP outputs here.
    shap = ctx.get("shap", {"top_features": []})
    return StepResult(name="model_shap", ok=True, out={"shap": shap})

def step_evidence_chain(ctx: Dict[str,Any]) -> StepResult:
    # Evidence is a list mapping finding -> source cell/pdf snippet.
    evidence = ctx.get("evidence", [])
    return StepResult(name="evidence_chain", ok=True, out={"evidence": evidence})

def step_report(ctx: Dict[str,Any]) -> StepResult:
    meta = {"job_id": ctx["job_id"], "tenant_id": ctx["tenant_id"], "user_id": ctx["user_id"]}
    report_payload = assemble_report_payload(meta, ctx.get("validations", {}), ctx.get("shap", {}), ctx.get("evidence", []))
    return StepResult(name="report", ok=True, out={"report_payload": report_payload})

def run_compare_job_wired(job_id: str, tenant_id: str, user_id: str, plan: str, spreadsheet_path: str, pdf_path: str, mapping: Optional[Dict[str,Any]]=None) -> Dict[str,Any]:
    write_audit("job.start", user_id, tenant_id, "job", "start", {"job_id": job_id, "plan": plan})
    t0 = time.time()
    ctx = {
        "job_id": job_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "plan": plan,
        "spreadsheet_path": spreadsheet_path,
        "pdf_path": pdf_path,
        "mapping": mapping or {},
        "extracted": {},   # populate in your real ingest step
        "shap": {"top_features": []},
        "evidence": [],
    }

    pipe = Pipeline([step_ingest, step_ocr, step_schema_validate, step_model_and_shap, step_evidence_chain, step_report])
    out = pipe.run(ctx)

    # metering
    ocr_pages = 0
    try:
        for s in out.get("steps", []):
            if s["name"] == "ocr":
                ocr_pages = int(s.get("metrics", {}).get("ocr_pages", 0))
    except Exception:
        ocr_pages = 0

    record_run(tenant_id, ocr_pages=ocr_pages, tokens=0)

    ok = out.get("ok", False)
    dur_ms = int((time.time() - t0) * 1000)
    if ok:
        write_audit("job.complete", user_id, tenant_id, "job", "complete", {"job_id": job_id, "duration_ms": dur_ms})
    else:
        write_audit("job.fail", user_id, tenant_id, "job", "fail", {"job_id": job_id, "duration_ms": dur_ms, "error": out.get("ctx", {}).get("pipeline_error")})
    return {"ok": ok, "duration_ms": dur_ms, "pipeline": out}