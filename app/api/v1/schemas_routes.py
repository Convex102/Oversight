"""
Oversight ASGL v13.5 — Custom Schemas API
==========================================
Routes:
  GET    /schemas                     List schemas for tenant
  POST   /schemas                     Create custom schema
  GET    /schemas/presets             List system preset schemas
  POST   /schemas/from-preset/{id}   Clone a preset for tenant
  POST   /schemas/infer               Infer schema from uploaded file
  GET    /schemas/{id}               Get schema
  PUT    /schemas/{id}               Update schema
  DELETE /schemas/{id}               Delete schema
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import CustomSchema

logger = logging.getLogger(__name__)
router = APIRouter(tags=["schemas"])


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PRESETS (built-in schemas, no DB required)
# ══════════════════════════════════════════════════════════════════════════════

_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "preset_invoice",
        "name": "Standard Invoice",
        "domain": "invoice",
        "description": "UK supplier invoice with VAT, totals, line items",
        "fields": [
            {"name": "invoice_number",  "type": "string",  "required": True,  "format": r"^INV-?\d+"},
            {"name": "invoice_date",    "type": "date",    "required": True},
            {"name": "supplier_name",   "type": "string",  "required": True},
            {"name": "supplier_vat",    "type": "string",  "required": False, "format": r"^GB\d{9}$"},
            {"name": "net_amount",      "type": "decimal", "required": True,  "min": 0},
            {"name": "vat_amount",      "type": "decimal", "required": True,  "min": 0},
            {"name": "gross_amount",    "type": "decimal", "required": True,  "min": 0},
            {"name": "payment_terms",   "type": "string",  "required": False},
            {"name": "po_reference",    "type": "string",  "required": False},
            {"name": "currency",        "type": "string",  "required": False, "default": "GBP"},
        ],
        "rules": [
            {"id": "r1", "type": "cross_field", "expression": "gross_amount == net_amount + vat_amount",
             "message": "Gross amount must equal Net + VAT", "severity": "high"},
            {"id": "r2", "type": "cross_field", "expression": "vat_amount == net_amount * 0.2 OR vat_amount == 0",
             "message": "VAT should be 20% of net or zero-rated", "severity": "medium"},
        ],
    },
    {
        "id": "preset_timesheet",
        "name": "Employee Timesheet",
        "domain": "timesheet",
        "description": "Weekly or monthly employee timesheet",
        "fields": [
            {"name": "employee_name",   "type": "string",  "required": True},
            {"name": "employee_id",     "type": "string",  "required": False},
            {"name": "week_ending",     "type": "date",    "required": True},
            {"name": "regular_hours",   "type": "decimal", "required": True,  "min": 0, "max": 60},
            {"name": "overtime_hours",  "type": "decimal", "required": False, "min": 0},
            {"name": "total_hours",     "type": "decimal", "required": True,  "min": 0},
            {"name": "approved_by",     "type": "string",  "required": False},
            {"name": "department",      "type": "string",  "required": False},
        ],
        "rules": [
            {"id": "r1", "type": "cross_field",
             "expression": "total_hours == regular_hours + (overtime_hours or 0)",
             "message": "Total hours must equal regular + overtime", "severity": "high"},
            {"id": "r2", "type": "range", "field": "regular_hours",
             "expression": "regular_hours <= 40", "message": "Regular hours >40 is unusual", "severity": "low"},
        ],
    },
    {
        "id": "preset_bank_statement",
        "name": "Bank Statement",
        "domain": "bank_statement",
        "description": "Business bank account statement",
        "fields": [
            {"name": "transaction_date", "type": "date",    "required": True},
            {"name": "description",      "type": "string",  "required": True},
            {"name": "debit",            "type": "decimal", "required": False, "min": 0},
            {"name": "credit",           "type": "decimal", "required": False, "min": 0},
            {"name": "balance",          "type": "decimal", "required": True},
            {"name": "reference",        "type": "string",  "required": False},
        ],
        "rules": [
            {"id": "r1", "type": "completeness",
             "expression": "debit IS NOT NULL OR credit IS NOT NULL",
             "message": "Each row must have debit or credit", "severity": "high"},
        ],
    },
    {
        "id": "preset_payroll",
        "name": "Payroll Summary",
        "domain": "payroll",
        "description": "Monthly payroll with PAYE, NI, pension deductions",
        "fields": [
            {"name": "employee_name",    "type": "string",  "required": True},
            {"name": "employee_number",  "type": "string",  "required": False},
            {"name": "gross_pay",        "type": "decimal", "required": True,  "min": 0},
            {"name": "paye_tax",         "type": "decimal", "required": True,  "min": 0},
            {"name": "ni_employee",      "type": "decimal", "required": True,  "min": 0},
            {"name": "ni_employer",      "type": "decimal", "required": True,  "min": 0},
            {"name": "pension_employee", "type": "decimal", "required": False, "min": 0},
            {"name": "pension_employer", "type": "decimal", "required": False, "min": 0},
            {"name": "net_pay",          "type": "decimal", "required": True,  "min": 0},
            {"name": "period",           "type": "string",  "required": True},
        ],
        "rules": [
            {"id": "r1", "type": "cross_field",
             "expression": "net_pay == gross_pay - paye_tax - ni_employee - (pension_employee or 0)",
             "message": "Net pay reconciliation failed", "severity": "high"},
        ],
    },
]

_PRESET_MAP = {p["id"]: p for p in _PRESETS}


# ══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════════

class SchemaCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    domain: str = Field("general", max_length=80)
    description: Optional[str] = None
    fields: List[Dict[str, Any]] = Field(default_factory=list)
    rules: List[Dict[str, Any]] = Field(default_factory=list)
    tenant_id: int = Field(1, ge=1)


class SchemaUpdate(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[List[Dict[str, Any]]] = None
    rules: Optional[List[Dict[str, Any]]] = None


def _schema_to_dict(s: CustomSchema) -> Dict[str, Any]:
    return {
        "id": s.id,
        "tenant_id": s.tenant_id,
        "name": s.name,
        "domain": s.domain,
        "description": s.description,
        "is_preset": s.is_preset,
        "fields": s.fields or [],
        "rules": s.rules or [],
        "version": s.version,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/schemas", summary="List schemas for a tenant")
async def list_schemas(
    tenant_id: int = 1,
    domain: Optional[str] = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    q = db.query(CustomSchema).filter(
        CustomSchema.tenant_id == tenant_id,
        CustomSchema.is_active == True,  # noqa: E712
    )
    if domain:
        q = q.filter(CustomSchema.domain == domain)
    schemas = q.order_by(CustomSchema.name).all()
    return JSONResponse({
        "schemas": [_schema_to_dict(s) for s in schemas],
        "total": len(schemas),
    })


@router.post("/schemas", status_code=status.HTTP_201_CREATED, summary="Create custom schema")
async def create_schema(
    req: SchemaCreate,
    db: Session = Depends(get_db),
) -> JSONResponse:
    s = CustomSchema(
        tenant_id=req.tenant_id,
        name=req.name.strip(),
        domain=req.domain,
        description=(req.description or "").strip() or None,
        fields=req.fields,
        rules=req.rules,
        is_preset=False,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return JSONResponse(_schema_to_dict(s), status_code=201)


@router.get("/schemas/presets", summary="List system preset schemas")
async def list_presets() -> JSONResponse:
    return JSONResponse({"presets": _PRESETS, "total": len(_PRESETS)})


@router.post(
    "/schemas/from-preset/{preset_id}",
    status_code=status.HTTP_201_CREATED,
    summary="Clone a preset for this tenant",
)
async def clone_preset(
    preset_id: str,
    tenant_id: int = 1,
    db: Session = Depends(get_db),
) -> JSONResponse:
    preset = _PRESET_MAP.get(preset_id)
    if not preset:
        raise HTTPException(404, f"Preset '{preset_id}' not found")

    s = CustomSchema(
        tenant_id=tenant_id,
        name=preset["name"],
        domain=preset["domain"],
        description=preset.get("description"),
        fields=preset.get("fields", []),
        rules=preset.get("rules", []),
        is_preset=False,  # tenant-owned copy
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return JSONResponse({"schema": _schema_to_dict(s)}, status_code=201)


@router.post("/schemas/infer", summary="Infer schema from uploaded file")
async def infer_schema(
    file: UploadFile = File(...),
    schema_name: Optional[str] = Form(None),
    tenant_id: int = Form(1),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Analyse uploaded CSV/Excel/PDF and infer a field schema from column headers and sample data."""
    data = await file.read()
    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower()

    fields: List[Dict[str, Any]] = []

    try:
        if ext == "csv":
            fields = _infer_from_csv(data)
        elif ext in ("xlsx", "xlsm", "xls"):
            fields = _infer_from_excel(data)
        elif ext == "pdf":
            fields = _infer_from_pdf(data)
        else:
            raise HTTPException(400, f"Unsupported file type: .{ext}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"Schema inference failed: {exc}") from exc

    inferred_name = schema_name or filename.rsplit(".", 1)[0].replace("_", " ").title()

    return JSONResponse({
        "schema": {
            "name": inferred_name,
            "domain": _guess_domain(inferred_name, fields),
            "fields": fields,
            "rules": [],
        },
        "field_count": len(fields),
    })


@router.get("/schemas/{schema_id}", summary="Get a schema")
async def get_schema(
    schema_id: int,
    db: Session = Depends(get_db),
) -> JSONResponse:
    s = db.query(CustomSchema).filter(CustomSchema.id == schema_id).first()
    if not s:
        raise HTTPException(404, f"Schema {schema_id} not found")
    return JSONResponse(_schema_to_dict(s))


@router.put("/schemas/{schema_id}", summary="Update a schema")
async def update_schema(
    schema_id: int,
    req: SchemaUpdate,
    db: Session = Depends(get_db),
) -> JSONResponse:
    s = db.query(CustomSchema).filter(CustomSchema.id == schema_id).first()
    if not s:
        raise HTTPException(404, f"Schema {schema_id} not found")

    if req.name is not None:
        s.name = req.name.strip()
    if req.domain is not None:
        s.domain = req.domain
    if req.description is not None:
        s.description = req.description.strip() or None
    if req.fields is not None:
        s.fields = req.fields
    if req.rules is not None:
        s.rules = req.rules
    s.version = (s.version or 1) + 1

    db.commit()
    db.refresh(s)
    return JSONResponse(_schema_to_dict(s))


@router.delete("/schemas/{schema_id}", summary="Delete (soft) a schema")
async def delete_schema(
    schema_id: int,
    db: Session = Depends(get_db),
) -> JSONResponse:
    s = db.query(CustomSchema).filter(CustomSchema.id == schema_id).first()
    if not s:
        raise HTTPException(404, f"Schema {schema_id} not found")
    s.is_active = False
    db.commit()
    return JSONResponse({"deleted": True, "id": schema_id})


# ══════════════════════════════════════════════════════════════════════════════
# INFERENCE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_TYPE_PATTERNS = {
    "date":     re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{2}/\d{2}/\d{4}$|^\d{2}-[A-Za-z]{3}-\d{4}$"),
    "decimal":  re.compile(r"^-?[\d,]+\.\d+$|^-?[\d,]+$"),
    "integer":  re.compile(r"^\d+$"),
    "currency": re.compile(r"^[£$€]?-?[\d,]+\.?\d*$"),
}

_CURRENCY_HEADERS = re.compile(
    r"amount|total|gross|net|vat|tax|pay|salary|cost|price|value|fee|charge|balance|debit|credit",
    re.IGNORECASE,
)
_DATE_HEADERS = re.compile(r"date|period|week|month|year|due|from|to|ending", re.IGNORECASE)
_REQUIRED_HEADERS = re.compile(r"number|ref|id|name|description", re.IGNORECASE)


def _infer_field(header: str, samples: List[str]) -> Dict[str, Any]:
    clean_samples = [s.strip() for s in samples if s and s.strip()]
    inferred_type = "string"
    required = bool(_REQUIRED_HEADERS.search(header))

    if _DATE_HEADERS.search(header):
        inferred_type = "date"
    elif _CURRENCY_HEADERS.search(header):
        inferred_type = "decimal"
        required = True
    elif clean_samples:
        for type_name, pattern in _TYPE_PATTERNS.items():
            if all(pattern.match(s) for s in clean_samples[:5]):
                inferred_type = type_name
                break

    field: Dict[str, Any] = {
        "name": header.strip().lower().replace(" ", "_").replace("-", "_"),
        "label": header.strip(),
        "type": inferred_type,
        "required": required,
        "sample_values": clean_samples[:3],
    }
    return field


def _infer_from_csv(data: bytes) -> List[Dict[str, Any]]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    headers = list(rows[0].keys())
    fields = []
    for h in headers:
        samples = [r.get(h, "") for r in rows[:20]]
        fields.append(_infer_field(h, samples))
    return fields


def _infer_from_excel(data: bytes) -> List[Dict[str, Any]]:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if not header_row:
            return []
        headers = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(header_row)]
        data_rows = [list(r) for _, r in zip(range(20), rows_iter)]
        fields = []
        for i, h in enumerate(headers):
            if not h or h.startswith("col_"):
                continue
            samples = [str(r[i]) if i < len(r) and r[i] is not None else "" for r in data_rows]
            fields.append(_infer_field(h, samples))
        return fields
    except Exception as exc:
        raise RuntimeError(f"Excel inference failed: {exc}") from exc


def _infer_from_pdf(data: bytes) -> List[Dict[str, Any]]:
    """Try to extract table headers from PDF first page."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:3]:
                tables = page.extract_tables()
                for table in tables:
                    if table and len(table) >= 2:
                        headers = [str(c).strip() if c else f"col_{i}" for i, c in enumerate(table[0])]
                        data_rows = table[1:min(5, len(table))]
                        fields = []
                        for i, h in enumerate(headers):
                            if not h:
                                continue
                            samples = [str(r[i]) if i < len(r) and r[i] else "" for r in data_rows]
                            fields.append(_infer_field(h, samples))
                        if fields:
                            return fields
    except Exception:
        pass

    # Fall back to OCR
    try:
        from app.vision.vision import extract_text_from_pdf
        result = extract_text_from_pdf(data, use_ocr=True)
        text = result.get("text", "")
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if lines:
            # Treat first line as header row
            headers = re.split(r"\s{2,}|\t", lines[0])
            return [_infer_field(h, []) for h in headers if h.strip()]
    except Exception:
        pass

    return []


def _guess_domain(name: str, fields: List[Dict]) -> str:
    name_lower = name.lower()
    field_names = " ".join(f.get("name", "") for f in fields).lower()
    combined = f"{name_lower} {field_names}"

    if any(w in combined for w in ("invoice", "vat", "supplier")):
        return "invoice"
    if any(w in combined for w in ("timesheet", "hours", "week_ending")):
        return "timesheet"
    if any(w in combined for w in ("payroll", "paye", "ni_", "pension")):
        return "payroll"
    if any(w in combined for w in ("bank", "statement", "debit", "credit", "balance")):
        return "bank_statement"
    if any(w in combined for w in ("pension", "member", "tranche")):
        return "pension"
    return "general"
