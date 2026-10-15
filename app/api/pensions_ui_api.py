from __future__ import annotations

import logging
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, Response

from app.pipeline.pensions_pipeline import run_pensions_pack, TEMPLATE_STORE
from app.packs.pensions.policy import TemplatePolicyStore, TemplatePolicy
from app.packs.pensions.pdf import render_trustee_evidence_pdf
from app.telemetry.client import track
from app.db.models import User
from app.web_auth import get_current_user

# SECURITY-FIX 2026-10-15: every page here was public and took tenant_id from the query string or form,
# so anyone could read another tenant's templates, save its policies or run its pack. Each route now
# needs a session (app.web_auth.get_current_user) and the tenant is always str(current_user.tenant_id).

logger = logging.getLogger("asgl.ui.pensions")

router = APIRouter(prefix="/ui/pensions", tags=["pensions-ui"])

# Phase 8: simple policy store (sqlite-based) for per-template behaviour
POLICY_STORE = TemplatePolicyStore()


def _page(title: str, body: str, *, subtitle: str = "", active: str = "dashboard") -> str:
    """
    Minimal enterprise-themed UI consistent with ASGL dashboard aesthetics.
    This is a server-rendered UI to keep deployment simple (no JS build).
    """
    def nav_item(key: str, label: str, href: str) -> str:
        cls = "nav-item active" if key == active else "nav-item"
        return f'<a class="{cls}" href="{href}">{label}</a>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      --bg:#0b1220;
      --panel:#0f1a2f;
      --panel2:#111f38;
      --border:rgba(255,255,255,0.08);
      --text:#eaf0ff;
      --muted:rgba(234,240,255,0.72);
      --accent:#6ee7ff;
      --accent2:#a78bfa;
      --good:#34d399;
      --warn:#fbbf24;
      --bad:#fb7185;
      --shadow: 0 14px 60px rgba(0,0,0,0.40);
      --radius: 18px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin:0;
      font-family: var(--sans);
      color: var(--text);
      background: radial-gradient(1200px 600px at 10% 10%, rgba(110,231,255,0.12), transparent 50%),
                  radial-gradient(900px 500px at 90% 20%, rgba(167,139,250,0.10), transparent 45%),
                  var(--bg);
    }}
    .shell {{
      display:grid;
      grid-template-columns: 270px 1fr;
      min-height:100vh;
    }}
    .sidebar {{
      padding:22px 18px;
      border-right:1px solid var(--border);
      background: linear-gradient(180deg, rgba(15,26,47,0.95), rgba(11,18,32,0.90));
    }}
    .brand {{
      display:flex; align-items:center; gap:10px;
      padding:10px 10px; border-radius: 14px;
      background: rgba(255,255,255,0.03);
      border:1px solid var(--border);
      box-shadow: var(--shadow);
      margin-bottom:16px;
    }}
    .dot {{
      width:12px; height:12px; border-radius:99px;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      box-shadow: 0 0 0 4px rgba(110,231,255,0.08);
    }}
    .brand h1 {{ font-size:14px; margin:0; letter-spacing:0.2px; }}
    .brand p {{ margin:0; font-size:12px; color: var(--muted); }}
    .nav {{ display:flex; flex-direction:column; gap:8px; margin-top:14px; }}
    .nav-item {{
      text-decoration:none;
      color: var(--muted);
      padding:10px 12px;
      border-radius: 12px;
      border:1px solid rgba(255,255,255,0.06);
      background: rgba(255,255,255,0.02);
      transition: 0.15s ease;
    }}
    .nav-item:hover {{ color: var(--text); border-color: rgba(110,231,255,0.25); }}
    .nav-item.active {{
      color: var(--text);
      border-color: rgba(110,231,255,0.40);
      background: rgba(110,231,255,0.08);
    }}
    .content {{
      padding: 28px 30px;
    }}
    .topbar {{
      display:flex; align-items:flex-start; justify-content:space-between;
      gap:16px; margin-bottom: 18px;
    }}
    .title h2 {{ margin:0; font-size:22px; letter-spacing:0.2px; }}
    .title p {{ margin:6px 0 0 0; color: var(--muted); font-size:13px; }}
    .grid {{
      display:grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 14px;
    }}
    .card {{
      grid-column: span 12;
      border-radius: var(--radius);
      border:1px solid var(--border);
      background: linear-gradient(180deg, rgba(17,31,56,0.85), rgba(15,26,47,0.85));
      box-shadow: var(--shadow);
      padding: 16px 16px;
    }}
    .card h3 {{ margin:0 0 8px 0; font-size:14px; letter-spacing:0.2px; }}
    .muted {{ color: var(--muted); font-size: 12px; }}
    .kpis {{
      display:grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-top: 10px;
    }}
    .kpi {{
      border-radius: 16px;
      border:1px solid rgba(255,255,255,0.06);
      background: rgba(255,255,255,0.03);
      padding: 12px 12px;
    }}
    .kpi .label {{ font-size: 12px; color: var(--muted); }}
    .kpi .value {{ margin-top:6px; font-size: 18px; font-weight: 650; }}
    .pill {{
      display:inline-flex; align-items:center; gap:6px;
      padding:5px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.04);
      font-size: 12px;
      color: var(--muted);
    }}
    .pill.good {{ border-color: rgba(52,211,153,0.35); background: rgba(52,211,153,0.08); color: rgba(234,240,255,0.92); }}
    .pill.warn {{ border-color: rgba(251,191,36,0.35); background: rgba(251,191,36,0.08); color: rgba(234,240,255,0.92); }}
    .pill.bad {{ border-color: rgba(251,113,133,0.35); background: rgba(251,113,133,0.08); color: rgba(234,240,255,0.92); }}
    table {{
      width:100%;
      border-collapse: separate;
      border-spacing: 0;
      overflow:hidden;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.06);
      background: rgba(255,255,255,0.02);
    }}
    th, td {{
      padding: 10px 10px;
      font-size: 12px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      color: rgba(234,240,255,0.92);
      text-align:left;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 650; background: rgba(255,255,255,0.02); }}
    tr:last-child td {{ border-bottom:none; }}
    .actions {{
      display:flex; gap:8px; flex-wrap:wrap;
      justify-content:flex-end;
    }}
    .btn {{
      appearance:none;
      border:1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.04);
      color: var(--text);
      padding: 9px 12px;
      border-radius: 14px;
      font-size: 12px;
      cursor:pointer;
      text-decoration:none;
      transition: 0.15s ease;
    }}
    .btn:hover {{ border-color: rgba(110,231,255,0.35); background: rgba(110,231,255,0.08); }}
    .btn.primary {{
      border-color: rgba(110,231,255,0.45);
      background: linear-gradient(135deg, rgba(110,231,255,0.16), rgba(167,139,250,0.12));
    }}
    .btn.danger:hover {{ border-color: rgba(251,113,133,0.50); background: rgba(251,113,133,0.10); }}
    .code {{
      font-family: var(--mono);
      font-size: 11px;
      color: rgba(234,240,255,0.86);
      background: rgba(0,0,0,0.30);
      padding: 10px 12px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      overflow:auto;
      max-height: 300px;
      white-space: pre;
    }}
    .split {{
      display:grid;
      grid-template-columns: 1.3fr 1fr;
      gap: 14px;
    }}
    .form-row {{ display:flex; gap:10px; flex-wrap: wrap; }}
    .field {{
      flex: 1;
      min-width: 220px;
    }}
    .field label {{ display:block; font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
    .field input, .field textarea, .field select {{
      width:100%;
      padding: 10px 12px;
      border-radius: 14px;
      border:1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.03);
      color: var(--text);
      outline:none;
    }}
    .field textarea {{ min-height: 90px; font-family: var(--mono); font-size: 11px; }}
    .footer-note {{
      margin-top: 12px;
      color: var(--muted);
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="dot"></div>
        <div>
          <h1>Oversight • ASGL-P</h1>
          <p>Pensions Reconciliation & Equalisation</p>
        </div>
      </div>
      <nav class="nav">
        {nav_item("dashboard","Dashboard","/ui/pensions")}
        {nav_item("runner","Runner","/ui/pensions/runner")}
        {nav_item("review","Review Queue","/ui/pensions/review")}
        {nav_item("drift","Drift Monitor","/ui/pensions/drift")}
        {nav_item("templates","Template Library","/ui/pensions/templates")}
        {nav_item("policy","Template Policy","/ui/pensions/policy")}
      </nav>
      <div class="footer-note">Phase 8 • Enterprise UI • Deterministic evidence</div>
    </aside>
    <main class="content">
      <div class="topbar">
        <div class="title">
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        <div class="actions">
          <a class="btn" href="/api/v1/packs">Pack Registry</a>
          <a class="btn primary" href="/api/v1/pensions/templates">API: Templates</a>
        </div>
      </div>
      {body}
    </main>
  </div>
</body>
</html>"""


@router.get("", response_class=HTMLResponse)
def pensions_dashboard(current_user: User = Depends(get_current_user)):
    # Lightweight dashboard (no DB access needed)
    body = """
      <div class="grid">
        <div class="card" style="grid-column: span 12;">
          <h3>Command Centre</h3>
          <div class="muted">Live overview for ingestion → mapping → validation → evidence → telemetry.</div>
          <div class="kpis">
            <div class="kpi"><div class="label">Pack</div><div class="value">ASGL-P</div></div>
            <div class="kpi"><div class="label">Mode</div><div class="value">Human-in-loop</div></div>
            <div class="kpi"><div class="label">Evidence</div><div class="value">Trustee-ready</div></div>
            <div class="kpi"><div class="label">Flywheel</div><div class="value">Active</div></div>
          </div>
        </div>
        <div class="card" style="grid-column: span 7;">
          <h3>What this UI does</h3>
          <div class="muted">Phase 8 adds the missing enterprise UI glue: review queue, drift panel, policy controls and PDF evidence export.</div>
          <ul class="muted">
            <li>Runner calls <span class="pill">/api/v1/pensions/run</span> with mapped rows</li>
            <li>Review Queue shows deterministic findings and lets reviewers approve & export evidence</li>
            <li>Drift Monitor flags template changes between uploads</li>
            <li>Policy enforces autopilot thresholds and template locking</li>
          </ul>
        </div>
        <div class="card" style="grid-column: span 5;">
          <h3>Quick Actions</h3>
          <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:8px;">
            <a class="btn primary" href="/ui/pensions/runner">Open Runner</a>
            <a class="btn" href="/ui/pensions/review">Review Queue</a>
            <a class="btn" href="/ui/pensions/drift">Drift Monitor</a>
            <a class="btn" href="/ui/pensions/policy">Template Policy</a>
          </div>
        </div>
      </div>
    """
    return _page("ASGL-P Dashboard", body, subtitle="Enterprise pensions pack UI (Phase 8)", active="dashboard")


@router.get("/runner", response_class=HTMLResponse)
def pensions_runner(current_user: User = Depends(get_current_user)):
    body = """
    <div class="grid">
      <div class="card" style="grid-column: span 7;">
        <h3>Runner</h3>
        <div class="muted">Paste DI-mapped rows JSON to simulate the ingestion → pack run pathway. Intended for local demos and UI wiring.</div>
        <form method="post" action="/ui/pensions/runner/run">
          <div class="form-row" style="margin-top:12px;">
            <div class="field"><label>Scheme ID</label><input name="scheme_id" value="SCHEME123"/></div>
            <div class="field"><label>Template Name</label><input name="template_name" value="GMP Equalisation v3"/></div>
          </div>
          <div class="form-row" style="margin-top:10px;">
            <div class="field"><label>Sheet Name</label><input name="sheet_name" value="Equalisation"/></div>
            <div class="field"><label>Headers (comma separated)</label><input name="headers" value="member_id,ni_number,surname,forename,pre_value,post_value,calc_type"/></div>
          </div>
          <div class="field" style="margin-top:10px;">
            <label>Rows JSON (list of dict)</label>
            <textarea name="rows_json">[
  {"member_id":"10001","ni_number":"QQ123456C","surname":"Smith","forename":"Ava","pre_value":"12000","post_value":"12800","calc_type":"GMP_EQUALISATION"},
  {"member_id":"10002","ni_number":"AB123456D","surname":"Jones","forename":"Noah","pre_value":"9500","post_value":"9500","calc_type":"GMP_EQUALISATION"}
]</textarea>
          </div>
          <div class="actions" style="margin-top:12px;">
            <button class="btn primary" type="submit">Run ASGL-P</button>
          </div>
        </form>
      </div>
      <div class="card" style="grid-column: span 5;">
        <h3>Notes</h3>
        <div class="muted">
          This runner is deterministic: it does not invent values. It validates, fingerprints, detects drift and produces a trustee evidence bundle.
        </div>
        <div style="margin-top:10px;">
          <span class="pill good">Evidence export</span>
          <span class="pill">Template memory</span>
          <span class="pill warn">Drift alerts</span>
        </div>
      </div>
    </div>
    """
    return _page("Runner", body, subtitle="Run a pensions template through canonical + validation + evidence export", active="runner")


@router.post("/runner/run", response_class=HTMLResponse)
async def pensions_runner_run(request: Request, current_user: User = Depends(get_current_user)):
    form = await request.form()
    tenant_id = str(current_user.tenant_id)
    scheme_id = str(form.get("scheme_id", "SCHEME123"))
    template_name = str(form.get("template_name", "unknown"))
    sheet_name = str(form.get("sheet_name", "")) or None
    headers = [h.strip() for h in str(form.get("headers", "")).split(",") if h.strip()]
    rows_json = str(form.get("rows_json", "[]"))
    import json
    try:
        rows = json.loads(rows_json)
    except Exception as e:
        body = f'<div class="card"><h3>JSON Parse Error</h3><div class="code">{str(e)}</div></div>'
        return _page("Runner Error", body, subtitle="Rows JSON must be a valid JSON list", active="runner")

    out = run_pensions_pack(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        rows=rows,
        meta={},
        template_name=template_name,
        sheet_name=sheet_name,
        headers=headers,
    )
    track("ui.pensions.runner.run", tenant_id=tenant_id, pack_id="ASGL-P", scheme_id=scheme_id, meta={"template": template_name, "findings": len(out.get("findings", []))})

    findings = out.get("findings", [])
    drift = out.get("drift")
    match = out.get("template_match", {})

    drift_badge = '<span class="pill good">No drift</span>'
    if drift and drift.get("has_drift"):
        sev = drift.get("severity", "LOW")
        drift_badge = '<span class="pill warn">Drift: {}</span>'.format(sev) if sev != "HIGH" else '<span class="pill bad">Drift: HIGH</span>'

    body = f"""
      <div class="grid">
        <div class="card" style="grid-column: span 12;">
          <h3>Run Result</h3>
          <div class="muted">Fingerprint: <span class="pill">{out.get('fingerprint_key')}</span> &nbsp; Match score: <span class="pill">{match.get('score',0):.2f}</span> {drift_badge}</div>
        </div>
        <div class="card" style="grid-column: span 7;">
          <h3>Findings</h3>
          <table>
            <thead><tr><th>Severity</th><th>Code</th><th>Member</th><th>Message</th></tr></thead>
            <tbody>
              {''.join([f"<tr><td>{f.get('severity')}</td><td><span class='pill'>{f.get('code')}</span></td><td>{f.get('member_id') or '-'}</td><td>{f.get('message')}</td></tr>" for f in findings]) or "<tr><td colspan='4' class='muted'>No findings</td></tr>"}
            </tbody>
          </table>
          <div class="actions" style="margin-top:12px;">
            <a class="btn" href="/ui/pensions/review?tenant_id={tenant_id}">Open Review Queue</a>
            <a class="btn primary" href="/ui/pensions/evidence/pdf?tenant_id={tenant_id}&scheme_id={scheme_id}&fingerprint_key={out.get('fingerprint_key')}&template_name={template_name}">Download Evidence PDF</a>
          </div>
        </div>
        <div class="card" style="grid-column: span 5;">
          <h3>Canonical Snapshot</h3>
          <div class="code">{__import__('json').dumps(out.get('canonical', {}), indent=2)[:8000]}</div>
        </div>
      </div>
    """
    return _page("Runner • Result", body, subtitle="Pack output + findings + canonical snapshot", active="runner")


@router.get("/review", response_class=HTMLResponse)
def pensions_review_queue(current_user: User = Depends(get_current_user)):
    tenant_id = str(current_user.tenant_id)
    # In Phase 8 we show a simulated queue using template library; real queue comes from telemetry + storage.
    templates = TEMPLATE_STORE.list(tenant_id, limit=50)
    rows = ""
    for t in templates:
        rows += f"<tr><td><span class='pill'>{t.get('pack_id')}</span></td><td>{t.get('template_name')}</td><td>{t.get('sheet_hint') or '-'}</td><td><span class='pill'>{t.get('fp_key')[:10]}…</span></td><td>{t.get('updated_at')}</td><td><a class='btn' href='/ui/pensions/policy?tenant_id={tenant_id}&fp_key={t.get('fp_key')}'>Policy</a></td></tr>"
    body = f"""
      <div class="grid">
        <div class="card" style="grid-column: span 12;">
          <h3>Review Queue</h3>
          <div class="muted">Phase 8 UI: lists known templates for the tenant. In production, this queue is populated by telemetry events + pending finding sets.</div>
          <table style="margin-top:10px;">
            <thead><tr><th>Pack</th><th>Template</th><th>Sheet</th><th>Fingerprint</th><th>Updated</th><th>Actions</th></tr></thead>
            <tbody>
              {rows or "<tr><td colspan='6' class='muted'>No templates yet — run the Runner to create fingerprints.</td></tr>"}
            </tbody>
          </table>
        </div>
      </div>
    """
    return _page("Review Queue", body, subtitle="Reviewer workbench entry (Phase 8 UI wiring)", active="review")


@router.get("/drift", response_class=HTMLResponse)
def pensions_drift_monitor(current_user: User = Depends(get_current_user)):
    tenant_id = str(current_user.tenant_id)
    templates = TEMPLATE_STORE.list(tenant_id, limit=30)
    body = f"""
      <div class="grid">
        <div class="card" style="grid-column: span 12;">
          <h3>Drift Monitor</h3>
          <div class="muted">Drift events are emitted by the pack when current fingerprint diverges from best match. This view is ready to be populated from telemetry store.</div>
          <div class="muted" style="margin-top:10px;">Known templates: <span class="pill">{len(templates)}</span></div>
          <div class="card" style="margin-top:12px;">
            <h3>How drift is used</h3>
            <div class="muted">
              When drift is detected, autopilot can be disabled, the mapping UI can require manual confirmation, and the template can be versioned.
            </div>
          </div>
        </div>
      </div>
    """
    return _page("Drift Monitor", body, subtitle="Template change detection (Phase 7 engine + Phase 8 UI)", active="drift")


@router.get("/templates", response_class=HTMLResponse)
def pensions_templates_ui(current_user: User = Depends(get_current_user)):
    tenant_id = str(current_user.tenant_id)
    templates = TEMPLATE_STORE.list(tenant_id, limit=200)
    body = f"""
      <div class="grid">
        <div class="card" style="grid-column: span 12;">
          <h3>Template Library</h3>
          <div class="muted">Persisted template fingerprints per tenant (SQLite). Enables template reuse, drift detection and priors.</div>
          <table style="margin-top:10px;">
            <thead><tr><th>Template</th><th>Sheet</th><th>Columns</th><th>Key</th><th>Updated</th><th></th></tr></thead>
            <tbody>
              {''.join([f"<tr><td>{t.get('template_name')}</td><td>{t.get('sheet_hint') or '-'}</td><td>{t.get('column_count')}</td><td><span class='pill'>{t.get('fp_key')[:12]}…</span></td><td>{t.get('updated_at')}</td><td><a class='btn' href='/ui/pensions/policy?tenant_id={tenant_id}&fp_key={t.get('fp_key')}'>Policy</a></td></tr>" for t in templates]) or "<tr><td colspan='6' class='muted'>No templates yet</td></tr>"}
            </tbody>
          </table>
        </div>
      </div>
    """
    return _page("Template Library", body, subtitle="Template memory (tenant-based)", active="templates")


@router.get("/policy", response_class=HTMLResponse)
def pensions_policy_ui(fp_key: Optional[str] = None, current_user: User = Depends(get_current_user)):
    tenant_id = str(current_user.tenant_id)
    pol = POLICY_STORE.get(tenant_id=tenant_id, fp_key=fp_key) if fp_key else None
    pol = pol or TemplatePolicy(tenant_id=tenant_id, fp_key=fp_key or "", autopilot_enabled=False, autopilot_min_score=0.92, lock_template=False, require_manual_review=True)
    body = f"""
      <div class="grid">
        <div class="card" style="grid-column: span 7;">
          <h3>Template Policy</h3>
          <div class="muted">Control autopilot thresholds and safety gates per template fingerprint. Designed to prevent hallucinations and maintain deterministic review.</div>
          <form method="post" action="/ui/pensions/policy/save">
            <input type="hidden" name="tenant_id" value="{tenant_id}"/>
            <div class="field" style="margin-top:12px;">
              <label>Fingerprint Key</label>
              <input name="fp_key" value="{pol.fp_key}" placeholder="Paste fingerprint key"/>
            </div>
            <div class="form-row" style="margin-top:10px;">
              <div class="field">
                <label>Autopilot Enabled</label>
                <select name="autopilot_enabled">
                  <option value="false" {"selected" if not pol.autopilot_enabled else ""}>False</option>
                  <option value="true" {"selected" if pol.autopilot_enabled else ""}>True</option>
                </select>
              </div>
              <div class="field">
                <label>Autopilot Min Score</label>
                <input name="autopilot_min_score" value="{pol.autopilot_min_score}"/>
              </div>
            </div>
            <div class="form-row" style="margin-top:10px;">
              <div class="field">
                <label>Lock Template</label>
                <select name="lock_template">
                  <option value="false" {"selected" if not pol.lock_template else ""}>False</option>
                  <option value="true" {"selected" if pol.lock_template else ""}>True</option>
                </select>
              </div>
              <div class="field">
                <label>Require Manual Review</label>
                <select name="require_manual_review">
                  <option value="true" {"selected" if pol.require_manual_review else ""}>True</option>
                  <option value="false" {"selected" if not pol.require_manual_review else ""}>False</option>
                </select>
              </div>
            </div>
            <div class="actions" style="margin-top:12px;">
              <button class="btn primary" type="submit">Save Policy</button>
            </div>
          </form>
        </div>
        <div class="card" style="grid-column: span 5;">
          <h3>Safety model</h3>
          <div class="muted">
            Policies enforce deterministic behaviour:
            <ul>
              <li>Autopilot only allowed above min match score</li>
              <li>Templates can be locked to prevent silent changes</li>
              <li>Manual review can be required when drift is present</li>
            </ul>
          </div>
        </div>
      </div>
    """
    return _page("Template Policy", body, subtitle="Enterprise guardrails (Phase 8)", active="policy")


@router.post("/policy/save", response_class=HTMLResponse)
async def pensions_policy_save(request: Request, current_user: User = Depends(get_current_user)):
    form = await request.form()
    tenant_id = str(current_user.tenant_id)
    fp_key = str(form.get("fp_key", ""))
    if not fp_key:
        return _page("Policy Error", '<div class="card"><h3>Missing fingerprint key</h3><div class="muted">Paste a fingerprint key from Template Library.</div></div>', subtitle="", active="policy")
    def _bool(v: str) -> bool:
        return str(v).lower() in ("1", "true", "yes", "y", "on")
    try:
        pol = TemplatePolicy(
            tenant_id=tenant_id,
            fp_key=fp_key,
            autopilot_enabled=_bool(form.get("autopilot_enabled", "false")),
            autopilot_min_score=float(form.get("autopilot_min_score", "0.92")),
            lock_template=_bool(form.get("lock_template", "false")),
            require_manual_review=_bool(form.get("require_manual_review", "true")),
        )
        POLICY_STORE.upsert(pol)
        track("pack.pensions.policy.updated", tenant_id=tenant_id, pack_id="ASGL-P", scheme_id="*", meta={"fp_key": fp_key})
    except Exception as e:
        logger.exception("policy_save_failed", extra={"tenant_id": tenant_id})
        return _page("Policy Error", f'<div class="card"><h3>Save failed</h3><div class="code">{str(e)}</div></div>', subtitle="", active="policy")
    return _page("Policy Saved", f'<div class="card"><h3>Policy Saved</h3><div class="muted">Fingerprint: <span class="pill">{fp_key[:14]}…</span></div><div class="actions" style="margin-top:12px;"><a class="btn primary" href="/ui/pensions/templates?tenant_id={tenant_id}">Back to Template Library</a></div></div>', subtitle="Guardrails updated", active="policy")


@router.get("/evidence/pdf")
def pensions_evidence_pdf(scheme_id: str, fingerprint_key: str, template_name: str = "unknown", current_user: User = Depends(get_current_user)):
    """
    Phase 8: generate a deterministic trustee evidence PDF from the last pack result
    (caller provides evidence payload by rerunning pack if needed).
    For demo simplicity we run an empty payload if no stored snapshot exists.
    """
    tenant_id = str(current_user.tenant_id)
    try:
        # In production, evidence bundles are stored; for now, this endpoint expects rerun from UI runner or external call.
        # Minimal safe PDF render of "header + metadata" if no bundle provided.
        pdf_bytes = render_trustee_evidence_pdf(
            summary={
                "tenant_id": tenant_id,
                "scheme_id": scheme_id,
                "template_name": template_name,
                "fingerprint_key": fingerprint_key,
                "pack_id": "ASGL-P",
            },
            findings=[],
            canonical={},
        )
        track("pack.pensions.evidence.pdf.rendered", tenant_id=tenant_id, pack_id="ASGL-P", scheme_id=scheme_id, meta={"bytes": len(pdf_bytes)})
        return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="ASGL-P_Evidence_{scheme_id}.pdf"'})
    except Exception as e:
        logger.exception("evidence_pdf_failed", extra={"tenant_id": tenant_id, "scheme_id": scheme_id})
        raise HTTPException(status_code=500, detail="PDF generation failed")
