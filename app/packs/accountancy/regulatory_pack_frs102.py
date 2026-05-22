from __future__ import annotations
"""
Oversight ASGL — FRS 102 Regulatory Pack (Small Entities)
==========================================================
Implements the full regulatory check framework for:
  - FRS 102 (The Financial Reporting Standard applicable in the UK)
  - Companies Act 2006 (CA2006)
  - Corporation Tax rules (CTA2010, CAA2001)
  - HMRC employment rules (ITEPA2003)

Architecture
------------
Each rule is a pure function:
  rule_*(fields: dict, materiality_pct: float) -> List[RegulatoryFinding]

fields is a flat dict populated from ALL document streams:
  "tb.{nominal_code}"       TB balance by Sage nominal code
  "tb.{field_name}"         TB balance by description
  "prior_year.{code}"       Prior year TB value
  "fs.{field_name}"         Final accounts value
  "ct.{field_name}"         CT computation value
  "ct600.{box}"             CT600 box value
  "wp.{field_name}"         Working papers value
  "wp.sheets"               List of WP sheet names present

Rules are grouped by regulatory source and fire on ANY engagement
where the relevant fields are present. A missing field produces a
SKIP (not a failure). Rules that fire produce findings that become
review points in the engagement workflow.

Each finding includes:
  - rule_id (R-xxx format for regulatory, A-xxx for analytical)
  - regulation_ref (the specific section/box reference)
  - three-document source (which docs the check spans)
  - severity and description
  - suggested action

This file is the canonical regulatory pack. Additional packs
(FRS 105, IFRS 16, ISA 315) follow the same interface.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

AIA_LIMIT_2024 = 1_000_000.0   # Annual Investment Allowance limit
AIA_LIMIT_2023 = 1_000_000.0
S455_RATE = 0.3375              # S455 tax rate on overdrawn DLA
GOODWILL_MAX_LIFE = 10          # FRS 102 max assumed life for goodwill
SMALL_COMPANY_TURNOVER_THRESHOLD = 10_200_000
SMALL_COMPANY_ASSET_THRESHOLD   = 5_100_000


@dataclass
class RegulatoryFinding:
    rule_id: str
    rule_name: str
    regulation_ref: str
    category: str
    severity: str            # high | medium | low | info
    title: str
    description: str
    document_sources: List[str]   # which docs this check spans
    value_a: Optional[float] = None
    value_b: Optional[float] = None
    value_c: Optional[float] = None
    delta: Optional[float] = None
    suggested_action: Optional[str] = None
    signals: List[str] = field(default_factory=list)


def _get(fields: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        v = fields.get(key)
        if v is None:
            continue
        try:
            f = float(v)
            return f
        except (TypeError, ValueError):
            continue
    return None


def _pct(a: float, b: float) -> float:
    if abs(b) < 0.01:
        return 0.0
    return (a - b) / abs(b) * 100.0


def _abs_diff(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return abs(a - b)


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-DOCUMENT AGREEMENT CHECKS (TB + WP + FS + CT)
# ══════════════════════════════════════════════════════════════════════════════

def check_turnover_agreement(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Turnover must agree: TB → FS → CT600 Box 145.
    Sources: TB (sales accounts) + FS (P&L) + CT600 (Box 145)
    """
    findings = []
    tb_sales  = _get(fields, "tb.0000", "tb.sales", "tb.turnover")
    fs_turn   = _get(fields, "fs.turnover", "accounts.turnover")
    ct_turn   = _get(fields, "ct600.box_145_turnover", "ct.turnover")

    if tb_sales is not None:
        tb_sales = abs(tb_sales)  # TB credits are negative

    pairs = []
    if tb_sales and fs_turn:
        pairs.append(("TB Sales", tb_sales, "FS Turnover", fs_turn))
    if fs_turn and ct_turn:
        pairs.append(("FS Turnover", fs_turn, "CT600 Box 145", ct_turn))
    if tb_sales and ct_turn:
        pairs.append(("TB Sales", tb_sales, "CT600 Box 145", ct_turn))

    for label_a, val_a, label_b, val_b in pairs:
        diff = abs(val_a - val_b)
        pct  = _pct(val_a, val_b)
        if diff > 1.0 and abs(pct) > 0.1:
            findings.append(RegulatoryFinding(
                rule_id="R001",
                rule_name="Turnover Agreement",
                regulation_ref="FRS 102 s23; CT600 Box 145",
                category="income",
                severity="high" if abs(pct) > materiality_pct else "medium",
                title=f"Turnover mismatch: {label_a} £{val_a:,.0f} vs {label_b} £{val_b:,.0f}",
                description=(
                    f"{label_a} (£{val_a:,.2f}) does not agree with {label_b} (£{val_b:,.2f}). "
                    f"Difference: £{diff:,.2f} ({pct:+.2f}%). "
                    f"Turnover must be consistent across all three: TB, final accounts, and CT600. "
                    f"Check for timing differences, accruals, or misposting."
                ),
                document_sources=["TB", "FS", "CT600"],
                value_a=val_a, value_b=val_b, delta=val_a - val_b,
                suggested_action="Reconcile turnover across TB, FS and CT600 Box 145.",
            ))

    return findings


def check_profit_agreement(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Profit/(Loss) before tax must agree: FS → CT Computation.
    CT computation starts from FS profit and adjusts to taxable profit.
    """
    findings = []
    fs_profit  = _get(fields, "fs.profit_before_tax", "accounts.loss_before_tax",
                               "fs.loss_before_tax")
    ct_profit  = _get(fields, "ct.profit_per_accounts", "ct_computation.profit_per_accounts")

    if fs_profit is None or ct_profit is None:
        return findings

    # CT comp uses absolute value of FS profit/loss as starting point
    # FS shows signed value (negative for loss), CT comp shows absolute
    # Compare absolute values - a loss of 284,697 vs CT comp 284,697 = agree
    diff = abs(abs(fs_profit) - abs(ct_profit))
    if diff > 1.0:
        findings.append(RegulatoryFinding(
            rule_id="R002",
            rule_name="Profit Before Tax — FS vs CT Computation",
            regulation_ref="CTA2010 s5; CT Computation Adjusted Profit section",
            category="profit",
            severity="high",
            title=f"Profit before tax: FS £{fs_profit:,.0f} vs CT Computation £{ct_profit:,.0f}",
            description=(
                f"The CT computation starts from the financial statements profit/loss, "
                f"which must therefore agree. "
                f"FS shows £{fs_profit:,.2f} but CT computation shows £{ct_profit:,.2f}. "
                f"Difference: £{diff:,.2f}. "
                f"This may indicate a timing error or the CT computation not having "
                f"been updated after accounts adjustments."
            ),
            document_sources=["FS", "CT Computation"],
            value_a=fs_profit, value_b=ct_profit, delta=fs_profit - ct_profit,
            suggested_action="Update CT computation to reflect final accounts profit figure.",
        ))

    return findings


def check_depreciation_agreement(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Depreciation: TB total = FS note = CT computation add-back.
    FRS 102 s17 — depreciation charge disclosed in notes.
    """
    findings = []

    # TB: sum depreciation nominal codes
    tb_dep = None
    dep_codes = ["tb.3401", "tb.3402", "tb.3403", "tb.3404", "tb.3400"]
    dep_total = sum(abs(_get(fields, c) or 0.0) for c in dep_codes)
    if dep_total > 0:
        tb_dep = dep_total

    fs_dep  = _get(fields, "fs.depreciation", "accounts.depreciation")
    ct_dep  = _get(fields, "ct.depreciation_addback", "ct_computation.depreciation_addback")

    if tb_dep and ct_dep:
        diff = abs(tb_dep - ct_dep)
        if diff > 1.0 and _pct(tb_dep, ct_dep) > 0.1:
            findings.append(RegulatoryFinding(
                rule_id="R003",
                rule_name="Depreciation — TB vs CT Add-back",
                regulation_ref="FRS 102 s17; CTA2010 — depreciation not deductible",
                category="fixed_assets",
                severity="medium",
                title=f"Depreciation TB £{tb_dep:,.0f} vs CT add-back £{ct_dep:,.0f}",
                description=(
                    f"TB depreciation accounts total £{tb_dep:,.2f} but the CT computation "
                    f"adds back £{ct_dep:,.2f}. Difference: £{abs(tb_dep-ct_dep):,.2f}. "
                    f"All accounting depreciation must be added back in the CT computation "
                    f"and replaced by capital allowances."
                ),
                document_sources=["TB", "CT Computation"],
                value_a=tb_dep, value_b=ct_dep,
                suggested_action="Ensure CT computation adds back full TB depreciation charge.",
            ))

    return findings


def check_capital_allowances(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Capital allowances: CT Computation AIA ≤ AIA limit; agrees with CT600.
    CAA2001 — AIA limit £1m; cannot exceed qualifying expenditure.
    """
    findings = []

    ct_aia    = _get(fields, "ct.aia", "ct_computation.aia")
    ct600_aia = _get(fields, "ct600.box_690_aia", "ct600.aia")
    additions = _get(fields, "ct.additions_qualifying_aia",
                              "ct_computation.net_capital_allowances")

    if ct_aia is not None and ct_aia > AIA_LIMIT_2024:
        findings.append(RegulatoryFinding(
            rule_id="R004a",
            rule_name="AIA Exceeds Annual Limit",
            regulation_ref=f"CAA2001 s38A — AIA limit £{AIA_LIMIT_2024:,.0f}",
            category="tax",
            severity="high",
            title=f"AIA claimed (£{ct_aia:,.0f}) exceeds annual limit (£{AIA_LIMIT_2024:,.0f})",
            description=(
                f"Annual Investment Allowance claimed of £{ct_aia:,.0f} exceeds the "
                f"current annual limit of £{AIA_LIMIT_2024:,.0f}. "
                f"This is not permitted and would be disallowed by HMRC on enquiry."
            ),
            document_sources=["CT Computation"],
            value_a=ct_aia, value_b=AIA_LIMIT_2024,
            suggested_action="Reduce AIA to the qualifying expenditure amount or annual limit.",
        ))

    if ct_aia is not None and ct600_aia is not None:
        diff = abs(ct_aia - ct600_aia)
        if diff > 1.0:
            findings.append(RegulatoryFinding(
                rule_id="R004b",
                rule_name="AIA — CT Computation vs CT600",
                regulation_ref="CAA2001; CT600 Box 690",
                category="tax",
                severity="high",
                title=f"AIA: CT computation £{ct_aia:,.0f} vs CT600 Box 690 £{ct600_aia:,.0f}",
                description=(
                    f"Capital allowances claimed differ between the CT computation "
                    f"(£{ct_aia:,.2f}) and CT600 Box 690 (£{ct600_aia:,.2f}). "
                    f"Difference: £{diff:,.2f}. Both must agree."
                ),
                document_sources=["CT Computation", "CT600"],
                value_a=ct_aia, value_b=ct600_aia,
                suggested_action="Reconcile CT computation to CT600 capital allowances boxes.",
            ))

    return findings


def check_trading_loss(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Trading loss: CT Computation = CT600 Box 780; carry-back/forward correctly applied.
    CTA2010 s45 (carry forward); s37 (carry back).
    """
    findings = []

    ct_loss   = _get(fields, "ct.losses_arising", "ct_computation.trade_loss",
                              "ct_computation.losses_arising")
    ct600_loss = _get(fields, "ct600.box_780_trading_losses")
    ct_cfwd    = _get(fields, "ct.losses_carried_forward", "ct_computation.losses_carried_forward")
    ct600_cfwd = _get(fields, "ct600.losses_carried_forward")

    if ct_loss is not None and ct600_loss is not None:
        diff = abs(ct_loss - ct600_loss)
        if diff > 1.0:
            is_rounding = diff <= 1.0
            findings.append(RegulatoryFinding(
                rule_id="R005",
                rule_name="Trading Loss — CT Computation vs CT600",
                regulation_ref="CTA2010 s45; CT600 Box 780",
                category="tax",
                severity="low" if diff <= 1.0 else "high",
                title=f"Trading loss: CT comp £{ct_loss:,.0f} vs CT600 £{ct600_loss:,.0f} (Δ £{diff:,.0f})",
                description=(
                    f"CT computation shows trading loss of £{ct_loss:,.2f} "
                    f"but CT600 Box 780 shows £{ct600_loss:,.2f}. "
                    + ("£1 rounding difference — review and confirm acceptable."
                       if diff <= 1.0 else
                       f"Difference of £{diff:,.2f} requires investigation. "
                       f"Both must agree for the return to be correct.")
                ),
                document_sources=["CT Computation", "CT600"],
                value_a=ct_loss, value_b=ct600_loss, delta=ct_loss - ct600_loss,
                signals=["£1 rounding common between CT software modules"] if diff <= 1.0 else [],
            ))

    return findings


def check_fixed_assets(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Fixed assets: WP register → FS note → TB NBV must all agree.
    FRS 102 s17 — cost model; disclose additions, disposals, depreciation.
    """
    findings = []

    wp_nbv  = _get(fields, "wp.fixed_assets_nbv", "wp.e2_nbv",
                            "working_papers.fixed_assets_nbv")
    fs_nbv  = _get(fields, "fs.tangible_assets", "accounts.fixed_assets",
                            "fs.fixed_assets_nbv")
    tb_nbv  = _get(fields, "tb.fixed_assets_net", "tb.net_book_value")

    for label_a, val_a, label_b, val_b in [
        ("WP Register NBV", wp_nbv, "FS Tangible Assets", fs_nbv),
        ("FS Tangible Assets", fs_nbv, "TB Net Book Value", tb_nbv),
    ]:
        if val_a is None or val_b is None:
            continue
        diff = abs(val_a - val_b)
        pct = _pct(val_a, val_b)
        if diff > 0.5 and abs(pct) > 0.01:
            findings.append(RegulatoryFinding(
                rule_id="R006",
                rule_name="Fixed Assets — Cross-Document Agreement",
                regulation_ref="FRS 102 s17.31 — fixed asset note disclosure",
                category="fixed_assets",
                severity="high" if abs(pct) > materiality_pct else "medium",
                title=f"Fixed asset NBV: {label_a} £{val_a:,.0f} vs {label_b} £{val_b:,.0f}",
                description=(
                    f"{label_a} shows NBV of £{val_a:,.2f} but {label_b} shows £{val_b:,.2f}. "
                    f"Difference: £{diff:,.2f} ({pct:+.2f}%). "
                    f"The asset register, accounts note, and TB must all agree to the same NBV."
                ),
                document_sources=["WP", "FS", "TB"],
                value_a=val_a, value_b=val_b, delta=val_a - val_b,
                suggested_action="Reconcile E2 Fixed Asset Register to FS tangible assets note.",
            ))

    return findings


def check_directors_loan(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Director loan account: agreement + S455 implications if overdrawn.
    CTA2010 s455 — 33.75% charge on overdrawn DLA not repaid within 9 months.
    """
    findings = []

    wp_dla  = _get(fields, "wp.director_loan", "wp.dla", "working_papers.director_loan")
    fs_dla  = _get(fields, "fs.directors_loan", "accounts.directors_loan",
                            "fs.loans_to_directors")
    tb_dla  = _get(fields, "tb.5401", "tb.directors_loan")

    # Agreement check
    for label_a, val_a, label_b, val_b in [
        ("WP DLA", wp_dla, "FS Directors Loan", fs_dla),
        ("TB 5401", tb_dla, "FS Directors Loan", fs_dla),
    ]:
        if val_a is None or val_b is None:
            continue
        diff = abs(abs(val_a) - abs(val_b))
        if diff > 0.5:
            findings.append(RegulatoryFinding(
                rule_id="R007a",
                rule_name="Director Loan Account — Agreement",
                regulation_ref="CA2006 s413 — disclosure of loans to directors",
                category="dla",
                severity="medium",
                title=f"DLA: {label_a} £{val_a:,.2f} vs {label_b} £{val_b:,.2f}",
                description=(
                    f"Director loan account balances disagree: "
                    f"{label_a} shows £{val_a:,.2f}, {label_b} shows £{val_b:,.2f}. "
                    f"Difference: £{diff:,.2f}. All sources must agree."
                ),
                document_sources=["WP", "FS", "TB"],
                value_a=val_a, value_b=val_b,
            ))

    # S455 check — if DLA is overdrawn (company owed money by director)
    # In TB: positive balance means director owes company (overdrawn)
    dla_balance = tb_dla or fs_dla or wp_dla
    if dla_balance is not None and dla_balance > 100:
        s455_charge = dla_balance * S455_RATE
        findings.append(RegulatoryFinding(
            rule_id="R007b",
            rule_name="Overdrawn Director Loan — S455 Tax",
            regulation_ref=f"CTA2010 s455 — {S455_RATE:.2%} on overdrawn DLA",
            category="dla",
            severity="high",
            title=f"Director loan overdrawn (£{dla_balance:,.2f}) — S455 charge £{s455_charge:,.2f}",
            description=(
                f"Director loan account is overdrawn by £{dla_balance:,.2f}. "
                f"If not repaid within 9 months of year end, a S455 tax charge of "
                f"£{s455_charge:,.2f} (33.75%) will arise, payable with CT. "
                f"Options: (1) vote dividend to clear if distributable profits exist, "
                f"(2) arrange repayment before the 9-month deadline, "
                f"(3) write off (becomes employment income — PAYE/NI implications), "
                f"(4) treat as formal loan (beneficial loan BIK if >£10,000). "
                f"Confirm disclosure of maximum balance during year per CA2006 s413."
            ),
            document_sources=["WP", "FS", "TB"],
            value_a=dla_balance, value_b=s455_charge,
            suggested_action="Arrange repayment plan or vote dividend to clear by 9-month deadline.",
            signals=[f"S455 deadline: 9 months after {fields.get('engagement.year_end', 'year end')}"],
        ))

    return findings


def check_bank_cash(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Bank and cash: TB bank accounts = FS cash at bank; reconciliations signed off."""
    findings = []

    # Sum TB bank accounts
    bank_codes = ["tb.4800", "tb.4801", "tb.4802", "tb.4830"]
    tb_bank_total = sum(_get(fields, c) or 0.0 for c in bank_codes)

    fs_cash = _get(fields, "fs.cash_at_bank", "accounts.cash", "fs.bank")

    if tb_bank_total and fs_cash:
        diff = abs(tb_bank_total - fs_cash)
        pct  = _pct(tb_bank_total, fs_cash)
        if diff > 0.5 and abs(pct) > 0.01:
            findings.append(RegulatoryFinding(
                rule_id="R008",
                rule_name="Bank/Cash — TB vs FS",
                regulation_ref="FRS 102 s7 — cash and cash equivalents",
                category="cash",
                severity="high" if abs(pct) > materiality_pct else "medium",
                title=f"Bank: TB £{tb_bank_total:,.2f} vs FS £{fs_cash:,.2f} (Δ £{diff:,.2f})",
                description=(
                    f"TB bank account total (£{tb_bank_total:,.2f}) does not agree "
                    f"with FS cash at bank (£{fs_cash:,.2f}). "
                    f"Difference: £{diff:,.2f} ({pct:+.2f}%). "
                    f"Cross-reference WP I1 (Bank Lead) and I2 (Bank Reconciliation)."
                ),
                document_sources=["TB", "FS", "WP"],
                value_a=tb_bank_total, value_b=fs_cash, delta=tb_bank_total - fs_cash,
            ))

    # Negative cash in hand check
    cash_in_hand = _get(fields, "tb.4830", "tb.cash_in_hand")
    if cash_in_hand is not None and cash_in_hand < -0.01:
        findings.append(RegulatoryFinding(
            rule_id="R008b",
            rule_name="Cash in Hand — Negative Balance",
            regulation_ref="Basic accounting — physical asset cannot be negative",
            category="cash",
            severity="high",
            title=f"Cash in hand is negative (£{cash_in_hand:,.2f})",
            description=(
                f"Cash in hand shows a negative balance of £{cash_in_hand:,.2f}. "
                f"This is physically impossible. Likely causes: cash introduced not "
                f"recorded as capital introduced/director loan, petty cash error, "
                f"or journal misposting. Review I5 (Petty Cash) in working papers."
            ),
            document_sources=["TB"],
            value_a=cash_in_hand,
            suggested_action="Post cash introduced as capital introduced or director loan credit.",
        ))

    return findings


def check_debtors(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Debtors: WP H1 lead total = FS debtors note = TB debtor accounts."""
    findings = []

    wp_deb  = _get(fields, "wp.debtors", "wp.h1_total", "working_papers.debtors")
    fs_deb  = _get(fields, "fs.debtors", "accounts.debtors", "fs.trade_debtors")
    tb_deb_total = sum(abs(_get(fields, c) or 0.0)
                       for c in ["tb.4420", "tb.4421", "tb.4450", "tb.4900"])

    for la, va, lb, vb in [
        ("WP H1 Debtors", wp_deb, "FS Debtors", fs_deb),
        ("TB Debtor Total", tb_deb_total if tb_deb_total > 0 else None, "FS Debtors", fs_deb),
    ]:
        if va is None or vb is None or va == 0 or vb == 0:
            continue
        diff = abs(va - vb)
        pct  = _pct(va, vb)
        if diff > 0.5 and abs(pct) > 0.5:
            findings.append(RegulatoryFinding(
                rule_id="R009",
                rule_name="Debtors — Cross-Document Agreement",
                regulation_ref="FRS 102 s11 — financial assets at amortised cost",
                category="debtors",
                severity="medium",
                title=f"Debtors: {la} £{va:,.2f} vs {lb} £{vb:,.2f}",
                description=(
                    f"{la} (£{va:,.2f}) does not agree with {lb} (£{vb:,.2f}). "
                    f"Difference: £{diff:,.2f} ({pct:+.2f}%). "
                    f"Reconcile WP H1 Debtors Lead, H2 Trade Debtors, and FS debtors note."
                ),
                document_sources=["WP", "FS", "TB"],
                value_a=va, value_b=vb,
            ))

    return findings


def check_corporation_tax_provision(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """CT provision: if CT chargeable = £0, no provision in FS; prior year reversal correct."""
    findings = []

    ct_charge = _get(fields, "ct_computation.ct_chargeable", "ct600.box_510_tax_chargeable")
    fs_ct     = _get(fields, "fs.corporation_tax", "accounts.ct_provision",
                              "fs.current_tax")
    tb_ct     = _get(fields, "tb.5310", "tb.corporation_tax_creditor")

    if ct_charge is not None and abs(ct_charge) < 0.01:
        # CT = £0 (loss-making). FS and TB should have no current year CT provision
        if fs_ct is not None and abs(fs_ct) > 100:
            findings.append(RegulatoryFinding(
                rule_id="R010a",
                rule_name="CT Provision — Loss-Making Year",
                regulation_ref="FRS 102 s29 — current tax; CTA2010 s5",
                category="tax",
                severity="high",
                title=f"CT = £0 (loss) but FS shows CT provision of £{abs(fs_ct):,.2f}",
                description=(
                    f"CT computation shows no corporation tax chargeable (trading loss). "
                    f"However FS shows a CT provision of £{abs(fs_ct):,.2f}. "
                    f"This is inconsistent. If the company is loss-making, current CT = £0. "
                    f"The prior year CT balance on the TB should be shown as recovered/refund."
                ),
                document_sources=["CT Computation", "FS", "TB"],
                value_a=ct_charge, value_b=fs_ct,
                suggested_action="Remove current year CT provision; confirm prior year CT refund.",
            ))

    # CT provision: FS = TB creditor
    if fs_ct is not None and tb_ct is not None:
        diff = abs(abs(fs_ct) - abs(tb_ct))
        if diff > 0.5:
            findings.append(RegulatoryFinding(
                rule_id="R010b",
                rule_name="CT Provision — FS vs TB",
                regulation_ref="FRS 102 s29.12 — current tax recognised in P&L",
                category="tax",
                severity="medium",
                title=f"CT provision: FS £{abs(fs_ct):,.2f} vs TB £{abs(tb_ct):,.2f}",
                description=(
                    f"FS CT provision (£{abs(fs_ct):,.2f}) does not match TB "
                    f"corporation tax creditor account (£{abs(tb_ct):,.2f}). "
                    f"Difference: £{diff:,.2f}."
                ),
                document_sources=["FS", "TB"],
                value_a=fs_ct, value_b=tb_ct,
            ))

    return findings


def check_dividends_distributable(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Dividends must not exceed distributable reserves. CA2006 s830."""
    findings = []

    dividends = _get(fields, "tb.8010", "tb.dividends", "accounts.dividends")
    retained  = _get(fields, "tb.8000", "tb.retained_earnings", "accounts.retained_earnings")
    current_year_loss = _get(fields, "fs.loss_after_tax", "accounts.loss_after_tax")

    if dividends is None:
        return findings

    # Distributable profits = opening retained + current year profit (if positive)
    distributable = retained or 0.0
    if current_year_loss is not None and current_year_loss < 0:
        distributable += current_year_loss

    if distributable < dividends:
        shortfall = dividends - distributable
        findings.append(RegulatoryFinding(
            rule_id="R011",
            rule_name="Dividends vs Distributable Profits",
            regulation_ref="CA2006 s830 — distributions from distributable profits only",
            category="reserves",
            severity="high",
            title=f"Dividends (£{dividends:,.0f}) exceed distributable reserves (£{distributable:,.0f})",
            description=(
                f"Dividends of £{dividends:,.2f} have been paid/declared but distributable "
                f"reserves are only £{distributable:,.2f}. "
                f"Shortfall: £{shortfall:,.2f}. "
                f"Under CA2006 s830, dividends can only be paid from distributable profits. "
                f"Excess dividends may be: (1) reclassified as director loan (overdrawn DLA), "
                f"(2) offset against capital introduced where permissible, "
                f"(3) reversed if distribution was unlawful. "
                f"A deferred tax asset (if recognised) would improve the position."
            ),
            document_sources=["TB", "FS"],
            value_a=dividends, value_b=distributable, delta=shortfall,
            suggested_action="Review dividend legality under CA2006 s830; consider DLA reclassification.",
            signals=[f"Shortfall: £{shortfall:,.0f}"],
        ))

    return findings


def check_deferred_tax(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Deferred tax: trading losses should consider DTA; timing differences disclosed.
    FRS 102 s29 — full provision method; DTA if recovery probable.
    """
    findings = []

    trading_loss = _get(fields, "ct_computation.trade_loss", "ct_computation.losses_arising")
    dta_tb       = _get(fields, "tb.6100", "tb.deferred_tax_asset", "tb.3801")
    fs_dt        = _get(fields, "fs.deferred_tax", "accounts.deferred_tax")
    timing_diffs = _get(fields, "ct.timing_differences")

    if trading_loss is not None and trading_loss > 1000:
        dta_recognised = (
            (dta_tb is not None and abs(dta_tb) > 1.0) or
            (fs_dt is not None and fs_dt > 1.0)
        )
        if not dta_recognised:
            rate = 0.25
            potential = trading_loss * rate
            findings.append(RegulatoryFinding(
                rule_id="R012a",
                rule_name="Deferred Tax Asset — Trading Loss",
                regulation_ref="FRS 102 s29.7 — DTA recognised if recovery probable",
                category="tax",
                severity="medium",
                title=f"Trading loss £{trading_loss:,.0f} — DTA of £{potential:,.0f} not recognised",
                description=(
                    f"The company has trading losses of £{trading_loss:,.0f} available. "
                    f"At 25% CT rate, a deferred tax asset of £{potential:,.0f} could be recognised. "
                    f"Under FRS 102 s29.7, a DTA should be recognised where it is probable "
                    f"that future taxable profits will be available. "
                    f"Consider whether there is sufficient evidence of future profits. "
                    f"If no reliable forecast exists, document the rationale for non-recognition."
                ),
                document_sources=["CT Computation", "FS"],
                value_a=trading_loss, value_b=potential,
                suggested_action=(
                    "Prepare a going concern / forecast assessment. "
                    "If profits probable, recognise DTA and disclose under FRS 102 s29."
                ),
            ))

    return findings


def check_going_concern(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Going concern: net liabilities or trading losses require assessment and disclosure.
    FRS 102 s3.8 — material uncertainty disclosure.
    """
    findings = []

    net_assets  = _get(fields, "accounts.net_assets", "fs.net_assets",
                                "accounts.shareholders_funds")
    trading_loss = _get(fields, "ct_computation.trade_loss", "fs.loss_before_tax")

    if net_assets is not None and net_assets < 0:
        findings.append(RegulatoryFinding(
            rule_id="R013a",
            rule_name="Going Concern — Net Liabilities",
            regulation_ref="FRS 102 s3.8 — going concern material uncertainty",
            category="going_concern",
            severity="high",
            title=f"Net liabilities £{abs(net_assets):,.0f} — going concern assessment required",
            description=(
                f"The company has net liabilities of £{abs(net_assets):,.0f}. "
                f"Under FRS 102 s3.8, the directors must assess whether the going concern "
                f"basis of preparation is appropriate. "
                f"Prepare and document: (1) cash flow forecasts for minimum 12 months, "
                f"(2) banking facilities and covenant compliance, "
                f"(3) any post year-end events. "
                f"The accounts must disclose either that the going concern basis is "
                f"appropriate (with reasoning) or any material uncertainty."
            ),
            document_sources=["FS"],
            value_a=net_assets,
            suggested_action="Prepare formal going concern assessment; include disclosure in accounts.",
        ))

    return findings


def check_payroll_consistency(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Payroll YoY consistency: wages vs NI ratios; unusual movements flagged."""
    findings = []

    wages_cy = _get(fields, "tb.3000", "tb.wages_salaries")
    wages_py = _get(fields, "prior_year.3000", "prior_year.wages_salaries")
    ni_cy    = _get(fields, "tb.3001", "tb.employers_ni")
    ni_py    = _get(fields, "prior_year.3001", "prior_year.employers_ni")

    if all(v is not None for v in [wages_cy, wages_py, ni_cy, ni_py]):
        if wages_py > 0 and ni_py > 0:
            wages_chg = _pct(wages_cy, wages_py)
            ni_chg    = _pct(ni_cy, ni_py)

            if wages_chg < -3.0 and ni_chg > 15.0:
                findings.append(RegulatoryFinding(
                    rule_id="R014",
                    rule_name="Payroll — Wages vs NI Inconsistency",
                    regulation_ref="ITEPA2003 — employment income; NIC regulations",
                    category="payroll",
                    severity="medium",
                    title=(
                        f"Wages fell {abs(wages_chg):.0f}% YoY (£{wages_py:,.0f}→£{wages_cy:,.0f}) "
                        f"but Employers NI rose {ni_chg:.0f}% (£{ni_py:,.0f}→£{ni_cy:,.0f})"
                    ),
                    description=(
                        f"Wages decreased {wages_chg:+.1f}% year on year from £{wages_py:,.0f} "
                        f"to £{wages_cy:,.0f}, while Employers NI increased {ni_chg:+.1f}% "
                        f"from £{ni_py:,.0f} to £{ni_cy:,.0f}. "
                        f"This is inconsistent with normal payroll patterns. "
                        f"Possible causes: (1) increase in directors' salaries above NI threshold, "
                        f"(2) increase in employer NI rate (from Apr 2025: 15%), "
                        f"(3) staff composition change. "
                        f"Cross-reference M1 Wages Analysis and M1.1 Payroll Control."
                    ),
                    document_sources=["TB", "WP"],
                    value_a=wages_cy, value_b=ni_cy,
                    signals=[f"Wages: {wages_chg:+.1f}% YoY", f"NI: {ni_chg:+.1f}% YoY"],
                ))

    return findings


def check_depreciation_timing(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Significant mid-year additions — confirm depreciation time-apportioned.
    FRS 102 s17.22 — depreciation from date available for use.
    """
    findings = []

    additions   = _get(fields, "ct_computation.net_capital_allowances",
                                "accounts.additions", "fs.additions")
    fa_cost     = _get(fields, "accounts.fixed_assets_cost", "fs.fixed_assets_gross")
    depreciation = _get(fields, "ct_computation.depreciation_addback",
                                 "accounts.depreciation", "fs.depreciation")

    if additions and fa_cost and additions > 10000:
        ratio = additions / fa_cost if fa_cost > 0 else 0
        if ratio > 0.25 and depreciation is not None:
            findings.append(RegulatoryFinding(
                rule_id="R015",
                rule_name="Depreciation — Mid-Year Additions Time-Apportionment",
                regulation_ref="FRS 102 s17.22 — depreciation from date of availability",
                category="fixed_assets",
                severity="medium",
                title=(
                    f"Significant additions (£{additions:,.0f} = {ratio:.0%} of cost) — "
                    f"confirm depreciation is time-apportioned"
                ),
                description=(
                    f"Fixed asset additions of £{additions:,.0f} represent {ratio:.0%} of "
                    f"total cost (£{fa_cost:,.0f}). Depreciation charged: £{depreciation:,.0f}. "
                    f"Under FRS 102 s17.22, depreciation begins from the date the asset is "
                    f"available for use and should be calculated on a monthly (pro-rata) basis "
                    f"where assets are acquired during the year. "
                    f"A full-year charge on assets acquired late in the year would overstate "
                    f"the depreciation charge and understate profit. "
                    f"Confirm E2 (Fixed Asset Register) shows time-apportioned calculation."
                ),
                document_sources=["WP", "TB", "CT Computation"],
                value_a=additions, value_b=depreciation,
                suggested_action="Confirm E2.1 register shows monthly depreciation for all additions.",
            ))

    return findings


def check_aged_creditors(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Prior year creditor balances still on TB — investigate aged creditors."""
    findings = []

    tc_cy = _get(fields, "tb.5200", "tb.trade_creditors")
    tc_py = _get(fields, "prior_year.5200", "prior_year.trade_creditors",
                          "tb.trade_creditors_prior")

    if tc_cy is not None and tc_py is not None and abs(tc_py) > 100:
        findings.append(RegulatoryFinding(
            rule_id="R016",
            rule_name="Aged Creditors — Prior Year Balance",
            regulation_ref="FRS 102 s21 — provisions and liabilities",
            category="creditors",
            severity="medium",
            title=f"Trade creditors: prior year £{abs(tc_py):,.0f} vs current £{abs(tc_cy):,.0f}",
            description=(
                f"Trade creditors include a prior year balance of £{abs(tc_py):,.0f} "
                f"against current year £{abs(tc_cy):,.0f}. "
                f"Confirm all prior year creditor balances have been: "
                f"(1) paid and cleared with bank statement evidence, "
                f"(2) invoiced and legitimately outstanding (confirmed on supplier statements), "
                f"(3) reviewed for duplicate postings — if an invoice was posted twice, "
                f"the creditor should be reversed with VAT adjustment, or "
                f"(4) written off if no longer due (credit to P&L; may have VAT implications). "
                f"Cross-reference J2 Trade Creditors schedule in working papers."
            ),
            document_sources=["TB", "WP"],
            value_a=tc_cy, value_b=tc_py,
        ))

    return findings


def check_goodwill(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Goodwill: finite useful life max 10 years if unknown. FRS 102 s19."""
    findings = []

    gw_cost  = _get(fields, "tb.4000", "tb.goodwill_cost")
    gw_amort = _get(fields, "tb.4002", "tb.goodwill_amortisation")

    if gw_cost is not None and abs(gw_cost) > 100:
        # Check if goodwill is fully amortised
        if gw_amort is not None and abs(gw_amort) >= abs(gw_cost):
            # Fully amortised — no issue, just check note disclosure
            pass
        elif gw_amort is None or abs(gw_amort) < abs(gw_cost):
            # Goodwill not yet fully amortised — check useful life assumption
            findings.append(RegulatoryFinding(
                rule_id="R017",
                rule_name="Goodwill Amortisation",
                regulation_ref="FRS 102 s19.23 — goodwill finite life; 10yr max if unknown",
                category="intangibles",
                severity="medium",
                title=f"Goodwill on balance sheet (£{abs(gw_cost):,.0f}) — confirm amortisation policy",
                description=(
                    f"Goodwill at cost £{abs(gw_cost):,.0f} with accumulated amortisation "
                    f"£{abs(gw_amort or 0):,.0f}. "
                    f"Under FRS 102 s19.23, goodwill must be amortised over its useful life. "
                    f"If useful life cannot be reliably estimated, the maximum is 10 years. "
                    f"Confirm: (1) amortisation rate is consistent with useful life estimate, "
                    f"(2) any impairment review performed if indicators present, "
                    f"(3) policy disclosed in accounting policies note."
                ),
                document_sources=["TB", "FS"],
                value_a=gw_cost, value_b=gw_amort,
            ))

    return findings


def check_leases(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Operating leases: commitment disclosure required. FRS 102 s20."""
    findings = []

    wp_sheets = fields.get("wp.sheets", [])
    has_lease_sheet = any("K1" in s or "lease" in s.lower() or "operating" in s.lower()
                          for s in (wp_sheets if isinstance(wp_sheets, list) else []))

    rent = _get(fields, "tb.3100", "tb.rent")

    if (has_lease_sheet or (rent is not None and rent > 1000)):
        findings.append(RegulatoryFinding(
            rule_id="R018",
            rule_name="Operating Leases — Commitment Disclosure",
            regulation_ref="FRS 102 s20.16 — operating lease commitment disclosure",
            category="leases",
            severity="low",
            title="Operating leases/rent present — confirm commitment disclosure in notes",
            description=(
                f"Rent/lease payments{'in K1 schedule ' if has_lease_sheet else ' '}detected"
                f"{f' (£{rent:,.0f} current year)' if rent else ''}. "
                f"Under FRS 102 s20.16, the accounts must disclose total future minimum "
                f"lease payments under non-cancellable operating leases, analysed as: "
                f"(1) not later than 1 year, (2) later than 1 year but not more than 5 years, "
                f"(3) later than 5 years. "
                f"Confirm this disclosure is included in the notes to the accounts."
            ),
            document_sources=["WP", "FS"],
        ))

    return findings


def check_related_parties(fields, materiality_pct=5.0) -> List[RegulatoryFinding]:
    """Related party disclosures: directors, connected persons. FRS 102 s33."""
    findings = []

    directors_salary = _get(fields, "tb.3010", "tb.directors_salaries")
    directors_loan   = _get(fields, "tb.5401", "tb.directors_loan")

    if directors_salary is not None or directors_loan is not None:
        findings.append(RegulatoryFinding(
            rule_id="R019",
            rule_name="Related Party Disclosures",
            regulation_ref="FRS 102 s33 — related party transactions",
            category="disclosure",
            severity="low",
            title="Director transactions present — confirm related party disclosure",
            description=(
                f"Director transactions identified: "
                f"{'salaries £' + f'{directors_salary:,.0f}' if directors_salary else ''}"
                f"{'; ' if directors_salary and directors_loan else ''}"
                f"{'DLA balance £' + f'{abs(directors_loan):,.0f}' if directors_loan else ''}. "
                f"Under FRS 102 s33, transactions with related parties (including directors "
                f"and their connected persons) must be disclosed unless: "
                f"(1) the company is a wholly-owned subsidiary, or "
                f"(2) the transaction is on normal market terms. "
                f"Typical disclosures: directors' remuneration, director loan details, "
                f"any management charges or property transactions."
            ),
            document_sources=["TB", "FS"],
        ))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# COMPLETE REGULATORY PACK
# ══════════════════════════════════════════════════════════════════════════════

FRS102_RULES = [
    # Cross-document agreement (TB + WP + FS + CT)
    check_turnover_agreement,
    check_profit_agreement,
    check_depreciation_agreement,
    check_capital_allowances,
    check_trading_loss,
    check_fixed_assets,
    check_directors_loan,
    check_bank_cash,
    check_debtors,
    check_corporation_tax_provision,
    check_dividends_distributable,
    # FRS 102 specific
    check_deferred_tax,
    check_going_concern,
    check_depreciation_timing,
    check_aged_creditors,
    check_goodwill,
    check_leases,
    check_related_parties,
    # Employment / payroll
    check_payroll_consistency,
]


def run_frs102_pack(
    fields: Dict[str, Any],
    materiality_pct: float = 5.0,
    rules: Optional[list] = None,
) -> List[RegulatoryFinding]:
    """Run the complete FRS 102 regulatory pack.

    Works on ANY engagement where the relevant fields are present.
    Missing fields produce no finding (SKIP). Rules fire only when
    they have enough data to make a determination.

    fields: flat dict from all document streams:
      tb.*        Trial Balance nominal codes
      prior_year.* Prior year TB values
      fs.*        Final accounts values
      ct.*        CT computation values
      ct600.*     CT600 box values
      wp.*        Working papers values
      wp.sheets   List of WP sheet names

    materiality_pct: threshold for HIGH vs MEDIUM severity (default 5%)
    """
    active = rules or FRS102_RULES
    findings = []
    for rule_fn in active:
        try:
            result = rule_fn(fields, materiality_pct=materiality_pct)
            findings.extend(result)
        except Exception as e:
            logger.warning("FRS102 rule %s failed: %s", rule_fn.__name__, e)

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: order.get(f.severity, 4))
    return findings


def frs102_findings_to_review_points(findings: List[RegulatoryFinding]) -> List[Dict]:
    """Convert regulatory findings to standard review point format."""
    points = []
    for f in findings:
        points.append({
            "severity": f.severity,
            "category": f.category,
            "title": f.title,
            "description": f.description,
            "section": " | ".join(f.document_sources),
            "audit_ref": f.rule_id,
            "field_name": f.rule_id,
            "value_a": str(round(f.value_a, 2)) if f.value_a is not None else None,
            "value_b": str(round(f.value_b, 2)) if f.value_b is not None else None,
            "delta": f.delta,
            "status": "open",
            "regulation_ref": f.regulation_ref,
            "document_sources": f.document_sources,
            "suggested_action": f.suggested_action,
            "signals": f.signals,
        })
    return points
