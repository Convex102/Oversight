from __future__ import annotations
from typing import Dict, Any

def enrich_report_with_di(report_payload: Dict[str,Any], di_out: Dict[str,Any])->Dict[str,Any]:
    ctx = di_out.get("ctx", {})
    fb = ctx.get("factbundle")
    drift = ctx.get("drift", {})
    report_payload["di"] = {
        "factbundle": fb,
        "drift": drift,
        "nodes": di_out.get("nodes", [])
    }
    return report_payload