"""Pipeline job store shared by every worker.

Jobs used to live in a per process dict, so with two workers a status poll could land on the worker that
did not run the job and get a 404, and a restart lost everything. Jobs are now rows in the database.
The job dict shape is unchanged. Results are stored as JSON (values that are not JSON become strings).
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base, SessionLocal

KEEP = dt.timedelta(days=7)           # finished jobs are deleted after this
STALE = dt.timedelta(minutes=45)      # a queued or running job nobody has touched for this long is marked failed
_PROGRESS_EVERY = 1.0                 # seconds between progress-only writes


class PipelineJobRow(Base):
    __tablename__ = "pipeline_jobs"
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    data: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


def _dump(d: Dict[str, Any]) -> str:
    return json.dumps(d, default=str)


_last_progress: Dict[str, float] = {}
_lock = threading.Lock()


def _ensure_table() -> None:
    from app.db.session import engine
    PipelineJobRow.__table__.create(bind=engine, checkfirst=True)


def create(tenant_id: str) -> str:
    _ensure_table()
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id, "tenant_id": tenant_id, "status": "queued", "stage": None, "progress": 0.0,
        "message": "Job created", "started_at": None, "completed_at": None, "results": None, "error": None,
    }
    db = SessionLocal()
    try:
        db.query(PipelineJobRow).filter(PipelineJobRow.created_at < dt.datetime.utcnow() - KEEP).delete()
        db.add(PipelineJobRow(job_id=job_id, tenant_id=tenant_id, status="queued", data=_dump(job)))
        db.commit()
    finally:
        db.close()
    return job_id


def update(job_id: str, **kwargs: Any) -> None:
    progress_only = set(kwargs) <= {"stage", "progress", "message"}
    now = time.monotonic()
    if progress_only:
        with _lock:
            if now - _last_progress.get(job_id, 0.0) < _PROGRESS_EVERY:
                return
            _last_progress[job_id] = now
    db = SessionLocal()
    try:
        row = db.get(PipelineJobRow, job_id)
        if row is None:
            return
        job = json.loads(row.data)
        job.update(kwargs)
        row.data = _dump(job)
        row.status = str(job.get("status", row.status))[:20]
        row.updated_at = dt.datetime.utcnow()
        db.commit()
    finally:
        db.close()
    if job.get("status") in ("completed", "failed"):
        _last_progress.pop(job_id, None)


def get(job_id: str) -> Optional[Dict[str, Any]]:
    db = SessionLocal()
    try:
        row = db.get(PipelineJobRow, job_id)
        if row is None:
            return None
        job = json.loads(row.data)
        if row.status in ("queued", "running") and dt.datetime.utcnow() - row.updated_at > STALE:
            job.update(status="failed", error="Interrupted: the service restarted or the job stalled", message="Pipeline failed")
            row.data, row.status, row.updated_at = _dump(job), "failed", dt.datetime.utcnow()
            db.commit()
        return job
    finally:
        db.close()


def list_for_tenant(tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = (db.query(PipelineJobRow).filter(PipelineJobRow.tenant_id == tenant_id)
                .order_by(PipelineJobRow.created_at.desc()).limit(limit).all())
        out = []
        for r in rows:
            j = json.loads(r.data)
            j.pop("results", None)   # the list is a summary; fetch /results for the payload
            out.append(j)
        return out
    finally:
        db.close()
