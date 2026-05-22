from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple


@dataclass
class StatRequirement:
    key: str
    title: str
    required_for: List[str]  # size bands: micro/small/medium/large/all
    exemption_if: Optional[str] = None  # canonical flag expression (simple)
    evidence_keys: Optional[List[str]] = None


def _flag(c: Dict[str, Any], name: str) -> bool:
    return bool((c.get("flags") or {}).get(name))


def infer_size_band(canonical: Dict[str, Any]) -> str:
    flags = canonical.get("flags") or {}
    sb = str(flags.get("size_band") or "").lower().strip()
    if sb in {"micro", "small", "medium", "large"}:
        return sb
    if flags.get("micro_entity"):
        return "micro"
    if flags.get("small_entity"):
        return "small"
    return "unknown"


def infer_framework(canonical: Dict[str, Any]) -> str:
    flags = canonical.get("flags") or {}
    fw = str(flags.get("framework") or "").upper().strip()
    if fw in {"FRS105", "FRS101", "FRS102", "FRS102A"}:
        return fw
    if flags.get("small_entity"):
        return "FRS102A"
    if flags.get("micro_entity"):
        return "FRS105"
    if flags.get("reduced_disclosure"):
        return "FRS101"
    return "FRS102"


# Phase 4: a minimal but expandable Companies Act requirements matrix
# NOTE: This is a high-level deterministic scaffold; it does not attempt to encode every CA2006 nuance.
REQ_MATRIX: List[StatRequirement] = [
    StatRequirement(
        key="directors_report",
        title="Directors’ Report",
        required_for=["medium", "large"],
        exemption_if="micro_or_small_exempt",
        evidence_keys=["statutory_reports.directors_report"],
    ),
    StatRequirement(
        key="strategic_report",
        title="Strategic Report",
        required_for=["medium", "large"],
        exemption_if="micro_or_small_exempt",
        evidence_keys=["statutory_reports.strategic_report"],
    ),
    StatRequirement(
        key="cashflow_statement",
        title="Cash Flow Statement",
        required_for=["medium", "large", "small"],
        exemption_if="framework_frs105",
        evidence_keys=["statements.cashflow"],
    ),
]


def _exemption_applies(canonical: Dict[str, Any], expr: Optional[str]) -> bool:
    if not expr:
        return False
    sb = infer_size_band(canonical)
    fw = infer_framework(canonical)
    if expr == "micro_or_small_exempt":
        return sb in {"micro", "small"}
    if expr == "framework_frs105":
        return fw == "FRS105"
    return False


def get_statutory_requirements(canonical: Dict[str, Any]) -> List[Dict[str, Any]]:
    sb = infer_size_band(canonical)
    fw = infer_framework(canonical)
    out = []
    for r in REQ_MATRIX:
        required = (sb in r.required_for) or ("all" in r.required_for)
        exempt = _exemption_applies(canonical, r.exemption_if) if required else False
        out.append(
            {
                "key": r.key,
                "title": r.title,
                "required": bool(required and not exempt),
                "exempt": bool(required and exempt),
                "exemption_rule": r.exemption_if,
                "size_band": sb,
                "framework": fw,
                "evidence_keys": r.evidence_keys or [],
            }
        )
    return out


def evidence_present(canonical: Dict[str, Any], dotted_key: str) -> bool:
    parts = dotted_key.split(".")
    cur: Any = canonical
    for p in parts:
        if not isinstance(cur, dict):
            return False
        cur = cur.get(p)
        if cur is None:
            return False
    # If value is empty string/list/dict treat as not present
    if isinstance(cur, str) and not cur.strip():
        return False
    if isinstance(cur, (list, dict)) and len(cur) == 0:
        return False
    return True


def statutory_gap_findings(canonical: Dict[str, Any]) -> List[Dict[str, Any]]:
    reqs = get_statutory_requirements(canonical)
    findings: List[Dict[str, Any]] = []
    for r in reqs:
        if r["required"]:
            # evidence must exist
            missing = []
            for ek in r["evidence_keys"]:
                if not evidence_present(canonical, ek):
                    missing.append(ek)
            if missing:
                findings.append(
                    {
                        "severity": "WARN",
                        "code": "STATUTORY_REQUIREMENT_MISSING",
                        "message": f"Statutory requirement missing: {r['title']} (size band={r['size_band']}, framework={r['framework']}).",
                        "meta": {"requirement": r, "missing_evidence": missing, "tags": ["CompaniesAct", "Schedules", "Disclosure", "Reviewer"]},
                    }
                )
        elif r["exempt"]:
            findings.append(
                {
                    "severity": "INFO",
                    "code": "STATUTORY_REQUIREMENT_EXEMPT",
                    "message": f"Statutory requirement exempt: {r['title']} (size band={r['size_band']}, framework={r['framework']}). Confirm exemption is valid and documented.",
                    "meta": {"requirement": r, "tags": ["CompaniesAct", "Exemptions", "Disclosure", "Reviewer"]},
                }
            )
    return findings
