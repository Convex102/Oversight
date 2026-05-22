"""ASGL Enterprise UI (Python) — v1

This module provides a complete, themed UI shell + all required screens using:
- FastAPI routes
- Jinja2 templates
- A single CSS theme + minimal JS

It is designed to be *compatible* with the existing ASGL API routes:
- /api/active-learning/*
- /api/telemetry/*

If your repo already has a frontend, this UI can run in parallel as an
"admin/ops" interface or starter SaaS UI.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from fastapi import APIRouter, Depends, Request
    from fastapi.responses import HTMLResponse
    from fastapi.templating import Jinja2Templates
except Exception:
    APIRouter = None
    Depends = None
    Request = None
    HTMLResponse = None
    Jinja2Templates = None

from app.auth import get_tenant_id as _get_tenant_id
from app.services.telemetry import report as telemetry_report

templates = Jinja2Templates(directory="app/web/templates") if Jinja2Templates else None

router = APIRouter(prefix="", tags=["ui"], include_in_schema=False) if APIRouter else None

def _ctx(request: Request, tenant_id: str, title: str, nav: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "request": request,
        "title": title,
        "tenant_id": tenant_id,
        "active_nav": nav,
        "vertical": request.query_params.get("vertical", "A"),
        **(extra or {}),
    }

if router:
    @router.get("/", response_class=HTMLResponse)
    def home(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        rep = telemetry_report(tenant_id, days=30)
        return templates.TemplateResponse("pages/dashboard.html", _ctx(request, tenant_id, "Workspace", "workspace", {"report": rep}))

    @router.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/jobs.html", _ctx(request, tenant_id, "Jobs", "jobs"))

    @router.get("/ingestion", response_class=HTMLResponse)
    def ingestion(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/ingestion.html", _ctx(request, tenant_id, "Ingestion", "ingestion"))

    @router.get("/mapping", response_class=HTMLResponse)
    def mapping_summary(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/mapping_summary.html", _ctx(request, tenant_id, "Mapping Summary", "mapping"))

    @router.get("/review-queue", response_class=HTMLResponse)
    def review_queue(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/review_queue.html", _ctx(request, tenant_id, "Review Queue", "review"))

    @router.get("/review-item", response_class=HTMLResponse)
    def review_item(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/review_item.html", _ctx(request, tenant_id, "Review Item", "review"))

    @router.get("/templates", response_class=HTMLResponse)
    def templates_lib(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/templates.html", _ctx(request, tenant_id, "Templates", "templates"))

    @router.get("/template-editor", response_class=HTMLResponse)
    def template_editor(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/template_editor.html", _ctx(request, tenant_id, "Template Editor", "templates"))

    @router.get("/packs", response_class=HTMLResponse)
    def packs_marketplace(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/packs.html", _ctx(request, tenant_id, "Packs Marketplace", "packs"))

    @router.get("/packs-installed", response_class=HTMLResponse)
    def packs_installed(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/packs_installed.html", _ctx(request, tenant_id, "Installed Packs", "packs"))

    @router.get("/pack-details", response_class=HTMLResponse)
    def pack_details(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/pack_details.html", _ctx(request, tenant_id, "Pack Details", "packs"))

    @router.get("/exports", response_class=HTMLResponse)
    def exports(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/exports.html", _ctx(request, tenant_id, "Exports", "exports"))

    @router.get("/audit", response_class=HTMLResponse)
    def audit_trace(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/audit.html", _ctx(request, tenant_id, "Audit / Trace", "audit"))

    @router.get("/metrics", response_class=HTMLResponse)
    def metrics(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        rep = telemetry_report(tenant_id, days=int(request.query_params.get("days", "30")))
        return templates.TemplateResponse("pages/metrics.html", _ctx(request, tenant_id, "Metrics", "metrics", {"report": rep}))

    @router.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/settings.html", _ctx(request, tenant_id, "Settings", "settings"))

    @router.get("/admin", response_class=HTMLResponse)
    def admin(request: Request, tenant_id: str = Depends(_get_tenant_id())):
        return templates.TemplateResponse("pages/admin.html", _ctx(request, tenant_id, "Admin", "admin"))
