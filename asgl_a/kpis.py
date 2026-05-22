from typing import Dict, Any
import math

class KPIEngine:
    def compute(self, statements: Dict[str, Any]) -> Dict[str, Any]:
        pnl = statements.get("pnl", {}) if isinstance(statements, dict) else {}
        bs = statements.get("balance_sheet", {}) if isinstance(statements, dict) else {}
        revenue = float(pnl.get("revenue") or 0.0)
        gp = float(pnl.get("gross_profit") or 0.0)
        net = float(pnl.get("net_profit") or 0.0)
        opex = float(pnl.get("opex") or 0.0)

        def pct(x, base):
            return (x/base*100.0) if base not in (0, None) else None

        k = {
            "gross_margin_pct": pct(gp, revenue),
            "net_margin_pct": pct(net, revenue),
            "opex_ratio_pct": pct(abs(opex), revenue),
        }

        cash = bs.get("cash")
        receivables = bs.get("receivables")
        inventory = bs.get("inventory")
        payables = bs.get("payables")
        debt = bs.get("debt")
        equity = bs.get("equity")

        # working capital and ratios if we have components
        current_assets = sum([v for v in [cash, receivables, inventory] if isinstance(v, (int,float))])
        current_liabs = sum([v for v in [payables, debt] if isinstance(v, (int,float))])
        k["working_capital"] = (current_assets - abs(current_liabs)) if (current_assets or current_liabs) else None
        k["current_ratio"] = (current_assets / abs(current_liabs)) if (current_liabs not in (0, None) and isinstance(current_liabs,(int,float))) else None
        if isinstance(debt,(int,float)) and isinstance(equity,(int,float)) and equity != 0:
            k["debt_to_equity"] = abs(debt) / abs(equity)
        else:
            k["debt_to_equity"] = None

        return k
