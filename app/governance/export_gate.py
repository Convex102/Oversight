from __future__ import annotations

from typing import Any, Dict, Tuple


class ExportGateError(RuntimeError):
    pass


def export_allowed(ctx: Dict[str, Any]) -> Tuple[bool, str]:
    gov = (ctx or {}).get("governance") or {}
    state = gov.get("state") or {}
    approvals = gov.get("approvals") or []

    if state.get("blocked"):
        return False, "governance_blocked"

    if state.get("requires_approval"):
        needed = [p for p in (state.get("approval_policies") or []) if not p.get("passed")]
        approved_policy_ids = {a.get("policy_id") for a in approvals if a.get("status") == "APPROVED"}
        for n in needed:
            if n.get("policy_id") not in approved_policy_ids:
                return False, "approval_missing"
        return True, "approved"

    return True, "ok"


def enforce_export_gate(ctx: Dict[str, Any]) -> None:
    ok, reason = export_allowed(ctx)
    if not ok:
        raise ExportGateError(f"Export blocked by governance: {reason}")
