from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger("asgl.pensions.auth")


@dataclass
class UserContext:
    user_id: str
    email: str = ""
    role: str = "reviewer"  # reviewer/manager/admin
    tenant_id: str = "am"

    def to_dict(self) -> Dict[str, Any]:
        return {"user_id": self.user_id, "email": self.email, "role": self.role, "tenant_id": self.tenant_id}


def get_user_from_headers(headers: Dict[str, str]) -> UserContext:
    """
    Phase 10: Minimal role/tenant context without adding full auth complexity.
    - ASGL_USER_ID
    - ASGL_USER_ROLE
    - ASGL_TENANT_ID
    Can be overridden by HTTP headers for internal deployments:
    - X-ASGL-User
    - X-ASGL-Role
    - X-ASGL-Tenant
    """
    user_id = headers.get("x-asgl-user") or os.environ.get("ASGL_USER_ID", "local_user")
    role = headers.get("x-asgl-role") or os.environ.get("ASGL_USER_ROLE", "admin")
    tenant_id = headers.get("x-asgl-tenant") or os.environ.get("ASGL_TENANT_ID", "am")
    email = headers.get("x-asgl-email") or os.environ.get("ASGL_USER_EMAIL", "")
    return UserContext(user_id=user_id, role=role, tenant_id=tenant_id, email=email)


def require_role(ctx: UserContext, allowed: set[str]) -> None:
    if ctx.role not in allowed:
        raise PermissionError(f"role {ctx.role} not allowed; requires one of {sorted(list(allowed))}")
