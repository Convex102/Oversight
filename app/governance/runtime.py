from __future__ import annotations

from typing import Any, Dict, List, Optional

from .audit import AuditLog
from .policies import evaluate_policies, DEFAULT_POLICIES
from .incidents import IncidentManager
from .models import ModelRegistry


class GovernanceRuntime:
    def __init__(self, *, tenant_id: str, client_id: str, run_id: str, logger=None):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.run_id = run_id
        self.logger = logger
        self.audit = AuditLog(tenant_id, client_id, run_id, logger=logger)
        self.incidents = IncidentManager(logger=logger)
        self.models = ModelRegistry(logger=logger)

    def bootstrap(self, ctx: Dict[str, Any]) -> None:
        ctx["governance"] = ctx.get("governance") or {}
        ctx["governance"]["audit_log"] = []
        ctx["governance"]["policy_decisions"] = []
        ctx["governance"]["incidents"] = []
        ctx["governance"]["model_registry"] = []
        self.audit.emit("governance.bootstrap", "Governance runtime initialised for run.", meta={"run_id": self.run_id})

    def evaluate(self, *, ctx: Dict[str, Any], findings: List[Dict[str, Any]], vertical: str) -> Dict[str, Any]:
        decisions = evaluate_policies(policies=DEFAULT_POLICIES, ctx=ctx, findings=findings, vertical=vertical)
        ctx["governance"]["policy_decisions"] = decisions

        blocked = [d for d in decisions if (not d.get("passed") and d.get("mode") == "block")]
        approvals = [d for d in decisions if (not d.get("passed") and d.get("mode") == "require_approval")]

        state = {
            "blocked": bool(blocked),
            "requires_approval": bool(approvals),
            "blocking_policies": blocked,
            "approval_policies": approvals,
        }
        ctx["governance"]["state"] = state
        self.audit.emit(
            "governance.policy_evaluated",
            "Governance policies evaluated.",
            meta={"vertical": vertical, "blocked": state["blocked"], "requires_approval": state["requires_approval"]},
        )
        return state

    def record_incident(self, *, ctx: Dict[str, Any], title: str, severity: str, category: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        inc = self.incidents.create(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            run_id=self.run_id,
            title=title,
            severity=severity,
            category=category,
            meta=meta,
        )
        ctx["governance"]["incidents"].append(inc.to_dict())
        self.audit.emit("governance.incident", title, severity="ERROR" if severity in {"HIGH", "CRITICAL"} else "WARN", meta=inc.to_dict())
        return inc.to_dict()

    def flush(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        ctx["governance"]["audit_log"] = self.audit.events
        ctx["governance"]["audit_summary"] = self.audit.summary()
        ctx["governance"]["model_registry"] = self.models.list()
        self.audit.emit("governance.flush", "Governance runtime flushed to ctx.", meta=ctx["governance"]["audit_summary"])
        ctx["governance"]["audit_log"] = self.audit.events
        return ctx["governance"]
