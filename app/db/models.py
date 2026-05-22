"""
Oversight ASGL v13.5 — SQLAlchemy Models
=========================================
All tables. Single source of truth.
"""
from __future__ import annotations

import datetime as dt
from sqlalchemy import (
    String, DateTime, Integer, ForeignKey, Boolean,
    Text, JSON, Index, Float, BigInteger,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


# ══════════════════════════════════════════════════════════════════════════════
# MIXINS
# ══════════════════════════════════════════════════════════════════════════════

class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, nullable=False
    )
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime, default=dt.datetime.utcnow,
        onupdate=dt.datetime.utcnow, nullable=True
    )


# ══════════════════════════════════════════════════════════════════════════════
# EXISTING MODELS (unchanged — kept verbatim for compatibility)
# ══════════════════════════════════════════════════════════════════════════════

class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), index=True)
    plan: Mapped[str] = mapped_column(String(50), default="basic")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="default")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Upload(Base, TimestampMixin):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_uri: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64), index=True)


class Template(Base, TimestampMixin):
    __tablename__ = "templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class UsageRecord(Base, TimestampMixin):
    __tablename__ = "usage_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    period_ym: Mapped[str] = mapped_column(String(7), index=True)
    packs_processed: Mapped[int] = mapped_column(Integer, default=0)
    compute_seconds: Mapped[int] = mapped_column(Integer, default=0)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SessionToken(Base, TimestampMixin):
    __tablename__ = "session_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)


# ══════════════════════════════════════════════════════════════════════════════
# NEW MODELS
# ══════════════════════════════════════════════════════════════════════════════


class Engagement(Base, TimestampMixin):
    """Client audit engagement — full lifecycle from draft to sign-off."""
    __tablename__ = "engagements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True, default=1)

    # Identity
    client_name: Mapped[str] = mapped_column(String(300))
    period: Mapped[str] = mapped_column(String(100))          # "FY 2025", "Q1 2025"
    engagement_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    regulatory_pack: Mapped[str] = mapped_column(String(80), default="frs102")
    preparer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    job_type: Mapped[str] = mapped_column(String(50), default="comparison")

    # Workflow status
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    # draft | submitted | ai_review | in_correction | reassessing | reviewed | signed_off | void

    # Counters (denormalised for list performance)
    open_points_count: Mapped[int] = mapped_column(Integer, default=0)
    resolved_points_count: Mapped[int] = mapped_column(Integer, default=0)
    run_count: Mapped[int] = mapped_column(Integer, default=0)

    # Sign-off
    signed_off_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signed_off_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    sign_off_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Latest pipeline result summary (cached for list)
    latest_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latest_run_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ReviewPoint(Base, TimestampMixin):
    """A single audit/DQ review point raised against an engagement."""
    __tablename__ = "review_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("engagements.id", ondelete="CASCADE"), index=True
    )
    run_number: Mapped[int] = mapped_column(Integer, default=1)

    # Classification
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    # high | medium | low
    category: Mapped[str] = mapped_column(String(80), default="difference")
    # difference | anomaly | missing | format | policy | ocr_confidence

    # Content
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    audit_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    section: Mapped[str | None] = mapped_column(String(200), nullable=True)
    field_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    value_a: Mapped[str | None] = mapped_column(String(500), nullable=True)   # working papers
    value_b: Mapped[str | None] = mapped_column(String(500), nullable=True)   # final accounts
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Workflow status
    status: Mapped[str] = mapped_column(String(40), default="open", index=True)
    # open | acknowledged | in_progress | corrected | waived | resolved | escalated

    # Resolution
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    waiver_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalated_to: Mapped[str | None] = mapped_column(String(200), nullable=True)
    escalation_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Correction evidence (file path or inline text)
    correction_file_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    correction_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class EngagementRun(Base, TimestampMixin):
    """One pipeline execution linked to an engagement."""
    __tablename__ = "engagement_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("engagements.id", ondelete="CASCADE"), index=True
    )
    run_number: Mapped[int] = mapped_column(Integer, default=1)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Job outcome
    status: Mapped[str] = mapped_column(String(40), default="queued")
    pipeline_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    review_points_raised: Mapped[int] = mapped_column(Integer, default=0)
    llm_narrative: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Files submitted
    file_a_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_b_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    label_a: Mapped[str | None] = mapped_column(String(200), nullable=True)
    label_b: Mapped[str | None] = mapped_column(String(200), nullable=True)


class AuditEvent(Base):
    """Immutable audit trail entry for an engagement."""
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("engagements.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(200), default="system")
    action: Mapped[str] = mapped_column(String(100))    # "created", "submitted", "point_raised", etc.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


# ── Notifications ─────────────────────────────────────────────────────────────

class Notification(Base):
    """User-facing notification (bell icon)."""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, index=True
    )

    # Content
    type: Mapped[str] = mapped_column(String(60))
    # job_complete | review_point_raised | sign_off_required | dq_complete | ocr_complete | info
    title: Mapped[str] = mapped_column(String(300))
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    # info | warning | error | success

    # Links
    engagement_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recipient_role: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # State
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


# ── Custom Schemas ────────────────────────────────────────────────────────────

class CustomSchema(Base, TimestampMixin):
    """Tenant-defined field schema for DQ/OCR validation."""
    __tablename__ = "custom_schemas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True, default=1)

    name: Mapped[str] = mapped_column(String(200), index=True)
    domain: Mapped[str] = mapped_column(String(80), default="general")
    # general | invoice | timesheet | bank_statement | payroll | pension | accountancy

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False)  # system-provided presets

    # Fields: [{name, type, required, format, validation_rules}]
    fields: Mapped[list] = mapped_column(JSON, default=list)
    # Rules: [{id, type, expression, message, severity}]
    rules: Mapped[list] = mapped_column(JSON, default=list)

    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ── Data Quality Jobs ─────────────────────────────────────────────────────────

class DQJob(Base, TimestampMixin):
    """A batch data quality / OCR job."""
    __tablename__ = "dq_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True, default=1)
    engagement_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    schema_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    job_type: Mapped[str] = mapped_column(String(40), default="dq")   # dq | ocr | full_pipeline
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)

    files_count: Mapped[int] = mapped_column(Integer, default=0)
    records_extracted: Mapped[int] = mapped_column(Integer, default=0)
    total_violations: Mapped[int] = mapped_column(Integer, default=0)
    high_severity: Mapped[int] = mapped_column(Integer, default=0)
    medium_severity: Mapped[int] = mapped_column(Integer, default=0)
    low_severity: Mapped[int] = mapped_column(Integer, default=0)
    population_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    download_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    review_points_created: Mapped[int] = mapped_column(Integer, default=0)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DQFile(Base, TimestampMixin):
    """One file processed within a DQ/OCR job."""
    __tablename__ = "dq_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dq_jobs.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    file_type: Mapped[str] = mapped_column(String(20), default="unknown")
    ocr_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ocr_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    records_extracted: Mapped[int] = mapped_column(Integer, default=0)
    violation_count: Mapped[int] = mapped_column(Integer, default=0)
    extracted_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DQViolation(Base, TimestampMixin):
    """A single data quality violation within a DQ job."""
    __tablename__ = "dq_violations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dq_jobs.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    severity: Mapped[str] = mapped_column(String(20), default="medium")
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    field: Mapped[str | None] = mapped_column(String(200), nullable=True)
    value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    population_affected: Mapped[float] = mapped_column(Float, default=0.0)
    is_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)


# ══════════════════════════════════════════════════════════════════════════════
# INDEXES
# ══════════════════════════════════════════════════════════════════════════════

Index("idx_jobs_tenant_status",     Job.tenant_id,          Job.status)
Index("idx_usage_tenant_period",    UsageRecord.tenant_id,  UsageRecord.period_ym, unique=True)
Index("idx_engagements_tenant",     Engagement.tenant_id,   Engagement.status)
Index("idx_review_points_eng",      ReviewPoint.engagement_id, ReviewPoint.status)
Index("idx_notifications_tenant",   Notification.tenant_id, Notification.is_read)
Index("idx_dq_jobs_tenant",         DQJob.tenant_id,        DQJob.status)
Index("idx_custom_schemas_tenant",  CustomSchema.tenant_id, CustomSchema.domain)
Index("idx_audit_events_eng",       AuditEvent.engagement_id, AuditEvent.ts)
