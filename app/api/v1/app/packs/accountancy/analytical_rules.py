from __future__ import annotations
"""
Oversight ASGL — Accountancy Vertical: Analytical Review Rules
==============================================================
Rules derived from real AM review workflow (Katie/Paul Burns review points).

These rules detect the findings a senior accountant would raise on first review:

  RP1: Dividends > Retained Earnings  -> Negative P&L / Overdrawn DLA risk
  RP2: Wages down YoY + NI up YoY     -> Payroll inconsistency  
  RP3: Cash in hand negative           -> Impossible balance
  RP4: Trading loss + no DTA           -> FRS 102 s29 deferred tax consideration
  RP5: Large mid-year CapEx + full-year depreciation -> Review monthly basis
  RP6: Prior year creditor remaining   -> Aged creditor investigation
  RP7: Lead schedule not complete      -> WP completion check

Each rule takes a flat field dict extracted from TB/WP/NL/FS and returns
a list of AnalyticalFinding objects. Findings become review points in the
engagement workflow with Correct/Waive/Escalate actions.

USAGE:
    from app.packs.accountancy.analytical_rules import run_analytical_review
    findings = run_analytical_review(fields, materiality_pct=5.0)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class AnalyticalFinding:
    rule_id: str
    rule_name: str
    category: str          # payroll | reserves | tax | depreciation | creditors | cash | wp
    severity: str          # high | medium | low
    title: str
    description: str
    source_fields: List[str]
    value_a: Optional[float] = None
    value_b: Optional[float] = None
    delta: Optional[float] = None
    regulation_ref: Optional[str] = None  # FRS 102 s29, CA2006, etc
    signals: List[str] = field(default_factory=list)


def _get(fields: Dict[str, Any], *keys: str) -> Optional[float]:
    """Try multiple key names, return first float found."""
    for key in keys:
        v = fields.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _pct_change(current: float, prior: float) -> Optional[float]:
    if prior is None or abs(prior) < 0.01:
        return None
    return (current - prior) / abs(prior) * 100.0


# ══════════════════════════════════════════════════════════════════════════════
# RP1: Dividends vs Retained Earnings — Overdrawn DLA / Negative P&L Risk
# ══════════════════════════════════════════════════════════════════════════════

def rule_dividends_vs_reserves(fields: Dict[str, Any], materiality_pct: float = 5.0) -> List[AnalyticalFinding]:
    """Detect when dividends exceed retained earnings creating negative reserves.
    
    Katie RP1: "Too many dividends have been voted, The P&L Account is Negative.
    Can some go against the Capital Introduced and then some will have to be
    Overdrawn DLA..."
    """
    findings = []

    dividends = _get(fields,
        "tb.dividends", "tb.final_dividends", "tb.8010",
        "accounts.dividends", "working_papers.dividends",
    )
    retained = _get(fields,
        "tb.retained_earnings", "tb.profit_loss_account", "tb.8000",
        "accounts.profit_loss_account", "accounts.retained_earnings",
    )
    profit_loss = _get(fields,
        "tb.profit_for_period", "accounts.profit_after_tax",
        "accounts.loss_after_tax", "tb.profit_loss_period",
    )

    if dividends is not None and retained is not None:
        # Reserves after dividends
        net_reserves = retained - dividends
        if net_reserves < 0:
            findings.append(AnalyticalFinding(
                rule_id="A001",
                rule_name="Dividends vs Retained Earnings",
                category="reserves",
                severity="high",
                title=f"Dividends exceed retained earnings — negative reserves (£{net_reserves:,.0f})",
                description=(
                    f"Dividends of £{dividends:,.2f} exceed retained earnings of £{retained:,.2f}, "
                    f"resulting in negative reserves of £{net_reserves:,.0f}. "
                    f"Consider: (1) offset against capital introduced, "
                    f"(2) reclassify excess as overdrawn director loan account. "
                    f"Review whether dividends were lawfully declared under CA2006 s830."
                ),
                source_fields=["dividends", "retained_earnings"],
                value_a=dividends,
                value_b=retained,
                delta=net_reserves,
                regulation_ref="CA2006 s830 — distributions from distributable profits only",
                signals=[f"Net reserves: £{net_reserves:,.0f}"],
            ))

    # Also flag if P&L balance itself is negative
    if retained is not None and retained < 0:
        findings.append(AnalyticalFinding(
            rule_id="A001b",
            rule_name="Negative P&L Account Balance",
            category="reserves",
            severity="high",
            title=f"P&L account balance is negative (£{retained:,.0f})",
            description=(
                f"The retained earnings / P&L account shows a negative balance of £{retained:,.0f}. "
                f"This may indicate accumulated losses or excess dividends. "
                f"Review DLA balances and consider whether a deferred tax asset would improve position."
            ),
            source_fields=["retained_earnings"],
            value_a=retained,
            regulation_ref="FRS 102 s29 — deferred tax asset on losses if recovery probable",
        ))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# RP2: Wages/Payroll YoY Inconsistency
# ══════════════════════════════════════════════════════════════════════════════

def rule_payroll_consistency(fields: Dict[str, Any], materiality_pct: float = 5.0) -> List[AnalyticalFinding]:
    """Detect wages/NI inconsistencies year-on-year.
    
    Katie RP2: "Wages down but Employers NI doubled?..."
    """
    findings = []

    wages_cy = _get(fields, "tb.wages_salaries", "tb.3000", "accounts.wages")
    wages_py = _get(fields, "tb.wages_salaries_prior", "prior_year.wages_salaries")
    ni_cy = _get(fields, "tb.employers_ni", "tb.3001", "accounts.employers_ni")
    ni_py = _get(fields, "tb.employers_ni_prior", "prior_year.employers_ni")

    if wages_cy and wages_py and ni_cy and ni_py:
        wages_pct = _pct_change(wages_cy, wages_py)
        ni_pct = _pct_change(ni_cy, ni_py)

        if wages_pct is not None and ni_pct is not None:
            # Flag if wages moved significantly in one direction and NI in the other
            if wages_pct < -5.0 and ni_pct > 20.0:
                findings.append(AnalyticalFinding(
                    rule_id="A002",
                    rule_name="Wages vs Employers NI Inconsistency",
                    category="payroll",
                    severity="medium",
                    title=f"Wages decreased {abs(wages_pct):.0f}% YoY but Employers NI increased {ni_pct:.0f}% YoY",
                    description=(
                        f"Wages fell from £{wages_py:,.0f} to £{wages_cy:,.0f} ({wages_pct:+.1f}%) "
                        f"while Employers NI rose from £{ni_py:,.0f} to £{ni_cy:,.0f} ({ni_pct:+.1f}%). "
                        f"This is inconsistent with normal payroll patterns. "
                        f"Possible explanations: increase in directors' salaries above NI threshold, "
                        f"rising employer NI rates, or payroll classification changes. "
                        f"Cross-reference M1 Wages Analysis and payroll records."
                    ),
                    source_fields=["wages_salaries", "employers_ni"],
                    value_a=wages_cy,
                    value_b=ni_cy,
                    signals=[f"Wages: {wages_pct:+.1f}% YoY, NI: {ni_pct:+.1f}% YoY"],
                ))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# RP3: Cash in Hand Negative Balance
# ══════════════════════════════════════════════════════════════════════════════

def rule_cash_in_hand_negative(fields: Dict[str, Any], materiality_pct: float = 5.0) -> List[AnalyticalFinding]:
    """Detect negative cash in hand — impossible balance.
    
    Katie RP3: "the cash in hand was on a negative but should have shown cash introduced"
    """
    findings = []

    cash_in_hand = _get(fields,
        "tb.cash_in_hand", "tb.4830", "accounts.cash_in_hand",
        "working_papers.cash_in_hand",
    )

    if cash_in_hand is not None and cash_in_hand < -0.01:
        findings.append(AnalyticalFinding(
            rule_id="A003",
            rule_name="Cash in Hand — Negative Balance",
            category="cash",
            severity="high",
            title=f"Cash in hand is negative (£{cash_in_hand:,.2f}) — impossible balance",
            description=(
                f"Cash in hand shows a negative balance of £{cash_in_hand:,.2f}. "
                f"This is not possible physically. Likely causes: "
                f"(1) cash introduced not recorded — should be posted as capital introduced, "
                f"(2) petty cash payments incorrectly coded, "
                f"(3) journal error. "
                f"Cross-reference I5 Petty Cash and Journal entries. "
                f"Post any cash introduced as capital introduced or director loan."
            ),
            source_fields=["cash_in_hand"],
            value_a=cash_in_hand,
            regulation_ref="Basic accounting — asset cannot be negative",
            signals=["Negative physical asset is impossible"],
        ))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# RP4: Trading Loss — Deferred Tax Asset Not Recognised
# ══════════════════════════════════════════════════════════════════════════════

def rule_deferred_tax_asset(fields: Dict[str, Any], materiality_pct: float = 5.0) -> List[AnalyticalFinding]:
    """Flag DTA consideration when company has trading losses.
    
    Katie RP4: "As Future Taxable Profits is 'Probable' there is a case for
    recognising the Deferred Tax Asset..."
    """
    findings = []

    ct_chargeable = _get(fields, "ct_computation.ct_chargeable", "ct600.box_510_tax_chargeable")
    trading_loss = _get(fields, "ct_computation.trade_loss", "ct_computation.losses_arising")
    deferred_tax = _get(fields,
        "tb.deferred_tax", "tb.6100", "accounts.deferred_tax",
        "tb.3801",
    )

    # DTA consideration: trading loss exists and DTA not recognised (nil or small)
    if trading_loss is not None and trading_loss > 1000:
        dta_recognised = deferred_tax is not None and abs(deferred_tax) > 1.0
        if not dta_recognised:
            tax_rate = 0.25
            potential_dta = trading_loss * tax_rate
            findings.append(AnalyticalFinding(
                rule_id="A004",
                rule_name="Deferred Tax Asset — Recognition Consideration",
                category="tax",
                severity="medium",
                title=f"Trading loss of £{trading_loss:,.0f} — consider DTA recognition (£{potential_dta:,.0f} at 25%)",
                description=(
                    f"The company has a trading loss of £{trading_loss:,.0f} available. "
                    f"Under FRS 102 s29, a deferred tax asset should be recognised where "
                    f"recovery against future taxable profits is probable. "
                    f"At 25% corporation tax rate, the potential DTA is £{potential_dta:,.0f}. "
                    f"Recognising the DTA would: (1) reduce the current year loss, "
                    f"(2) improve the P&L balance, (3) potentially make dividend position viable. "
                    f"Consider whether there is sufficient evidence of future taxable profits. "
                    f"If no reliable forecasts exist, waive this point."
                ),
                source_fields=["ct_computation.trade_loss", "deferred_tax"],
                value_a=trading_loss,
                value_b=potential_dta,
                regulation_ref="FRS 102 s29.7 — deferred tax asset on losses",
                signals=[f"Potential DTA: £{potential_dta:,.0f}"],
            ))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# RP5: Depreciation — Mid-Year Additions vs Full-Year Charge
# ══════════════════════════════════════════════════════════════════════════════

def rule_depreciation_timing(fields: Dict[str, Any], materiality_pct: float = 5.0) -> List[AnalyticalFinding]:
    """Detect where large CapEx additions may have full-year depreciation applied.
    
    Katie RP5: "there was a lot charged in the year but they were bought throughout
    the year, if you calculate Depreciation on a monthly basis instead of a full
    year then that should help."
    """
    findings = []

    additions = _get(fields,
        "tb.fixed_asset_additions", "accounts.additions",
        "tb.4101", "tb.4121", "tb.4141", "tb.4151",
    )
    # Sum all addition TB codes if individual ones
    if additions is None:
        total = 0.0
        found_any = False
        for code in ("tb.4101", "tb.4121", "tb.4141", "tb.4151"):
            v = _get(fields, code)
            if v is not None:
                total += abs(v)
                found_any = True
        if found_any:
            additions = total

    depreciation = _get(fields,
        "tb.depreciation", "accounts.depreciation",
        "tb.3401", "tb.3403", "tb.3404",
    )
    if depreciation is None:
        dep_total = 0.0
        for code in ("tb.3401", "tb.3403", "tb.3404"):
            v = _get(fields, code)
            if v is not None:
                dep_total += abs(v)
        if dep_total > 0:
            depreciation = dep_total

    if additions is not None and depreciation is not None and additions > 10000:
        # If depreciation charge is more than 90% of what full-year would be on additions alone
        # and additions are significant, flag for monthly basis review
        # Simple check: additions > 50% of total fixed assets cost = significant mid-year CapEx
        fa_cost = _get(fields, "accounts.fixed_assets_cost", "tb.fixed_assets_gross")
        if fa_cost and additions / fa_cost > 0.3:
            # Significant additions - depreciation should be time-apportioned
            findings.append(AnalyticalFinding(
                rule_id="A005",
                rule_name="Depreciation — Significant Mid-Year Additions",
                category="depreciation",
                severity="medium",
                title=f"Significant CapEx additions (£{additions:,.0f}) — confirm depreciation time-apportioned",
                description=(
                    f"Fixed asset additions of £{additions:,.0f} represent a significant "
                    f"proportion of total assets. Depreciation charged this year is £{depreciation:,.0f}. "
                    f"Where assets were acquired throughout the year, depreciation should be "
                    f"calculated on a monthly (time-apportioned) basis from the date of acquisition, "
                    f"not a full-year charge. This is particularly important where assets were "
                    f"acquired late in the year. Confirm that depreciation has been calculated "
                    f"on a monthly basis for all additions. This may materially reduce the "
                    f"depreciation charge and improve the profit position."
                ),
                source_fields=["fixed_asset_additions", "depreciation"],
                value_a=additions,
                value_b=depreciation,
                regulation_ref="FRS 102 s17 — depreciation from date asset available for use",
                signals=[f"Additions/Cost ratio: {additions/fa_cost:.0%}"],
            ))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# RP6: Prior Year Creditor Balance Remaining
# ══════════════════════════════════════════════════════════════════════════════

def rule_aged_creditors(fields: Dict[str, Any], materiality_pct: float = 5.0) -> List[AnalyticalFinding]:
    """Detect prior year creditor balances still present in current year.
    
    Katie RP6: "We are accruing for British Gas which is fine but why is there
    still an amount in Creditors in the older column?"
    """
    findings = []

    trade_creditors_cy = _get(fields,
        "tb.trade_creditors", "tb.5200", "accounts.trade_creditors",
    )
    trade_creditors_py = _get(fields,
        "prior_year.trade_creditors", "tb.trade_creditors_prior",
    )

    if trade_creditors_cy is not None and trade_creditors_py is not None:
        # If prior year balance is similar to current (not reducing) flag
        if abs(trade_creditors_py) > 100 and abs(trade_creditors_cy) > abs(trade_creditors_py) * 0.5:
            findings.append(AnalyticalFinding(
                rule_id="A006",
                rule_name="Aged Creditors — Prior Year Balance",
                category="creditors",
                severity="medium",
                title=f"Prior year creditor balance £{abs(trade_creditors_py):,.0f} — confirm cleared or written off",
                description=(
                    f"Trade creditors show £{abs(trade_creditors_cy):,.0f} current year "
                    f"vs £{abs(trade_creditors_py):,.0f} prior year. "
                    f"Confirm that all prior year creditor balances have been: "
                    f"(1) paid and cleared, "
                    f"(2) accrued correctly with supporting invoices on statements, or "
                    f"(3) written off if irrecoverable. "
                    f"Investigate any individual balances from the prior year still appearing. "
                    f"Check for duplicate invoice postings — an invoice accounted for twice "
                    f"would create a creditor without a corresponding liability."
                ),
                source_fields=["trade_creditors"],
                value_a=trade_creditors_cy,
                value_b=trade_creditors_py,
            ))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# RP7: Lead Schedule Completion
# ══════════════════════════════════════════════════════════════════════════════

def rule_lead_schedule_completion(fields: Dict[str, Any], materiality_pct: float = 5.0) -> List[AnalyticalFinding]:
    """Check lead schedules are completed in working papers.
    
    Katie RP7: "L1 to update on completion"
    """
    findings = []

    # If WP has an L1 sheet (lead schedule) and it's not marked complete
    wp_sheets = fields.get("wp.sheets", [])
    if isinstance(wp_sheets, list):
        lead_sheets = [s for s in wp_sheets if s.startswith("L") or "Lead" in s or "lead" in s]
        if lead_sheets:
            # This is a flag to prompt completion review
            findings.append(AnalyticalFinding(
                rule_id="A007",
                rule_name="Lead Schedule Completion",
                category="working_papers",
                severity="low",
                title=f"Lead schedules present — confirm all updated on completion ({len(lead_sheets)} found)",
                description=(
                    f"Working papers contain {len(lead_sheets)} lead schedule(s): "
                    f"{', '.join(lead_sheets[:5])}. "
                    f"Confirm all lead schedules have been updated to reflect final figures "
                    f"and are cross-referenced to the financial statements. "
                    f"L1 and other lead schedules should be updated as the last step before sign-off."
                ),
                source_fields=["wp.sheets"],
                signals=lead_sheets[:5],
            ))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL RULES: Common accountancy review points
# ══════════════════════════════════════════════════════════════════════════════

def rule_director_loan_overdrawn(fields: Dict[str, Any], materiality_pct: float = 5.0) -> List[AnalyticalFinding]:
    """Detect overdrawn director loan accounts — beneficial loan/tax implications."""
    findings = []

    dla = _get(fields,
        "tb.directors_loan", "tb.5401", "accounts.directors_loan",
        "director_loan.balance",
    )

    if dla is not None and dla > 100:  # Positive = owed TO company = overdrawn
        findings.append(AnalyticalFinding(
            rule_id="A008",
            rule_name="Director Loan Account — Overdrawn",
            category="dla",
            severity="high",
            title=f"Director loan account overdrawn (£{dla:,.2f} owed to company)",
            description=(
                f"The director loan account shows £{dla:,.2f} owed to the company. "
                f"Overdrawn DLA has tax implications: "
                f"(1) S455 tax charge at 33.75% on balance outstanding 9 months after year end, "
                f"(2) Benefit-in-kind charge if balance exceeds £10,000. "
                f"Consider: whether dividends can be voted to clear the balance, "
                f"or repayment before the 9-month deadline. "
                f"Confirm balance with director and agree repayment plan if required."
            ),
            source_fields=["directors_loan"],
            value_a=dla,
            regulation_ref="CTA2010 s455 — tax on loans to participators",
        ))

    return findings


def rule_going_concern(fields: Dict[str, Any], materiality_pct: float = 5.0) -> List[AnalyticalFinding]:
    """Flag going concern consideration when net liabilities exist."""
    findings = []

    net_assets = _get(fields,
        "accounts.net_assets", "tb.net_assets",
        "accounts.shareholders_funds",
    )

    if net_assets is not None and net_assets < 0:
        findings.append(AnalyticalFinding(
            rule_id="A009",
            rule_name="Going Concern — Net Liabilities Position",
            category="going_concern",
            severity="high",
            title=f"Net liabilities of £{abs(net_assets):,.0f} — going concern assessment required",
            description=(
                f"The balance sheet shows net liabilities of £{abs(net_assets):,.0f}. "
                f"Under FRS 102 s3.8, directors must assess whether the going concern basis "
                f"is appropriate. Prepare and document a going concern assessment covering: "
                f"(1) cash flow forecasts for at least 12 months, "
                f"(2) available banking facilities and headroom, "
                f"(3) any events after the reporting date. "
                f"The accounts should include appropriate disclosure of material uncertainties "
                f"or confirmation that the going concern basis is appropriate."
            ),
            source_fields=["net_assets"],
            value_a=net_assets,
            regulation_ref="FRS 102 s3.8 — going concern assessment and disclosure",
        ))

    return findings


# ══════════════════════════════════════════════════════════════════════════════
# ENGINE
# ══════════════════════════════════════════════════════════════════════════════

ALL_ANALYTICAL_RULES = [
    rule_dividends_vs_reserves,
    rule_payroll_consistency,
    rule_cash_in_hand_negative,
    rule_deferred_tax_asset,
    rule_depreciation_timing,
    rule_aged_creditors,
    rule_lead_schedule_completion,
    rule_director_loan_overdrawn,
    rule_going_concern,
]


def run_analytical_review(
    fields: Dict[str, Any],
    materiality_pct: float = 5.0,
    rules: Optional[list] = None,
) -> List[AnalyticalFinding]:
    """Run all analytical review rules against extracted field values.

    fields: flat dict with "source.field_name" -> value
    e.g. {"tb.3000": 382602.59, "tb.8010": 106600.0, "tb.4830": -500.0}

    Returns findings sorted by severity (high first).
    """
    active = rules or ALL_ANALYTICAL_RULES
    findings = []
    for rule_fn in active:
        try:
            findings.extend(rule_fn(fields, materiality_pct=materiality_pct))
        except Exception as e:
            logger.warning("Analytical rule %s failed: %s", rule_fn.__name__, e)

    # Sort: high first, then medium, then low
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f.severity, 3))
    return findings


def findings_to_review_points(findings: List[AnalyticalFinding]) -> List[Dict[str, Any]]:
    """Convert analytical findings to standard review point format."""
    points = []
    for f in findings:
        points.append({
            "severity": f.severity,
            "category": f.category,
            "title": f.title,
            "description": f.description,
            "section": f.category.replace("_", " ").title(),
            "audit_ref": f.rule_id,
            "field_name": f.source_fields[0] if f.source_fields else None,
            "value_a": str(f.value_a) if f.value_a is not None else None,
            "value_b": str(f.value_b) if f.value_b is not None else None,
            "delta": f.delta,
            "status": "open",
            "rule_id": f.rule_id,
            "regulation_ref": f.regulation_ref,
            "signals": f.signals,
        })
    return points
