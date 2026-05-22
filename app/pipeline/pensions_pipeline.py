from __future__ import annotations

from typing import Dict, Any, List, Optional

from app.pipeline.pack_router import get_pack
from app.telemetry.client import track


def run_pensions_pack(*, tenant_id: str, scheme_id: str, rows: List[Dict[str, Any]], meta: Optional[Dict[str, Any]] = None, template_name: str = "unknown", sheet_name: Optional[str] = None, headers: Optional[List[str]] = None) -> Dict[str, Any]:
    meta = meta or {}
    headers = headers or []
    pack = get_pack("ASGL-P")
    track("pack.run.start", tenant_id=tenant_id, pack_id="ASGL-P", scheme_id=scheme_id, meta={"template": template_name})
    canon = pack["build_canonical"](scheme_id, rows, meta=meta)
    findings = pack["validate"](canon)
    fp = pack["fingerprint"](tenant_id, template_name, sheet_name, headers, column_count=len(headers))
    track("pack.pensions.fingerprint", tenant_id=tenant_id, pack_id="ASGL-P", scheme_id=scheme_id, meta={"fingerprint_key": fp.key(), "columns": len(headers), "template_name": template_name})
    track("pack.run.finish", tenant_id=tenant_id, pack_id="ASGL-P", scheme_id=scheme_id, meta={"members": len(canon.members), "services": len(canon.services), "results": len(canon.results), "findings": len(findings)})
    return {"pack_id": "ASGL-P", "scheme_id": scheme_id, "canonical": canon.to_dict(), "findings": [f.__dict__ for f in findings], "fingerprint_key": fp.key()}
