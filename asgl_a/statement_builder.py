from __future__ import annotations
import pandas as pd
from typing import Dict, Any, Optional, Tuple, List
from .coa_mapper import map_line_item
from .types import StatementPack

def _find_amount_cols(df: pd.DataFrame) -> List[str]:
    num = df.select_dtypes(include=["number"]).columns.tolist()
    return num

def _find_text_col(df: pd.DataFrame) -> Optional[str]:
    # Prefer a descriptive name column over a code/number column
    # ("Account Name" beats "Account Code").
    cols = list(df.columns)
    def lc(c): return str(c).lower()
    for pref in (["name", "description", "narrative", "particulars"], ["line", "nominal", "account"]):
        for c in cols:
            l = lc(c)
            if any(k in l for k in pref) and not any(x in l for x in ["code", "no.", "number", "ref", "id"]):
                return c
    for c in cols:
        if any(k in lc(c) for k in ["account", "description", "line", "name", "nominal"]):
            return c
    obj = df.select_dtypes(include=["object"]).columns.tolist()
    return obj[0] if obj else None

def _find_debit_credit(df: pd.DataFrame, num_cols: List[str]) -> Optional[Tuple[str, str]]:
    dr = next((c for c in num_cols if str(c).strip().lower() in ("debit", "debits", "dr")), None)
    cr = next((c for c in num_cols if str(c).strip().lower() in ("credit", "credits", "cr")), None)
    return (dr, cr) if dr and cr else None

class StatementBuilder:
    def build(self, df: pd.DataFrame, asglc_schema: Dict[str, Any]) -> Dict[str, Any]:
        pack = StatementPack(confidence=0.0)
        if df is None or len(df) == 0:
            pack.warnings.append("Empty dataframe")
            return pack.__dict__

        d = df.copy()

        text_col = _find_text_col(d)
        amt_cols = _find_amount_cols(d)
        if text_col is None or not amt_cols:
            pack.warnings.append("Could not identify line-item text column or numeric amount columns")
            return pack.__dict__

        # Trial balance with Debit and Credit columns: use the magnitude of the net
        # (category decides the sign meaning); otherwise pick the last numeric column
        # (often current period).
        drcr = _find_debit_credit(d, amt_cols)
        if drcr:
            dr_c, cr_c = drcr
            d["__net__"] = (pd.to_numeric(d[dr_c], errors="coerce").fillna(0.0)
                            - pd.to_numeric(d[cr_c], errors="coerce").fillna(0.0)).abs()
            amount_col = "__net__"
        else:
            amt_cols = [c for c in amt_cols if not any(k in str(c).lower() for k in ("code", "no.", "number", "ref"))] or amt_cols
            amount_col = amt_cols[-1]
        # drop rows without line item
        d = d.dropna(subset=[text_col]).copy()
        d[text_col] = d[text_col].astype(str)

        line_items = []
        cat_sums = {}
        confs = []

        for _, row in d.iterrows():
            name = str(row[text_col]).strip()
            if not name or name.lower() in ("nan","none"):
                continue
            try:
                val = float(row[amount_col]) if pd.notna(row[amount_col]) else 0.0
            except Exception:
                continue
            m = map_line_item(name)
            line_items.append({"name": name, "amount": val, "category": m.category, "confidence": m.confidence, "method": m.method})
            cat_sums[m.category] = cat_sums.get(m.category, 0.0) + val
            confs.append(m.confidence)

        pack.line_items = line_items
        if confs:
            pack.confidence = float(sum(confs)/len(confs))

        # Build P&L basics if we have revenue / cogs etc
        revenue = cat_sums.get("revenue", 0.0)
        cogs = cat_sums.get("cogs", 0.0)
        gp = revenue - abs(cogs) if revenue else (cat_sums.get("revenue",0.0) - cat_sums.get("cogs",0.0))
        staff = cat_sums.get("staff_costs", 0.0)
        admin = cat_sums.get("admin_expenses", 0.0)
        dep = cat_sums.get("depreciation", 0.0)
        other_exp = cat_sums.get("other_expenses", 0.0)
        other_inc = cat_sums.get("other_income", 0.0)
        opex = staff + admin + dep + other_exp
        op_profit = gp + other_inc - abs(opex)
        interest = cat_sums.get("interest", 0.0)
        tax = cat_sums.get("tax", 0.0)
        net = op_profit - abs(interest) - abs(tax)

        pack.pnl = {
            "revenue": revenue,
            "cogs": cogs,
            "gross_profit": gp,
            "opex": opex,
            "operating_profit": op_profit,
            "interest": interest,
            "tax": tax,
            "net_profit": net,
            "amount_column_used": ("Debit - Credit" if amount_col == "__net__" else amount_col),
            "line_item_column_used": text_col,
        }

        # Balance sheet approximations (if present)
        pack.balance_sheet = {
            "cash": cat_sums.get("cash", None),
            "receivables": cat_sums.get("receivables", None),
            "payables": cat_sums.get("payables", None),
            "inventory": cat_sums.get("inventory", None),
            "debt": cat_sums.get("debt", None),
            "equity": cat_sums.get("equity", None),
        }

        return pack.__dict__
