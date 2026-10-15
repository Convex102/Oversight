"""Per company product licences and user display names for Oversight.

Products: A (Accountancy), P (Pensions), F (Fusion). A company only sees and can only call what it is licensed for.
A tenant with no licence rows gets DEFAULT_PRODUCTS (A only). Rows are managed by a super_admin through
/api/v1/tenants. The server enforces the licence in rbac.group_guard, the UI only hides what is not licensed.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.models import Tenant, User
from app.db.session import Base, get_db

PRODUCTS = {"A": "Oversight-A", "P": "Oversight-P", "F": "Oversight-F"}
PRODUCT_DESC = {"A": "Accountancy", "P": "Pensions", "F": "Fusion"}
DEFAULT_PRODUCTS = ["A"]


class TenantProduct(Base):
    __tablename__ = "tenant_products"
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    product: Mapped[str] = mapped_column(String(1), primary_key=True)
    plan: Mapped[str] = mapped_column(String(30), default="standard")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class UserProfile(Base):
    __tablename__ = "user_profile"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)


def tenant_products(db: Session, tenant_id: int) -> List[str]:
    rows = db.query(TenantProduct).filter(TenantProduct.tenant_id == tenant_id).all()
    if not rows:
        return list(DEFAULT_PRODUCTS)
    return [c for c in PRODUCTS if any(r.product == c and r.enabled for r in rows)]


def licensed(db: Session, tenant_id: int, product: str) -> bool:
    return product in tenant_products(db, tenant_id)


def set_products(db: Session, tenant_id: int, products: List[str]) -> List[str]:
    keep = sorted({p.upper() for p in products if p.upper() in PRODUCTS})
    db.query(TenantProduct).filter(TenantProduct.tenant_id == tenant_id).delete()
    for p in keep:
        db.add(TenantProduct(tenant_id=tenant_id, product=p, plan="standard", enabled=True))
    db.commit()
    return keep


def display_name(db: Session, user: User) -> str:
    prof = db.get(UserProfile, user.id)
    if prof and prof.display_name and prof.display_name.strip():
        return prof.display_name.strip()
    local = (user.email or "").split("@")[0]
    return local[:1].upper() + local[1:]


def set_display_name(db: Session, user_id: int, name: Optional[str]) -> None:
    name = (name or "").strip()[:120] or None
    prof = db.get(UserProfile, user_id)
    if prof is None:
        db.add(UserProfile(user_id=user_id, display_name=name))
    else:
        prof.display_name = name
    db.commit()


def identity(db: Session, user: Optional[User], tenant_id: int) -> dict:
    """Welcome data for the UI: who, which company, which products."""
    t = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    name = display_name(db, user) if user else None
    return {
        "name": name,
        "first_name": (name or "").split(" ")[0] if name else None,
        "company": t.name if t else None,
        "products": [{"code": p, "name": PRODUCTS[p], "desc": PRODUCT_DESC[p]} for p in tenant_products(db, tenant_id)],
    }


# ── super admin management of licences ──────────────────────────────────────
router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


class ProductsIn(BaseModel):
    products: List[str]


from app.rbac import Principal, require_perm  # noqa: E402  (rbac imports this module lazily, so import after definitions)


@router.get("")
def list_tenants(p: Principal = Depends(require_perm("tenants.manage")), db: Session = Depends(get_db)):
    out = []
    for t in db.query(Tenant).order_by(Tenant.id).all():
        out.append({"id": t.id, "name": t.name, "active": t.is_active, "products": tenant_products(db, t.id),
                    "explicit": db.query(TenantProduct).filter(TenantProduct.tenant_id == t.id).count() > 0})
    return {"tenants": out}


@router.put("/{tenant_id}/products")
def put_products(tenant_id: int, body: ProductsIn, p: Principal = Depends(require_perm("tenants.manage")),
                 db: Session = Depends(get_db)):
    if not db.query(Tenant).filter(Tenant.id == tenant_id).first():
        raise HTTPException(404, "Tenant not found")
    bad = [x for x in body.products if x.upper() not in PRODUCTS]
    if bad:
        raise HTTPException(400, "Unknown product. Allowed: A, P, F")
    return {"tenant_id": tenant_id, "products": set_products(db, tenant_id, body.products)}
