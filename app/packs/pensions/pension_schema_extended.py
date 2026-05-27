"""
OVERSIGHT-P — Extended Pensions Schema
========================================
Extends the existing schema.py with the full member record fields
required for mass ingestion, anomaly detection, and reporting.

Drop-in replacement for app/packs/pensions/schema.py.
Backwards compatible — all existing fields preserved.
New fields are Optional with None defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class PensionsMember:
    """Full canonical pension member record."""
    # ── Identity ──────────────────────────────────────────────────────────────
    member_id: str
    member_name: Optional[str] = None
    forename: Optional[str] = None
    surname: Optional[str] = None
    date_of_birth: Optional[str] = None       # ISO or DD/MM/YYYY
    ni_number: Optional[str] = None
    address: Optional[str] = None
    gender: Optional[str] = None

    # ── Scheme & Employment ───────────────────────────────────────────────────
    scheme_name: Optional[str] = None
    employer_name: Optional[str] = None
    status: Optional[str] = None              # Active / Deferred / Pensioner
    scheme_join_date: Optional[str] = None
    scheme_leave_date: Optional[str] = None
    service_years: Optional[float] = None     # Derived
    age: Optional[float] = None               # Derived from DOB

    # ── Benefit / Tranche ─────────────────────────────────────────────────────
    assigned_tranche: Optional[str] = None    # tranche_A / tranche_B / tranche_C
    expected_tranche: Optional[str] = None    # Derived by engine
    retirement_age: Optional[int] = None
    normal_retirement_age: Optional[int] = None  # Scheme NRA for comparison

    # ── Financial ─────────────────────────────────────────────────────────────
    fund_value: Optional[float] = None
    employee_contribution_pct: Optional[float] = None
    employer_contribution_pct: Optional[float] = None
    expected_employer_contrib_pct: Optional[float] = None  # Derived
    avc_monthly: Optional[float] = None
    pensionable_salary: Optional[float] = None

    # ── Investment ────────────────────────────────────────────────────────────
    investment_allocation: Optional[str] = None  # Free text or JSON
    investment_total_pct: Optional[float] = None

    # ── Beneficiary ───────────────────────────────────────────────────────────
    beneficiary_name: Optional[str] = None
    beneficiary_relationship: Optional[str] = None
    beneficiary_pct: Optional[float] = None
    nomination_date: Optional[str] = None

    # ── Oversight metadata ────────────────────────────────────────────────────
    source_document: Optional[str] = None       # Filename ingested from
    extraction_strategy: Optional[str] = None   # pdfplumber / gemini / etc.
    mapping_confidence: Optional[float] = None  # 0.0–1.0
    ingestion_timestamp: Optional[str] = None
    raw_labels: Optional[Dict[str, Any]] = None  # Original OCR labels before mapping


@dataclass
class PensionsService:
    member_id: str
    service_start: Optional[str] = None
    service_end: Optional[str] = None
    pensionable_salary: Optional[float] = None
    employer_name: Optional[str] = None
    contribution_rate: Optional[float] = None


@dataclass
class PensionsCalcResult:
    member_id: str
    calc_type: str
    pre_value: Optional[float] = None
    post_value: Optional[float] = None
    delta_value: Optional[float] = None
    assumptions: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PensionsAnomalyFlag:
    """A single anomaly finding against a member record."""
    member_id: str
    check_code: str
    severity: str           # CRITICAL / HIGH / MEDIUM / LOW / INFO
    title: str
    description: str
    field_name: str
    observed_value: Any
    expected_value: Any
    regulation_ref: str
    anomaly_score: float    # 0.0–1.0
    recommended_action: str
    shap_contributions: Dict[str, float] = field(default_factory=dict)
    bocpd_changepoint: bool = False
    hurst_exponent: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "member_id": self.member_id,
            "check_code": self.check_code,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "field_name": self.field_name,
            "observed_value": self.observed_value,
            "expected_value": self.expected_value,
            "regulation_ref": self.regulation_ref,
            "anomaly_score": round(self.anomaly_score, 4),
            "recommended_action": self.recommended_action,
            "shap_contributions": self.shap_contributions,
            "bocpd_changepoint": self.bocpd_changepoint,
            "hurst_exponent": self.hurst_exponent,
        }


@dataclass
class PensionsCanonical:
    scheme_id: str
    members: List[PensionsMember] = field(default_factory=list)
    services: List[PensionsService] = field(default_factory=list)
    results: List[PensionsCalcResult] = field(default_factory=list)
    anomaly_flags: List[PensionsAnomalyFlag] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scheme_id": self.scheme_id,
            "members": [m.__dict__ for m in self.members],
            "services": [s.__dict__ for s in self.services],
            "results": [r.__dict__ for r in self.results],
            "anomaly_flags": [f.to_dict() for f in self.anomaly_flags],
            "meta": dict(self.meta),
        }
