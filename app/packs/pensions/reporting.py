from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple

logger = logging.getLogger("asgl.pensions.reporting")


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


@dataclass
class DataHealthReport:
    tenant_id: str
    scheme_id: str
    run_id: str
    total_members: int
    missing_common_fields: int
    dashboard_matching_risk: int
    spouse_data_gaps: int
    protection_flags_missing: int
    findings_open: int
    findings_high_sev_open: int
    auto_map_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "scheme_id": self.scheme_id,
            "run_id": self.run_id,
            "total_members": self.total_members,
            "missing_common_fields": self.missing_common_fields,
            "dashboard_matching_risk": self.dashboard_matching_risk,
            "spouse_data_gaps": self.spouse_data_gaps,
            "protection_flags_missing": self.protection_flags_missing,
            "findings_open": self.findings_open,
            "findings_high_sev_open": self.findings_high_sev_open,
            "auto_map_score": float(self.auto_map_score),
            "rates": {
                "missing_common_fields_rate": _safe_div(self.missing_common_fields, self.total_members),
                "dashboard_matching_risk_rate": _safe_div(self.dashboard_matching_risk, self.total_members),
                "spouse_data_gap_rate": _safe_div(self.spouse_data_gaps, self.total_members),
                "protection_flag_gap_rate": _safe_div(self.protection_flags_missing, self.total_members),
            },
        }


def build_data_health_report(*, tenant_id: str, scheme_id: str, run_id: str, rows: List[Dict[str, Any]], findings: List[Dict[str, Any]], match_score: float) -> DataHealthReport:
    total_members = len(rows or [])
    missing_common = sum(1 for f in findings if (f.get("code") or "") == "COMMON_DATA_GAP")
    dash_risk = sum(1 for f in findings if (f.get("code") or "") == "DASHBOARD_MATCHING_RISK")
    spouse_gap = sum(1 for f in findings if (f.get("code") or "") == "SPOUSE_DATA_GAP")
    prot_gap = sum(1 for f in findings if (f.get("code") or "") == "TRANSITIONAL_PROTECTION_GAP")
    open_items = sum(1 for f in findings if (f.get("severity") or "").upper() in ("WARN", "ERROR"))
    high_open = sum(1 for f in findings if (f.get("severity") or "").upper() == "ERROR")

    return DataHealthReport(
        tenant_id=tenant_id,
        scheme_id=scheme_id,
        run_id=run_id,
        total_members=total_members,
        missing_common_fields=missing_common,
        dashboard_matching_risk=dash_risk,
        spouse_data_gaps=spouse_gap,
        protection_flags_missing=prot_gap,
        findings_open=open_items,
        findings_high_sev_open=high_open,
        auto_map_score=float(match_score or 0.0),
    )


def build_dashboard_eri_report(*, tenant_id: str, scheme_id: str, run_id: str, findings: List[Dict[str, Any]], match_score: float) -> Dict[str, Any]:
    """
    Deterministic ERI-style evidence: matching quality proxy + key exception counts.
    """
    dash_risk = sum(1 for f in findings if (f.get("code") or "") == "DASHBOARD_MATCHING_RISK")
    common_gap = sum(1 for f in findings if (f.get("code") or "") == "COMMON_DATA_GAP")
    score = float(match_score or 0.0)
    return {
        "tenant_id": tenant_id,
        "scheme_id": scheme_id,
        "run_id": run_id,
        "eri": {
            "match_proxy_score": score,
            "dashboard_matching_risk_findings": dash_risk,
            "common_data_gap_findings": common_gap,
            "evidence": [
                {"metric": "match_proxy_score", "value": score, "note": "Proxy derived from template match score; can be replaced by real dashboard matching metrics."},
                {"metric": "dashboard_matching_risk_findings", "value": dash_risk},
                {"metric": "common_data_gap_findings", "value": common_gap},
            ],
        },
    }


def build_member_comms_report(*, tenant_id: str, scheme_id: str, run_id: str, comms: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Phase 13: deterministic comms MI.
    comms items expected: {member_id, comm_type, channel, status, sent_at, template_id}
    """
    total = len(comms or [])
    by_type: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for c in (comms or []):
        by_type[c.get("comm_type","unknown")] = by_type.get(c.get("comm_type","unknown"), 0) + 1
        by_status[c.get("status","unknown")] = by_status.get(c.get("status","unknown"), 0) + 1
    audit_gaps = sum(1 for f in findings if (f.get("code") or "") == "MEMBER_COMMS_AUDIT_GAP")
    return {
        "tenant_id": tenant_id,
        "scheme_id": scheme_id,
        "run_id": run_id,
        "total_comms": total,
        "by_type": by_type,
        "by_status": by_status,
        "audit_gaps": audit_gaps,
        "notes": "Comms MI is deterministic; can be extended to integrate provider email systems later.",
    }
