"""UK GDPR helpers: erase one engagement's client content, and purge old job artifacts.

Erasure removes the content (files, review points, run results, data quality results, notifications) and blanks the
identifying fields on the engagement row. The engagement row itself (id, tenant, status "void") and its audit trail are kept
as the record that the erasure happened. The audit trail is not edited.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.db.models import (
    AuditEvent, DQFile, DQJob, DQViolation, Engagement, EngagementRun, Notification, ReviewPoint,
)

logger = logging.getLogger(__name__)
ERASED = "[erased]"


def erase_engagement(db: Session, tenant_id: int, eng_id: int, actor: str, reason: str = "") -> Optional[Dict[str, Any]]:
    """Erase the content of one engagement of this tenant. Returns None when it does not exist for the tenant.
    The caller commits."""
    from app.storage import storage

    eng = db.query(Engagement).filter(Engagement.id == eng_id, Engagement.tenant_id == tenant_id).first()
    if eng is None:
        return None
    if eng.status == "void" and eng.client_name == ERASED:
        return {"engagement_id": eng_id, "already_erased": True, "complete": True}

    uris = set()
    errors = []
    for (u,) in db.query(ReviewPoint.correction_file_uri).filter(
            ReviewPoint.engagement_id == eng_id, ReviewPoint.correction_file_uri.isnot(None)):
        uris.add(u)
    try:
        uris.update(storage.list_uris(f"engagements/{eng_id}/"))
    except Exception as exc:
        errors.append("list:" + type(exc).__name__)

    dq_ids = [j for (j,) in db.query(DQJob.id).filter(DQJob.tenant_id == tenant_id, DQJob.engagement_id == eng_id)]
    if dq_ids:
        for (u,) in db.query(DQFile.storage_uri).filter(DQFile.job_id.in_(dq_ids), DQFile.storage_uri.isnot(None)):
            uris.add(u)

    files_deleted = 0
    for u in sorted(uris):
        try:
            if storage.delete(u):
                files_deleted += 1
        except Exception as exc:
            errors.append(type(exc).__name__)

    counts = {}
    if dq_ids:
        counts["dq_violations"] = db.query(DQViolation).filter(DQViolation.job_id.in_(dq_ids)).delete(synchronize_session=False)
        counts["dq_files"] = db.query(DQFile).filter(DQFile.job_id.in_(dq_ids)).delete(synchronize_session=False)
        counts["dq_jobs"] = db.query(DQJob).filter(DQJob.id.in_(dq_ids)).delete(synchronize_session=False)
    counts["review_points"] = db.query(ReviewPoint).filter(ReviewPoint.engagement_id == eng_id).delete(synchronize_session=False)
    counts["runs"] = db.query(EngagementRun).filter(EngagementRun.engagement_id == eng_id).delete(synchronize_session=False)
    counts["notifications"] = db.query(Notification).filter(
        Notification.tenant_id == tenant_id, Notification.engagement_id == eng_id).delete(synchronize_session=False)

    eng.client_name = ERASED
    eng.engagement_ref = None
    eng.preparer_name = None
    eng.signed_off_by = None
    eng.sign_off_notes = None
    eng.latest_run_id = None
    eng.latest_run_summary = None
    eng.open_points_count = 0
    eng.resolved_points_count = 0
    eng.status = "void"

    db.add(AuditEvent(
        engagement_id=eng_id, actor=actor or "unknown", action="erasure_completed",
        detail="Client content erased on request" + ((": " + reason[:200]) if reason else ""),
        payload={"files_deleted": files_deleted, "rows_deleted": counts, "error_types": sorted(set(errors)),
                 "at": dt.datetime.utcnow().isoformat() + "Z"},
    ))
    db.flush()
    logger.info("engagement %s erased for tenant %s by %s: %s files, %s", eng_id, tenant_id, actor, files_deleted, counts)
    return {"engagement_id": eng_id, "complete": not errors, "files_deleted": files_deleted,
            "rows_deleted": counts, "error_types": sorted(set(errors))}


def purge_old_job_artifacts(tenant_id: int, days: int) -> Dict[str, Any]:
    """Delete files older than `days` under tenants/<tenant_id>/jobs/ (reports, results, error text). Local storage only;
    on S3 use a bucket lifecycle rule."""
    from app.storage import storage

    if storage.backend != "local":
        return {"skipped": "s3 backend: set a lifecycle rule on the bucket prefix tenants/%s/jobs/" % tenant_id, "deleted": 0}
    cutoff = time.time() - int(days) * 86400
    base = storage.local_root / "tenants" / str(int(tenant_id)) / "jobs"
    deleted = 0
    if base.exists():
        for p in base.rglob("*"):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    deleted += 1
            except OSError:
                continue
    return {"deleted": deleted, "older_than_days": int(days)}
