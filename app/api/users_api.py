"""User management for Oversight-A.

Admins manage the users of their own tenant. A super_admin manages users in any tenant.
Nobody can assign a role above their own, change their own role, or deactivate themselves.
Cross tenant lookups return 404, same convention as the rest of the platform.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.auth_ext import set_must_change
from app.db.models import SessionToken, User
from app.db.session import get_db
from app.rbac import ASSIGNABLE, ROLES, Principal, require_perm

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class NewUser(BaseModel):
    email: str
    password: str
    role: str = "reviewer"


class UpdateUser(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


def _row(u: User) -> dict:
    return {"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active, "tenant_id": u.tenant_id}


def _check_role(p: Principal, role: str) -> None:
    if role not in ROLES:
        raise HTTPException(400, "Unknown role. Allowed: " + ", ".join(ROLES))
    if role not in ASSIGNABLE.get(p.role, frozenset()):
        raise HTTPException(403, "You cannot assign that role")


def _scope(db: Session, p: Principal):
    q = db.query(User)
    if p.role != "super_admin":
        q = q.filter(User.tenant_id == p.tenant_id)
    return q


@router.get("")
def list_users(p: Principal = Depends(require_perm("users.manage")), db: Session = Depends(get_db)):
    return {"users": [_row(u) for u in _scope(db, p).order_by(User.id).all()]}


@router.post("", status_code=201)
def create_user(body: NewUser, p: Principal = Depends(require_perm("users.manage")), db: Session = Depends(get_db)):
    email = (body.email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Invalid email")
    if len(body.password or "") < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    _check_role(p, body.role)
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Account already exists")
    u = User(tenant_id=p.tenant_id, email=email, password_hash=hash_password(body.password),
             role=body.role, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    set_must_change(db, u.id, True)
    return _row(u)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UpdateUser, p: Principal = Depends(require_perm("users.manage")),
                db: Session = Depends(get_db)):
    u = _scope(db, p).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "User not found")
    is_self = p.user is not None and p.user.id == u.id
    reset_for = None
    if body.role is not None:
        if is_self:
            raise HTTPException(403, "You cannot change your own role")
        if u.role == "super_admin" and p.role != "super_admin":
            raise HTTPException(403, "You cannot change a super admin")
        _check_role(p, body.role)
        u.role = body.role
    if body.is_active is not None:
        if is_self and body.is_active is False:
            raise HTTPException(403, "You cannot deactivate yourself")
        if u.role == "super_admin" and p.role != "super_admin":
            raise HTTPException(403, "You cannot change a super admin")
        u.is_active = body.is_active
        if not body.is_active:
            db.query(SessionToken).filter(SessionToken.user_id == u.id).delete()
    if body.password is not None:
        if len(body.password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters")
        if u.role == "super_admin" and p.role != "super_admin":
            raise HTTPException(403, "You cannot change a super admin")
        u.password_hash = hash_password(body.password)
        db.query(SessionToken).filter(SessionToken.user_id == u.id).delete()
        reset_for = u.id if not is_self else None
    db.commit()
    if reset_for is not None:
        set_must_change(db, reset_for, True)
    db.refresh(u)
    return _row(u)
