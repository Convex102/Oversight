"""
Call `audit_report_access(principal, job_id)` in your report download endpoint.
"""
from app.auth.models import Principal
from app.observability.audit import write_audit

def audit_report_access(principal: Principal, job_id: str):
    write_audit("report.access", principal.user_id, principal.tenant_id, "report", "read", {"job_id": job_id})