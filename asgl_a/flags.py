from typing import Dict, Any, List

class FlagEngine:
    def generate(self, statements: Dict[str, Any], kpis: Dict[str, Any]) -> List[Dict[str, Any]]:
        flags: List[Dict[str, Any]] = []
        pnl = statements.get("pnl", {})
        bs = statements.get("balance_sheet", {})

        revenue = float(pnl.get("revenue") or 0.0)
        gp_margin = kpis.get("gross_margin_pct")
        net_margin = kpis.get("net_margin_pct")
        current_ratio = kpis.get("current_ratio")
        wc = kpis.get("working_capital")

        if revenue == 0:
            flags.append({"title":"Revenue not detected", "severity":"Medium",
                          "evidence":["Revenue could not be confidently identified from line items."],
                          "checks":["Confirm sales/turnover lines exist and are labelled consistently."]})

        if gp_margin is not None and gp_margin < 20:
            flags.append({"title":"Low gross margin", "severity":"Medium",
                          "evidence":[f"Gross margin {gp_margin:.1f}%"],
                          "checks":["Review cost of sales classification and pricing pressure."]})

        if net_margin is not None and net_margin < 5:
            flags.append({"title":"Low net margin", "severity":"Low",
                          "evidence":[f"Net margin {net_margin:.1f}%"],
                          "checks":["Review overhead base and one-off costs."]})

        if current_ratio is not None and current_ratio < 1.0:
            ev = [f"Current ratio {current_ratio:.2f}"]
            if wc is not None:
                ev.append(f"Working capital {wc:,.0f}")
            flags.append({"title":"Liquidity pressure", "severity":"High" if current_ratio<0.8 else "Medium",
                          "evidence":ev,
                          "checks":["Check debtor collectability, creditor terms, and cash runway."]})

        # profit warning
        net_profit = float(pnl.get("net_profit") or 0.0)
        if net_profit < 0:
            flags.append({"title":"Net loss", "severity":"High",
                          "evidence":[f"Net profit {net_profit:,.0f}"],
                          "checks":["Review going concern, exceptional items, and cost base."]})

        # negative equity (if detected)
        eq = bs.get("equity")
        if isinstance(eq,(int,float)) and eq < 0:
            flags.append({"title":"Negative equity", "severity":"High",
                          "evidence":[f"Equity {eq:,.0f}"],
                          "checks":["Consider balance sheet solvency and shareholder support."]})

        return flags
