from __future__ import annotations

"""
Oversight ASGL — Accountancy Vertical Reconciliation Rules
==========================================================
Stage 3 of the build sequence: cross-document rules that operate
on extracted named fields from N documents.

These are the 5 core AM checks:
1. CT per accounts == CT per CT600
2. Turnover (P&L) ~= Net outputs (VAT return) ± reconciling items
3. Fixed asset closing NBV (register) == Fixed asset NBV (balance sheet)
4. Bank per accounts == Bank per bank reconciliation
5. Director loan per accounts == Director loan per working papers

Each rule is a function that receives a dict of named fields extracted
from all documents in the engagement, and returns a list of RuleResult.

Rules are tolerance-aware (materiality_pct configurable).
Rules are non-blocking — a missing document produces a WARN, not a crash.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import math
import logging

logger = logging.getLogger(__name__)


@dataclass
class RuleResult:
    rule_id: str
    rule_name: str
    status: str          # PASS | FAIL | WARN | SKIP
    severity: str        # high | medium | low | info
    source_a: str        # document label
    source_b: str        # document label
    field_name: str
    value_a: Optional[float]
    value_b: Optional[float]
    delta: Optional[float]
    pct_delta: Optional[float]
    message: str
    materiality_flag: bool = False
    signals: List[str] = field(default_factory=list)


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return a - b


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    denom = abs(b) if abs(b) > 1e-9 else (abs(a) if abs(a) > 1e-9 else 1.0)
    return (a - b) / denom * 100.0


def _is_material(delta: Optional[float], pct: Optional[float], materiality_pct: float) -> bool:
    if delta is None or pct is None:
        return False
    return abs(delta) > 0.01 and abs(pct) > materiality_pct


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


# ══════════════════════════════════════════════════════════════════════════════
# RULE 1: Corporation Tax — CT600 vs Accounts
# ══════════════════════════════════════════════════════════════════════════════

def rule_ct_vs_accounts(
    fields: Dict[str, Any],
    materiality_pct: float = 5.0,
) -> RuleResult:
    """CT600 Box 1 (tax chargeable) must equal CT provision in accounts."""
    ct_600 = _get(fields,
        "ct600.box_510_tax_chargeable",
        "ct600.tax_chargeable",
        "ct600.box_440_ct_chargeable",
        "ct_computation.ct_chargeable",
        "ct.ct600_liability",
    )
    ct_accounts = _get(fields,
        "accounts.corporation_tax",
        "accounts.ct_provision",
        "working_papers.ct_liability",
        "tb.corporation_tax",
        "accounts.tax_charge",
        "statutory_accounts.ct_provision",
    )

    if ct_600 is None and ct_accounts is None:
        return RuleResult(
            rule_id="R001", rule_name="CT600 vs Accounts CT",
            status="SKIP", severity="info",
            source_a="CT600", source_b="Accounts",
            field_name="corporation_tax",
            value_a=None, value_b=None, delta=None, pct_delta=None,
            message="CT600 and accounts CT values not found — upload CT600 and final accounts",
        )
    if ct_600 is None:
        return RuleResult(
            rule_id="R001", rule_name="CT600 vs Accounts CT",
            status="WARN", severity="medium",
            source_a="CT600", source_b="Accounts",
            field_name="corporation_tax",
            value_a=None, value_b=ct_accounts, delta=None, pct_delta=None,
            message=f"CT600 not found — cannot verify CT provision of £{ct_accounts:,.2f} in accounts",
        )
    if ct_accounts is None:
        return RuleResult(
            rule_id="R001", rule_name="CT600 vs Accounts CT",
            status="WARN", severity="medium",
            source_a="CT600", source_b="Accounts",
            field_name="corporation_tax",
            value_a=ct_600, value_b=None, delta=None, pct_delta=None,
            message=f"CT in accounts not found — cannot verify against CT600 Box 1 of £{ct_600:,.2f}",
        )

    delta = _delta(ct_600, ct_accounts)
    pct = _pct(ct_600, ct_accounts)
    material = _is_material(delta, pct, materiality_pct)
    passes = abs(delta) < max(1.0, abs(ct_accounts) * materiality_pct / 100.0)

    return RuleResult(
        rule_id="R001", rule_name="CT600 vs Accounts CT",
        status="PASS" if passes else "FAIL",
        severity="high" if (not passes and material) else ("medium" if not passes else "low"),
        source_a="CT600", source_b="Accounts",
        field_name="corporation_tax",
        value_a=ct_600, value_b=ct_accounts,
        delta=delta, pct_delta=pct,
        materiality_flag=material,
        message=(
            f"CT600 Box 1 (£{ct_600:,.2f}) agrees with accounts CT provision (£{ct_accounts:,.2f})"
            if passes else
            f"MISMATCH: CT600 Box 1 £{ct_600:,.2f} vs accounts CT £{ct_accounts:,.2f} — "
            f"difference £{delta:+,.2f} ({pct:+.1f}%)"
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# RULE 2: Turnover — P&L vs VAT Net Outputs
# ══════════════════════════════════════════════════════════════════════════════

def rule_turnover_vs_vat(
    fields: Dict[str, Any],
    materiality_pct: float = 5.0,
) -> RuleResult:
    """Turnover per P&L should reconcile to VAT Box 6 (net outputs)."""
    turnover = _get(fields,
        "accounts.turnover", "accounts.revenue", "accounts.sales",
        "tb.turnover", "tb.revenue", "working_papers.turnover",
        "tb.4000",  # Sage account code
    )
    vat_box6 = _get(fields,
        "vat.box_6", "vat.net_outputs", "vat.total_value_sales",
        "vat_return.box_6",
    )

    if turnover is None or vat_box6 is None:
        missing = []
        if turnover is None: missing.append("P&L turnover")
        if vat_box6 is None: missing.append("VAT Box 6")
        return RuleResult(
            rule_id="R002", rule_name="Turnover vs VAT Net Outputs",
            status="SKIP", severity="info",
            source_a="P&L / Accounts", source_b="VAT Return",
            field_name="turnover",
            value_a=turnover, value_b=vat_box6, delta=None, pct_delta=None,
            message=f"Incomplete data — missing: {', '.join(missing)}",
        )

    delta = _delta(turnover, vat_box6)
    pct = _pct(turnover, vat_box6)
    # Turnover/VAT reconciliation allows for exempt supplies, zero-rated differences
    # Use a wider tolerance (10% default) and flag as WARN not FAIL
    tolerance = max(materiality_pct, 10.0)
    material = _is_material(delta, pct, tolerance)

    return RuleResult(
        rule_id="R002", rule_name="Turnover vs VAT Net Outputs",
        status="WARN" if material else "PASS",
        severity="medium" if material else "low",
        source_a="P&L / Accounts", source_b="VAT Return",
        field_name="turnover",
        value_a=turnover, value_b=vat_box6,
        delta=delta, pct_delta=pct,
        materiality_flag=material,
        message=(
            f"Turnover £{turnover:,.2f} and VAT Box 6 £{vat_box6:,.2f} — "
            f"difference £{delta:+,.2f} ({pct:+.1f}%). "
            + ("Review reconciling items (exempt/zero-rated supplies)." if material
               else "Within tolerance — review reconciling items if any.")
        ),
        signals=["Turnover/VAT differences may include exempt supplies, deposits, timing"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# RULE 3: Fixed Asset NBV — Register vs Balance Sheet
# ══════════════════════════════════════════════════════════════════════════════

def rule_fixed_asset_nbv(
    fields: Dict[str, Any],
    materiality_pct: float = 5.0,
) -> RuleResult:
    """Fixed asset closing NBV in register must equal NBV on balance sheet."""
    nbv_register = _get(fields,
        "fixed_assets.closing_nbv", "fixed_assets.net_book_value",
        "fixed_assets.nbv", "working_papers.fixed_assets_nbv",
    )
    nbv_accounts = _get(fields,
        "accounts.fixed_assets", "accounts.tangible_assets",
        "accounts.property_plant_equipment", "tb.fixed_assets",
        "accounts.nbv",
    )

    if nbv_register is None or nbv_accounts is None:
        missing = []
        if nbv_register is None: missing.append("Fixed asset register NBV")
        if nbv_accounts is None: missing.append("Balance sheet fixed assets")
        return RuleResult(
            rule_id="R003", rule_name="Fixed Asset NBV — Register vs Accounts",
            status="SKIP", severity="info",
            source_a="Fixed Asset Register", source_b="Balance Sheet",
            field_name="fixed_assets_nbv",
            value_a=nbv_register, value_b=nbv_accounts, delta=None, pct_delta=None,
            message=f"Incomplete data — missing: {', '.join(missing)}",
        )

    delta = _delta(nbv_register, nbv_accounts)
    pct = _pct(nbv_register, nbv_accounts)
    material = _is_material(delta, pct, materiality_pct)

    return RuleResult(
        rule_id="R003", rule_name="Fixed Asset NBV — Register vs Accounts",
        status="FAIL" if material else "PASS",
        severity="high" if material else "low",
        source_a="Fixed Asset Register", source_b="Balance Sheet",
        field_name="fixed_assets_nbv",
        value_a=nbv_register, value_b=nbv_accounts,
        delta=delta, pct_delta=pct,
        materiality_flag=material,
        message=(
            f"Fixed asset NBV agrees: register £{nbv_register:,.2f} = accounts £{nbv_accounts:,.2f}"
            if not material else
            f"MISMATCH: Fixed asset register NBV £{nbv_register:,.2f} vs "
            f"balance sheet £{nbv_accounts:,.2f} — difference £{delta:+,.2f} ({pct:+.1f}%)"
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# RULE 4: Bank — Accounts vs Reconciliation Statement
# ══════════════════════════════════════════════════════════════════════════════

def rule_bank_balance(
    fields: Dict[str, Any],
    materiality_pct: float = 5.0,
) -> RuleResult:
    """Bank balance per accounts must equal closing balance per bank reconciliation."""
    bank_accounts = _get(fields,
        "accounts.bank", "accounts.cash_at_bank", "accounts.cash_and_bank",
        "tb.bank", "tb.1200", "working_papers.bank",  # 1200 = Sage bank
    )
    bank_recon = _get(fields,
        "bank_statement.closing_balance", "bank_recon.balance",
        "working_papers.bank_reconciliation",
    )

    if bank_accounts is None or bank_recon is None:
        missing = []
        if bank_accounts is None: missing.append("Bank per accounts")
        if bank_recon is None: missing.append("Bank reconciliation / statement")
        return RuleResult(
            rule_id="R004", rule_name="Bank — Accounts vs Reconciliation",
            status="SKIP", severity="info",
            source_a="Accounts / TB", source_b="Bank Reconciliation",
            field_name="bank_balance",
            value_a=bank_accounts, value_b=bank_recon, delta=None, pct_delta=None,
            message=f"Incomplete data — missing: {', '.join(missing)}",
        )

    delta = _delta(bank_accounts, bank_recon)
    pct = _pct(bank_accounts, bank_recon)
    material = _is_material(delta, pct, materiality_pct)

    return RuleResult(
        rule_id="R004", rule_name="Bank — Accounts vs Reconciliation",
        status="FAIL" if material else "PASS",
        severity="high" if material else "low",
        source_a="Accounts / TB", source_b="Bank Reconciliation",
        field_name="bank_balance",
        value_a=bank_accounts, value_b=bank_recon,
        delta=delta, pct_delta=pct,
        materiality_flag=material,
        message=(
            f"Bank balance agrees: accounts £{bank_accounts:,.2f} = reconciliation £{bank_recon:,.2f}"
            if not material else
            f"MISMATCH: Bank per accounts £{bank_accounts:,.2f} vs "
            f"reconciliation £{bank_recon:,.2f} — difference £{delta:+,.2f} ({pct:+.1f}%)"
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# RULE 5: Director Loan — Accounts vs Working Papers
# ══════════════════════════════════════════════════════════════════════════════

def rule_director_loan(
    fields: Dict[str, Any],
    materiality_pct: float = 5.0,
) -> RuleResult:
    """Director loan account balance must agree across accounts, TB, and working papers."""
    dla_accounts = _get(fields,
        "accounts.director_loan", "accounts.directors_loan",
        "tb.director_loan", "tb.2300",  # 2300 = Sage director loan
        "accounts.loans_to_directors",
    )
    dla_workpapers = _get(fields,
        "working_papers.director_loan", "working_papers.dla",
        "fixed_assets.director_loan",
        "director_loan.closing_balance", "director_loan.balance",
    )

    if dla_accounts is None or dla_workpapers is None:
        missing = []
        if dla_accounts is None: missing.append("Director loan per accounts/TB")
        if dla_workpapers is None: missing.append("Director loan per working papers")
        return RuleResult(
            rule_id="R005", rule_name="Director Loan — Accounts vs Working Papers",
            status="SKIP", severity="info",
            source_a="Accounts / TB", source_b="Working Papers",
            field_name="director_loan",
            value_a=dla_accounts, value_b=dla_workpapers, delta=None, pct_delta=None,
            message=f"Incomplete data — missing: {', '.join(missing)}",
        )

    delta = _delta(dla_accounts, dla_workpapers)
    pct = _pct(dla_accounts, dla_workpapers)
    material = _is_material(delta, pct, materiality_pct)

    return RuleResult(
        rule_id="R005", rule_name="Director Loan — Accounts vs Working Papers",
        status="FAIL" if material else "PASS",
        severity="high" if material else "low",
        source_a="Accounts / TB", source_b="Working Papers",
        field_name="director_loan",
        value_a=dla_accounts, value_b=dla_workpapers,
        delta=delta, pct_delta=pct,
        materiality_flag=material,
        message=(
            f"Director loan agrees: accounts £{dla_accounts:,.2f} = working papers £{dla_workpapers:,.2f}"
            if not material else
            f"MISMATCH: Director loan per accounts £{dla_accounts:,.2f} vs "
            f"working papers £{dla_workpapers:,.2f} — difference £{delta:+,.2f} ({pct:+.1f}%)"
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# RULE 6: CT Computation trading loss agrees with CT600
# ══════════════════════════════════════════════════════════════════════════════

def rule_ct_computation_vs_ct600(
    fields: Dict[str, Any],
    materiality_pct: float = 5.0,
) -> RuleResult:
    """CT Computation trading loss must agree with CT600 Box 780.

    These are produced by different software modules and £1 rounding
    differences are common — flag as REVIEW not FAIL unless material.
    """
    loss_comp = _get(fields,
        "ct_computation.losses_arising",
        "ct_computation.trade_loss",
    )
    loss_ct600 = _get(fields,
        "ct600.box_780_trading_losses",
        "ct600.trading_losses",
    )

    if loss_comp is None or loss_ct600 is None:
        return RuleResult(
            rule_id="R006", rule_name="CT Computation vs CT600 Trading Loss",
            status="SKIP", severity="info",
            source_a="CT Computation", source_b="CT600",
            field_name="trading_losses",
            value_a=loss_comp, value_b=loss_ct600, delta=None, pct_delta=None,
            message="CT Computation or CT600 not available for loss comparison",
        )

    delta = _delta(loss_comp, loss_ct600)
    pct = _pct(loss_comp, loss_ct600)
    # £1 rounding is normal between computation and CT600 — use tighter threshold
    is_rounding = abs(delta) <= 1.0
    material = _is_material(delta, pct, materiality_pct) and not is_rounding

    return RuleResult(
        rule_id="R006", rule_name="CT Computation vs CT600 Trading Loss",
        status="PASS" if is_rounding else ("FAIL" if material else "REVIEW"),
        severity="low" if is_rounding else ("high" if material else "medium"),
        source_a="CT Computation", source_b="CT600",
        field_name="trading_losses",
        value_a=loss_comp, value_b=loss_ct600,
        delta=delta, pct_delta=pct,
        materiality_flag=material,
        message=(
            f"CT Computation loss £{loss_comp:,.0f} agrees with CT600 Box 780 £{loss_ct600:,.0f}"
            + (" (£1 rounding)" if is_rounding and abs(delta) > 0 else "")
            if (is_rounding or not material) else
            f"MISMATCH: CT Computation loss £{loss_comp:,.0f} vs CT600 £{loss_ct600:,.0f} — "
            f"difference £{delta:+,.0f}"
        ),
        signals=["£1 rounding differences are normal between CT computation software and CT600"] if is_rounding else [],
    )


# ══════════════════════════════════════════════════════════════════════════════
# ENGINE: Run all rules against extracted fields
# ══════════════════════════════════════════════════════════════════════════════

ALL_RULES = [
    rule_ct_vs_accounts,
    rule_ct_computation_vs_ct600,
    rule_turnover_vs_vat,
    rule_fixed_asset_nbv,
    rule_bank_balance,
    rule_director_loan,
]


def run_reconciliation_rules(
    fields: Dict[str, Any],
    materiality_pct: float = 5.0,
    rules: Optional[list] = None,
) -> List[RuleResult]:
    """Run all (or specified) reconciliation rules against extracted fields.

    fields: flat dict of "source.field_name" -> value
    e.g. {"ct600.box_1_tax_chargeable": 54000.0,
          "accounts.corporation_tax": 54000.0,
          "tb.turnover": 920000.0,
          "vat.box_6": 890000.0}

    Returns list of RuleResult — PASS/FAIL/WARN/SKIP per rule.
    Only FAIL and WARN become review points.
    """
    active_rules = rules or ALL_RULES
    results = []
    for rule_fn in active_rules:
        try:
            result = rule_fn(fields, materiality_pct=materiality_pct)
            results.append(result)
            logger.info(
                "Reconciliation rule %s [%s]: %s",
                result.rule_id, result.rule_name, result.status,
            )
        except Exception as e:
            logger.warning("Rule %s failed: %s", rule_fn.__name__, e)
    return results


def results_to_review_points(
    results: List[RuleResult],
    include_pass: bool = False,
) -> List[Dict]:
    """Convert rule results to the standard review point format."""
    points = []
    for r in results:
        if r.status in ("SKIP",) and not include_pass:
            continue
        if r.status == "PASS" and not include_pass:
            continue
        points.append({
            "severity": r.severity,
            "category": "reconciliation",
            "title": f"{r.rule_name}: {r.status}",
            "description": r.message,
            "section": f"{r.source_a} vs {r.source_b}",
            "audit_ref": r.rule_id,
            "field_name": r.field_name,
            "value_a": str(r.value_a) if r.value_a is not None else None,
            "value_b": str(r.value_b) if r.value_b is not None else None,
            "delta": r.delta,
            "status": "open",
            "rule_id": r.rule_id,
            "materiality_flag": r.materiality_flag,
            "signals": r.signals,
        })
    return points
