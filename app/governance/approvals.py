from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from .rbac import require_permission


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class ApprovalRecord:
    approval_id: str
    ts: str
    tenant_id: str
    client_id: str
    run_id: str
    policy_id: str
    decision_mode: str
    actor: str
    actor_role: str
    status: str
    note: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ApprovalManager:
    def __init__(self, logger=None):
        self.logger = logger
        self._items: Dict[str, ApprovalRecord] = {}

    def approve(
        self,
        *,
        tenant_id: str,
        client_id: str,
        run_id: str,
        policy_id: str,
        decision_mode: str,
        actor: str,
        actor_role: str,
        note: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> ApprovalRecord:
        perm = "governance.approve.low" if decision_mode == "require_approval" else "governance.approve.high"
        require_permission(actor_role, perm)

        approval_id = f"apr_{len(self._items)+1:06d}"
        rec = ApprovalRecord(
            approval_id=approval_id,
            ts=_utc(),
            tenant_id=tenant_id,
            client_id=client_id,
            run_id=run_id,
            policy_id=policy_id,
            decision_mode=decision_mode,
            actor=actor,
            actor_role=actor_role,
            status="APPROVED",
            note=note,
            meta=meta or {},
        )
        self._items[approval_id] = rec
        if self.logger:
            try:
                self.logger.info("governance.approval", extra={"approval_id": approval_id, "policy_id": policy_id, "actor": actor})
            except Exception:
                pass
        return rec

    def list(self, *, run_id: str = "") -> List[Dict[str, Any]]:
        out = []
        for rec in self._items.values():
            if run_id and rec.run_id != run_id:
                continue
            out.append(rec.to_dict())
        return sorted(out, key=lambda x: x["ts"])

    def has_approval(self, *, run_id: str, policy_id: str) -> bool:
        for rec in self._items.values():
            if rec.run_id == run_id and rec.policy_id == policy_id and rec.status == "APPROVED":
                return True
        return False
