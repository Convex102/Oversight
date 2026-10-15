"""Oversight-A role based access control.

Roles: super_admin, admin, reviewer, accountant.
One table (ROLE_PERMS) decides what each role can do. Every router is gated in app/main.py
with group_guard(group), and the UI asks /auth/me for the permission list so it can hide what
a user cannot use. The server is the enforcement point, the UI hiding is cosmetic.

A request is authenticated by a session cookie (a person, role from users.role) or by a tenant
API key (a program, treated as role "api" which carries the admin work permissions but cannot
manage users or API keys).
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import ApiKey, SessionToken, Tenant, User
from app.db.session import get_db
from app.security import hash_api_key

COOKIE_NAME = "asgl_session"

ROLES = ("super_admin", "admin", "reviewer", "accountant")

# ── permission catalogue ────────────────────────────────────────────────────
WORK_READ = {
    "pipeline.read", "engagements.read", "schemas.read", "reports.read",
    "dq.read", "analytics.read", "notifications.read",
}
REVIEWER = WORK_READ | {
    "pipeline.run", "review.resolve", "engagements.write", "reports.generate",
    "dq.run", "compare.run", "analytics.run",
}
ACCOUNTANT = WORK_READ | {
    "pipeline.run", "engagements.write", "reports.generate", "compare.run", "billing.read",
}
ADMIN_WORK = REVIEWER | {"schemas.write", "engagements.signoff", "billing.read"}
ADMIN = ADMIN_WORK | {"users.manage", "keys.manage"}
SUPER_ADMIN = ADMIN | {"tenants.manage", "users.manage_all"}
API_KEY = ADMIN_WORK  # programs: all work permissions, no user or key management

ROLE_PERMS: Dict[str, FrozenSet[str]] = {
    "super_admin": frozenset(SUPER_ADMIN),
    "admin": frozenset(ADMIN),
    "reviewer": frozenset(REVIEWER),
    "accountant": frozenset(ACCOUNTANT),
    "api": frozenset(API_KEY),
}

# Roles a given role may assign to other users (never above its own level).
ASSIGNABLE: Dict[str, FrozenSet[str]] = {
    "super_admin": frozenset(ROLES),
    "admin": frozenset({"admin", "reviewer", "accountant"}),
}

# ── router groups: (read permission, write permission) ───────────────────────
GROUPS: Dict[str, tuple] = {
    "pipeline": ("pipeline.read", "pipeline.run"),
    "compare": ("compare.run", "compare.run"),
    "engagements": ("engagements.read", "engagements.write"),
    "notifications": ("notifications.read", "notifications.read"),
    "schemas": ("schemas.read", "schemas.write"),
    "dq": ("dq.read", "dq.run"),
    "quality": ("dq.read", "dq.run"),
    "reports": ("reports.read", "reports.generate"),
    "analytics": ("analytics.read", "analytics.run"),
}

# (path fragment, method set or None for any, permission) first match wins, overrides the group.
OVERRIDES = (
    ("/sign-off", {"POST"}, "engagements.signoff"),
    ("/review-points/", {"POST"}, "review.resolve"),
    ("/review-queue", {"POST"}, "review.resolve"),
    ("/flywheel/queue", None, "review.resolve"),
)

# Liveness style paths stay public.
PUBLIC_SUFFIXES = ("/health", "/ping", "/version")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    kind: str                  # "user" or "api"
    role: str                  # one of ROLES or "api"
    tenant_id: int
    user: Optional[User] = None

    @property
    def perms(self) -> FrozenSet[str]:
        return ROLE_PERMS.get(self.role, frozenset())

    def can(self, perm: str) -> bool:
        return perm in self.perms


def _api_key_from(request: Request) -> Optional[str]:
    x = request.headers.get("x-api-key")
    if settings.AUTH_ACCEPT_X_API_KEY and x:
        return x.strip()
    a = request.headers.get("authorization", "")
    if settings.AUTH_ACCEPT_BEARER and a.lower().startswith("bearer "):
        return a.split(" ", 1)[1].strip()
    return None


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        th = hashlib.sha256(raw.encode()).hexdigest()
        sess = db.query(SessionToken).filter(SessionToken.token_hash == th).first()
        if sess and sess.expires_at >= dt.datetime.utcnow():
            user = db.query(User).filter(User.id == sess.user_id, User.is_active == True).first()  # noqa: E712
            if user:
                tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
                if tenant and tenant.is_active:
                    role = user.role if user.role in ROLES else "reviewer"
                    return Principal("user", role, int(user.tenant_id), user)
        raise HTTPException(status_code=401, detail="Session expired")

    key = _api_key_from(request)
    if key:
        row = db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(key), ApiKey.is_active == True).first()  # noqa: E712
        if not row:
            raise HTTPException(status_code=401, detail="Invalid API key")
        tenant = db.query(Tenant).filter(Tenant.id == row.tenant_id, Tenant.is_active == True).first()  # noqa: E712
        if not tenant:
            raise HTTPException(status_code=403, detail="Tenant inactive")
        return Principal("api", "api", int(row.tenant_id), None)

    raise HTTPException(status_code=401, detail="Not authenticated")


def require_perm(perm: str) -> Callable[..., Principal]:
    def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if not p.can(perm):
            raise HTTPException(status_code=403, detail="Your role does not allow this")
        return p
    return _dep


def group_guard(group: str) -> Callable[..., Optional[Principal]]:
    """Router level dependency: authenticate, then check the role against the group's permission.

    Read methods need the group's read permission, everything else its write permission, with
    OVERRIDES for sign off and review actions. Liveness paths pass with no credentials.
    """
    read_perm, write_perm = GROUPS[group]

    def _dep(request: Request, db: Session = Depends(get_db)) -> Optional[Principal]:
        path = request.url.path
        if path.endswith(PUBLIC_SUFFIXES):
            return None
        p = get_principal(request, db)
        need = read_perm if request.method in SAFE_METHODS else write_perm
        for frag, methods, perm in OVERRIDES:
            if frag in path and (methods is None or request.method in methods):
                need = perm
                break
        if not p.can(need):
            raise HTTPException(status_code=403, detail="Your role does not allow this")
        return p
    return _dep
