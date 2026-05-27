"""
OVERSIGHT-P — Pension Schema Mapper
=====================================
Maps raw OCR-extracted labels from physical/scanned pension documents
to canonical PensionsMember fields.

Integrates with:
  - multi_ocr_pdf_ingest.py (OCR extraction)
  - pension_mass_ingestor.py (batch processing)
  - Flywheel (confirmed mappings improve future runs)

Strategy:
  1. Exact match against CANONICAL_LABEL_MAP
  2. RapidFuzz fuzzy match (threshold 80)
  3. Confidence score per field

No FinBERT required here — pension field labels are structured
and domain-specific enough for high-quality fuzzy matching.
FinBERT integration available as enhancement when sentence-transformers
is on the deployment target.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Canonical label map ───────────────────────────────────────────────────────
# Maps every known label variation → canonical PensionsMember field name.
# Organised by section to match physical document layout.

CANONICAL_LABEL_MAP: Dict[str, str] = {
    # ── Identity ──────────────────────────────────────────────────────────────
    "member reference":                "member_id",
    "member ref":                      "member_id",
    "membership number":               "member_id",
    "member no":                       "member_id",
    "member id":                       "member_id",
    "reference":                       "member_id",
    "policy number":                   "member_id",
    "plan number":                     "member_id",

    "full name":                       "member_name",
    "name":                            "member_name",
    "member name":                     "member_name",
    "policyholder":                    "member_name",

    "forename":                        "forename",
    "first name":                      "forename",
    "given name":                      "forename",

    "surname":                         "surname",
    "last name":                       "surname",
    "family name":                     "surname",

    "date of birth":                   "date_of_birth",
    "dob":                             "date_of_birth",
    "birth date":                      "date_of_birth",
    "d.o.b":                           "date_of_birth",

    "national insurance no":           "ni_number",
    "national insurance number":       "ni_number",
    "ni number":                       "ni_number",
    "nino":                            "ni_number",
    "ni no":                           "ni_number",
    "n.i. number":                     "ni_number",

    "residential address":             "address",
    "address":                         "address",
    "home address":                    "address",

    "gender":                          "gender",
    "sex":                             "gender",

    # ── Scheme & Employment ───────────────────────────────────────────────────
    "scheme name":                     "scheme_name",
    "plan name":                       "scheme_name",
    "pension scheme":                  "scheme_name",
    "scheme":                          "scheme_name",

    "employer name":                   "employer_name",
    "employer":                        "employer_name",
    "company name":                    "employer_name",
    "employing company":               "employer_name",

    "member status":                   "status",
    "status":                          "status",
    "membership status":               "status",
    "employment status":               "status",

    "date joined scheme":              "scheme_join_date",
    "joined scheme":                   "scheme_join_date",
    "scheme entry date":               "scheme_join_date",
    "date of entry":                   "scheme_join_date",
    "commencement date":               "scheme_join_date",
    "start date":                      "scheme_join_date",

    "date left scheme":                "scheme_leave_date",
    "leaving date":                    "scheme_leave_date",
    "exit date":                       "scheme_leave_date",
    "date of exit":                    "scheme_leave_date",

    # ── Benefit / Tranche ─────────────────────────────────────────────────────
    "benefit category":                "assigned_tranche",
    "category":                        "assigned_tranche",
    "member category":                 "assigned_tranche",
    "section":                         "assigned_tranche",
    "tranche":                         "assigned_tranche",

    "selected retirement age":         "retirement_age",
    "retirement age":                  "retirement_age",
    "normal retirement age":           "retirement_age",
    "nra":                             "retirement_age",
    "target retirement age":           "retirement_age",

    # ── Financial ─────────────────────────────────────────────────────────────
    "total fund value":                "fund_value",
    "fund value":                      "fund_value",
    "plan value":                      "fund_value",
    "policy value":                    "fund_value",
    "transfer value":                  "fund_value",
    "current value":                   "fund_value",

    "employee contribution":           "employee_contribution_pct",
    "employee contribution rate":      "employee_contribution_pct",
    "member contribution":             "employee_contribution_pct",
    "employee rate":                   "employee_contribution_pct",
    "contribution rate":               "employee_contribution_pct",

    "employer contribution":           "employer_contribution_pct",
    "employer contribution rate":      "employer_contribution_pct",
    "company contribution":            "employer_contribution_pct",
    "employer rate":                   "employer_contribution_pct",

    "additional voluntary":            "avc_monthly",
    "avc":                             "avc_monthly",
    "additional voluntary contribution": "avc_monthly",
    "voluntary contribution":          "avc_monthly",

    "pensionable salary":              "pensionable_salary",
    "salary":                          "pensionable_salary",
    "annual salary":                   "pensionable_salary",
    "basic salary":                    "pensionable_salary",

    # ── Investment ────────────────────────────────────────────────────────────
    "investment strategy":             "investment_allocation",
    "investment choice":               "investment_allocation",
    "fund choice":                     "investment_allocation",
    "investment funds":                "investment_allocation",

    # ── Beneficiary ───────────────────────────────────────────────────────────
    "beneficiary":                     "beneficiary_name",
    "beneficiary name":                "beneficiary_name",
    "nominated beneficiary":           "beneficiary_name",
    "expression of wishes":            "beneficiary_name",

    "beneficiary relationship":        "beneficiary_relationship",
    "relationship":                    "beneficiary_relationship",
    "relationship to member":          "beneficiary_relationship",

    "beneficiary nomination pct":      "beneficiary_pct",
    "share":                           "beneficiary_pct",
    "percentage":                      "beneficiary_pct",
    "nomination percentage":           "beneficiary_pct",

    "nomination date":                 "nomination_date",
    "date of nomination":              "nomination_date",
    "expression of wishes date":       "nomination_date",
}


@dataclass
class MappingResult:
    """Result of mapping a single raw label to a canonical field."""
    raw_label: str
    canonical_field: str
    value: Any
    confidence: float       # 0.0–1.0
    method: str             # "exact" | "fuzzy" | "unknown"
    matched_key: str        # The map key that matched


def map_record(
    raw_dict: Dict[str, Any],
    fuzzy_threshold: int = 78,
) -> Tuple[Dict[str, Any], List[MappingResult], List[str]]:
    """
    Map a raw OCR-extracted dict to canonical PensionsMember fields.

    Args:
        raw_dict:        {raw_label: value} from OCR extraction
        fuzzy_threshold: RapidFuzz score threshold (0–100)

    Returns:
        (canonical_dict, mapping_results, unmapped_labels)
        canonical_dict:    {canonical_field: value}
        mapping_results:   Full audit trail of each mapping decision
        unmapped_labels:   Labels that couldn't be mapped
    """
    canonical: Dict[str, Any] = {}
    results: List[MappingResult] = []
    unmapped: List[str] = []

    for raw_label, value in raw_dict.items():
        clean = _clean_label(raw_label)
        if not clean:
            continue

        # Strategy 1: Exact match
        exact = CANONICAL_LABEL_MAP.get(clean)
        if exact:
            canonical[exact] = value
            results.append(MappingResult(
                raw_label=raw_label,
                canonical_field=exact,
                value=value,
                confidence=1.0,
                method="exact",
                matched_key=clean,
            ))
            continue

        # Strategy 2: Fuzzy match
        best_key, best_score = _fuzzy_match(clean)
        if best_score >= fuzzy_threshold:
            canon = CANONICAL_LABEL_MAP[best_key]
            confidence = best_score / 100.0
            canonical[canon] = value
            results.append(MappingResult(
                raw_label=raw_label,
                canonical_field=canon,
                value=value,
                confidence=confidence,
                method="fuzzy",
                matched_key=best_key,
            ))
        else:
            unmapped.append(raw_label)

    # Compute overall mapping coverage
    total = len(raw_dict)
    mapped_count = len(results)
    coverage = mapped_count / total if total > 0 else 0.0

    logger.info(
        "pension_schema_mapper: %d/%d fields mapped (%.0f%%), %d unmapped",
        mapped_count, total, coverage * 100, len(unmapped),
    )

    return canonical, results, unmapped


def _clean_label(label: str) -> str:
    """Normalise a raw OCR label for matching."""
    s = str(label).strip().lower()
    s = re.sub(r"[:\-_\*]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _fuzzy_match(query: str) -> Tuple[str, int]:
    """Find the best matching key in CANONICAL_LABEL_MAP using RapidFuzz."""
    try:
        from rapidfuzz import process, fuzz
        result = process.extractOne(
            query,
            CANONICAL_LABEL_MAP.keys(),
            scorer=fuzz.token_sort_ratio,
        )
        if result:
            return result[0], result[1]
    except ImportError:
        # Fallback: simple substring match
        for key in CANONICAL_LABEL_MAP:
            if query in key or key in query:
                return key, 85
    return "", 0


def compute_confidence(results: List[MappingResult]) -> float:
    """
    Compute overall confidence for a record's mapping.
    Weighted by field importance (identity fields weighted higher).
    """
    CRITICAL_FIELDS = {"member_id", "ni_number", "date_of_birth", "status"}
    IMPORTANT_FIELDS = {"scheme_join_date", "retirement_age", "fund_value",
                        "employee_contribution_pct", "employer_contribution_pct"}

    if not results:
        return 0.0

    weighted_sum = 0.0
    weight_total = 0.0

    for r in results:
        if r.canonical_field in CRITICAL_FIELDS:
            w = 3.0
        elif r.canonical_field in IMPORTANT_FIELDS:
            w = 2.0
        else:
            w = 1.0
        weighted_sum += r.confidence * w
        weight_total += w

    return round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0
