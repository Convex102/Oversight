from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List
import re

try:
    from sentence_transformers import SentenceTransformer
    _EMB = SentenceTransformer("all-MiniLM-L6-v2")
except Exception:
    _EMB = None

CANON = {
    "revenue": ["revenue","turnover","sales","fee income","income","total income"],
    "cogs": ["cost of sales","direct costs","costs of sales","cogs"],
    "staff_costs": ["staff costs","wages","salaries","payroll","paye"],
    "admin_expenses": ["admin expenses","overheads","general & admin","g&a","office costs"],
    "depreciation": ["depreciation","amortisation","amortization"],
    "interest": ["interest","finance costs","bank interest"],
    "tax": ["tax","corporation tax"],
    "other_income": ["other income","misc income","other revenue"],
    "other_expenses": ["other expenses","misc expenses","exceptional","one-off"],
    "cash": ["cash","bank","cash at bank","cash in hand"],
    "receivables": ["debtors","accounts receivable","trade receivables"],
    "payables": ["creditors","accounts payable","trade payables"],
    "inventory": ["stock","inventory"],
    "debt": ["loan","borrowings","debt","notes payable"],
    "equity": ["equity","share capital","retained earnings","reserves"],
}

@dataclass
class CoAMapResult:
    category: str
    confidence: float
    method: str

def _norm(s: str) -> str:
    return re.sub(r"\s+"," ", re.sub(r"[^a-z0-9\s&]"," ", (s or "").lower())).strip()

def map_line_item(name: str) -> CoAMapResult:
    n = _norm(name)
    # rule: direct keyword contains
    for cat, kws in CANON.items():
        for kw in kws:
            if kw in n:
                return CoAMapResult(cat, 0.90, "rule")
    # regex heuristics
    if re.search(r"\b(turnover|sales)\b", n):
        return CoAMapResult("revenue", 0.85, "regex")
    if re.search(r"\b(cost of sales|direct costs)\b", n):
        return CoAMapResult("cogs", 0.85, "regex")

    # embedding similarity (optional)
    if _EMB is not None and n:
        cats = list(CANON.keys())
        cat_text = ["; ".join(CANON[c]) for c in cats]
        emb_item = _EMB.encode([n])[0]
        emb_cats = _EMB.encode(cat_text)
        import numpy as np
        sims = (emb_cats @ emb_item) / (np.linalg.norm(emb_cats, axis=1) * (np.linalg.norm(emb_item) + 1e-9))
        best_i = int(sims.argmax())
        best = float(sims[best_i])
        # calibrate
        if best > 0.45:
            return CoAMapResult(cats[best_i], min(0.80, 0.55 + best/2), "embedding")
    return CoAMapResult("unclassified", 0.20, "fallback")
