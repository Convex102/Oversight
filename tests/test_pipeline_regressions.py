"""Regression tests for the Oversight-A pipeline fixes (trial balance handling)."""
import asyncio
import io

import pandas as pd

from asgl_a.coa_mapper import map_line_item
from asgl_a.statement_builder import StatementBuilder


def _tb_df():
    rows = [
        ("1000", "Cash at bank", 15000, 0), ("1100", "Trade debtors", 42000, 0),
        ("1200", "Prepayments", 3000, 0), ("2000", "Trade creditors", 0, 28000),
        ("2100", "VAT control", 0, 6000), ("3000", "Share capital", 0, 100),
        ("3100", "Retained earnings", 0, 20000), ("4000", "Sales", 0, 95000),
        ("5000", "Cost of sales", 52000, 0), ("6000", "Wages", 28000, 0),
        ("6100", "Rent", 9000, 0), ("7000", "Bank charges", 100, 0),
    ]
    return pd.DataFrame(rows, columns=["Account Code", "Account Name", "Debit", "Credit"])


def test_mapper_specific_beats_generic():
    assert map_line_item("Cost of sales").category == "cogs"
    assert map_line_item("Sales").category == "revenue"
    assert map_line_item("Bank charges").category == "admin_expenses"
    assert map_line_item("Cash at bank").category == "cash"


def test_statement_builder_trial_balance():
    out = StatementBuilder().build(_tb_df(), {})
    pnl = out["pnl"]
    assert pnl["line_item_column_used"] == "Account Name"
    assert pnl["revenue"] == 95000 and pnl["cogs"] == 52000
    assert round(pnl["gross_profit"]) == 43000
    assert round(pnl["net_profit"]) == 5900


def test_run_pipeline_inside_running_loop():
    """run_pipeline must work when called from a thread that has a running loop."""
    from app.asgl_engine.pipeline import run_pipeline
    buf = io.BytesIO()
    _tb_df().to_excel(buf, index=False)
    data = buf.getvalue()

    async def caller():
        return run_pipeline(files=[("tb.xlsx", data)])

    res = asyncio.run(caller())
    assert res.get("asgl_a", {}).get("kpis", {}).get("gross_margin_pct") is not None
