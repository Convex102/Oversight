"""
Oversight ASGL v13.5 â€” FastAPI Main Application
================================================
Production entry point. All routers wired at correct prefixes.
Fixes applied:
  - analytics prefix (was /api/v1/api/v1 double-prefix, now /analytics)
  - quality prefix (now consistent /api/v1)
  - compare endpoint deduplicated (compare_routes.py is canonical)
  - engagements, review-points, notifications, schemas, dq all wired
"""
from __future__ import annotations

import datetime as dt
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import hashlib

from fastapi import FastAPI, Request, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.logging import configure_logging
from app.ratelimit import limiter
from app.db.session import engine, Base, get_db
from app.db.models import Tenant, User, SessionToken, ApiKey
from app.security import generate_api_key
from app.auth import hash_password, verify_password, new_session_token, token_expiry
from app.web_auth import get_current_user, COOKIE_NAME
from app.rbac import group_guard, get_principal, Principal, ROLE_PERMS

logger = configure_logging()


# â”€â”€ Lifespan â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


# â”€â”€ App â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
app = FastAPI(
    title=settings.APP_NAME,
    version="13.5.0",
    lifespan=lifespan,
)
app.state.limiter = limiter

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENV == "dev" else [settings.PUBLIC_BASE_URL],
    allow_headers=["*"],
    allow_methods=["*"],
    allow_credentials=True,
)

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
templates = Jinja2Templates(directory="app/web/templates")


# â”€â”€ Global exception handler â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# â”€â”€ Template context helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _ctx(request: Request, title: str = "") -> Dict[str, Any]:
    return {"request": request, "year": dt.datetime.utcnow().year,
            "title": title or "Oversight"}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SAFE ROUTER MOUNT HELPER
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _mount(router, prefix: str = "", tags=None, group: str = None):
    try:
        # SECURITY-FIX 2026-10-15: group makes every route in the router require a login (session or
        # tenant API key) and the role permission for that group, see app/rbac.py.
        deps = [Depends(group_guard(group))] if group else None
        app.include_router(router, prefix=prefix, tags=tags or [], dependencies=deps)
        logger.info("âœ“ Router mounted: %s", prefix or "(root)")
    except Exception as exc:
        logger.error("âœ— Router mount failed [%s]: %s", prefix or "(root)", exc)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CORE v1 ROUTERS â€” ordered by dependency
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# 1. Core pipeline, normalise, flywheel, governance, DI â€” the main v1 router
from app.api.v1.routes import router as v1_router
_mount(v1_router, prefix="/api/v1", tags=["v1"], group="pipeline")

# 2. Add-on routes (version, flywheel train, DI review queue, governance export)
from app.api.v1.routes_addon import router as v1_addon_router
_mount(v1_addon_router, prefix="/api/v1", tags=["v1"], group="pipeline")

# 3. Compare â€” canonical implementation (asgla_compare pipeline, full DiffRow output)
try:
    from app.api.v1.compare_routes import router as compare_router
    _mount(compare_router, prefix="/api/v1", tags=["comparison"], group="compare")
except Exception as _compare_exc:
    import traceback as _tb
    logger.error("COMPARE ROUTES LOAD FAILED: %s", _compare_exc)
    logger.error(_tb.format_exc())


# 4. Engagements + Review Points lifecycle
from app.api.v1.engagements_routes import router as engagements_router
_mount(engagements_router, prefix="/api/v1", tags=["engagements"], group="engagements")

# 5. Notifications
from app.api.v1.notifications_routes import router as notifications_router
_mount(notifications_router, prefix="/api/v1", tags=["notifications"], group="notifications")

# 6. Custom Schemas
from app.api.v1.schemas_routes import router as schemas_router
_mount(schemas_router, prefix="/api/v1", tags=["schemas"], group="schemas")

# 7. Data Quality / OCR
from app.api.v1.dq_routes import router as dq_router
_mount(dq_router, prefix="/api/v1", tags=["dq"], group="dq")

# 8. Quality â€” prefix /quality (router has prefix="/quality", mounted at /api/v1)
from app.quality.routes import router as quality_router
_mount(quality_router, prefix="/api/v1", tags=["quality"], group="quality")

# 5. Reporting â€” evidence packs
from app.reporting.report_routes import router as reports_router
_mount(reports_router, prefix="/api/v1", tags=["reporting"], group="reports")

# 6. Analytics â€” prefix /analytics (router has prefix="/analytics", mounted at /api/v1)
from app.analytics.routes import router as analytics_router
_mount(analytics_router, prefix="/api/v1", tags=["analytics"], group="analytics")


# â”€â”€ OPTIONAL ROUTERS (defensive â€” failure doesn't crash startup) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# 9. User management (admin and super_admin only, enforced inside the router)
from app.api.users_api import router as users_router
_mount(users_router, tags=["users"])

_optional = [
    ("app.api.health_api",            "router", None),
    ("app.api.pensions_api",          "router", None),
    ("app.packs.pensions.pension_batch_task", "pension_batch_router", None),
    ("app.api.pensions_ui_api",       "router", None),
    ("app.api.packs_api",             "router", None),
    ("app.api.di_health_api",         "router", None),
    ("app.api.di_admin_api",          "router", None),
    ("app.api.di_learning_api",       "router", None),
    ("app.api.active_learning_routes","router", None),
    ("app.api.telemetry_routes",      "router", None),
    ("app.api.governance_ui_api",     "router", None),
]

for _mod, _attr, _prefix in _optional:
    try:
        import importlib as _il
        _m = _il.import_module(_mod)
        _r = getattr(_m, _attr, None)
        if _r is not None:
            _mount(_r, prefix=_prefix or "", tags=[_mod.split(".")[-1]])
    except Exception as _exc:
        logger.debug("Optional router [%s] skipped: %s", _mod, _exc)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SYSTEM ENDPOINTS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/health", tags=["system"], include_in_schema=False)
@app.head("/health", include_in_schema=False)
async def health():
    return {"ok": True, "ts": dt.datetime.utcnow().isoformat() + "Z", "version": "13.5.0"}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# HTML PAGES
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _page_principal(request: Request, db: Session):
    """Return the logged in Principal for a page request, or None (caller redirects to /signin)."""
    try:
        return get_principal(request, db)
    except HTTPException:
        return None


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root(request: Request, db: Session = Depends(get_db)):
    # SECURITY-FIX 2026-10-15: the Command Centre was served to anyone. Now it needs a session.
    if _page_principal(request, db) is None:
        return RedirectResponse(url="/signin", status_code=303)
    for name in ["Command_Center_v14.html", "Command_Center_v13.html", "Command_Center_v12.html", "Command_Center_v11.html", "Command_Center_v9.html", "templates/Command Center.html", "Command Center.html", "index.html"]:
        try:
            return templates.TemplateResponse(name, {"request": request})
        except Exception:
            continue
    return HTMLResponse("<h1>Oversight ASGL</h1><p>No template found in app/web/templates/</p>")


@app.get("/comparison_only", response_class=HTMLResponse, include_in_schema=False)
async def comparison_only(request: Request, db: Session = Depends(get_db)):
    if _page_principal(request, db) is None:
        return RedirectResponse(url="/signin", status_code=303)
    return templates.TemplateResponse("comparison_only.html", {"request": request})


@app.get("/technology",  response_class=HTMLResponse, include_in_schema=False)
def technology(request: Request): return templates.TemplateResponse("technology.html", _ctx(request, "Technology"))

@app.get("/pricing",     response_class=HTMLResponse, include_in_schema=False)
def pricing(request: Request): return templates.TemplateResponse("pricing.html", _ctx(request, "Pricing"))

@app.get("/about",       response_class=HTMLResponse, include_in_schema=False)
def about(request: Request): return templates.TemplateResponse("about.html", _ctx(request, "About"))

@app.get("/contact",     response_class=HTMLResponse, include_in_schema=False)
def contact(request: Request): return templates.TemplateResponse("contact.html", _ctx(request, "Contact"))

@app.get("/demo",        response_class=HTMLResponse, include_in_schema=False)
def demo(request: Request): return templates.TemplateResponse("demo.html", _ctx(request, "Demo"))

@app.get("/runner",      response_class=HTMLResponse, include_in_schema=False)
def runner(request: Request, db: Session = Depends(get_db)):
    if _page_principal(request, db) is None:
        return RedirectResponse(url="/signin", status_code=303)
    return templates.TemplateResponse("runner.html", _ctx(request, "Runner"))

@app.get("/signup",      response_class=HTMLResponse, include_in_schema=False)
def signup(request: Request): return templates.TemplateResponse("signup.html", _ctx(request, "Sign Up"))

@app.get("/signin",      response_class=HTMLResponse, include_in_schema=False)
def signin(request: Request): return templates.TemplateResponse("signin.html", _ctx(request, "Sign In"))


# ══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATION — SECURITY-FIX 2026-10-15
# ══════════════════════════════════════════════════════════════════════════════
# These routes were missing from this file entirely: signin.html posts to
# /auth/login, which 404'd, so no one could ever establish a session and
# every API route behind get_current_user would have 401'd. Restored here
# (deduplicated) from app/main.py.corrupted, which had the real logic but
# failed to import at all (a stray import above `from __future__ import
# annotations`, invalid Python) and repeated its router-mount calls six times.

@app.post("/auth/register", include_in_schema=False)
def register(
    request: Request,
    tenant_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    plan: str = Form("standard"),
    db: Session = Depends(get_db),
):
    email_n = (email or "").strip().lower()
    if not email_n or "@" not in email_n:
        raise HTTPException(400, "Invalid email")
    if len(password or "") < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    tenant_name = (tenant_name or "").strip() or "Tenant"

    existing = db.query(User).filter(User.email == email_n).first()
    if existing:
        raise HTTPException(400, "Account already exists")

    tenant = Tenant(name=tenant_name, plan=plan, is_active=True)
    db.add(tenant); db.commit(); db.refresh(tenant)

    user = User(tenant_id=tenant.id, email=email_n, password_hash=hash_password(password), is_active=True, role="admin")
    db.add(user); db.commit(); db.refresh(user)

    raw, kh = generate_api_key(prefix="asgl")
    db.add(ApiKey(tenant_id=tenant.id, key_hash=kh, name="default", is_active=True))
    db.commit()

    raw_tok, tok_hash = new_session_token()
    sess = SessionToken(user_id=user.id, token_hash=tok_hash, expires_at=token_expiry())
    db.add(sess); db.commit()

    resp = RedirectResponse(url="/console", status_code=303)
    resp.set_cookie(COOKIE_NAME, raw_tok, httponly=True, samesite="lax")
    resp.set_cookie("asgl_bootstrap_api_key", raw, httponly=False, samesite="lax", max_age=300)
    return resp


@app.post("/auth/login", include_in_schema=False)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email_n = (email or "").strip().lower()
    user = db.query(User).filter(User.email == email_n, User.is_active == True).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    raw_tok, tok_hash = new_session_token()
    sess = SessionToken(user_id=user.id, token_hash=tok_hash, expires_at=token_expiry())
    db.add(sess); db.commit()

    resp = RedirectResponse(url="/console", status_code=303)
    resp.set_cookie(COOKIE_NAME, raw_tok, httponly=True, samesite="lax")
    return resp


@app.get("/auth/me", include_in_schema=False)
def me(p: Principal = Depends(get_principal)):
    """Who am I and what may I do. The UI uses perms to show or hide screens."""
    return {
        "email": p.user.email if p.user else None,
        "role": p.role,
        "tenant_id": p.tenant_id,
        "perms": sorted(ROLE_PERMS.get(p.role, [])),
    }


@app.post("/auth/logout", include_in_schema=False)
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        th = hashlib.sha256(raw.encode()).hexdigest()
        db.query(SessionToken).filter(SessionToken.token_hash == th).delete()
        db.commit()
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp

@app.get("/console",     response_class=HTMLResponse, include_in_schema=False)
def console(request: Request, db: Session = Depends(get_db)):
    if _page_principal(request, db) is None:
        return RedirectResponse(url="/signin", status_code=303)
    return templates.TemplateResponse("console.html", _ctx(request, "Console"))

@app.get("/console/usage",response_class=HTMLResponse, include_in_schema=False)
def console_usage(request: Request, db: Session = Depends(get_db)):
    if _page_principal(request, db) is None:
        return RedirectResponse(url="/signin", status_code=303)
    return templates.TemplateResponse("console_usage.html", _ctx(request, "Usage"))

@app.get("/console/jobs", response_class=HTMLResponse, include_in_schema=False)
def console_jobs(request: Request, db: Session = Depends(get_db)):
    if _page_principal(request, db) is None:
        return RedirectResponse(url="/signin", status_code=303)
    return templates.TemplateResponse("console_jobs.html", _ctx(request, "Jobs"))

@app.get("/console/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def console_job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    if _page_principal(request, db) is None:
        return RedirectResponse(url="/signin", status_code=303)
    return templates.TemplateResponse("console_job_detail.html", {**_ctx(request, "Job"), "job_id": job_id})

@app.get("/console/api-keys", response_class=HTMLResponse, include_in_schema=False)
def console_api_keys(request: Request, db: Session = Depends(get_db)):
    if _page_principal(request, db) is None:
        return RedirectResponse(url="/signin", status_code=303)
    return templates.TemplateResponse("console_api_keys.html", _ctx(request, "API Keys"))

@app.get("/template-picker", response_class=HTMLResponse, include_in_schema=False)
def template_picker(request: Request, db: Session = Depends(get_db)):
    if _page_principal(request, db) is None:
        return RedirectResponse(url="/signin", status_code=303)
    return templates.TemplateResponse("template_picker.html", _ctx(request, "Template Picker"))

