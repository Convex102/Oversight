"""
OVERSIGHT-P — Pension Report Builder
=======================================
Generates the Oversight-P output report:
  1. Per-member anomaly table with SHAP feature contributions
  2. Population-level analytics (entropy, regime, BOCPD)
  3. LLM-generated executive narrative (Gemini Flash)
  4. Evidence pack PDF (same structure as ASGL-A)
  5. Machine-readable JSON for API consumers

Mirrors the ASGL-A reporting structure so the same UI components
can render both verticals.

Integration:
  - Called from pension_batch_task.py after detect_batch()
  - Called from app/api/pensions_api.py for sync runs
  - Output PDF stored as evidence pack in DB
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PensionReport:
    """Complete Oversight-P report output."""
    scheme_id: str
    run_id: str
    generated_at: str
    total_members: int
    total_flags: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    population_analytics: Dict[str, Any]
    anomaly_summary: List[Dict[str, Any]]   # Top anomalies ranked by score
    member_exception_list: List[Dict]        # Full per-member flag list
    shap_feature_importance: Dict[str, float]  # Global feature importance
    narrative: str                           # LLM-generated summary
    narrative_model: str                     # Which LLM was used
    pdf_path: Optional[str] = None          # Path to evidence pack PDF


class PensionReportBuilder:
    """
    Builds the full Oversight-P report from anomaly engine output.
    """

    def __init__(self):
        self._llm_available = bool(os.environ.get("GOOGLE_API_KEY"))

    def build(
        self,
        scheme_id: str,
        run_id: str,
        members: List[Dict[str, Any]],
        flags: List[Any],            # List[AnomalyFlag]
        population_analytics: Dict[str, Any],
        output_dir: Optional[str] = None,
    ) -> PensionReport:
        """
        Build complete report from anomaly engine output.

        Args:
            scheme_id: Scheme identifier
            run_id:    Batch run ID
            members:   Canonical member records
            flags:     AnomalyFlag list from PensionAnomalyEngine
            population_analytics: Population stats from engine
            output_dir: If provided, write PDF evidence pack here
        """
        now = datetime.utcnow().isoformat() + "Z"

        sev_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}
        for f in flags:
            sev = getattr(f, "severity", "LOW")
            sev_counts[sev] = sev_counts.get(sev, 0) + 1

        # ── Per-member exception list ─────────────────────────────────────────
        exception_list = self._build_exception_list(flags)

        # ── Anomaly summary (top 20) ──────────────────────────────────────────
        anomaly_summary = sorted(
            [f.to_dict() if hasattr(f, "to_dict") else vars(f) for f in flags],
            key=lambda x: -x.get("anomaly_score", 0),
        )[:20]

        # ── Global SHAP feature importance ────────────────────────────────────
        shap_importance = self._aggregate_shap(flags)

        # ── LLM narrative ─────────────────────────────────────────────────────
        narrative, narrative_model = self._generate_narrative(
            scheme_id=scheme_id,
            total_members=len(members),
            flags=flags,
            sev_counts=sev_counts,
            analytics=population_analytics,
            shap_importance=shap_importance,
        )

        report = PensionReport(
            scheme_id=scheme_id,
            run_id=run_id,
            generated_at=now,
            total_members=len(members),
            total_flags=len(flags),
            critical_count=sev_counts.get("CRITICAL", 0),
            high_count=sev_counts.get("HIGH", 0),
            medium_count=sev_counts.get("MEDIUM", 0),
            low_count=sev_counts.get("LOW", 0),
            population_analytics=population_analytics,
            anomaly_summary=anomaly_summary,
            member_exception_list=exception_list,
            shap_feature_importance=shap_importance,
            narrative=narrative,
            narrative_model=narrative_model,
        )

        # ── PDF evidence pack ─────────────────────────────────────────────────
        if output_dir:
            try:
                pdf_path = self._build_pdf(report, output_dir)
                report.pdf_path = pdf_path
            except Exception as e:
                logger.warning("PDF evidence pack failed: %s", e)

        return report

    def to_dict(self, report: PensionReport) -> Dict[str, Any]:
        """Serialise report to API-ready dict."""
        return {
            "scheme_id": report.scheme_id,
            "run_id": report.run_id,
            "generated_at": report.generated_at,
            "summary": {
                "total_members": report.total_members,
                "total_flags": report.total_flags,
                "critical": report.critical_count,
                "high": report.high_count,
                "medium": report.medium_count,
                "low": report.low_count,
                "clean_members": report.total_members - len({
                    f.get("member_id") for f in report.member_exception_list
                }),
            },
            "population_analytics": report.population_analytics,
            "top_anomalies": report.anomaly_summary[:10],
            "member_exception_list": report.member_exception_list,
            "shap_feature_importance": report.shap_feature_importance,
            "narrative": report.narrative,
            "narrative_model": report.narrative_model,
            "pdf_path": report.pdf_path,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_exception_list(self, flags: List[Any]) -> List[Dict]:
        """Group flags by member_id, sorted by max severity."""
        from collections import defaultdict
        by_member: Dict[str, List] = defaultdict(list)
        for f in flags:
            mid = getattr(f, "member_id", "UNKNOWN")
            by_member[mid].append(
                f.to_dict() if hasattr(f, "to_dict") else vars(f)
            )

        SEV = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
        result = []
        for mid, member_flags in by_member.items():
            max_sev = max((SEV.get(f.get("severity", "LOW"), 1) for f in member_flags), default=0)
            max_sev_label = {v: k for k, v in SEV.items()}.get(max_sev, "LOW")
            result.append({
                "member_id": mid,
                "flag_count": len(member_flags),
                "max_severity": max_sev_label,
                "flags": member_flags,
            })

        result.sort(key=lambda x: -SEV.get(x["max_severity"], 0))
        return result

    def _aggregate_shap(self, flags: List[Any]) -> Dict[str, float]:
        """Aggregate SHAP contributions across all flags → global feature importance."""
        totals: Dict[str, float] = {}
        for f in flags:
            contribs = getattr(f, "shap_contributions", {})
            for field, score in contribs.items():
                totals[field] = totals.get(field, 0.0) + abs(score)
        total_sum = sum(totals.values()) or 1.0
        return {k: round(v / total_sum, 4) for k, v in
                sorted(totals.items(), key=lambda x: -x[1])[:10]}

    def _generate_narrative(
        self,
        scheme_id: str,
        total_members: int,
        flags: List[Any],
        sev_counts: Dict[str, int],
        analytics: Dict[str, Any],
        shap_importance: Dict[str, float],
    ) -> tuple[str, str]:
        """Generate LLM narrative. Falls back to template if Gemini unavailable."""

        # Build the structured prompt context
        top_checks: Dict[str, int] = {}
        for f in flags:
            code = getattr(f, "check_code", "?")
            top_checks[code] = top_checks.get(code, 0) + 1

        context = {
            "scheme_id": scheme_id,
            "total_members": total_members,
            "severity_counts": sev_counts,
            "top_check_codes": dict(sorted(top_checks.items(), key=lambda x: -x[1])[:5]),
            "shap_top_features": list(shap_importance.keys())[:5],
            "salary_analytics": analytics.get("salary", {}),
            "entropy_analytics": analytics.get("entropy", {}),
            "regime_analytics": analytics.get("regimes", {}),
        }

        if self._llm_available:
            try:
                narrative = self._call_gemini(context)
                return narrative, "gemini-2.0-flash"
            except Exception as e:
                logger.warning("Gemini narrative failed: %s", e)

        # Template fallback (no LLM)
        return self._template_narrative(context), "template"

    def _call_gemini(self, context: Dict) -> str:
        """Call Gemini Flash for executive narrative."""
        import urllib.request
        import json as _json

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        critical = context["severity_counts"].get("CRITICAL", 0)
        high = context["severity_counts"].get("HIGH", 0)
        total = context["total_members"]
        top_features = ", ".join(context.get("shap_top_features", [])[:3])

        prompt = f"""You are a senior pensions governance analyst writing an executive summary for a trustee board report.

Oversight-P has analysed {total} pension member records for scheme '{context["scheme_id"]}' and found:
- {critical} CRITICAL issues (regulatory breaches)
- {high} HIGH issues (significant data quality problems)  
- {context["severity_counts"].get("MEDIUM", 0)} MEDIUM issues
- Most frequent check codes: {context.get("top_check_codes", {})}
- Key data quality drivers (SHAP): {top_features}
- Population entropy: {context.get("entropy_analytics", {}).get("entropy", "N/A")} bits

Write a professional 3-paragraph executive summary (150 words max) for the trustee board covering:
1. Overall data quality assessment and key risk areas
2. Regulatory compliance position (auto-enrolment, TPR standards)
3. Recommended priority actions before any buy-in or scheme event

Use clear, non-technical language. Be direct about regulatory risks. Do not use bullet points."""

        payload = _json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
        }).encode()

        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-2.0-flash:generateContent?key={api_key}")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = _json.loads(resp.read().decode())
        return (body.get("candidates", [{}])[0]
                .get("content", {}).get("parts", [{}])[0].get("text", ""))

    def _template_narrative(self, context: Dict) -> str:
        """Fallback narrative when Gemini is unavailable."""
        total = context["total_members"]
        sev = context["severity_counts"]
        critical = sev.get("CRITICAL", 0)
        high = sev.get("HIGH", 0)
        clean = total - len({f for f in []})

        issues = critical + high
        pct = (issues / total * 100) if total > 0 else 0

        return (
            f"Oversight-P analysis of {total} member records identified {critical} critical "
            f"regulatory issues and {high} high-severity data quality concerns "
            f"({pct:.1f}% of the population). "
            f"Critical issues include auto-enrolment minimum breaches and impossible service year "
            f"records which require immediate remediation before any scheme event. "
            f"Priority actions: resolve all CRITICAL flags within 5 working days; "
            f"complete HIGH flags before any buy-in data submission; "
            f"implement ongoing data quality monitoring to prevent recurrence."
        )

    def _build_pdf(self, report: PensionReport, output_dir: str) -> str:
        """Generate PDF evidence pack using reportlab."""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.lib import colors
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
            )

            os.makedirs(output_dir, exist_ok=True)
            filename = os.path.join(
                output_dir,
                f"oversight_p_{report.scheme_id}_{report.run_id}.pdf"
            )

            doc = SimpleDocTemplate(filename, pagesize=A4,
                                    leftMargin=2*cm, rightMargin=2*cm,
                                    topMargin=2*cm, bottomMargin=2*cm)
            styles = getSampleStyleSheet()
            story = []

            # Header
            story.append(Paragraph("OVERSIGHT-P", styles["Title"]))
            story.append(Paragraph(
                f"Pension Member Data Quality Report | {report.scheme_id}",
                styles["Heading2"]
            ))
            story.append(Paragraph(
                f"Generated: {report.generated_at} | Run: {report.run_id}",
                styles["Normal"]
            ))
            story.append(HRFlowable(width="100%", thickness=1))
            story.append(Spacer(1, 0.3*cm))

            # Executive Summary
            story.append(Paragraph("Executive Summary", styles["Heading2"]))
            story.append(Paragraph(report.narrative, styles["Normal"]))
            story.append(Spacer(1, 0.5*cm))

            # Key metrics table
            metrics_data = [
                ["Metric", "Value"],
                ["Total Members Analysed", str(report.total_members)],
                ["Total Flags", str(report.total_flags)],
                ["CRITICAL Issues", str(report.critical_count)],
                ["HIGH Issues", str(report.high_count)],
                ["MEDIUM Issues", str(report.medium_count)],
            ]
            t = Table(metrics_data, colWidths=[10*cm, 6*cm])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f1a2f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4ff")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ]))
            story.append(t)
            story.append(Spacer(1, 0.5*cm))

            # SHAP feature importance
            story.append(Paragraph("Key Data Quality Drivers (SHAP Attribution)", styles["Heading2"]))
            shap_data = [["Field", "Importance"]]
            for feat, score in list(report.shap_feature_importance.items())[:8]:
                shap_data.append([feat.replace("_", " ").title(), f"{score:.1%}"])
            t2 = Table(shap_data, colWidths=[10*cm, 6*cm])
            t2.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f1a2f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4ff")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ]))
            story.append(t2)
            story.append(Spacer(1, 0.5*cm))

            # Top anomalies
            story.append(Paragraph("Top Anomalies by Severity", styles["Heading2"]))
            anom_data = [["Member ID", "Check", "Severity", "Field", "Score"]]
            for a in report.anomaly_summary[:15]:
                anom_data.append([
                    str(a.get("member_id", ""))[:12],
                    str(a.get("check_code", "")),
                    str(a.get("severity", "")),
                    str(a.get("field_name", ""))[:20],
                    f"{a.get('anomaly_score', 0):.3f}",
                ])
            t3 = Table(anom_data, colWidths=[3.5*cm, 2*cm, 2.5*cm, 5*cm, 2.5*cm])
            SEV_COLORS = {
                "CRITICAL": colors.HexColor("#fb7185"),
                "HIGH": colors.HexColor("#fbbf24"),
                "MEDIUM": colors.HexColor("#6ee7ff"),
                "LOW": colors.HexColor("#34d399"),
            }
            row_commands = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f1a2f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ]
            for i, a in enumerate(report.anomaly_summary[:15], start=1):
                sev = a.get("severity", "LOW")
                c = SEV_COLORS.get(sev, colors.white)
                row_commands.append(("BACKGROUND", (2, i), (2, i), c))
            t3.setStyle(TableStyle(row_commands))
            story.append(t3)

            # Footer
            story.append(Spacer(1, 1*cm))
            story.append(HRFlowable(width="100%", thickness=0.5))
            story.append(Paragraph(
                "Generated by Oversight-P | Oversight Labs Limited | oversightlab.ai | "
                "This report is confidential and intended solely for the named scheme trustee/administrator.",
                ParagraphStyle("footer", parent=styles["Normal"], fontSize=7,
                               textColor=colors.HexColor("#888888")),
            ))

            doc.build(story)
            logger.info("PDF evidence pack written: %s", filename)
            return filename

        except ImportError:
            logger.warning("reportlab not installed — PDF skipped")
            return ""
        except Exception as e:
            logger.warning("PDF build failed: %s", e)
            return ""
