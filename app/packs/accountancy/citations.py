from __future__ import annotations

import logging
from typing import Dict, Any, List

from app.packs.accountancy.regs import query_clauses_by_tags

logger = logging.getLogger("asgl.accountancy.citations")


DEFAULT_CODE_TAG_MAP: Dict[str, List[str]] = {
    "MISSING_STATEMENT": ["FRS102", "Presentation", "Disclosure"],
    "CASHFLOW_MISSING": ["FRS102", "CashFlow", "Disclosure"],
    "POLICY_NOTE_MISSING": ["FRS102", "Policies", "Notes"],
    "REVENUE_POLICY_RISK": ["FRS102", "Revenue", "Judgments", "Disclosure"],
    "LEASE_CLASSIFICATION_RISK": ["FRS102", "Leases", "Classification", "Disclosure"],
    "FIN_INSTRUMENT_DISCLOSURE_GAP": ["FRS102", "FinancialInstruments", "Disclosure"],
    "TAX_DISCLOSURE_GAP": ["FRS102", "Tax", "DeferredTax", "Disclosure"],
    "RELATED_PARTY_GAP": ["FRS102", "RelatedParties", "Disclosure"],
    "EVENTS_AFTER_RISK": ["FRS102", "EventsAfter", "Disclosure"],
    "GOING_CONCERN_GAP": ["FRS102", "GoingConcern", "Disclosure"],
    "DISCLOSURE_COMPLETENESS": ["FRS102", "Disclosure", "Completeness", "Reviewer"]
}


def citations_for_findings(findings: List[Dict[str, Any]], limit_per: int = 6) -> List[Dict[str, Any]]:
    out = []
    for f in findings or []:
        code = str(f.get("code") or "")
        tags = DEFAULT_CODE_TAG_MAP.get(code, ["FRS102", "Disclosure"])
        clauses = query_clauses_by_tags(tags, limit=limit_per)
        ff = dict(f)
        ff["citations_json"] = {
            "tags": tags,
            "clauses": [
                {"id": c.get("id"), "title": c.get("title"), "ref": c.get("ref"), "body": c.get("body")}
                for c in clauses
            ]
        }
        out.append(ff)
    return out
