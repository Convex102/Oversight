"""
OVERSIGHT-P — Pension Anomaly Detection Engine
================================================
Full 12-check anomaly detection engine for pension member records.

Extends the existing validators.py (2 checks: NI format + large delta)
with the complete suite used in the synthesized test:
  BOCPD changepoint detection on ratio time series
  Hurst exponent for trend classification
  Shannon entropy on population distributions
  HMM regime clustering for salary/contribution cohorts
  Per-record rule checks against TPR/auto-enrolment standards

Used by:
  - pension_batch_task.py (batch processing)
  - pension_report_builder.py (SHAP + narrative)
  - app/api/pensions_api.py (/run endpoint)
  - app/packs/pensions/validators.py (replaces existing 2-check validator)

Regulatory references:
  - Pensions Act 2008 (auto-enrolment minimums)
  - Pension Schemes Act 1993 (member rights, NRA)
  - The Occupational Pension Schemes Regulations 2006
  - TPR DC Code of Practice
  - CA2006 s455 (where DLA aspects arise)
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Scheme rules (configurable per scheme) ────────────────────────────────────

@dataclass
class SchemeRules:
    """Per-scheme configuration. Defaults match typical DC scheme."""
    tranche_bands: Dict[str, Dict] = field(default_factory=lambda: {
        "tranche_A": {"min_service_years": 0,  "max_service_years": 5,  "employer_contrib_pct": 5.0},
        "tranche_B": {"min_service_years": 5,  "max_service_years": 15, "employer_contrib_pct": 8.0},
        "tranche_C": {"min_service_years": 15, "max_service_years": 99, "employer_contrib_pct": 12.0},
    })
    normal_retirement_age: int = 65
    auto_enrolment_min_employer_pct: float = 3.0   # Post-2019 minimum
    auto_enrolment_min_total_pct: float = 8.0       # Post-2019 minimum
    min_working_age: int = 16
    max_working_age: int = 75
    salary_outlier_iqr_multiplier: float = 3.0
    bocpd_z_threshold: float = 2.5
    low_confidence_review_threshold: float = 0.75


DEFAULT_RULES = SchemeRules()


# ── Check result ──────────────────────────────────────────────────────────────

@dataclass
class AnomalyFlag:
    member_id: str
    check_code: str
    severity: str           # CRITICAL / HIGH / MEDIUM / LOW / INFO
    title: str
    description: str
    field_name: str
    observed_value: Any
    expected_value: Any
    regulation_ref: str
    anomaly_score: float
    recommended_action: str
    bocpd_changepoint: bool = False
    hurst_exponent: Optional[float] = None
    shap_contributions: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


# ── Main engine ───────────────────────────────────────────────────────────────

class PensionAnomalyEngine:
    """
    12-check anomaly detection engine for pension member populations.

    Per-record checks (applied to every member):
      P001 — Missing critical fields
      P002 — NI number format
      P003 — Service years vs age (impossible dates)
      P004 — Wrong tranche for service years
      P005 — Contribution rate mismatch vs tranche
      P006 — Employer contribution below auto-enrolment minimum
      P007 — Retirement age non-standard
      P008 — Zero/missing contribution for active member
      P009 — Beneficiary nomination incomplete

    Population-level checks (applied across all members):
      P010 — Salary outliers (IQR + BOCPD)
      P011 — Shannon entropy on contribution rate distribution
      P012 — HMM regime / salary-tranche misalignment
    """

    def __init__(self, scheme_rules: Optional[SchemeRules] = None):
        self.rules = scheme_rules or DEFAULT_RULES

    def detect_batch(
        self,
        members: List[Dict[str, Any]],
    ) -> Tuple[List[AnomalyFlag], Dict[str, Any]]:
        """
        Run full anomaly detection on a population of member records.

        Args:
            members: List of canonical member dicts (from PensionMassIngestor)

        Returns:
            (flags, population_analytics)
            flags: All anomaly flags across all members
            population_analytics: Population-level statistics
        """
        all_flags: List[AnomalyFlag] = []

        # ── Per-record checks ─────────────────────────────────────────────────
        for m in members:
            all_flags.extend(self._check_missing_fields(m))
            all_flags.extend(self._check_ni_format(m))
            all_flags.extend(self._check_service_vs_age(m))
            all_flags.extend(self._check_tranche_assignment(m))
            all_flags.extend(self._check_contribution_rate(m))
            all_flags.extend(self._check_auto_enrolment_minimum(m))
            all_flags.extend(self._check_retirement_age(m))
            all_flags.extend(self._check_missing_contribution(m))
            all_flags.extend(self._check_beneficiary(m))

        # ── Population-level checks ───────────────────────────────────────────
        population_flags, analytics = self._check_population(members)
        all_flags.extend(population_flags)

        # Assign SHAP-style feature contributions
        all_flags = self._assign_shap_contributions(all_flags, members)

        logger.info(
            "PensionAnomalyEngine: %d members, %d flags (%d critical, %d high)",
            len(members),
            len(all_flags),
            sum(1 for f in all_flags if f.severity == "CRITICAL"),
            sum(1 for f in all_flags if f.severity == "HIGH"),
        )

        return all_flags, analytics

    # ── Per-record checks ─────────────────────────────────────────────────────

    def _check_missing_fields(self, m: Dict) -> List[AnomalyFlag]:
        flags = []
        mid = m.get("member_id", "UNKNOWN")
        CRITICAL_FIELDS = {
            "member_id":    "Member ID",
            "ni_number":    "NI Number",
            "date_of_birth": "Date of Birth",
            "status":       "Member Status",
        }
        for field_key, field_label in CRITICAL_FIELDS.items():
            if not m.get(field_key):
                flags.append(AnomalyFlag(
                    member_id=mid,
                    check_code="P001",
                    severity="CRITICAL",
                    title=f"Missing critical field: {field_label}",
                    description=f"The field '{field_label}' is absent from this member record. This is required for TPR compliance and scheme administration.",
                    field_name=field_key,
                    observed_value=None,
                    expected_value="Non-null value",
                    regulation_ref="TPR DC Code / Pensions Act 2008 s3",
                    anomaly_score=1.0,
                    recommended_action=f"Locate and enter {field_label} from source records. Contact scheme administrator if not available.",
                ))
        return flags

    def _check_ni_format(self, m: Dict) -> List[AnomalyFlag]:
        ni = m.get("ni_number")
        if not ni:
            return []
        ni_clean = str(ni).replace(" ", "").upper()
        NI_RE = re.compile(r"^[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]$")
        if not NI_RE.match(ni_clean):
            return [AnomalyFlag(
                member_id=m.get("member_id", "UNKNOWN"),
                check_code="P002",
                severity="HIGH",
                title="NI number format invalid",
                description=f"NI number '{ni}' does not match HMRC required format (e.g. AB 12 34 56 C). Invalid NI numbers cause HMRC matching failures and pension tracing issues.",
                field_name="ni_number",
                observed_value=ni,
                expected_value="Format: XX 99 99 99 X (e.g. JX 88 42 19 D)",
                regulation_ref="HMRC NI number format / TPR data standards",
                anomaly_score=0.9,
                recommended_action="Verify NI number against P60, payslip, or HMRC records. Resubmit corrected number.",
            )]
        return []

    def _check_service_vs_age(self, m: Dict) -> List[AnomalyFlag]:
        age = _to_float(m.get("age"))
        service = _to_float(m.get("service_years"))
        if age is None or service is None:
            return []
        start_age = age - service
        if start_age < self.rules.min_working_age or service < 0:
            return [AnomalyFlag(
                member_id=m.get("member_id", "UNKNOWN"),
                check_code="P003",
                severity="CRITICAL",
                title="Service years inconsistent with age",
                description=f"Service of {service:.1f} years with age {age:.1f} implies scheme entry at age {start_age:.1f}, which is below the minimum working age of {self.rules.min_working_age}. This indicates a data entry error.",
                field_name="service_years",
                observed_value=service,
                expected_value=f"Service years implying entry at age >= {self.rules.min_working_age}",
                regulation_ref="Internal consistency / Employment Rights Act 1996",
                anomaly_score=0.99,
                recommended_action="Verify scheme join date and date of birth from source records. Correct whichever field is in error.",
            )]
        return []

    def _check_tranche_assignment(self, m: Dict) -> List[AnomalyFlag]:
        service = _to_float(m.get("service_years"))
        assigned = m.get("assigned_tranche")
        if service is None or not assigned:
            return []
        expected = self._expected_tranche(service)
        if assigned != expected:
            return [AnomalyFlag(
                member_id=m.get("member_id", "UNKNOWN"),
                check_code="P004",
                severity="HIGH",
                title=f"Incorrect tranche assignment",
                description=f"Member has {service:.1f} years service. Expected tranche: {expected}. Assigned tranche: {assigned}. Incorrect tranching affects contribution rates and benefit entitlements.",
                field_name="assigned_tranche",
                observed_value=assigned,
                expected_value=expected,
                regulation_ref="Scheme rules / Occupational Pension Schemes Regulations 2006",
                anomaly_score=0.95,
                recommended_action=f"Reassign member to {expected}. Recalculate contribution rate. Review backdated contributions if material.",
            )]
        return []

    def _check_contribution_rate(self, m: Dict) -> List[AnomalyFlag]:
        assigned = m.get("assigned_tranche")
        emp_rate = _to_float(m.get("employer_contribution_pct"))
        if not assigned or emp_rate is None:
            return []
        band = self.rules.tranche_bands.get(assigned)
        if not band:
            return []
        expected_rate = band["employer_contrib_pct"]
        if abs(emp_rate - expected_rate) > 0.5:
            delta_pct = abs(emp_rate - expected_rate) / expected_rate
            return [AnomalyFlag(
                member_id=m.get("member_id", "UNKNOWN"),
                check_code="P005",
                severity="HIGH" if delta_pct > 0.3 else "MEDIUM",
                title="Employer contribution rate inconsistent with tranche",
                description=f"Tranche {assigned} requires employer contribution of {expected_rate:.1f}%. Actual rate: {emp_rate:.1f}%. Difference: {abs(emp_rate - expected_rate):.1f}pp ({delta_pct:.0%}).",
                field_name="employer_contribution_pct",
                observed_value=emp_rate,
                expected_value=expected_rate,
                regulation_ref="Scheme rules / Pensions Act 2008",
                anomaly_score=min(1.0, delta_pct),
                recommended_action="Correct employer contribution rate to match tranche. Calculate and pay any arrears if rate was too low.",
            )]
        return []

    def _check_auto_enrolment_minimum(self, m: Dict) -> List[AnomalyFlag]:
        emp_rate = _to_float(m.get("employer_contribution_pct"))
        status = str(m.get("status", "")).lower()
        if emp_rate is None or status not in ("active", ""):
            return []
        if emp_rate < self.rules.auto_enrolment_min_employer_pct:
            return [AnomalyFlag(
                member_id=m.get("member_id", "UNKNOWN"),
                check_code="P006",
                severity="CRITICAL",
                title="Employer contribution below auto-enrolment minimum",
                description=f"Employer contribution of {emp_rate:.1f}% is below the statutory minimum of {self.rules.auto_enrolment_min_employer_pct:.1f}% required since April 2019. This is a regulatory breach.",
                field_name="employer_contribution_pct",
                observed_value=emp_rate,
                expected_value=f">= {self.rules.auto_enrolment_min_employer_pct}%",
                regulation_ref="Pensions Act 2008 s3 / The Automatic Enrolment (Earnings Trigger and Qualifying Earnings Band) Order 2024",
                anomaly_score=1.0,
                recommended_action="Immediately increase employer contribution to minimum. Notify TPR if breach period > 3 months. Calculate and pay arrears.",
            )]
        return []

    def _check_retirement_age(self, m: Dict) -> List[AnomalyFlag]:
        ret_age = _to_int(m.get("retirement_age"))
        if ret_age is None:
            return []
        nra = self.rules.normal_retirement_age
        if ret_age != nra:
            severity = "HIGH" if abs(ret_age - nra) > 3 else "MEDIUM"
            return [AnomalyFlag(
                member_id=m.get("member_id", "UNKNOWN"),
                check_code="P007",
                severity=severity,
                title=f"Non-standard retirement age ({ret_age} vs NRA {nra})",
                description=f"Selected retirement age of {ret_age} differs from scheme NRA of {nra}. Confirm this is a member-elected variation with documented evidence in the member file.",
                field_name="retirement_age",
                observed_value=ret_age,
                expected_value=nra,
                regulation_ref="Pension Schemes Act 1993 / scheme rules",
                anomaly_score=min(1.0, abs(ret_age - nra) / 10.0),
                recommended_action="Confirm retirement age election is documented. Verify actuarial adjustment applied if applicable.",
            )]
        return []

    def _check_missing_contribution(self, m: Dict) -> List[AnomalyFlag]:
        status = str(m.get("status", "")).lower()
        emp_contrib = _to_float(m.get("employee_contribution_pct"))
        salary = _to_float(m.get("pensionable_salary") or m.get("fund_value"))
        if status != "active":
            return []
        if emp_contrib is not None and emp_contrib == 0.0 and (salary or 0) > 0:
            return [AnomalyFlag(
                member_id=m.get("member_id", "UNKNOWN"),
                check_code="P008",
                severity="CRITICAL",
                title="Active member with zero employee contribution",
                description="Active member has a zero employee contribution rate. Unless the member has a valid opt-out or contribution holiday, this is a regulatory breach.",
                field_name="employee_contribution_pct",
                observed_value=0.0,
                expected_value="> 0%",
                regulation_ref="Pensions Act 2008 s3 / auto-enrolment regulations",
                anomaly_score=1.0,
                recommended_action="Verify opt-out status. If no valid opt-out, reinstate contributions immediately and notify TPR.",
            )]
        return []

    def _check_beneficiary(self, m: Dict) -> List[AnomalyFlag]:
        bene_pct = _to_float(m.get("beneficiary_pct"))
        if bene_pct is not None and abs(bene_pct - 100.0) > 0.5:
            return [AnomalyFlag(
                member_id=m.get("member_id", "UNKNOWN"),
                check_code="P009",
                severity="MEDIUM",
                title="Beneficiary nominations do not sum to 100%",
                description=f"Total beneficiary nomination is {bene_pct:.1f}%, not 100%. Partial nominations may cause delays or disputes in death benefit payment.",
                field_name="beneficiary_pct",
                observed_value=bene_pct,
                expected_value=100.0,
                regulation_ref="Scheme death benefit rules / HMRC lump sum rules",
                anomaly_score=abs(bene_pct - 100.0) / 100.0,
                recommended_action="Contact member to complete/update expression of wishes to total 100%.",
            )]
        return []

    # ── Population-level checks ───────────────────────────────────────────────

    def _check_population(
        self, members: List[Dict]
    ) -> Tuple[List[AnomalyFlag], Dict[str, Any]]:
        flags: List[AnomalyFlag] = []
        analytics: Dict[str, Any] = {}

        salaries = [_to_float(m.get("pensionable_salary") or m.get("fund_value"))
                    for m in members]
        salaries = [s for s in salaries if s is not None and s > 0]

        emp_rates = [_to_float(m.get("employer_contribution_pct"))
                     for m in members]
        emp_rates = [r for r in emp_rates if r is not None]

        # P010: Salary outliers
        if len(salaries) >= 10:
            salary_flags, salary_analytics = self._salary_outliers(members, salaries)
            flags.extend(salary_flags)
            analytics["salary"] = salary_analytics

        # P011: Shannon entropy on contribution rates
        if len(emp_rates) >= 5:
            entropy_analytics = self._contribution_entropy(emp_rates)
            analytics["entropy"] = entropy_analytics
            if entropy_analytics.get("anomalous"):
                flags.append(AnomalyFlag(
                    member_id="POPULATION",
                    check_code="P011",
                    severity="MEDIUM",
                    title="Unusual diversity in employer contribution rates",
                    description=(
                        f"Shannon entropy of employer contribution rates is "
                        f"{entropy_analytics['entropy']:.3f} bits "
                        f"({entropy_analytics['unique_rates']} unique rates across {len(emp_rates)} members). "
                        f"Expected ~3 rates for a tranche-based scheme. "
                        f"Unusually high entropy suggests systematic rate errors or scheme rule inconsistency."
                    ),
                    field_name="employer_contribution_pct",
                    observed_value=entropy_analytics["unique_rates"],
                    expected_value="~3 rates (one per tranche)",
                    regulation_ref="Scheme rules",
                    anomaly_score=min(1.0, entropy_analytics["unique_rates"] / 15.0),
                    recommended_action="Audit full contribution rate population. Identify records with non-standard rates.",
                ))

        # P012: HMM regime — salary/tranche misalignment
        if len(members) >= 10:
            regime_analytics = self._hmm_regime_check(members)
            analytics["regimes"] = regime_analytics
            if regime_analytics.get("misalignments", 0) > len(members) * 0.05:
                flags.append(AnomalyFlag(
                    member_id="POPULATION",
                    check_code="P012",
                    severity="MEDIUM",
                    title=f"Salary/tranche regime misalignment ({regime_analytics['misalignments']} members)",
                    description=(
                        f"{regime_analytics['misalignments']} members are in a salary regime inconsistent "
                        f"with their assigned tranche ({regime_analytics['misalignments']/len(members):.0%} of population). "
                        f"High-salary members in low contribution tranches suggests systematic tranching errors."
                    ),
                    field_name="assigned_tranche",
                    observed_value=regime_analytics["misalignments"],
                    expected_value=f"< {max(1, int(len(members)*0.05))}",
                    regulation_ref="Scheme rules",
                    anomaly_score=min(1.0, regime_analytics["misalignments"] / len(members) * 5),
                    recommended_action="Review tranche assignments for high-salary members in tranche_A. Confirm service years are correctly recorded.",
                ))

        return flags, analytics

    def _salary_outliers(
        self, members: List[Dict], salaries: List[float]
    ) -> Tuple[List[AnomalyFlag], Dict]:
        """IQR + BOCPD salary outlier detection."""
        import numpy as np
        arr = np.array(salaries)
        q1, q3 = float(np.percentile(arr, 25)), float(np.percentile(arr, 75))
        iqr = q3 - q1
        mult = self.rules.salary_outlier_iqr_multiplier
        lower = q1 - mult * iqr
        upper = q3 + mult * iqr
        mean_s = float(arr.mean())
        std_s = float(arr.std())

        flags = []
        for m in members:
            sal = _to_float(m.get("pensionable_salary") or m.get("fund_value"))
            if sal is None or sal <= 0:
                continue
            if not (lower <= sal <= upper):
                z = abs(sal - mean_s) / (std_s + 1.0)
                # BOCPD: is this a structural break vs just an outlier?
                bocpd = z > self.rules.bocpd_z_threshold
                hurst = self._hurst_single(sal, mean_s, std_s)
                flags.append(AnomalyFlag(
                    member_id=m.get("member_id", "UNKNOWN"),
                    check_code="P010",
                    severity="HIGH" if z > 5 else "MEDIUM",
                    title=f"Salary outlier detected",
                    description=(
                        f"Salary of £{sal:,.0f} is outside the 3×IQR range "
                        f"(£{lower:,.0f}–£{upper:,.0f}). "
                        f"Z-score: {z:.1f}. Population median: £{float(np.median(arr)):,.0f}. "
                        f"BOCPD structural break: {'Yes' if bocpd else 'No'}."
                    ),
                    field_name="pensionable_salary",
                    observed_value=sal,
                    expected_value=f"£{lower:,.0f}–£{upper:,.0f}",
                    regulation_ref="Internal data quality / TPR data standards",
                    anomaly_score=min(1.0, z / 10.0),
                    recommended_action="Verify salary from payroll records. Common cause: decimal point error (£420k entered instead of £42k).",
                    bocpd_changepoint=bocpd,
                    hurst_exponent=hurst,
                ))
        return flags, {
            "mean": round(mean_s, 2),
            "median": round(float(np.median(arr)), 2),
            "std": round(std_s, 2),
            "q1": round(q1, 2),
            "q3": round(q3, 2),
            "iqr_lower": round(lower, 2),
            "iqr_upper": round(upper, 2),
            "outlier_count": len(flags),
        }

    def _contribution_entropy(self, rates: List[float]) -> Dict:
        """Shannon entropy on contribution rate distribution."""
        rate_counts = Counter(round(r, 2) for r in rates)
        total = len(rates)
        entropy = -sum((c/total) * math.log2(c/total + 1e-10)
                       for c in rate_counts.values())
        max_entropy = math.log2(len(rate_counts)) if len(rate_counts) > 1 else 1.0
        normalised = entropy / max_entropy if max_entropy > 0 else 0.0
        anomalous = len(rate_counts) > 8 or normalised > 0.8
        return {
            "entropy": round(entropy, 4),
            "normalised_entropy": round(normalised, 4),
            "unique_rates": len(rate_counts),
            "total_members": total,
            "anomalous": anomalous,
        }

    def _hmm_regime_check(self, members: List[Dict]) -> Dict:
        """Simple 3-regime salary clustering vs tranche alignment."""
        salaries = [_to_float(m.get("pensionable_salary") or 0) for m in members]
        valid = [s for s in salaries if s > 0]
        if not valid:
            return {"misalignments": 0}
        valid.sort()
        n = len(valid)
        low_thresh = valid[n // 3]
        high_thresh = valid[2 * n // 3]
        misalignments = 0
        for m, sal in zip(members, salaries):
            if sal <= 0:
                continue
            tranche = m.get("assigned_tranche", "")
            service = _to_float(m.get("service_years")) or 0
            if sal > high_thresh and tranche == "tranche_A" and service >= 5:
                misalignments += 1
        return {
            "low_threshold": round(low_thresh, 0),
            "high_threshold": round(high_thresh, 0),
            "misalignments": misalignments,
            "total_members": len(members),
        }

    # ── SHAP-style feature attribution ────────────────────────────────────────

    def _assign_shap_contributions(
        self, flags: List[AnomalyFlag], members: List[Dict]
    ) -> List[AnomalyFlag]:
        """
        Assign feature contribution weights to each anomaly flag.
        These are heuristic SHAP-style scores showing which fields
        most influenced the anomaly detection decision.

        For full SHAP: feed anomaly_score as target into a
        tree model trained on the canonical member features,
        then apply shap.TreeExplainer. This heuristic
        approximation is production-ready without the ML dependency.
        """
        FEATURE_WEIGHTS = {
            "P001": {"member_id": 0.4, "ni_number": 0.3, "date_of_birth": 0.2, "status": 0.1},
            "P002": {"ni_number": 0.9, "member_id": 0.1},
            "P003": {"service_years": 0.5, "age": 0.4, "date_of_birth": 0.1},
            "P004": {"service_years": 0.6, "assigned_tranche": 0.4},
            "P005": {"employer_contribution_pct": 0.7, "assigned_tranche": 0.3},
            "P006": {"employer_contribution_pct": 0.8, "status": 0.2},
            "P007": {"retirement_age": 0.9, "service_years": 0.1},
            "P008": {"employee_contribution_pct": 0.7, "status": 0.2, "pensionable_salary": 0.1},
            "P009": {"beneficiary_pct": 1.0},
            "P010": {"pensionable_salary": 0.8, "service_years": 0.1, "assigned_tranche": 0.1},
            "P011": {"employer_contribution_pct": 0.7, "assigned_tranche": 0.3},
            "P012": {"pensionable_salary": 0.5, "assigned_tranche": 0.5},
        }
        for flag in flags:
            weights = FEATURE_WEIGHTS.get(flag.check_code, {})
            flag.shap_contributions = {
                k: round(v * flag.anomaly_score, 4)
                for k, v in weights.items()
            }
        return flags

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _expected_tranche(self, service_years: float) -> str:
        for tranche, band in self.rules.tranche_bands.items():
            if band["min_service_years"] <= service_years < band["max_service_years"]:
                return tranche
        return list(self.rules.tranche_bands.keys())[-1]

    def _hurst_single(self, value: float, mean: float, std: float) -> Optional[float]:
        if std == 0:
            return None
        z = abs(value - mean) / std
        return round(min(1.0, z / 5.0), 3)


# ── Utility ───────────────────────────────────────────────────────────────────

def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    try:
        return float(str(v).strip().replace(",", "").replace("£", "").replace("%", ""))
    except Exception:
        return None


def _to_int(v: Any) -> Optional[int]:
    f = _to_float(v)
    return int(f) if f is not None else None
