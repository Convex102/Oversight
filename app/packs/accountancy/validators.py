from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List


@dataclass
class ValidationFinding:
    severity: str = "INFO"
    code: str = ""
    message: str = ""
    meta: Dict[str, Any] = None


def validate_frs102_basics(*, canonical: Dict[str, Any]) -> List[ValidationFinding]:
    findings: List[ValidationFinding] = []
    meta = canonical or {}

    stmts = meta.get("statements") or {}
    if not stmts:
        findings.append(ValidationFinding(severity="WARN", code="MISSING_STATEMENT", message="No financial statements detected in canonical output.", meta={"area": "presentation"}))
    else:
        if not stmts.get("balance_sheet"):
            findings.append(ValidationFinding(severity="WARN", code="MISSING_STATEMENT", message="Balance sheet not detected.", meta={"statement": "balance_sheet"}))
        if not stmts.get("profit_and_loss"):
            findings.append(ValidationFinding(severity="WARN", code="MISSING_STATEMENT", message="Profit and loss not detected.", meta={"statement": "profit_and_loss"}))
        if meta.get("flags", {}).get("cashflow_required") and not stmts.get("cashflow"):
            findings.append(ValidationFinding(severity="WARN", code="CASHFLOW_MISSING", message="Cash flow statement appears required but is missing.", meta={"statement": "cashflow"}))

    notes = meta.get("notes") or {}
    if meta.get("flags", {}).get("policies_required") and not notes.get("accounting_policies"):
        findings.append(ValidationFinding(severity="WARN", code="POLICY_NOTE_MISSING", message="Accounting policies note missing or not detected.", meta={"area": "notes"}))

    if meta.get("flags", {}).get("material_revenue") and not (notes.get("revenue_policy") or notes.get("accounting_policies")):
        findings.append(ValidationFinding(severity="INFO", code="REVENUE_POLICY_RISK", message="Revenue appears material; confirm revenue recognition policy is disclosed and appropriate.", meta={"area": "revenue"}))

    if meta.get("flags", {}).get("leases_present") and not (notes.get("leases") or notes.get("commitments")):
        findings.append(ValidationFinding(severity="INFO", code="LEASE_CLASSIFICATION_RISK", message="Leases appear present; confirm classification and commitment disclosures.", meta={"area": "leases"}))

    if meta.get("flags", {}).get("fin_instruments_present") and not notes.get("financial_instruments"):
        findings.append(ValidationFinding(severity="INFO", code="FIN_INSTRUMENT_DISCLOSURE_GAP", message="Financial instruments appear present; confirm measurement and risk disclosures.", meta={"area": "fin_instruments"}))

    if meta.get("flags", {}).get("tax_present") and not notes.get("tax"):
        findings.append(ValidationFinding(severity="INFO", code="TAX_DISCLOSURE_GAP", message="Tax appears present; confirm current/deferred tax disclosures.", meta={"area": "tax"}))

    if meta.get("flags", {}).get("related_parties_possible") and not notes.get("related_parties"):
        findings.append(ValidationFinding(severity="INFO", code="RELATED_PARTY_GAP", message="Consider whether related party disclosures are required and complete.", meta={"area": "related_parties"}))

    if meta.get("flags", {}).get("events_after_possible") and not notes.get("events_after"):
        findings.append(ValidationFinding(severity="INFO", code="EVENTS_AFTER_RISK", message="Confirm whether events after the reporting date require adjustment or disclosure.", meta={"area": "events_after"}))

    if meta.get("flags", {}).get("going_concern_note_expected") and not notes.get("going_concern"):
        findings.append(ValidationFinding(severity="WARN", code="GOING_CONCERN_GAP", message="Going concern disclosure expected but not detected; confirm assessment and disclosures.", meta={"area": "going_concern"}))

    if meta.get("flags", {}).get("material_balances_present") and not notes:
        findings.append(ValidationFinding(severity="WARN", code="DISCLOSURE_COMPLETENESS", message="Material balances detected but no notes; confirm disclosure completeness.", meta={"area": "disclosure"}))

    return findings
