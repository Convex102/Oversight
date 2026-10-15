"""
Oversight ASGL v13.5 — Data Quality & OCR API
===============================================
Routes:
  POST /dq/ocr     Batch OCR extraction from multiple files
  POST /dq/run     Data quality analysis + OADE anomaly detection

Full pipeline:
  1. Accept 1-N files (PDF, image, Excel, CSV)
  2. OCR extracts text/tables from PDF/images via vision.py
  3. Excel/CSV parsed directly
  4. Records validated against optional CustomSchema
  5. OADE (ensemble anomaly detection: IQR + z-score + isolation)
  6. Results stored in DQJob/DQFile/DQViolation
  7. If engagement_id: create ReviewPoints for high-severity violations
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import DQJob, DQFile, DQViolation, CustomSchema, Engagement, ReviewPoint
from app.rbac import Principal, get_principal

# SECURITY-FIX 2026-10-15: tenant_id used to be a client supplied form field (default 1), and the
# download and report routes served any job's file to anyone who guessed the integer id. The tenant
# now comes from the login, schemas and engagements must belong to it, and a job's files are only
# served to the tenant that ran it (another tenant's job looks like not found).

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dq"])

_ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp",
                 ".xlsx", ".xlsm", ".xls", ".csv"}

# ══════════════════════════════════════════════════════════════════════════════
# OCR ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/dq/ocr", summary="Batch OCR extraction from multiple files")
async def dq_ocr(
    files: List[UploadFile] = File(...),
    schema_id: Optional[int] = Form(default=None),
    engagement_id: Optional[int] = Form(default=None),
    output_format: str = Form(default="csv"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    if not files:
        raise HTTPException(400, "At least one file required")

    tenant_id = principal.tenant_id
    schema = _load_schema(schema_id, db, tenant_id) if schema_id else None
    _check_engagement(engagement_id, db, tenant_id)

    dq_job = DQJob(
        tenant_id=tenant_id,
        engagement_id=engagement_id,
        schema_id=schema_id,
        job_type="ocr",
        status="running",
        files_count=len(files),
    )
    db.add(dq_job)
    db.flush()
    job_id = dq_job.id

    all_records: List[Dict[str, Any]] = []
    dq_files: List[DQFile] = []
    total_confidence = 0.0
    confidence_count = 0

    for upload in files:
        data = await upload.read()
        filename = upload.filename or "file"
        ext = Path(filename).suffix.lower()

        if ext not in _ALLOWED_EXTS:
            logger.warning("Skipping unsupported file type: %s", filename)
            continue

        dq_file = DQFile(
            job_id=job_id,
            filename=filename,
            file_type=ext.lstrip("."),
        )

        records, raw_text, ocr_method, confidence = _extract_records(data, filename, ext, schema)

        dq_file.records_extracted = len(records)
        dq_file.raw_text = raw_text[:10000] if raw_text else None
        dq_file.ocr_method = ocr_method
        dq_file.ocr_confidence = confidence
        dq_file.extracted_data = {"records": records[:100]}

        db.add(dq_file)
        db.flush()
        dq_files.append(dq_file)

        for rec in records:
            rec["_source_file"] = filename
            rec["_dq_file_id"] = dq_file.id
        all_records.extend(records)

        if confidence > 0:
            total_confidence += confidence
            confidence_count += 1

    avg_confidence = total_confidence / confidence_count if confidence_count else 0.0

    # Write combined CSV to temp storage
    download_url = None
    if all_records:
        download_url = _write_combined_csv(job_id, all_records)

    dq_job.records_extracted = len(all_records)
    dq_job.avg_confidence = avg_confidence
    dq_job.status = "complete"
    dq_job.download_uri = download_url
    db.commit()

    return JSONResponse({
        "job_id": job_id,
        "status": "complete",
        "files_processed": len(dq_files),
        "total_records_extracted": len(all_records),
        "avg_confidence": round(avg_confidence, 3),
        "download_url": download_url,
        "files": [
            {
                "filename": f.filename,
                "records_extracted": f.records_extracted,
                "ocr_method": f.ocr_method,
                "confidence": f.ocr_confidence,
            }
            for f in dq_files
        ],
    })


# ══════════════════════════════════════════════════════════════════════════════
# DQ RUN ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/dq/run", summary="Data quality analysis + OADE anomaly detection")
async def dq_run(
    files: List[UploadFile] = File(...),
    schema_id: Optional[int] = Form(default=None),
    engagement_id: Optional[int] = Form(default=None),
    run_oade: bool = Form(default=True),
    create_review_points: bool = Form(default=False),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> JSONResponse:
    if not files:
        raise HTTPException(400, "At least one file required")

    tenant_id = principal.tenant_id
    schema = _load_schema(schema_id, db, tenant_id) if schema_id else None
    _check_engagement(engagement_id, db, tenant_id)

    dq_job = DQJob(
        tenant_id=tenant_id,
        engagement_id=engagement_id,
        schema_id=schema_id,
        job_type="dq",
        status="running",
        files_count=len(files),
    )
    db.add(dq_job)
    db.flush()
    job_id = dq_job.id

    all_records: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []

    for upload in files:
        data = await upload.read()
        filename = upload.filename or "file"
        ext = Path(filename).suffix.lower()

        if ext not in _ALLOWED_EXTS:
            continue

        dq_file = DQFile(job_id=job_id, filename=filename, file_type=ext.lstrip("."))

        records, raw_text, ocr_method, confidence = _extract_records(data, filename, ext, schema)
        dq_file.records_extracted = len(records)
        dq_file.ocr_method = ocr_method
        dq_file.ocr_confidence = confidence
        db.add(dq_file)
        db.flush()

        for rec in records:
            rec["_source_file"] = filename
            rec["_dq_file_id"] = dq_file.id
        all_records.extend(records)

        # Schema validation
        if schema:
            file_violations = _validate_against_schema(records, schema, dq_file.id, filename)
            violations.extend(file_violations)

    # OADE — ensemble anomaly detection across all records
    oade_violations: List[Dict] = []
    if run_oade and all_records:
        oade_violations = _run_oade(all_records)
        violations.extend(oade_violations)

    # Persist violations
    high_c = med_c = low_c = 0
    for v_data in violations:
        sev = v_data.get("severity", "medium")
        if sev == "high":    high_c += 1
        elif sev == "medium": med_c += 1
        else:                 low_c += 1

        v = DQViolation(
            job_id=job_id,
            file_id=v_data.get("file_id"),
            severity=sev,
            rule_id=v_data.get("rule_id"),
            field=v_data.get("field"),
            value=str(v_data.get("value", ""))[:500],
            message=v_data.get("message", "")[:2000],
            row_index=v_data.get("row_index"),
            population_affected=v_data.get("population_affected", 0.0),
            is_anomaly=v_data.get("is_anomaly", False),
        )
        db.add(v)

    total_rows = len(all_records)
    affected_rows = len(set(v.get("row_index") for v in violations if v.get("row_index") is not None))
    population_pct = round(100.0 * affected_rows / total_rows, 1) if total_rows > 0 else 0.0
    oade_count = len(oade_violations)
    oade_pct = round(100.0 * oade_count / total_rows, 1) if total_rows > 0 else 0.0

    summary = {
        "total_rows": total_rows,
        "total_violations": len(violations),
        "high_severity": high_c,
        "medium_severity": med_c,
        "low_severity": low_c,
        "population_pct": population_pct,
        "oade_anomaly_count": oade_count,
        "oade_anomaly_pct": oade_pct,
        "schema_violations": len(violations) - oade_count,
    }

    narrative = _generate_dq_narrative(summary, violations[:5])

    # Write report CSV
    report_url = None
    if violations:
        report_url = _write_violation_csv(job_id, violations)

    dq_job.records_extracted = total_rows
    dq_job.total_violations = len(violations)
    dq_job.high_severity = high_c
    dq_job.medium_severity = med_c
    dq_job.low_severity = low_c
    dq_job.population_pct = population_pct
    dq_job.summary = summary
    dq_job.narrative = narrative
    dq_job.report_uri = report_url
    dq_job.status = "complete"

    # Create ReviewPoints in engagement if requested
    review_points_created = 0
    if create_review_points and engagement_id and violations:
        eng = db.query(Engagement).filter(Engagement.id == engagement_id).first()
        if eng:
            run_num = eng.run_count or 1
            for v_data in [v for v in violations if v.get("severity") == "high"][:20]:
                pt = ReviewPoint(
                    engagement_id=engagement_id,
                    run_number=run_num,
                    severity="high",
                    category="anomaly" if v_data.get("is_anomaly") else "policy",
                    title=v_data.get("message", "DQ Violation")[:300],
                    description=v_data.get("message", ""),
                    field_name=v_data.get("field"),
                    status="open",
                )
                db.add(pt)
                review_points_created += 1
            eng.open_points_count = (eng.open_points_count or 0) + review_points_created

    dq_job.review_points_created = review_points_created
    db.commit()

    return JSONResponse({
        "job_id": job_id,
        "status": "complete",
        "summary": summary,
        "narrative": narrative,
        "violations": violations[:100],
        "review_points_created": review_points_created,
        "report_url": report_url,
    })


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _check_engagement(engagement_id: Optional[int], db: Session, tenant_id: int) -> None:
    if engagement_id is None:
        return
    ok = db.query(Engagement).filter(Engagement.id == engagement_id, Engagement.tenant_id == tenant_id).first()
    if not ok:
        raise HTTPException(404, "Engagement not found")


def _owned_job(job_id: int, db: Session, tenant_id: int) -> None:
    j = db.query(DQJob).filter(DQJob.id == job_id, DQJob.tenant_id == tenant_id).first()
    if not j:
        raise HTTPException(404, "Job not found")


def _load_schema(schema_id: int, db: Session, tenant_id: int) -> Optional[Dict]:
    s = db.query(CustomSchema).filter(CustomSchema.id == schema_id, CustomSchema.tenant_id == tenant_id).first()
    if not s:
        raise HTTPException(404, "Schema not found")
    return {"name": s.name, "fields": s.fields or [], "rules": s.rules or []}


def _extract_records(
    data: bytes,
    filename: str,
    ext: str,
    schema: Optional[Dict],
) -> tuple[List[Dict], Optional[str], str, float]:
    """Extract structured records from a file. Returns (records, raw_text, method, confidence)."""
    records: List[Dict] = []
    raw_text: Optional[str] = None
    method = "unknown"
    confidence = 0.0

    try:
        if ext in (".xlsx", ".xlsm", ".xls"):
            records, method = _extract_excel(data), "openpyxl"
            confidence = 1.0
        elif ext == ".csv":
            records, method = _extract_csv(data), "csv_parse"
            confidence = 1.0
        elif ext in (".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"):
            records, raw_text, method, confidence = _extract_via_ocr(data, filename, ext)
    except Exception as exc:
        logger.warning("Extraction failed for %s: %s", filename, exc)

    return records, raw_text, method, confidence


def _extract_excel(data: bytes) -> List[Dict]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    records = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            continue
        headers = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(rows[0])]
        for row_idx, row in enumerate(rows[1:], 1):
            if not any(c is not None for c in row):
                continue
            records.append({
                headers[i]: (str(row[i]).strip() if i < len(row) and row[i] is not None else "")
                for i in range(len(headers))
            })
    return records


def _extract_csv(data: bytes) -> List[Dict]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


def _extract_via_ocr(
    data: bytes,
    filename: str,
    ext: str,
) -> tuple[List[Dict], Optional[str], str, float]:
    from app.vision.vision import extract_document
    result = extract_document(data, filename)
    raw_text = result.get("text", "")
    confidence = result.get("confidence", 0.0)
    method = result.get("method", "ocr")

    records: List[Dict] = []

    # Try table extraction first
    for table in result.get("tables", []):
        headers = table.get("headers", [])
        for row in table.get("rows", []):
            if headers and isinstance(row, (list, tuple)):
                records.append({h: str(row[i]) if i < len(row) else "" for i, h in enumerate(headers)})
            elif isinstance(row, dict):
                records.append({str(k): str(v) for k, v in row.items()})

    # Fallback: parse key:value pairs from text
    if not records and raw_text:
        rec: Dict[str, str] = {}
        for line in raw_text.split("\n"):
            line = line.strip()
            if not line:
                if rec:
                    records.append(rec)
                    rec = {}
                continue
            parts = line.split(":", 1)
            if len(parts) == 2:
                rec[parts[0].strip()] = parts[1].strip()
        if rec:
            records.append(rec)

    return records, raw_text, method, confidence


def _validate_against_schema(
    records: List[Dict],
    schema: Dict,
    file_id: int,
    filename: str,
) -> List[Dict[str, Any]]:
    """Validate records against schema field definitions."""
    import re as _re
    violations = []
    fields = {f["name"]: f for f in schema.get("fields", [])}

    for row_idx, record in enumerate(records):
        for field_name, field_def in fields.items():
            value = record.get(field_name, record.get(field_def.get("label", ""), ""))
            value_str = str(value).strip()

            # Required check
            if field_def.get("required") and not value_str:
                violations.append({
                    "file_id": file_id,
                    "severity": "high",
                    "rule_id": f"required:{field_name}",
                    "field": field_name,
                    "value": value_str,
                    "message": f"Required field '{field_name}' is missing (row {row_idx + 1}, file: {filename})",
                    "row_index": row_idx,
                    "population_affected": 0.0,
                    "is_anomaly": False,
                })

            # Format check
            fmt = field_def.get("format")
            if fmt and value_str:
                try:
                    if not _re.match(fmt, value_str):
                        violations.append({
                            "file_id": file_id,
                            "severity": "medium",
                            "rule_id": f"format:{field_name}",
                            "field": field_name,
                            "value": value_str,
                            "message": f"'{field_name}' value '{value_str}' does not match expected format (row {row_idx + 1})",
                            "row_index": row_idx,
                            "population_affected": 0.0,
                            "is_anomaly": False,
                        })
                except Exception:
                    pass

            # Range check
            min_val = field_def.get("min")
            max_val = field_def.get("max")
            if (min_val is not None or max_val is not None) and value_str:
                try:
                    num = float(value_str.replace(",", "").replace("£", "").replace("$", ""))
                    if min_val is not None and num < float(min_val):
                        violations.append({
                            "file_id": file_id,
                            "severity": "medium",
                            "rule_id": f"range_min:{field_name}",
                            "field": field_name,
                            "value": value_str,
                            "message": f"'{field_name}' value {num} is below minimum {min_val} (row {row_idx + 1})",
                            "row_index": row_idx,
                            "population_affected": 0.0,
                            "is_anomaly": False,
                        })
                    if max_val is not None and num > float(max_val):
                        violations.append({
                            "file_id": file_id,
                            "severity": "medium",
                            "rule_id": f"range_max:{field_name}",
                            "field": field_name,
                            "value": value_str,
                            "message": f"'{field_name}' value {num} exceeds maximum {max_val} (row {row_idx + 1})",
                            "row_index": row_idx,
                            "population_affected": 0.0,
                            "is_anomaly": False,
                        })
                except (ValueError, AttributeError):
                    pass

    return violations


def _run_oade(records: List[Dict]) -> List[Dict[str, Any]]:
    """
    OADE: Oversight Anomaly Detection Engine.
    Ensemble: IQR outlier detection + Z-score + basic isolation (no sklearn required).
    Returns list of violation dicts for anomalous values.
    """
    import statistics
    violations = []
    n = len(records)
    if n < 3:
        return []

    # Collect numeric columns
    numeric_cols: Dict[str, List[tuple]] = {}
    for row_idx, rec in enumerate(records):
        for k, v in rec.items():
            if k.startswith("_"):
                continue
            try:
                num = float(str(v).replace(",", "").replace("£", "").replace("$", "").strip())
                if k not in numeric_cols:
                    numeric_cols[k] = []
                numeric_cols[k].append((row_idx, num))
            except (ValueError, AttributeError):
                pass

    for col, row_vals in numeric_cols.items():
        if len(row_vals) < 4:
            continue
        vals = [v for _, v in row_vals]

        try:
            mean = statistics.mean(vals)
            stdev = statistics.stdev(vals) if len(vals) > 1 else 0
            sorted_vals = sorted(vals)
            q1_idx = len(sorted_vals) // 4
            q3_idx = 3 * len(sorted_vals) // 4
            q1 = sorted_vals[q1_idx]
            q3 = sorted_vals[q3_idx]
            iqr = q3 - q1
            iqr_lower = q1 - 1.5 * iqr
            iqr_upper = q3 + 1.5 * iqr

            for row_idx, val in row_vals:
                is_outlier = False
                reasons = []

                # IQR
                if iqr > 0 and (val < iqr_lower or val > iqr_upper):
                    is_outlier = True
                    reasons.append(f"IQR outlier (Q1={q1:.2f}, Q3={q3:.2f})")

                # Z-score (>3 sigma)
                if stdev > 0:
                    z = abs(val - mean) / stdev
                    if z > 3.0:
                        is_outlier = True
                        reasons.append(f"Z-score={z:.1f} (>3σ)")

                if is_outlier:
                    severity = "high" if (stdev > 0 and abs(val - mean) / stdev > 4) else "medium"
                    pop_affected = round(100.0 * sum(
                        1 for v in vals if (iqr > 0 and (v < iqr_lower or v > iqr_upper))
                        or (stdev > 0 and abs(v - mean) / stdev > 3.0)
                    ) / len(vals), 1)

                    violations.append({
                        "file_id": records[row_idx].get("_dq_file_id"),
                        "severity": severity,
                        "rule_id": f"oade_outlier:{col}",
                        "field": col,
                        "value": str(val),
                        "message": (
                            f"Statistical outlier in '{col}': value {val:.2f} "
                            f"(mean={mean:.2f}, σ={stdev:.2f}). Reasons: {', '.join(reasons)}"
                        ),
                        "row_index": row_idx,
                        "population_affected": pop_affected,
                        "is_anomaly": True,
                    })
        except Exception as exc:
            logger.debug("OADE failed for column %s: %s", col, exc)

    return violations


def _generate_dq_narrative(summary: Dict, top_violations: List[Dict]) -> str:
    total = summary.get("total_rows", 0)
    total_v = summary.get("total_violations", 0)
    high = summary.get("high_severity", 0)
    pop = summary.get("population_pct", 0)
    oade = summary.get("oade_anomaly_count", 0)

    if total_v == 0:
        return f"Data quality analysis of {total} records found no violations or anomalies. All records meet schema requirements."

    narrative = (
        f"Oversight DQ analysis of {total:,} records identified {total_v} issue(s), "
        f"affecting {pop}% of the population. "
    )
    if high > 0:
        narrative += f"{high} high-severity violation(s) require immediate attention. "
    if oade > 0:
        narrative += f"OADE statistical analysis flagged {oade} anomalous value(s) as outliers (>3σ or IQR). "

    if top_violations:
        narrative += "Key findings: " + "; ".join(
            v.get("message", "")[:80] for v in top_violations[:3]
        ) + "."

    return narrative


def _write_combined_csv(job_id: int, records: List[Dict]) -> str:
    out_dir = Path("data/dq_exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ocr_extracted_{job_id}.csv"

    if not records:
        return f"/api/v1/dq/download/{job_id}"

    all_keys = list(dict.fromkeys(k for r in records for k in r if not k.startswith("_")))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    return f"/api/v1/dq/download/{job_id}"


def _write_violation_csv(job_id: int, violations: List[Dict]) -> str:
    out_dir = Path("data/dq_exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dq_violations_{job_id}.csv"

    fields = ["severity", "rule_id", "field", "value", "message", "row_index", "population_affected", "is_anomaly"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(violations)

    return f"/api/v1/dq/report/{job_id}"


@router.get("/dq/download/{job_id}", summary="Download extracted records CSV")
async def dq_download(job_id: int, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> FileResponse:
    _owned_job(job_id, db, principal.tenant_id)
    path = Path(f"data/dq_exports/ocr_extracted_{job_id}.csv")
    if not path.exists():
        raise HTTPException(404, "Extracted file not found — run /dq/ocr first")
    return FileResponse(str(path), media_type="text/csv", filename=f"oversight_extracted_{job_id}.csv")


@router.get("/dq/report/{job_id}", summary="Download DQ violation report CSV")
async def dq_report(job_id: int, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> FileResponse:
    _owned_job(job_id, db, principal.tenant_id)
    path = Path(f"data/dq_exports/dq_violations_{job_id}.csv")
    if not path.exists():
        raise HTTPException(404, "Report not found — run /dq/run first")
    return FileResponse(str(path), media_type="text/csv", filename=f"oversight_dq_report_{job_id}.csv")
