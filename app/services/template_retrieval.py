"""Thin wrapper around template retrieval that emits telemetry hit/miss."""

from __future__ import annotations

from typing import Optional

from app.services.telemetry import track_template_retrieved
from app.logging import get_logger

log = get_logger(__name__)

def retrieve_template(*, tenant_id: str, job_id: Optional[str], vertical: Optional[str], source_column: str, retriever_fn, **kwargs):
    tpl = None
    try:
        try:
            tpl = retriever_fn(source_column=source_column, **kwargs)
        except TypeError:
            tpl = retriever_fn(source_column, **kwargs)
    except Exception as e:
        log.exception("template_retrieve_failed", error=str(e))
        tpl = None
    try:
        tpl_id = None
        if isinstance(tpl, dict):
            tpl_id = tpl.get("id") or tpl.get("template_id")
        else:
            tpl_id = getattr(tpl, "id", None)
        track_template_retrieved(tenant_id=tenant_id, job_id=job_id, vertical=vertical, template_id=str(tpl_id) if tpl_id else None, hit=bool(tpl))
    except Exception:
        pass
    return tpl
