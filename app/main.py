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

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.logging import configure_logging
from app.ratelimit import limiter
from app.db.session import engine, Base

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

def _mount(router, prefix: str = "", tags=None):
    try:
        app.include_router(router, prefix=prefix, tags=tags or [])
        logger.info("âœ“ Router mounted: %s", prefix or "(root)")
    except Exception as exc:
        logger.error("âœ— Router mount failed [%s]: %s", prefix or "(root)", exc)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CORE v1 ROUTERS â€” ordered by dependency
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# 1. Core pipeline, normalise, flywheel, governance, DI â€” the main v1 router
from app.api.v1.routes import router as v1_router
_mount(v1_router, prefix="/api/v1", tags=["v1"])

# 2. Add-on routes (version, flywheel train, DI review queue, governance export)
from app.api.v1.routes_addon import router as v1_addon_router
_mount(v1_addon_router, prefix="/api/v1", tags=["v1"])

# 3. Compare â€” canonical implementation (asgla_compare pipeline, full DiffRow output)
try:
    from app.api.v1.compare_routes import router as compare_router
    _mount(compare_router, prefix="/api/v1", tags=["comparison"])
except Exception as _compare_exc:
    import traceback as _tb
    logger.error("COMPARE ROUTES LOAD FAILED: %s", _compare_exc)
    logger.error(_tb.format_exc())


# 4. Engagements + Review Points lifecycle
from app.api.v1.engagements_routes import router as engagements_router
_mount(engagements_router, prefix="/api/v1", tags=["engagements"])

# 5. Notifications
from app.api.v1.notifications_routes import router as notifications_router
_mount(notifications_router, prefix="/api/v1", tags=["notifications"])

# 6. Custom Schemas
from app.api.v1.schemas_routes import router as schemas_router
_mount(schemas_router, prefix="/api/v1", tags=["schemas"])

# 7. Data Quality / OCR
from app.api.v1.dq_routes import router as dq_router
_mount(dq_router, prefix="/api/v1", tags=["dq"])

# 8. Quality â€” prefix /quality (router has prefix="/quality", mounted at /api/v1)
from app.quality.routes import router as quality_router
_mount(quality_router, prefix="/api/v1", tags=["quality"])

# 5. Reporting â€” evidence packs
from app.reporting.report_routes import router as reports_router
_mount(reports_router, prefix="/api/v1", tags=["reporting"])

# 6. Analytics â€” prefix /analytics (router has prefix="/analytics", mounted at /api/v1)
from app.analytics.routes import router as analytics_router
_mount(analytics_router, prefix="/api/v1", tags=["analytics"])


# â”€â”€ OPTIONAL ROUTERS (defensive â€” failure doesn't crash startup) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root(request: Request):
    for name in ["Command_Center_v14.html", "Command_Center_v13.html", "Command_Center_v12.html", "Command_Center_v11.html", "Command_Center_v9.html", "templates/Command Center.html", "Command Center.html", "index.html"]:
        try:
            return templates.TemplateResponse(name, {"request": request})
        except Exception:
            continue
    return HTMLResponse("<h1>Oversight ASGL</h1><p>No template found in app/web/templates/</p>")


@app.get("/comparison_only", response_class=HTMLResponse, include_in_schema=False)
async def comparison_only(request: Request):
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
def runner(request: Request): return templates.TemplateResponse("runner.html", _ctx(request, "Runner"))

@app.get("/signup",      response_class=HTMLResponse, include_in_schema=False)
def signup(request: Request): return templates.TemplateResponse("signup.html", _ctx(request, "Sign Up"))

@app.get("/signin",      response_class=HTMLResponse, include_in_schema=False)
def signin(request: Request): return templates.TemplateResponse("signin.html", _ctx(request, "Sign In"))

@app.get("/console",     response_class=HTMLResponse, include_in_schema=False)
def console(request: Request): return templates.TemplateResponse("console.html", _ctx(request, "Console"))

@app.get("/console/usage",response_class=HTMLResponse, include_in_schema=False)
def console_usage(request: Request): return templates.TemplateResponse("console_usage.html", _ctx(request, "Usage"))

@app.get("/console/jobs", response_class=HTMLResponse, include_in_schema=False)
def console_jobs(request: Request): return templates.TemplateResponse("console_jobs.html", _ctx(request, "Jobs"))

@app.get("/console/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def console_job_detail(request: Request, job_id: int):
    return templates.TemplateResponse("console_job_detail.html", {**_ctx(request, "Job"), "job_id": job_id})

@app.get("/console/api-keys", response_class=HTMLResponse, include_in_schema=False)
def console_api_keys(request: Request): return templates.TemplateResponse("console_api_keys.html", _ctx(request, "API Keys"))

@app.get("/template-picker", response_class=HTMLResponse, include_in_schema=False)
def template_picker(request: Request): return templates.TemplateResponse("template_picker.html", _ctx(request, "Template Picker"))

