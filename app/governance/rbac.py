from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Any, Set


@dataclass
class Role:
    role_id: str
    title: str
    permissions: Set[str]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["permissions"] = sorted(list(self.permissions))
        return d


DEFAULT_ROLES: Dict[str, Role] = {
    "viewer": Role("viewer", "Viewer", {"governance.view"}),
    "reviewer": Role("reviewer", "Reviewer", {"governance.view", "governance.approve.low", "governance.annotate"}),
    "manager": Role(
        "manager",
        "Manager",
        {"governance.view", "governance.approve.low", "governance.approve.high", "governance.override.block", "governance.resolve.incident"},
    ),
    "compliance": Role(
        "compliance",
        "Compliance",
        {"governance.view", "governance.approve.low", "governance.approve.high", "governance.override.block", "governance.resolve.incident", "governance.manage.policy"},
    ),
    "admin": Role("admin", "Admin", {"governance.*"}),
}


def has_permission(role_id: str, permission: str) -> bool:
    role = DEFAULT_ROLES.get(role_id)
    if not role:
        return False
    if "governance.*" in role.permissions:
        return True
    return permission in role.permissions


def require_permission(role_id: str, permission: str) -> None:
    if not has_permission(role_id, permission):
        raise PermissionError(f"RBAC: role '{role_id}' missing permission '{permission}'")
