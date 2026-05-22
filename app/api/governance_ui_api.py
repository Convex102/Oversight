from __future__ import annotations

from typing import Any, Dict, List


def _pill(txt: str) -> str:
    return f"<span class='pill'>{txt}</span>"


def render_governance_panel(ctx: Dict[str, Any]) -> str:
    """Render a governance panel HTML fragment.

    This file was generated from conversation patches; earlier patch content was truncated.
    The function is kept small but valid so the repo compiles and the UI can include it later.
    """
    gov = (ctx or {}).get("governance") or {}
    state = gov.get("state") or {}
    summary = gov.get("audit_summary") or {}
    decisions: List[Dict[str, Any]] = list(gov.get("policy_decisions") or [])
    incidents: List[Dict[str, Any]] = list(gov.get("incidents") or [])

    badge = "OK"
    if state.get("blocked"):
        badge = "BLOCKED"
    elif state.get("requires_approval"):
        badge = "APPROVAL REQUIRED"

    dec_rows = []
    for d in decisions[:50]:
        status = "PASS" if d.get("passed") else "FAIL"
        mode = d.get("mode", "")
        dec_rows.append(
            f"<tr><td>{_pill(str(d.get('policy_id','')))}</td><td>{_pill(status)}</td><td>{_pill(str(mode))}</td><td class='muted'>{str(d.get('reason',''))}</td></tr>"
        )

    inc_rows = []
    for i in incidents[:20]:
        inc_rows.append(
            f"<tr><td>{_pill(str(i.get('incident_id','')))}</td><td>{_pill(str(i.get('severity','')))}</td><td>{_pill(str(i.get('category','')))}</td><td class='muted'>{str(i.get('title',''))}</td></tr>"
        )

    return f"""
<div class='card'>
  <div class='row' style='justify-content:space-between;align-items:center;'>
    <div>
      <div class='h2'>Governance</div>
      <div class='muted'>Policy enforcement, approvals, audit trail</div>
    </div>
    <div>{_pill(badge)}</div>
  </div>

  <div class='grid2' style='margin-top:12px;'>
    <div class='stat'>
      <div class='muted'>Approvals required</div>
      <div class='big'>{int(summary.get('approvals_required', 0) or 0)}</div>
    </div>
    <div class='stat'>
      <div class='muted'>Incidents</div>
      <div class='big'>{int(summary.get('incidents', len(incidents)) or 0)}</div>
    </div>
  </div>

  <h3 style='margin-top:16px;'>Policy decisions</h3>
  <table class='table'>
    <thead><tr><th>Policy</th><th>Status</th><th>Mode</th><th>Reason</th></tr></thead>
    <tbody>{''.join(dec_rows) if dec_rows else '<tr><td colspan=4 class=muted>No decisions</td></tr>'}</tbody>
  </table>

  <h3 style='margin-top:16px;'>Incidents</h3>
  <table class='table'>
    <thead><tr><th>ID</th><th>Severity</th><th>Category</th><th>Title</th></tr></thead>
    <tbody>{''.join(inc_rows) if inc_rows else '<tr><td colspan=4 class=muted>No incidents</td></tr>'}</tbody>
  </table>
</div>
"""
