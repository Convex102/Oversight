from __future__ import annotations

"""
Oversight ASGL Ã¢â‚¬â€ Document Type Classifier
==========================================
Stage 1 of the build sequence: identify what each uploaded document IS
before attempting extraction.

Classification is structural pattern recognition:
- Column header patterns (Sage TB has "Nominal Code", "Debit", "Credit")
- Nominal code ranges (4000-4999 = revenue in Sage COA)
- Known field positions (CT600 Box 1 = "Tax chargeable")
- PDF text landmarks ("CT600", "HMRC", "Company Tax Return")
- Sheet names ("Trial Balance", "P&L", "Balance Sheet")

Returns a DocumentType with confidence score and extracted metadata
that downstream extraction stages use to route to the right extractor.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import re
import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Ã¢â€â‚¬Ã¢â€â‚¬ Document type registry Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

DOCUMENT_TYPES = {
    "sage_trial_balance":       "Sage Trial Balance Export",
    "software_trial_balance":   "Trial Balance (Software Export)",
    "xero_trial_balance":       "Xero Trial Balance Export",
    "quickbooks_trial_balance": "QuickBooks Trial Balance",
    "excel_working_papers":     "Excel Working Papers",
    "ct600":                    "CT600 Company Tax Return",
    "ct_computation":           "Corporation Tax Computation",
    "vat_return":               "VAT Return (VAT100)",
    "statutory_accounts":       "Statutory Accounts / Financial Statements",
    "bank_statement":           "Bank Statement",
    "fixed_asset_register":     "Fixed Asset Register",
    "director_loan_account":    "Director Loan Account",
    "payroll_summary":          "Payroll Summary",
    "journal_entries":          "Journal Entries",
    "nominal_ledger":           "Nominal Ledger",
    "audit_trail":              "Audit Trail",
    "unknown":                  "Unknown Document",
}


@dataclass
class ClassificationResult:
    doc_type: str
    label: str
    confidence: float          # 0.0 - 1.0
    schema_hint: str           # which packs/schemas/*.json to use
    extraction_config: Dict[str, Any] = field(default_factory=dict)
    signals: List[str] = field(default_factory=list)  # human-readable explanation


# Ã¢â€â‚¬Ã¢â€â‚¬ Sage COA nominal code ranges Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

SAGE_RANGES = {
    "turnover":             (4000, 4999),
    "cost_of_sales":        (5000, 5999),
    "direct_expenses":      (6000, 6999),
    "overheads":            (7000, 7999),
    "depreciation":         (8000, 8999),
    "other_income":         (3000, 3999),
    "trade_debtors":        (1100, 1199),
    "bank":                 (1200, 1299),
    "prepayments":          (1300, 1399),
    "trade_creditors":      (2100, 2199),
    "vat_control":          (2200, 2299),
    "paye_control":         (2210, 2219),
    "directors_loan":       (2300, 2399),
    "share_capital":        (3000, 3099),
    "retained_earnings":    (3200, 3299),
    "fixed_assets":         (0, 999),
}


def _has_sage_nominals(df: pd.DataFrame) -> Tuple[bool, float, List[str]]:
    """Check if a DataFrame has Sage nominal codes."""
    signals = []
    code_cols = [c for c in df.columns if any(
        kw in str(c).lower() for kw in ("nominal", "code", "account code", "a/c")
    )]
    if not code_cols:
        return False, 0.0, []

    codes = []
    for col in code_cols[:2]:
        for val in df[col].dropna().astype(str).head(50):
            try:
                codes.append(int(re.sub(r"[^0-9]", "", val)))
            except ValueError:
                pass

    if not codes:
        return False, 0.0, []

    # Check how many fall in known Sage ranges
    hits = sum(1 for c in codes for lo, hi in SAGE_RANGES.values() if lo <= c <= hi)
    coverage = hits / max(1, len(codes))
    if coverage > 0.3:
        signals.append(f"Sage nominal codes detected ({hits}/{len(codes)} in known ranges)")
        return True, min(0.95, 0.5 + coverage), signals
    return False, 0.0, []


def _score_headers(df: pd.DataFrame, patterns: List[str]) -> Tuple[float, List[str]]:
    """Score how many header patterns match."""
    headers = [str(c).lower() for c in df.columns]
    header_text = " ".join(headers)
    signals = []
    hits = 0
    for p in patterns:
        if re.search(p, header_text, re.IGNORECASE):
            hits += 1
            signals.append(f"Header match: {p!r}")
    return hits / max(1, len(patterns)), signals


# Ã¢â€â‚¬Ã¢â€â‚¬ Excel/tabular classifiers Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

def _classify_excel_df(df: pd.DataFrame, filename: str, sheet_name: str = "") -> ClassificationResult:
    fname_lower = filename.lower()
    sheet_lower = sheet_name.lower()
    signals = []

    # Sage TB
    sage_match, sage_conf, sage_sigs = _has_sage_nominals(df)
    tb_header_score, tb_sigs = _score_headers(df, [
        r"nominal|account code|a/c",
        r"debit|dr",
        r"credit|cr",
        r"balance|net|amount",
    ])
    if sage_match and tb_header_score > 0.5:
        return ClassificationResult(
            doc_type="sage_trial_balance",
            label=DOCUMENT_TYPES["sage_trial_balance"],
            confidence=min(0.97, sage_conf + 0.1),
            schema_hint="trial_balance_1.0.0.json",
            extraction_config={"nominal_col": "auto", "balance_col": "auto", "sage_ranges": True},
            signals=sage_sigs + tb_sigs,
        )

    # Xero TB
    xero_score, xero_sigs = _score_headers(df, [
        r"account code|account id",
        r"ytd debit|ytd credit",
        r"account type",
    ])
    if xero_score > 0.6:
        return ClassificationResult(
            doc_type="xero_trial_balance",
            label=DOCUMENT_TYPES["xero_trial_balance"],
            confidence=0.88,
            schema_hint="trial_balance_1.0.0.json",
            extraction_config={"provider": "xero"},
            signals=xero_sigs,
        )

    # Generic TB (Debit + Credit + Account columns)
    if tb_header_score > 0.6:
        return ClassificationResult(
            doc_type="sage_trial_balance",
            label="Trial Balance",
            confidence=0.75,
            schema_hint="trial_balance_1.0.0.json",
            extraction_config={},
            signals=tb_sigs + [f"Generic TB pattern (score={tb_header_score:.2f})"],
        )

    # Fixed asset register
    fa_score, fa_sigs = _score_headers(df, [
        r"asset|fixed asset",
        r"cost|addition|disposal",
        r"depreciation|accumulated|nbv|net book",
    ])
    if fa_score > 0.6 or "fixed asset" in fname_lower or "asset register" in fname_lower:
        return ClassificationResult(
            doc_type="fixed_asset_register",
            label=DOCUMENT_TYPES["fixed_asset_register"],
            confidence=0.85 if fa_score > 0.6 else 0.70,
            schema_hint="working_papers_summary_1.0.0.json",
            extraction_config={"extract_nbv": True, "extract_cost": True},
            signals=fa_sigs + [f"File: {filename}"],
        )

    # Director loan account
    if any(kw in fname_lower + sheet_lower for kw in ("director", "dla", "loan", "director loan")):
        return ClassificationResult(
            doc_type="director_loan_account",
            label=DOCUMENT_TYPES["director_loan_account"],
            confidence=0.80,
            schema_hint="working_papers_summary_1.0.0.json",
            extraction_config={"extract_balance": True},
            signals=[f"Filename/sheet match: director loan"],
        )

    # Journal entries
    je_score, je_sigs = _score_headers(df, [
        r"journal|jnl|entry",
        r"debit|dr",
        r"credit|cr",
        r"narrative|description|ref",
    ])
    if je_score > 0.6:
        return ClassificationResult(
            doc_type="journal_entries",
            label=DOCUMENT_TYPES["journal_entries"],
            confidence=0.80,
            schema_hint="journal_entries_1.0.0.json",
            extraction_config={},
            signals=je_sigs,
        )

    # Working papers (fallback for Excel with financial content)
    wp_score, wp_sigs = _score_headers(df, [
        r"balance|amount|value|Ã‚Â£",
        r"description|item|line|account",
    ])
    if wp_score > 0.4:
        return ClassificationResult(
            doc_type="excel_working_papers",
            label=DOCUMENT_TYPES["excel_working_papers"],
            confidence=0.65,
            schema_hint="working_papers_summary_1.0.0.json",
            extraction_config={},
            signals=wp_sigs + [f"Sheet: {sheet_name}"] if sheet_name else wp_sigs,
        )

    return ClassificationResult(
        doc_type="unknown",
        label=DOCUMENT_TYPES["unknown"],
        confidence=0.20,
        schema_hint="working_papers_summary_1.0.0.json",
        extraction_config={},
        signals=["No structural pattern matched"],
    )


# Ã¢â€â‚¬Ã¢â€â‚¬ PDF text classifiers Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

CT600_LANDMARKS = [
    r"CT600",
    r"Company Tax Return",
    r"HM Revenue.{0,20}Customs",
    r"Tax chargeable",
    r"Total turnover from trade",
    r"Profits chargeable to Corporation Tax",
    r"Self-assessment of tax payable",
]

CT_COMPUTATION_LANDMARKS = [
    r"Corporation Tax Computation",
    r"Adjusted Profit",
    r"Profits Chargeable to Corporation Tax",
    r"Corporation Tax Calculation",
    r"Trading Losses Summary",
    r"Capital Allowances Pool",
]

TB_PDF_LANDMARKS = [
    r"Trial Balance",
    r"Account.*Description",
    r"Debit.*Credit.*Variance",
    r"Profit.{0,10}Loss.*for period",
]

NOMINAL_LEDGER_LANDMARKS = [
    r"Nominal Ledger",
    r"Trans no.*Narrative",
    r"Sub-Total",
    r"WP ref",
]

AUDIT_TRAIL_LANDMARKS = [
    r"Audit Trail",
    r"Trans.*no.*A/c.*code",
    r"Account.*description.*Narrative",
]

VAT_LANDMARKS = [
    r"VAT Return",
    r"VAT100",
    r"Box 1.*VAT due on sales",
    r"Box 6.*Total value of sales",
    r"Value Added Tax",
]

ACCOUNTS_LANDMARKS = [
    r"Balance Sheet",
    r"Profit and Loss",
    r"Income Statement",
    r"Statement of Financial Position",
    r"FRS 10[25]",
    r"Companies Act 2006",
]


def _score_text_landmarks(text: str, landmarks: List[str]) -> Tuple[float, List[str]]:
    hits = []
    for lm in landmarks:
        if re.search(lm, text, re.IGNORECASE):
            hits.append(lm)
    return len(hits) / max(1, len(landmarks)), hits


def _classify_pdf_text(text: str, filename: str) -> ClassificationResult:
    fname_lower = filename.lower()

    ct600_score, ct600_sigs = _score_text_landmarks(text, CT600_LANDMARKS)
    ct_comp_score, ct_comp_sigs = _score_text_landmarks(text, CT_COMPUTATION_LANDMARKS)
    vat_score, vat_sigs = _score_text_landmarks(text, VAT_LANDMARKS)
    accounts_score, accounts_sigs = _score_text_landmarks(text, ACCOUNTS_LANDMARKS)
    tb_score, tb_sigs = _score_text_landmarks(text, TB_PDF_LANDMARKS)
    nl_score, nl_sigs = _score_text_landmarks(text, NOMINAL_LEDGER_LANDMARKS)
    at_score, at_sigs = _score_text_landmarks(text, AUDIT_TRAIL_LANDMARKS)

    # Audit Trail Ã¢â‚¬â€ must check before nominal ledger (similar structure)
    if at_score > 0.4 or "at_" in fname_lower or "audit trail" in fname_lower or "audit_trail" in fname_lower:
        return ClassificationResult(
            doc_type="audit_trail",
            label=DOCUMENT_TYPES["audit_trail"],
            confidence=min(0.93, 0.55 + at_score * 0.5),
            schema_hint="journal_entries_1.0.0.json",
            extraction_config={"extract_transactions": True},
            signals=[f"Audit trail landmark: {s}" for s in at_sigs] or ["Filename match: AT_"],
        )

    # Nominal Ledger
    if nl_score > 0.4 or "nl_" in fname_lower or "nominal" in fname_lower:
        return ClassificationResult(
            doc_type="nominal_ledger",
            label=DOCUMENT_TYPES["nominal_ledger"],
            confidence=min(0.93, 0.55 + nl_score * 0.5),
            schema_hint="journal_entries_1.0.0.json",
            extraction_config={"extract_balances": True},
            signals=[f"Nominal ledger landmark: {s}" for s in nl_sigs] or ["Filename match: NL_"],
        )

    # Trial Balance (PDF export from software)
    if tb_score > 0.4 or "tb_" in fname_lower or "trial balance" in fname_lower:
        return ClassificationResult(
            doc_type="software_trial_balance",
            label=DOCUMENT_TYPES["software_trial_balance"],
            confidence=min(0.93, 0.55 + tb_score * 0.5),
            schema_hint="trial_balance_1.0.0.json",
            extraction_config={"nominal_codes": True, "extract_balances": True},
            signals=[f"TB landmark: {s}" for s in tb_sigs] or ["Filename match: TB_"],
        )

    # CT Computation (separate from CT600 Ã¢â‚¬â€ produced by accountancy software)
    if ct_comp_score > 0.5 and ct600_score < 0.5:
        values = _extract_ct_computation_values(text)
        return ClassificationResult(
            doc_type="ct_computation",
            label=DOCUMENT_TYPES["ct_computation"],
            confidence=min(0.95, 0.6 + ct_comp_score * 0.4),
            schema_hint="sa302_hmrc_1.0.0.json",
            extraction_config={"values": values},
            signals=[f"CT Computation landmark: {s}" for s in ct_comp_sigs],
        )

    # CT600 HMRC Form (the actual filing)
    if ct600_score > 0.3 and ("ct600" in fname_lower or ct600_score > vat_score):
        boxes = _extract_ct600_boxes(text)
        return ClassificationResult(
            doc_type="ct600",
            label=DOCUMENT_TYPES["ct600"],
            confidence=min(0.97, 0.6 + ct600_score * 0.4),
            schema_hint="sa302_hmrc_1.0.0.json",
            extraction_config={"boxes": boxes, "provider": "hmrc"},
            signals=[f"CT600 landmark: {s}" for s in ct600_sigs],
        )

    if vat_score > accounts_score and vat_score > 0.2:
        return ClassificationResult(
            doc_type="vat_return", label=DOCUMENT_TYPES["vat_return"],
            confidence=min(0.95, 0.6 + vat_score * 0.4),
            schema_hint="vat_return_1.0.0.json",
            extraction_config={"extract_boxes": True},
            signals=[f"VAT landmark: {s}" for s in vat_sigs],
        )

    if accounts_score > 0.2:
        return ClassificationResult(
            doc_type="statutory_accounts",
            label=DOCUMENT_TYPES["statutory_accounts"],
            confidence=min(0.93, 0.55 + accounts_score * 0.4),
            schema_hint="statutory_accounts_pl_1.0.0.json",
            extraction_config={"extract_pl": True, "extract_bs": True},
            signals=[f"Accounts landmark: {s}" for s in accounts_sigs],
        )

    # Filename hints
    if "ct600" in fname_lower: return ClassificationResult(doc_type="ct600", label=DOCUMENT_TYPES["ct600"], confidence=0.70, schema_hint="sa302_hmrc_1.0.0.json", extraction_config={}, signals=["Filename: ct600"])
    if "vat" in fname_lower: return ClassificationResult(doc_type="vat_return", label=DOCUMENT_TYPES["vat_return"], confidence=0.65, schema_hint="vat_return_1.0.0.json", extraction_config={}, signals=["Filename: vat"])
    if "fs_" in fname_lower or "accounts" in fname_lower: return ClassificationResult(doc_type="statutory_accounts", label=DOCUMENT_TYPES["statutory_accounts"], confidence=0.65, schema_hint="statutory_accounts_pl_1.0.0.json", extraction_config={}, signals=["Filename: FS_"])

    return ClassificationResult(
        doc_type="statutory_accounts", label="PDF Document",
        confidence=0.30, schema_hint="statutory_accounts_pl_1.0.0.json",
        extraction_config={}, signals=["No landmark match"],
    )


def _extract_ct600_boxes(text: str) -> Dict[str, Optional[float]]:
    """Extract key CT600 box values from PDF text.
    
    CT600 PDFs store values in a digit grid: "7 7 0 3 8 7 . 0 0" = 770387.00
    Patterns match the box label then extract and reassemble the spaced digits.
    """
    boxes: Dict[str, Optional[float]] = {}

    def _extract_grid_number(label_pattern: str, search_text: str) -> Optional[float]:
        """Find a box label then extract the digit-grid value after it.
        CT600 PDFs store values as spaced digit grids:
          Decimal: "7 7 0 3 8 7 . 0 0" -> 770387.00
          Integer: "3 0 3 0 8 8" -> 303088
        """
        m = re.search(label_pattern, search_text, re.IGNORECASE)
        if not m:
            return None
        after = search_text[m.end():][:120]

        # Try decimal grid first: digits spaces decimal-point digits
        decimal_m = re.search(
            r'[Â£\s]*([0-9](?:\s*[0-9]){0,11})\s*[.,]\s*([0-9]{1,2})',
            after
        )
        if decimal_m:
            integer_part = re.sub(r'\s+', '', decimal_m.group(1))
            decimal_part = re.sub(r'\s+', '', decimal_m.group(2))
            try:
                return float(f"{integer_part}.{decimal_part}")
            except ValueError:
                pass

        # Try integer grid: space-separated digits (at least 4 digits total)
        int_m = re.search(r'[Â£\s]*([0-9](?:\s+[0-9]){3,11})(?:\s|$)', after)
        if int_m:
            val = re.sub(r'\s+', '', int_m.group(1))
            try:
                return float(val)
            except ValueError:
                pass

        # Fallback: clean number without spaces
        clean_m = re.search(r'([0-9][0-9,]{3,}(?:\.[0-9]{2})?)', after)
        if clean_m:
            try:
                return float(clean_m.group(1).replace(',', ''))
            except ValueError:
                pass
        return None

    box_patterns = {
        "box_1_tax_chargeable":  r"(?:Box\s*1.*?chargeable|Tax chargeable|510\s+Tax chargeable)",
        "box_145_turnover":      r"(?:145\s+Total turnover|Box\s*145|Total turnover from trade)",
        "box_155_net_trading":   r"(?:155\s+Trading profits|Box\s*155|Net trading profits)",
        "box_330_profits":       r"(?:315\s+Profits chargeable|330\s+|Profits chargeable to Corporation)",
        "box_780_trading_losses": r"(?:780\s+|Losses of trades carried on wholly)",
        "box_690_aia":           r"(?:690\s+|Annual investment allowance\s+690)",
    }

    for key, pattern in box_patterns.items():
        val = _extract_grid_number(pattern, text)
        boxes[key] = val

    return boxes


def _extract_ct_computation_values(text: str) -> Dict[str, Optional[float]]:
    """Extract key values from a CT Computation PDF."""
    import re
    values: Dict[str, Optional[float]] = {}
    patterns = {
        "profit_per_accounts":      r"Profit.{0,10}Loss.{0,10}per financial statements[^0-9\-\(]*[\-\(]?\s*([0-9,]+)",
        "net_capital_allowances":   r"Net capital allowances[^0-9]*?([0-9,]+)",
        "trade_loss":               r"Trade and Professional Profit.{0,10}Loss[^0-9\-\(]*[\-\(]?\s*([0-9,]+)",
        "losses_arising":           r"Trade and professional losses arising[^0-9]*([0-9,]+)",
        "losses_carried_back":      r"Carried back against profits[^0-9]*([0-9,]+)",
        "losses_carried_forward":   r"Total trading losses carried forward[^0-9]*([0-9,]+)",
        "aia":                      r"AIA Claimed[^0-9\(]*[\(]?\s*([0-9,]+)",
        "depreciation_addback":     r"Depreciation[^0-9]*([0-9,]+)",
        "ct_chargeable":            r"Net corporation tax chargeable[^0-9]*?([0-9,\.]+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                values[key] = float(m.group(1).replace(",", ""))
            except (ValueError, IndexError):
                values[key] = None
        else:
            values[key] = None
    return values


# Ã¢â€â‚¬Ã¢â€â‚¬ Main classification entry point Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬

def classify_document(
    filename: str,
    data: bytes,
    *,
    sheet_name: str = "",
    pdf_text: Optional[str] = None,
) -> ClassificationResult:
    """Classify a document from its bytes.

    For Excel: checks ALL sheet names first for AM-style working paper section
    coding (C1, D, E2, H1, I1, J1, M1, O1 etc) before header-based analysis.
    For PDF: extracts text and applies landmark matching.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in ("xlsx", "xlsm", "xls", "csv"):
        try:
            import io, re as _re
            if ext == "csv":
                df = pd.read_csv(io.BytesIO(data), nrows=100)
                return _classify_excel_df(df, filename, sheet_name=sheet_name)

            xf = pd.ExcelFile(io.BytesIO(data))
            all_sheets = xf.sheet_names

            # AM working papers detection via section-coded sheet names:
            # C1-CT Lead, D1-Directors Loans, E2-Fixed Assets, H1-Debtors,
            # I1-Bank Lead, J1-Creditors, M1-Wages, O1-VAT, Q-Tax info
            WP_SHEET_PATTERNS = [
                r"^[A-Z]\d",            # C1, D1, E2, H1, I1, J1, M1, O1
                r"\bLead\b",            # "Debtors Lead", "Bank Lead"
                r"File control",        # File control sheet
                r"Sage (TB|Nominal)",   # Sage TB / Nominal Ledger
                r"^Index$",             # Index tab
                r"^Notes$",             # Notes
                r"DLA|Director.*Loan",  # Director loan
                r"Wages|Payroll",       # Wages analysis
                r"VAT Lead",            # VAT section
            ]
            wp_hits = sum(
                1 for s in all_sheets
                if any(_re.search(p, s, _re.IGNORECASE) for p in WP_SHEET_PATTERNS)
            )
            if wp_hits >= 3 or (wp_hits >= 2 and len(all_sheets) >= 10):
                return ClassificationResult(
                    doc_type="excel_working_papers",
                    label=DOCUMENT_TYPES["excel_working_papers"],
                    confidence=min(0.95, 0.65 + wp_hits * 0.02),
                    schema_hint="working_papers_summary_1.0.0.json",
                    extraction_config={
                        "sheet_count": len(all_sheets),
                        "wp_sheet_hits": wp_hits,
                        "sheets": all_sheets[:30],
                    },
                    signals=[
                        f"AM working papers: {wp_hits}/{len(all_sheets)} sheets match section coding",
                        f"Sheets: {', '.join(all_sheets[:5])}...",
                    ],
                )

            df = pd.read_excel(xf, sheet_name=0, nrows=100)
            return _classify_excel_df(df, filename, sheet_name=sheet_name)

        except Exception as e:
            logger.warning("Excel classification failed for %s: %s", filename, e)
            return ClassificationResult(
                doc_type="unknown", label="Unknown",
                confidence=0.1, schema_hint="working_papers_summary_1.0.0.json",
                extraction_config={}, signals=[f"Parse error: {e}"],
            )

    elif ext == "pdf":
        text = pdf_text or ""
        if not text:
            try:
                import pdfplumber, io
                with pdfplumber.open(io.BytesIO(data)) as pdf:
                    pages = []
                    for page in pdf.pages[:5]:
                        t = page.extract_text() or ""
                        pages.append(t)
                    text = " ".join(pages)
            except Exception as e:
                logger.warning("PDF text extraction failed for %s: %s", filename, e)
        return _classify_pdf_text(text, filename)

    return ClassificationResult(
        doc_type="unknown", label="Unknown file type",
        confidence=0.0, schema_hint="working_papers_summary_1.0.0.json",
        extraction_config={}, signals=[f"Unsupported extension: {ext}"],
    )


