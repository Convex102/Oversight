from __future__ import annotations

import io
import logging
from typing import Dict, Any, List

logger = logging.getLogger("asgl.pensions.pdf")


def render_trustee_evidence_pdf(*, summary: Dict[str, Any], findings: List[Dict[str, Any]], canonical: Dict[str, Any]) -> bytes:
    """
    Phase 8: Deterministic PDF renderer for trustee evidence.
    Uses ReportLab if available; otherwise falls back to a plain PDF-like bytes error.
    This keeps deployments resilient.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except Exception as e:
        logger.warning("reportlab_missing", extra={"error": str(e)})
        return b"%PDF-1.4\n% ASGL-P Evidence PDF requires reportlab.\n"

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    x = 18 * mm
    y = h - 22 * mm
    c.setTitle("ASGL-P Trustee Evidence Bundle")

    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, "Oversight • ASGL-P Trustee Evidence Bundle")
    y -= 10 * mm
    c.setFont("Helvetica", 10)
    for k in ("tenant_id", "scheme_id", "template_name", "fingerprint_key", "pack_id"):
        if k in summary:
            c.drawString(x, y, f"{k.replace('_',' ').title()}: {summary.get(k)}")
            y -= 6 * mm

    y -= 4 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "Findings (deterministic)")
    y -= 8 * mm
    c.setFont("Helvetica", 9)
    if not findings:
        c.drawString(x, y, "No findings provided in this PDF render call.")
        y -= 6 * mm
    else:
        for f in findings[:45]:
            line = f"{f.get('severity','')} {f.get('code','')} member={f.get('member_id','-')}  {f.get('message','')}"
            c.drawString(x, y, line[:130])
            y -= 5 * mm
            if y < 18 * mm:
                c.showPage()
                y = h - 22 * mm
                c.setFont("Helvetica", 9)

    c.showPage()
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, h - 22 * mm, "Canonical Snapshot (truncated)")
    c.setFont("Helvetica", 8)
    import json
    text = json.dumps(canonical or {}, indent=2)[:12000]
    y = h - 32 * mm
    for line in text.splitlines()[:120]:
        c.drawString(x, y, line[:140])
        y -= 4.2 * mm
        if y < 18 * mm:
            c.showPage()
            y = h - 22 * mm
            c.setFont("Helvetica", 8)

    c.save()
    return buf.getvalue()
