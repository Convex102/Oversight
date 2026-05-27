"""
Oversight ASGL v13.5 — Compare Routes (PRODUCTION)
===================================================
Direct integration with asgla_compare pipeline.
Returns full DiffRow objects with all fields populated.

NO STUBS. NO PLACEHOLDERS. FULLY FUNCTIONAL.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/compare", tags=["comparison"])
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE MODELS
# ══════════════════════════════════════════════════════════════════════════════

class DiffRowOut(BaseModel):
    """Single comparison row (DiffRow converted to API response)."""
    key_excel: str
    key_pdf: str
    value_excel: Optional[float]
    value_pdf: Optional[float]
    delta: Optional[float]
    abs_delta: Optional[float]
    pct_delta: Optional[float]
    status: str
    match_score: float


class DeltaSummary(BaseModel):
    """Summary statistics from comparison."""
    total_deltas: int
    mismatches: int
    reviews: int
    missing_in_pdf: int
    missing_in_excel: int
    matches: int


class CompareResponse(BaseModel):
    """Complete comparison response."""
    compare_id: str
    label_a: str
    label_b: str
    file_a: str
    file_b: str
    excel_items: int
    pdf_items: int
    delta_summary: DeltaSummary
    differences: List[DiffRowOut]
    excel_warnings: List[str]
    pdf_warnings: List[str]
    compare_pdf_path: Optional[str]
    compare_csv_path: Optional[str]


# ══════════════════════════════════════════════════════════════════════════════
# POST /compare/ — MAIN ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/", response_model=CompareResponse, summary="Compare two documents")
async def compare_documents(
    file_a: UploadFile = File(..., description="Excel/CSV working papers"),
    file_b: UploadFile = File(..., description="PDF final accounts"),
    label_a: str = Form("Working Papers"),
    label_b: str = Form("Final Accounts"),
    vertical: Optional[str] = Form(None),
    materiality_pct: float = Form(5.0),
    tenant_id: str = Form("default"),
    client_id: str = Form("default"),
) -> CompareResponse:
    """
    Compare two structured documents using semantic matching.
    
    Flow:
    1. Extract Excel key-value pairs (all sheets)
    2. Extract PDF text via pdfplumber
    3. Parse PDF for key-value patterns
    4. Match Excel keys to PDF keys (RapidFuzz, 75%+)
    5. Compare values with abs/pct tolerance
    6. Generate CSV and PDF reports
    7. Return all diff rows + summary
    """

    # ──────────────────────────────────────────────────────────────────────────
    # VALIDATE INPUTS
    # ──────────────────────────────────────────────────────────────────────────

    if not file_a.filename:
        raise HTTPException(400, "file_a is required")
    if not file_b.filename:
        raise HTTPException(400, "file_b is required")

    ext_a = os.path.splitext(file_a.filename)[1].lower()
    ext_b = os.path.splitext(file_b.filename)[1].lower()

    if ext_a not in {".xlsx", ".xlsm", ".xls", ".csv"}:
        raise HTTPException(400, f"file_a must be Excel/CSV, got '{ext_a}'")
    if ext_b not in {".pdf"}:
        raise HTTPException(400, f"file_b must be PDF, got '{ext_b}'")

    content_a = await file_a.read()
    content_b = await file_b.read()

    if not content_a:
        raise HTTPException(400, "file_a is empty")
    if not content_b:
        raise HTTPException(400, "file_b is empty")

    # ──────────────────────────────────────────────────────────────────────────
    # WRITE TO TEMP
    # ──────────────────────────────────────────────────────────────────────────

    tmp = tempfile.mkdtemp(prefix="oversight_cmp_")
    try:
        excel_path = os.path.join(tmp, f"file_a{ext_a}")
        pdf_path = os.path.join(tmp, f"file_b{ext_b}")
        out_dir = os.path.join(tmp, "output")

        with open(excel_path, "wb") as f:
            f.write(content_a)
        with open(pdf_path, "wb") as f:
            f.write(content_b)

        logger.info(
            "Compare: %s (%d B) vs %s (%d B)",
            file_a.filename, len(content_a),
            file_b.filename, len(content_b),
        )

        # ────────────────────────────────────────────────────────────────────
        # IMPORT PIPELINE
        # ────────────────────────────────────────────────────────────────────

        try:
            from asgla_compare.ingest.excel_ingest import extract_excel_kv
            from asgla_compare.ingest.pdf_ingest import extract_pdf_kv
            from asgla_compare.compare.diff_engine import compare_kv
            from asgla_compare.report.report_writer import write_csv, write_pdf
        except ImportError as e:
            logger.error("Pipeline import failed: %s", e)
            raise HTTPException(
                500,
                f"Pipeline not available: {e}. "
                "Install asgla_compare and ensure it's on PYTHONPATH."
            ) from e

        # ────────────────────────────────────────────────────────────────────
        # STEP 1: EXTRACT EXCEL
        # ────────────────────────────────────────────────────────────────────

        try:
            ex_result = extract_excel_kv(excel_path, logger=logger)
        except Exception as e:
            logger.exception("Excel extraction failed")
            raise HTTPException(500, f"Excel extraction failed: {e}") from e

        if not ex_result.items:
            logger.warning("Excel: 0 items. Warnings: %s", ex_result.warnings)

        # ────────────────────────────────────────────────────────────────────
        # STEP 2: EXTRACT PDF
        # ────────────────────────────────────────────────────────────────────

        try:
            pdf_result = extract_pdf_kv(pdf_path, logger=logger)
        except Exception as e:
            logger.exception("PDF extraction failed")
            raise HTTPException(500, f"PDF extraction failed: {e}") from e

        if not pdf_result.items:
            logger.warning("PDF: 0 items. Warnings: %s", pdf_result.warnings)

        # ────────────────────────────────────────────────────────────────────
        # STEP 3: SEMANTIC DIFF
        # ────────────────────────────────────────────────────────────────────

        try:
            diff_rows = compare_kv(
                ex_result.items,
                pdf_result.items,
                abs_tolerance=0.5,
                pct_tolerance=float(materiality_pct),
                min_match_score=75.0,
            )
        except Exception as e:
            logger.exception("Diff engine failed")
            raise HTTPException(500, f"Diff engine failed: {e}") from e

        # ────────────────────────────────────────────────────────────────────
        # STEP 4: WRITE REPORTS
        # ────────────────────────────────────────────────────────────────────

        os.makedirs(out_dir, exist_ok=True)
        csv_path = os.path.join(out_dir, "diff_report.csv")
        pdf_out = os.path.join(out_dir, "diff_report.pdf")
        csv_path_result = None
        pdf_out_result = None

        try:
            write_csv(diff_rows, csv_path)
            csv_path_result = csv_path
        except Exception as e:
            logger.warning("CSV write failed: %s", e)

        try:
            write_pdf(diff_rows, pdf_out)
            pdf_out_result = pdf_out
        except Exception as e:
            logger.warning("PDF write failed: %s", e)

        # ────────────────────────────────────────────────────────────────────
        # STEP 5: BUILD RESPONSE
        # ────────────────────────────────────────────────────────────────────

        status_counts: Dict[str, int] = {
            "MATCH": 0,
            "MISMATCH": 0,
            "REVIEW": 0,
            "MISSING_IN_PDF": 0,
            "MISSING_IN_EXCEL": 0,
        }

        for row in diff_rows:
            s = row.status
            if s in status_counts:
                status_counts[s] += 1

        def _clean(v):
            """Convert NaN/Inf floats to None so JSON serialisation never fails."""
            if v is None:
                return None
            try:
                return None if (math.isnan(v) or math.isinf(v)) else v
            except (TypeError, ValueError):
                return None

        differences: List[DiffRowOut] = [
            DiffRowOut(
                key_excel=row.key_excel,
                key_pdf=row.key_pdf,
                value_excel=_clean(row.value_excel),
                value_pdf=_clean(row.value_pdf),
                delta=_clean(row.delta),
                abs_delta=_clean(row.abs_delta),
                pct_delta=_clean(row.pct_delta),
                status=row.status,
                match_score=_clean(row.match_score) or 0.0,
            )
            for row in diff_rows
        ]

        logger.info(
            "Compare complete: %d rows | "
            "MISMATCH=%d REVIEW=%d MISSING_PDF=%d MISSING_EXCEL=%d MATCH=%d",
            len(diff_rows),
            status_counts["MISMATCH"],
            status_counts["REVIEW"],
            status_counts["MISSING_IN_PDF"],
            status_counts["MISSING_IN_EXCEL"],
            status_counts["MATCH"],
        )

        return CompareResponse(
            compare_id=str(uuid.uuid4()),
            label_a=label_a,
            label_b=label_b,
            file_a=file_a.filename,
            file_b=file_b.filename,
            excel_items=len(ex_result.items),
            pdf_items=len(pdf_result.items),
            delta_summary=DeltaSummary(
                total_deltas=len(diff_rows),
                mismatches=status_counts["MISMATCH"],
                reviews=status_counts["REVIEW"],
                missing_in_pdf=status_counts["MISSING_IN_PDF"],
                missing_in_excel=status_counts["MISSING_IN_EXCEL"],
                matches=status_counts["MATCH"],
            ),
            differences=differences,
            excel_warnings=ex_result.warnings,
            pdf_warnings=pdf_result.warnings,
            compare_pdf_path=pdf_out_result,
            compare_csv_path=csv_path_result,
        )

    finally:
        try:
            shutil.rmtree(tmp)
        except Exception as e:
            logger.warning("Temp cleanup failed: %s", e)



# ══════════════════════════════════════════════════════════════════════════════
# PARSE HELPERS — used by engagements_routes._compare_and_raise()
# ══════════════════════════════════════════════════════════════════════════════

def _parse_excel(data: bytes, filename: str) -> dict:
    """Extract sheets → list-of-dicts from Excel bytes."""
    import io
    import pandas as pd
    result = {}
    try:
        xf = pd.ExcelFile(io.BytesIO(data))
        for sheet in xf.sheet_names:
            try:
                df = pd.read_excel(xf, sheet_name=sheet, header=0)
                df = df.dropna(how="all").fillna("")
                result[sheet] = df.to_dict(orient="records")
            except Exception:
                continue
    except Exception:
        pass
    return result


def _parse_csv(data: bytes, filename: str) -> dict:
    """Extract a single sheet from CSV bytes."""
    import io
    import pandas as pd
    try:
        df = pd.read_csv(io.BytesIO(data)).fillna("")
        return {"Sheet1": df.to_dict(orient="records")}
    except Exception:
        return {}


def _parse_pdf(data: bytes, filename: str) -> dict:
    """Extract text lines from PDF using pdfplumber."""
    import io
    import re
    try:
        import pdfplumber
        lines = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                lines.extend(text.split("\n"))
        rows = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Try to parse as key: value or key value pairs
            m = re.match(r"^(.+?)\s{2,}([\d,\.\-]+)\s*$", line)
            if m:
                try:
                    rows.append({"label": m.group(1).strip(), "value": float(m.group(2).replace(",", ""))})
                    continue
                except ValueError:
                    pass
            rows.append({"label": line, "value": None})
        return {"Page 1": rows} if rows else {}
    except Exception:
        return {}


def _compare_sheets(
    name: str,
    rows_a: list,
    rows_b: list,
    label_a: str,
    label_b: str,
    materiality: float,
) -> dict:
    """Compare two lists of row dicts and return anomalies."""
    import math
    anomalies = []

    if not rows_a:
        return {"anomalies": [{"severity": "medium", "description": f"Section '{name}' present in {label_b} only", "sheet": name, "data": {}}], "delta_count": 1, "material_delta_count": 0}
    if not rows_b:
        return {"anomalies": [{"severity": "low", "description": f"Section '{name}' present in {label_a} only", "sheet": name, "data": {}}], "delta_count": 1, "material_delta_count": 0}

    # Build numeric maps
    def _to_num(v):
        if v is None or v == "":
            return None
        try:
            return float(str(v).replace(",", "").replace("£", "").strip())
        except (ValueError, TypeError):
            return None

    def _label_key(row):
        for k in ("label", "Label", "Account", "Description", "Name", "Field"):
            if k in row and row[k]:
                return str(row[k]).strip().lower()
        # First string value
        for v in row.values():
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
        return ""

    def _value(row):
        for k in ("value", "Value", "Amount", "Balance", "Debit", "Credit"):
            if k in row:
                v = _to_num(row[k])
                if v is not None:
                    return v
        for v in row.values():
            n = _to_num(v)
            if n is not None:
                return n
        return None

    map_a = {_label_key(r): _value(r) for r in rows_a if _label_key(r)}
    map_b = {_label_key(r): _value(r) for r in rows_b if _label_key(r)}

    all_keys = set(map_a) | set(map_b)
    delta_count = 0
    material_count = 0

    for key in all_keys:
        va = map_a.get(key)
        vb = map_b.get(key)
        if va is None and vb is None:
            continue
        if key not in map_a:
            anomalies.append({"severity": "medium", "description": f"'{key.title()}' present in {label_b} only", "sheet": name, "data": {"field": key, "value_a": None, "value_b": vb}})
            delta_count += 1
            continue
        if key not in map_b:
            anomalies.append({"severity": "low", "description": f"'{key.title()}' present in {label_a} only", "sheet": name, "data": {"field": key, "value_a": va, "value_b": None}})
            delta_count += 1
            continue
        if va is None or vb is None:
            continue
        delta = vb - va
        if abs(delta) < 0.01:
            continue
        pct = abs(delta / va * 100) if va != 0 else 100.0
        is_material = pct >= materiality
        sev = "high" if is_material else "medium" if pct >= 1.0 else "low"
        delta_count += 1
        if is_material:
            material_count += 1
        anomalies.append({
            "severity": sev,
            "description": f"'{key.title()}': {label_a} £{va:,.2f} vs {label_b} £{vb:,.2f} (Δ £{delta:+,.2f}, {pct:.1f}%)",
            "sheet": name,
            "data": {"field": key, "value_a": va, "value_b": vb, "delta": delta, "pct": pct},
        })

    return {"anomalies": anomalies, "delta_count": delta_count, "material_delta_count": material_count}

# ══════════════════════════════════════════════════════════════════════════════
# GET /compare/health
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/health", summary="Pipeline health check")
async def compare_health():
    """Verify all pipeline dependencies are available."""
    checks = {}
    all_ok = True

    required = [
        "asgla_compare.ingest.excel_ingest",
        "asgla_compare.ingest.pdf_ingest",
        "asgla_compare.compare.diff_engine",
        "asgla_compare.report.report_writer",
        "pdfplumber",
        "rapidfuzz",
        "pandas",
        "reportlab",
    ]

    for mod in required:
        try:
            __import__(mod)
            checks[mod] = "ok"
        except ImportError as e:
            checks[mod] = f"MISSING: {e}"
            all_ok = False

    return {
        "ok": all_ok,
        "pipeline_available": all_ok,
        "checks": checks,
    }
