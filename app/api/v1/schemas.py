"""Pydantic request/response models for Oversight Labs v1 API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal
from datetime import datetime
from pydantic import BaseModel, Field


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "oversight-labs-api"
    version: str = "13.5.0"
    timestamp: str
    engines: Dict[str, str] = Field(default_factory=dict)


# ── Job management ────────────────────────────────────────────────────────────

class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    stage: Optional[str] = None
    progress: Optional[float] = None
    message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class JobRunResponse(BaseModel):
    job_id: str
    status: str
    message: str
    artifacts: Optional[Dict[str, Any]] = None


class JobResultsResponse(BaseModel):
    job_id: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    tables: Optional[List[Dict[str, Any]]] = None
    validations: Optional[Dict[str, Any]] = None
    asgl_a: Optional[Dict[str, Any]] = None
    asgl_c: Optional[Dict[str, Any]] = None
    di: Optional[Dict[str, Any]] = None
    artifacts: Optional[Dict[str, Any]] = None
    report_path: Optional[str] = None
    error: Optional[str] = None


# ── Pipeline run (synchronous) ────────────────────────────────────────────────

class PipelineRunOptions(BaseModel):
    vertical: Optional[str] = Field(None, description="financial|accountancy|pensions|medical|defence")
    max_tables: int = 3
    validation_schema_override: Optional[str] = None
    enable_shap: bool = True
    enable_llm_summary: bool = False
    pack_id: Optional[str] = None


# ── Normalisation (Phase 1 + Phase 2) ─────────────────────────────────────────

class ColumnPreview(BaseModel):
    """Preview of a column for mapping — header + a few sample values."""
    headers: List[str]
    sample_values: Dict[str, List[Any]] = Field(default_factory=dict)
    sheet_names: Optional[List[str]] = None


class NormaliseRequest(BaseModel):
    workbook_name: str = "uploaded_file.xlsx"
    vertical: Optional[str] = None
    preview: ColumnPreview
    template_id: Optional[int] = None
    top_k: int = 5
    use_llm: bool = False


class ColumnSignatureOut(BaseModel):
    header: str
    inferred_type: str
    position: int
    sample_values: List[str] = Field(default_factory=list)


class TemplateMatch(BaseModel):
    id: int
    name: str
    score: float


class NormaliseResponse(BaseModel):
    workbook_name: str
    columns: List[ColumnSignatureOut]
    embeddings_dim: int
    retrieved_templates: List[TemplateMatch]
    selected_template_id: Optional[int]
    field_selections: Optional[Dict[str, Any]] = None
    confidence_summary: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: int


# ── Comparison (working papers ↔ final accounts, etc.) ────────────────────────

class CompareRequest(BaseModel):
    label_a: str = "document_a"
    label_b: str = "document_b"
    vertical: Optional[str] = None
    options: Optional[Dict[str, Any]] = None


class CompareResponse(BaseModel):
    compare_id: str
    label_a: str
    label_b: str
    table_count: int
    delta_summary: Dict[str, Any]
    differences: List[Dict[str, Any]] = Field(default_factory=list)
    shap_attribution: Optional[Dict[str, Any]] = None
    compare_pdf_path: Optional[str] = None
    compare_csv_path: Optional[str] = None


# ── Column encoder (Phase 2) ──────────────────────────────────────────────────

class ColumnEncodeRequest(BaseModel):
    preview: ColumnPreview
    workbook_name: str = ""
    return_embeddings: bool = False


class ColumnEncodeResponse(BaseModel):
    workbook_name: str
    signatures: List[ColumnSignatureOut]
    embeddings: Optional[List[List[float]]] = None
    embedding_dim: int
    inferred_types: Dict[str, str]


# ── Flywheel ──────────────────────────────────────────────────────────────────

class FlywheelCaptureRequest(BaseModel):
    tenant_id: str
    client_id: str
    run_id: str
    vertical: str = "accountancy"
    event_type: str = Field(..., description="mapping_proposed|mapping_confirmed|reviewer_override|...")
    actor: str = "user"
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    payload: Dict[str, Any]
    meta: Optional[Dict[str, Any]] = None


class FlywheelConfirmRequest(BaseModel):
    tenant_id: str
    client_id: str
    run_id: str
    vertical: str = "accountancy"
    workbook_name: str
    template_id: Optional[int] = None
    job_id: Optional[int] = None
    field_selections: Dict[str, str] = Field(
        ...,
        description="Mapping of canonical_field -> source_column_name confirmed by a reviewer"
    )
    preview: ColumnPreview


class FlywheelHealthResponse(BaseModel):
    tenant_id: str
    client_id: Optional[str]
    run_id: Optional[str]
    vertical: str
    dataset: Dict[str, Any]
    queue: Dict[str, Any]
    recent_training: List[Dict[str, Any]]


# ── Governance ────────────────────────────────────────────────────────────────

class AuditEventOut(BaseModel):
    ts: str
    tenant_id: str
    client_id: str
    run_id: str
    event_type: str
    actor: str
    severity: str
    message: str
    meta: Dict[str, Any]
    sha256: str


class AuditSummaryResponse(BaseModel):
    schema: str
    tenant_id: str
    client_id: str
    run_id: str
    generated_at: str
    event_count: int
    severity_counts: Dict[str, int]
    events_sha256: str


# ── DI orchestrator ───────────────────────────────────────────────────────────

class DIRunRequest(BaseModel):
    tenant_id: str
    job_id: str
    schema_domain: Optional[str] = ""
    schema_id: Optional[str] = ""
    schema_version: Optional[str] = ""
    spreadsheet_path: Optional[str] = None
    pdf_path: Optional[str] = None
    mapping: Optional[Dict[str, Any]] = None
    previous_mapping_fp: Optional[str] = ""


class DIRunResponse(BaseModel):
    tenant_id: str
    job_id: str
    factbundle: Dict[str, Any]
    drift: Dict[str, Any]
    tables: Dict[str, Any]
    node_results: List[Dict[str, Any]]
    warnings: List[str] = Field(default_factory=list)


# ── Generic error ─────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None
