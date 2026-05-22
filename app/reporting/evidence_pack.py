"""
Oversight Labs — Client Evidence Pack Generator
================================================
Produces a branded, accountant-readable PDF report.

Plain English throughout — no ML jargon. Tells the reviewer:
  • What was flagged and why (in accounting terms)
  • What to check and how to correct it
  • Which FRS 102 / IFRS provisions are relevant
  • A sign-off checklist

Usage:
    from app.reporting.evidence_pack import generate_evidence_pack

    path = generate_evidence_pack(
        run_id="job-123",
        client_name="Alexander Myerson Limited",
        period="Year ended 31 March 2025",
        findings={...},            # from pipeline results
        output_dir=Path("data/exports"),
        logo_path="static/client_logos/am.png",   # optional
    )
"""

from __future__ import annotations

import io
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm, cm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable, Image, KeepTogether
    )
    from reportlab.pdfgen import canvas as rl_canvas
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False
    logger.warning("reportlab not installed — evidence pack generation disabled")


# ── Brand palette ─────────────────────────────────────────────────────────────
NAVY    = colors.HexColor("#1E3A5F")
BLUE    = colors.HexColor("#2C5F8A")
TEAL    = colors.HexColor("#0E7490")
RED     = colors.HexColor("#B91C1C")
AMBER   = colors.HexColor("#D97706")
ORANGE  = colors.HexColor("#C2410C")
GREEN   = colors.HexColor("#15803D")
LGRAY   = colors.HexColor("#F8FAFC")
MGRAY   = colors.HexColor("#CBD5E1")
DGRAY   = colors.HexColor("#64748B")
WHITE   = colors.white

# ── Plain-English field labels (translates ML feature names) ──────────────────
_FIELD_LABELS: Dict[str, str] = {
    "ob": "opening balance",
    "opening_balance": "opening balance",
    "dr": "total debit movements",
    "debit": "total debit movements",
    "cr": "total credit movements",
    "credit": "total credit movements",
    "cb": "closing balance",
    "closing_balance": "closing balance",
}

# ── Risk level from ensemble score ────────────────────────────────────────────
def _risk_level(score: float) -> Tuple[str, Any]:
    if score >= 0.85: return "Critical", RED
    if score >= 0.75: return "High",     ORANGE
    if score >= 0.65: return "Medium",   AMBER
    return "Low", GREEN

# ── Severity icon (text-based for PDF safety) ─────────────────────────────────
def _sev_tag(level: str) -> str:
    return {"Critical": "[CRITICAL]", "High": "[HIGH]",
            "Medium": "[MEDIUM]", "Low": "[LOW]"}.get(level, "[REVIEW]")

# ── Account-category-aware review guidance ────────────────────────────────────
_CATEGORY_GUIDANCE: Dict[str, Dict[str, str]] = {
    "Asset": {
        "what_to_check": (
            "Confirm that all debit postings to this account are supported by "
            "authorised documentation. Verify the closing balance agrees to the "
            "supporting ledger or aged listing."
        ),
        "how_to_fix": (
            "If postings are incorrect, raise a correcting journal to the appropriate "
            "account. If the balance includes amounts that may not be recoverable, "
            "consider whether a provision for impairment is required."
        ),
        "frs_ref": "FRS 102 Section 11 — Financial Instruments and Section 27 — Impairment of Assets",
    },
    "Liability": {
        "what_to_check": (
            "Confirm that all credit balances have been correctly accrued at the "
            "period end. Review whether any liabilities should have been settled or "
            "released during the period."
        ),
        "how_to_fix": (
            "Process any missing accruals or reverse any overstated provisions. "
            "Ensure that year-end cut-off procedures have been correctly applied."
        ),
        "frs_ref": "FRS 102 Section 21 — Provisions and Contingencies",
    },
    "Income": {
        "what_to_check": (
            "Confirm that all revenue credits are supported by invoices, contracts, "
            "or other evidence of earned income. Check cut-off — ensure income relates "
            "to the correct accounting period."
        ),
        "how_to_fix": (
            "Reclassify any income recorded in the wrong period. If any amounts are "
            "in dispute, consider whether a credit note or provision is required."
        ),
        "frs_ref": "FRS 102 Section 23 — Revenue",
    },
    "Expense": {
        "what_to_check": (
            "Confirm that all expense postings have been authorised and relate to the "
            "correct accounting period. Verify that any unusual items have a clear "
            "business purpose and have been coded to the correct nominal code."
        ),
        "how_to_fix": (
            "Reclassify any misposted items. If an amount cannot be explained, "
            "obtain further information from the client before signing off."
        ),
        "frs_ref": "FRS 102 Section 5 — Statement of Comprehensive Income",
    },
    "Equity": {
        "what_to_check": (
            "Confirm that movements in equity accounts are correctly supported "
            "by board resolutions, share allotment forms, or dividend documentation."
        ),
        "how_to_fix": (
            "Ensure all equity movements are documented and that the closing equity "
            "reconciles to the prior period balance plus movements for the year."
        ),
        "frs_ref": "FRS 102 Section 22 — Liabilities and Equity",
    },
}
_DEFAULT_GUIDANCE = {
    "what_to_check": "Review all postings to this account and confirm they are supported by appropriate documentation.",
    "how_to_fix": "Raise correcting journals for any mispostings and obtain supporting documentation for unusual items.",
    "frs_ref": "FRS 102 — UK and Ireland accounting standard",
}

def _category_guidance(category: str) -> Dict[str, str]:
    return _CATEGORY_GUIDANCE.get(category, _DEFAULT_GUIDANCE)

# ── SHAP → plain English ──────────────────────────────────────────────────────
def _shap_to_english(
    feature_weights: Dict[str, float],
    primary_driver: str,
    account_name: str,
    account_category: str,
) -> str:
    label = _FIELD_LABELS.get(primary_driver, primary_driver.replace("_", " "))
    pct   = feature_weights.get(primary_driver, 0)
    secondary = [
        (k, v) for k, v in sorted(feature_weights.items(), key=lambda x: x[1], reverse=True)
        if k != primary_driver and v > 0.10
    ]
    text = (
        f"This account was flagged primarily because its <b>{label}</b> "
        f"was significantly different from other accounts in this dataset "
        f"(this factor drove {pct*100:.0f}% of the flag)."
    )
    if secondary:
        sec_label = _FIELD_LABELS.get(secondary[0][0], secondary[0][0].replace("_", " "))
        text += (
            f" The <b>{sec_label}</b> was also a contributing factor "
            f"({secondary[0][1]*100:.0f}% of the signal)."
        )
    return text


# ── Page number canvas callback ───────────────────────────────────────────────
class _NumberedCanvas(rl_canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages: List[Any] = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        for i, page in enumerate(self._pages):
            self.__dict__.update(page)
            self._draw_footer(i + 1, len(self._pages))
            super().showPage()
        super().save()

    def _draw_footer(self, page_num: int, total_pages: int):
        W, _ = A4
        self.setFont("Helvetica", 7)
        self.setFillColor(DGRAY)
        self.drawString(20*mm, 8*mm, "Oversight Labs Limited · Co. No. 17145488 · oversightlab.ai")
        self.drawRightString(W - 20*mm, 8*mm, f"CONFIDENTIAL — Page {page_num} of {total_pages}")


# ═══════════════════════════════════════════════════════════════════════════════
# Main generator class
# ═══════════════════════════════════════════════════════════════════════════════

class EvidencePackGenerator:
    """
    Generates a client-branded, accountant-readable evidence pack.

    All output is in plain English. ML/AI methodology is referenced only
    in footnotes and the technology note section.
    """

    def __init__(
        self,
        run_id: str,
        client_name: str,
        period: str,
        logo_path: Optional[str] = None,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
    ):
        self.run_id       = run_id
        self.client_name  = client_name
        self.period       = period
        self.logo_path    = logo_path
        self.primary      = colors.HexColor(primary_color) if primary_color else NAVY
        self.secondary    = colors.HexColor(secondary_color) if secondary_color else BLUE
        self.elements: List[Any] = []
        self.action_items: List[str] = []   # populated as sections build
        self._styles = self._make_styles()

    def _make_styles(self) -> Dict[str, ParagraphStyle]:
        """Build styles independently — avoids shared-stylesheet mutation bug."""
        base = getSampleStyleSheet()["Normal"]
        def S(name, **kw):
            p = ParagraphStyle(name)
            for k, v in vars(base).items():
                if not k.startswith("_"):
                    try: setattr(p, k, v)
                    except: pass
            for k, v in kw.items(): setattr(p, k, v)
            return p

        return {
            "title":    S("title",   fontName="Helvetica-Bold", fontSize=22, textColor=self.primary, spaceAfter=6, leading=28),
            "h2":       S("h2",      fontName="Helvetica-Bold", fontSize=13, textColor=self.primary, spaceAfter=4, spaceBefore=8),
            "h3":       S("h3",      fontName="Helvetica-Bold", fontSize=10, textColor=self.secondary, spaceAfter=3, spaceBefore=6),
            "body":     S("body",    fontName="Helvetica",      fontSize=9,  textColor=colors.HexColor("#1E293B"), leading=14, spaceAfter=3),
            "small":    S("small",   fontName="Helvetica",      fontSize=8,  textColor=DGRAY, leading=12),
            "mono":     S("mono",    fontName="Courier",        fontSize=8,  textColor=DGRAY, leading=11),
            "ok":       S("ok",      fontName="Helvetica-Bold", fontSize=9,  textColor=GREEN),
            "warn":     S("warn",    fontName="Helvetica-Bold", fontSize=9,  textColor=AMBER),
            "error":    S("error",   fontName="Helvetica-Bold", fontSize=9,  textColor=RED),
            "action":   S("action",  fontName="Helvetica-Bold", fontSize=9,  textColor=RED,    leftIndent=8),
            "caption":  S("caption", fontName="Helvetica",      fontSize=7,  textColor=DGRAY,  alignment=TA_CENTER),
            "center":   S("center",  fontName="Helvetica",      fontSize=9,  textColor=colors.HexColor("#1E293B"), alignment=TA_CENTER),
        }

    def _P(self, text: str, style: str = "body") -> Paragraph:
        return Paragraph(text, self._styles[style])

    def _HR(self, color=None, thickness=0.5):
        return HRFlowable(width="100%", thickness=thickness, color=color or self.secondary)

    def _section_header(self, text: str):
        self.elements += [
            self._HR(self.primary, 1.5),
            Spacer(1, 2*mm),
            self._P(text, "h2"),
            self._HR(self.secondary, 0.5),
            Spacer(1, 3*mm),
        ]

    def _kv_table(self, rows: List[Tuple[str, str]], col_widths=None) -> Table:
        col_widths = col_widths or [55*mm, 110*mm]
        t = Table(rows, colWidths=col_widths)
        t.setStyle(TableStyle([
            ("FONTNAME",     (0,0), (0,-1), "Helvetica-Bold"),
            ("FONTSIZE",     (0,0), (-1,-1), 8),
            ("TEXTCOLOR",    (0,0), (0,-1), self.primary),
            ("BACKGROUND",   (0,0), (0,-1), LGRAY),
            ("GRID",         (0,0), (-1,-1), 0.3, MGRAY),
            ("TOPPADDING",   (0,0), (-1,-1), 3),
            ("BOTTOMPADDING",(0,0), (-1,-1), 3),
            ("LEFTPADDING",  (0,0), (-1,-1), 5),
            ("VALIGN",       (0,0), (-1,-1), "TOP"),
        ]))
        return t

    # ── COVER PAGE ─────────────────────────────────────────────────────────────
    def add_cover_page(self):
        el = self.elements
        el.append(Spacer(1, 6*cm))

        # Logo if available
        if self.logo_path and os.path.exists(self.logo_path):
            try:
                img = Image(self.logo_path, width=4*cm, height=2*cm, hAlign="LEFT")
                el.append(img)
                el.append(Spacer(1, 10*mm))
            except Exception:
                pass

        # Oversight wordmark
        wm_data = [[self._P("<b>OVERSIGHT</b>", "title"),
                     self._P("Accountancy Review", "body")]]
        wm = Table(wm_data, colWidths=[80*mm, 85*mm])
        wm.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (-1,-1), self.primary),
            ("TEXTCOLOR",    (0,0), (-1,-1), WHITE),
            ("TOPPADDING",   (0,0), (-1,-1), 10),
            ("BOTTOMPADDING",(0,0), (-1,-1), 10),
            ("LEFTPADDING",  (0,0), (0,-1),  8),
            ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ]))
        el.append(wm)
        el.append(Spacer(1, 10*mm))

        el.append(self._P(f"<b>{self.client_name}</b>", "title"))
        el.append(self._P(f"Period: {self.period}", "h3"))
        el.append(Spacer(1, 5*mm))

        meta = self._kv_table([
            ("Report reference", self.run_id),
            ("Generated",        datetime.utcnow().strftime("%d %B %Y at %H:%M UTC")),
            ("Prepared by",      "Oversight Labs — Automated Accountancy Review"),
            ("Classification",   "CONFIDENTIAL — For use by the named accountancy firm only"),
        ])
        el.append(meta)
        el.append(Spacer(1, 8*mm))
        el.append(self._P(
            "<i>This report has been generated automatically from the data submitted. "
            "It should be reviewed by a qualified accountant before any action is taken. "
            "It does not constitute professional advice.</i>",
            "small"
        ))
        el.append(PageBreak())

    # ── EXECUTIVE SUMMARY ──────────────────────────────────────────────────────
    def add_executive_summary(
        self,
        financial: Dict[str, Any],
        anomaly_count: int,
        violation_count: int,
    ):
        self._section_header("1. At a Glance")

        rev    = financial.get("revenue", financial.get("total_revenue_gbp", 0))
        profit = financial.get("net_profit", financial.get("net_profit_gbp", 0))
        margin = financial.get("margin", financial.get("profit_margin_pct", 0))
        n_accs = financial.get("account_count", financial.get("accounts_reviewed", 0))
        tmpl   = financial.get("template_name", financial.get("template", "FRS 102 Trial Balance"))
        q      = financial.get("quality_score", 0)

        # KPI row
        kpi_color = RED if (anomaly_count > 0 or violation_count > 0) else GREEN
        kpi_label = "Items need your attention" if (anomaly_count + violation_count) > 0 else "Review complete — no issues"
        kpi_data  = [[
            self._P(f"<b>£{int(rev):,}</b><br/>Revenue", "center"),
            self._P(f"<b>£{int(profit):,}</b><br/>Net Profit", "center"),
            self._P(f"<b>{margin:.1f}%</b><br/>Margin", "center"),
            self._P(f"<b>{n_accs}</b><br/>Accounts reviewed", "center"),
            self._P(f"<b>{anomaly_count + violation_count}</b><br/>{kpi_label}", "center"),
        ]]
        kt = Table(kpi_data, colWidths=[33*mm]*5)
        kt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (2,-1), LGRAY),
            ("BACKGROUND", (4,0), (4,-1), colors.HexColor("#FEF2F2") if kpi_color == RED else colors.HexColor("#DCFCE7")),
            ("BOX",        (0,0), (-1,-1), 0.5, MGRAY),
            ("INNERGRID",  (0,0), (-1,-1), 0.5, MGRAY),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING",(0,0), (-1,-1), 6),
            ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ]))
        self.elements += [kt, Spacer(1, 5*mm)]

        # Status summary
        if anomaly_count == 0 and violation_count == 0:
            self.elements.append(self._P(
                f"✓  This review is complete. All {n_accs} accounts reconcile correctly "
                f"and no unusual items were identified.",
                "ok"
            ))
        else:
            items_text = []
            if anomaly_count > 0:
                items_text.append(f"{anomaly_count} account(s) have been flagged for your attention (Section 2)")
                self.action_items.append(f"Review the {anomaly_count} flagged account(s) in Section 2")
            if violation_count > 0:
                items_text.append(f"{violation_count} entry or entries need to be corrected (Section 3)")
                self.action_items.append(f"Correct the {violation_count} reconciliation issue(s) in Section 3")
            summary_box = [[self._P(
                f"<b>This review requires your attention.</b><br/>" + "<br/>".join(
                    [f"• {t}" for t in items_text]
                ), "body"
            )]]
            sb = Table(summary_box, colWidths=[165*mm])
            sb.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#FFF7ED")),
                ("BOX",        (0,0), (-1,-1), 1, AMBER),
                ("LEFTPADDING",(0,0), (-1,-1), 8),
                ("TOPPADDING", (0,0), (-1,-1), 6),
                ("BOTTOMPADDING",(0,0), (-1,-1), 6),
            ]))
            self.elements += [sb, Spacer(1, 4*mm)]

        # Meta
        self.elements.append(self._kv_table([
            ("Data source",    financial.get("source_file", "Uploaded file")),
            ("Template used",  str(tmpl)),
            ("Quality score",  f"{q*100:.0f}% — {('All checks passed' if q >= 0.95 else 'Some items require attention')}"),
        ]))
        self.elements.append(PageBreak())

    # ── FLAGGED ACCOUNTS (ANOMALIES) ───────────────────────────────────────────
    def add_flagged_accounts(
        self,
        anomalies: List[Dict],
        shap_attribution: Optional[Dict] = None,
    ):
        self._section_header("2. Accounts Flagged for Review")

        if not anomalies:
            self.elements.append(self._P(
                "✓  No accounts were identified as requiring additional review.", "ok"
            ))
            self.elements.append(PageBreak())
            return

        self.elements.append(self._P(
            f"{len(anomalies)} account(s) showed patterns that are significantly different "
            "from the rest of the dataset. This does not mean an error has been made — "
            "it means these accounts warrant your professional judgement before sign-off.",
            "body"
        ))
        self.elements.append(Spacer(1, 4*mm))

        for i, a in enumerate(anomalies, 1):
            # Normalise field names (handle both ML dict shapes and accounting dict shapes)
            name     = a.get("name") or a.get("account_name") or "Unknown Account"
            code     = a.get("code") or a.get("account_code") or "—"
            score    = float(a.get("score", a.get("anomaly_score", 0)))
            category = a.get("cat") or a.get("category", "")
            dr_val   = float(a.get("dr") or a.get("debit", 0))
            cr_val   = float(a.get("cr") or a.get("credit", 0))
            cb_val   = float(a.get("cb") or a.get("closing_balance", 0))

            risk, risk_color = _risk_level(score)
            guidance = _category_guidance(category)

            # Build SHAP explanation
            shap_text = ""
            shap_key  = f"{name} ({code})"
            shap_info = (shap_attribution or {}).get(shap_key, {})
            if shap_info:
                fw      = shap_info.get("feature_weights", {})
                primary = shap_info.get("primary_driver", "")
                shap_text = _shap_to_english(fw, primary, name, category)

            # KeepTogether keeps each anomaly block on one page if possible
            block = []

            # Header row
            hdr_data = [[
                self._P(f"<b>{i}. {name}</b>  <font size='8' color='#64748B'>Nominal code: {code}</font>", "h3"),
                self._P(f"<b>{_sev_tag(risk)}</b>", "body"),
            ]]
            hdr = Table(hdr_data, colWidths=[130*mm, 35*mm])
            hdr.setStyle(TableStyle([
                ("BACKGROUND",   (0,0), (-1,-1), LGRAY),
                ("TOPPADDING",   (0,0), (-1,-1), 5),
                ("BOTTOMPADDING",(0,0), (-1,-1), 5),
                ("LEFTPADDING",  (0,0), (0,-1),  6),
                ("BOX",          (0,0), (-1,-1), 0.5, MGRAY),
                ("TEXTCOLOR",    (1,0), (1,-1),  risk_color),
            ]))
            block.append(hdr)
            block.append(Spacer(1, 2*mm))

            # Figures
            block.append(self._kv_table([
                ("Debit movements",  f"£{dr_val:,.2f}"),
                ("Credit movements", f"£{cr_val:,.2f}"),
                ("Closing balance",  f"£{cb_val:,.2f}"),
            ], col_widths=[55*mm, 110*mm]))
            block.append(Spacer(1, 2*mm))

            # Why flagged
            if shap_text:
                block.append(self._P("<b>Why this account was flagged</b>", "h3"))
                block.append(self._P(shap_text, "body"))
                block.append(Spacer(1, 1*mm))

            # What to check
            block.append(self._P("<b>What to check</b>", "h3"))
            block.append(self._P(guidance["what_to_check"], "body"))
            block.append(Spacer(1, 1*mm))

            # How to fix
            block.append(self._P("<b>What to do if an issue is found</b>", "h3"))
            block.append(self._P(guidance["how_to_fix"], "body"))
            block.append(Spacer(1, 1*mm))

            # FRS ref
            block.append(self._P(
                f"<i>Relevant standard: {guidance['frs_ref']}</i>",
                "small"
            ))
            block.append(Spacer(1, 4*mm))

            self.elements.append(KeepTogether(block))

        self.elements.append(PageBreak())

    # ── ENTRIES NEEDING CORRECTION (VIOLATIONS) ────────────────────────────────
    def add_corrections_required(self, violations: List[Dict]):
        self._section_header("3. Entries That Need to Be Corrected")

        if not violations:
            self.elements.append(self._P(
                "✓  All balance checks passed. No correcting entries are required.", "ok"
            ))
            self.elements.append(PageBreak())
            return

        self.elements.append(self._P(
            f"{len(violations)} entry or entries do not balance correctly. "
            "Each one must be investigated and corrected before the accounts can be finalised.",
            "body"
        ))
        self.elements.append(Spacer(1, 4*mm))

        for i, v in enumerate(violations, 1):
            name     = v.get("name") or v.get("account_name") or "Unknown Account"
            code     = v.get("code") or v.get("account_code") or "—"
            rule     = v.get("rule") or v.get("rule_name") or "Balance reconciliation"
            expected = float(v.get("expected", 0))
            actual   = float(v.get("actual", 0))
            variance = float(v.get("variance", abs(actual - expected)))
            severity = v.get("severity", "WARN")
            category = v.get("cat") or v.get("category", "")
            action   = v.get("action", "")
            guidance = _category_guidance(category)

            block = []

            hdr_data = [[
                self._P(f"<b>{i}. {name}</b>  <font size='8' color='#64748B'>Nominal code: {code}</font>", "h3"),
                self._P(f"<b>[{severity}]</b>", "error" if severity == "CRITICAL" else "warn"),
            ]]
            hdr = Table(hdr_data, colWidths=[130*mm, 35*mm])
            hdr.setStyle(TableStyle([
                ("BACKGROUND",   (0,0), (-1,-1), colors.HexColor("#FFF1F2") if severity == "CRITICAL" else colors.HexColor("#FFFBEB")),
                ("TOPPADDING",   (0,0), (-1,-1), 5),
                ("BOTTOMPADDING",(0,0), (-1,-1), 5),
                ("LEFTPADDING",  (0,0), (0,-1),  6),
                ("BOX",          (0,0), (-1,-1), 0.5, RED if severity == "CRITICAL" else AMBER),
            ]))
            block.append(hdr)
            block.append(Spacer(1, 2*mm))

            block.append(self._P(
                f"The <b>closing balance</b> recorded for this account does not agree to the "
                f"amount calculated from the opening balance and period movements. "
                f"This must be investigated and corrected before the accounts are finalised.",
                "body"
            ))
            block.append(Spacer(1, 2*mm))

            block.append(self._kv_table([
                ("Rule checked",    "Opening balance + Debits − Credits = Closing balance"),
                ("Computed figure", f"£{expected:,.2f}"),
                ("Recorded figure", f"£{actual:,.2f}"),
                ("Difference",      f"£{variance:,.2f}  ({variance/max(abs(expected),1)*100:.1f}% of expected)"),
            ]))
            block.append(Spacer(1, 2*mm))

            # Correcting action
            action_text = action if action else guidance["how_to_fix"]
            block.append(self._P("<b>Action required</b>", "h3"))
            block.append(self._P(
                f"Locate the posting of £{variance:,.2f} that explains this difference. "
                "This is likely a missing or duplicate journal entry. Once found, "
                f"post a correcting journal to account {code}.",
                "body"
            ))
            block.append(self._P(
                f"<i>Relevant standard: {_category_guidance(category)['frs_ref']}</i>",
                "small"
            ))
            block.append(Spacer(1, 4*mm))

            self.elements.append(KeepTogether(block))

        self.elements.append(PageBreak())

    # ── REVIEWER SIGN-OFF ──────────────────────────────────────────────────────
    def add_sign_off(self):
        self._section_header("4. Reviewer Sign-Off Checklist")

        self.elements.append(self._P(
            "Please confirm each of the following before signing off this review. "
            "This checklist should be retained with the working papers file.",
            "body"
        ))
        self.elements.append(Spacer(1, 4*mm))

        checklist = [
            ["", "Item", "Initials"],
            ["☐", "I have reviewed all flagged accounts and am satisfied that appropriate action has been taken or documented.", ""],
            ["☐", "I have confirmed that all reconciliation differences have been investigated and corrected or noted.", ""],
            ["☐", "I have reviewed the AI-generated commentary and confirm it is consistent with my own professional assessment.", ""],
            ["☐", "I am satisfied that the accounts are ready for client review / sign-off / filing.", ""],
        ]
        ct = Table(checklist, colWidths=[8*mm, 135*mm, 22*mm])
        ct.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), self.primary),
            ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("GRID",          (0,0), (-1,-1), 0.3, MGRAY),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING",   (0,0), (-1,-1), 4),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, LGRAY]),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ]))
        self.elements += [ct, Spacer(1, 8*mm)]

        # Signature block
        sig_data = [
            [self._P("Reviewed by (print)", "body"), self._P("_" * 30, "body"), self._P("Date", "body"), self._P("_" * 20, "body")],
            [self._P("Signature", "body"),           self._P("_" * 30, "body"), self._P("Firm",  "body"), self._P("_" * 20, "body")],
        ]
        sig = Table(sig_data, colWidths=[30*mm, 55*mm, 15*mm, 65*mm])
        sig.setStyle(TableStyle([
            ("TOPPADDING", (0,0),(-1,-1), 8),
            ("BOTTOMPADDING",(0,0),(-1,-1), 8),
            ("GRID", (0,0),(-1,-1), 0, WHITE),
        ]))
        self.elements += [sig, PageBreak()]

    # ── AI COMMENTARY ──────────────────────────────────────────────────────────
    def add_ai_commentary(self, commentary: str):
        self._section_header("5. Review Notes (AI-Assisted)")
        self.elements.append(self._P(
            "The following notes were generated automatically from the structured data. "
            "Every figure stated below is taken directly from the submitted data — "
            "no additional information has been introduced. These notes should be "
            "reviewed and verified by a qualified accountant.",
            "small"
        ))
        self.elements.append(Spacer(1, 3*mm))
        # Split on double newline for natural paragraphs
        for para in (commentary or "").split("\n\n"):
            para = para.strip()
            if para:
                self.elements.append(self._P(para, "body"))
        self.elements.append(Spacer(1, 4*mm))

    # ── AUDIT TRAIL ────────────────────────────────────────────────────────────
    def add_processing_record(self, events: List[Dict]):
        self._section_header("6. Processing Record")
        self.elements.append(self._P(
            "The following records show each step taken by the Oversight platform "
            "when processing this submission. This provides a tamper-evident record "
            "of the automated procedures applied.",
            "body"
        ))
        self.elements.append(Spacer(1, 3*mm))

        # Plain-English event type translations
        _event_labels = {
            "job.started":          "Processing started",
            "phase1.complete":      "Template matching completed",
            "phase2.complete":      "Column analysis completed",
            "mapping.confirmed":    "Field mapping confirmed",
            "validation.complete":  "Reconciliation checks run",
            "oade.complete":        "Account review completed",
            "report.generated":     "Report generated",
            "anomaly_detection.complete": "Account review completed",
            "quality.scored":       "Quality assessment completed",
            "run.summary":          "Processing summary",
        }

        rows = [["Step", "Description", "Result"]]
        for i, ev in enumerate(events[:20], 1):
            etype  = ev.get("event_type") or ev.get("type", "")
            msg    = ev.get("message", "")
            sev    = ev.get("severity", "INFO")
            label  = _event_labels.get(etype, etype.replace(".", " ").title())
            result = "✓ OK" if sev == "INFO" else ("⚠ Review" if sev == "WARN" else "✗ Error")
            rows.append([str(i), f"{label}: {msg}", result])

        t = Table(rows, colWidths=[10*mm, 130*mm, 25*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), self.primary),
            ("TEXTCOLOR",     (0,0), (-1,0), WHITE),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,-1), 7),
            ("GRID",          (0,0), (-1,-1), 0.3, MGRAY),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, LGRAY]),
            ("TOPPADDING",    (0,0), (-1,-1), 3),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
            ("LEFTPADDING",   (0,0), (-1,-1), 3),
            ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ]))
        self.elements.append(t)
        self.elements.append(Spacer(1, 4*mm))
        self.elements.append(self._P(
            "This report was generated by Oversight Labs Limited (Co. No. 17145488). "
            "The Oversight platform uses statistical analysis and machine learning as "
            "analytical tools to assist qualified accountants. It does not replace "
            "professional judgement and should not be relied upon as professional advice.",
            "small"
        ))

    # ── SAVE ───────────────────────────────────────────────────────────────────
    def save(self, output_path: str) -> str:
        if not REPORTLAB_OK:
            raise ImportError("reportlab is required — pip install reportlab")
        doc = SimpleDocTemplate(
            output_path, pagesize=A4,
            leftMargin=20*mm, rightMargin=20*mm,
            topMargin=18*mm, bottomMargin=20*mm,
        )
        doc.build(self.elements, canvasmaker=_NumberedCanvas)
        logger.info("Evidence pack written to %s (%d bytes)", output_path,
                    Path(output_path).stat().st_size)
        return output_path


# ═══════════════════════════════════════════════════════════════════════════════
# Public function — called by the API route and the pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def generate_evidence_pack(
    run_id: str,
    client_name: str,
    period: str,
    findings: Dict[str, Any],
    output_dir: Path = Path("./data/exports"),
    logo_path: Optional[str] = None,
    primary_color: Optional[str] = None,
    secondary_color: Optional[str] = None,
) -> str:
    """
    Generate a client-branded, accountant-readable PDF evidence pack.

    Parameters
    ----------
    run_id        : str   — pipeline run ID (used for filename)
    client_name   : str   — client name printed on cover and header
    period        : str   — e.g. "Year ended 31 March 2025"
    findings      : dict  — must contain:
                        financial_summary  : dict  (revenue, net_profit, margin, etc.)
                        anomalies          : list  (from OADE detection)
                        violations         : list  (from reconciliation checks)
                        shap_attribution   : dict  (optional, from SHAP analysis)
                        audit_events       : list  (from AuditLog.events)
                        llm_commentary     : str   (optional, from LLM summariser)
    output_dir    : Path  — where to save the PDF
    logo_path     : str   — path to client logo PNG/JPG (optional)
    primary_color : str   — hex colour for report branding (optional)
    secondary_color: str  — hex colour for secondary branding (optional)

    Returns
    -------
    str : absolute path to the generated PDF
    """
    if not REPORTLAB_OK:
        raise ImportError("reportlab is required — install it with: pip install reportlab")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"oversight_report_{run_id}.pdf"

    gen = EvidencePackGenerator(
        run_id=run_id,
        client_name=client_name,
        period=period,
        logo_path=logo_path,
        primary_color=primary_color,
        secondary_color=secondary_color,
    )

    # Cover
    gen.add_cover_page()

    # Executive summary
    financial      = findings.get("financial_summary", {})
    anomalies      = findings.get("anomalies", [])
    violations     = findings.get("violations", findings.get("reconciliation_violations", []))
    shap_attr      = findings.get("shap_attribution", {})
    audit_events   = findings.get("audit_events", [])
    llm_commentary = findings.get("llm_commentary", "")

    gen.add_executive_summary(financial, len(anomalies), len(violations))
    gen.add_flagged_accounts(anomalies, shap_attr)
    gen.add_corrections_required(violations)
    gen.add_sign_off()

    if llm_commentary:
        gen.add_ai_commentary(llm_commentary)

    gen.add_processing_record(audit_events)

    return gen.save(str(output_path))
