"""Phase 2 v6: Auto-template update from Active Learning resolutions."""

from __future__ import annotations

import os, time
from typing import Any, Optional

from app.logging import get_logger

log = get_logger(__name__)

def enabled() -> bool:
    return os.getenv("ASGL_TEMPLATE_AUTOLEARN_ENABLE", "1") not in ("0","false","FALSE","no","NO")

def _template_name(vertical: Optional[str], pack_id: Optional[str], workbook: Optional[str]) -> str:
    v = (vertical or "A").upper()
    p = pack_id or "default"
    w = workbook or "workbook"
    return f"AUTO/{v}/{p}/{w}"

def upsert_template_from_resolution(
    *,
    db: Any,
    tenant_id: str,
    vertical: Optional[str],
    pack_id: Optional[str],
    workbook: Optional[str],
    source_column: str,
    canonical_field: str,
    template_id: Optional[int] = None,
) -> Optional[int]:
    if not enabled():
        return None
    try:
        from app.db.models import Template
    except Exception as e:
        log.info("template_autolearn_models_missing", error=str(e))
        return None

    try:
        name = _template_name(vertical, pack_id, workbook)
        tpl = None
        if template_id:
            tpl = db.query(Template).filter(Template.id == int(template_id)).first()
        if not tpl:
            tpl = db.query(Template).filter(Template.tenant_id == str(tenant_id)).filter(Template.name == name).first()

        if tpl:
            payload = tpl.payload or {}
            fs = payload.get("field_selections") or payload.get("mappings") or {}
            if not isinstance(fs, dict):
                fs = {}
            fs[str(canonical_field)] = str(source_column)
            payload["field_selections"] = fs
            payload["vertical"] = vertical or payload.get("vertical") or "A"
            if pack_id:
                payload["pack_id"] = pack_id
            payload["updated_ts"] = int(time.time())
            tpl.payload = payload
            db.add(tpl)
            db.commit()
            log.info("template_autolearn_updated", tenant_id=str(tenant_id), template_id=getattr(tpl,"id",None), name=name)
            return getattr(tpl,"id",None)

        payload = {
            "field_selections": {str(canonical_field): str(source_column)},
            "vertical": (vertical or "A"),
            "pack_id": pack_id,
            "created_ts": int(time.time()),
        }
        tpl = Template(tenant_id=str(tenant_id), name=name, payload=payload)
        db.add(tpl)
        db.commit()
        log.info("template_autolearn_created", tenant_id=str(tenant_id), template_id=getattr(tpl,"id",None), name=name)
        return getattr(tpl,"id",None)
    except Exception as e:
        log.exception("template_autolearn_upsert_failed", error=str(e))
        try:
            db.rollback()
        except Exception:
            pass
        return None
