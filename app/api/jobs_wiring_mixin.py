"""
Mixin helpers to wire into existing jobs endpoints.
Call `preflight_and_audit_job_submit(...)` inside your POST /jobs/compare handler.
"""

import os
from typing import Dict, Any
from app.auth.models import Principal
from app.billing.metering import enforce_plan
from app.observability.audit import write_audit

DEFAULT_PLAN = os.getenv("ASGL_DEFAULT_PLAN","standard20")

def preflight_and_audit_job_submit(principal: Principal, payload: Dict[str,Any]) -> Dict[str,Any]:
    plan = payload.get("plan") or DEFAULT_PLAN
    enforce_plan(principal.tenant_id, plan)
    write_audit(
        event="job.submit",
        actor=principal.user_id,
        tenant_id=principal.tenant_id,
        resource="job",
        action="submit",
        meta={"plan": plan, "source": payload.get("source","api")}
    )
    payload["tenant_id"] = principal.tenant_id
    payload["user_id"] = principal.user_id
    payload["plan"] = plan
    return payload