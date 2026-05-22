from __future__ import annotations
from typing import Dict, Any, List
import re

def detect_totals(rows: List[List[Any]])->Dict[str,Any]:
    totals=[]
    for i,r in enumerate(rows):
        joined=" ".join([str(x) for x in r]).lower()
        if re.search(r"\b(total|subtotal|grand total)\b", joined):
            totals.append({"row_index": i, "label": joined[:80]})
    return {"totals_rows": totals}

def infer_headers(rows: List[List[Any]])->Dict[str,Any]:
    if not rows: return {"header_row": None}
    # naive: first non-empty row
    for i,r in enumerate(rows[:10]):
        if any(str(x).strip() for x in r):
            return {"header_row": i, "headers": [str(x).strip() for x in r]}
    return {"header_row": None}

def enrich_table(table: Dict[str,Any])->Dict[str,Any]:
    rows = table.get("rows", [])
    sem = {}
    sem.update(infer_headers(rows))
    sem.update(detect_totals(rows))
    table["semantics"]=sem
    return table