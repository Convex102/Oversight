from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from .policies import GovernancePolicy, DEFAULT_POLICIES


@dataclass
class TenantGovernanceConfig:
    tenant_id: str
    policies: List[GovernancePolicy]
    approvals_map: Dict[str, List[str]]
    export_gate_enabled: bool = True
    pii_redaction_enabled: bool = True
    drift_monitor_enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["policies"] = [p.to_dict() for p in self.policies]
        return d


DEFAULT_APPROVALS_MAP = {
    "require_approval": ["reviewer", "manager", "compliance", "admin"],
    "block": ["manager", "compliance", "admin"],
}


def load_tenant_config(tenant_id: str, *, raw: Optional[Dict[str, Any]] = None) -> TenantGovernanceConfig:
    if not raw:
        return TenantGovernanceConfig(tenant_id=tenant_id, policies=DEFAULT_POLICIES, approvals_map=DEFAULT_APPROVALS_MAP)

    policies_raw = raw.get("policies") or []
    policies: List[GovernancePolicy] = []
    for p in policies_raw:
        try:
            policies.append(GovernancePolicy(**p))
        except Exception:
            continue
    if not policies:
        policies = DEFAULT_POLICIES

    return TenantGovernanceConfig(
        tenant_id=tenant_id,
        policies=policies,
        approvals_map=raw.get("approvals_map") or DEFAULT_APPROVALS_MAP,
        export_gate_enabled=bool(raw.get("export_gate_enabled", True)),
        pii_redaction_enabled=bool(raw.get("pii_redaction_enabled", True)),
        drift_monitor_enabled=bool(raw.get("drift_monitor_enabled", True)),
    )
