from __future__ import annotations
from typing import Dict, Any, List, Tuple
import difflib, re

def normalize(s:str)->str:
    return re.sub(r"\s+"," ",str(s).strip().lower())

def suggest_mapping_repairs(mapping: Dict[str,Any], observed_headers: List[str]) -> List[Dict[str,Any]]:
    # mapping: field -> {sheet, addr, header?}
    headers=[normalize(h) for h in observed_headers if h]
    suggestions=[]
    for field, spec in (mapping or {}).items():
        header = spec.get("header") or spec.get("label") or field
        nh = normalize(header)
        if nh in headers:
            continue
        match = difflib.get_close_matches(nh, headers, n=1, cutoff=0.78)
        if match:
            suggestions.append({
                "field": field,
                "current_header": header,
                "suggested_header": match[0],
                "confidence": 0.80,
                "reason": "fuzzy_header_match"
            })
    return suggestions

def drift_score(drift: Dict[str,Any], repairs: List[Dict[str,Any]])->float:
    if not drift.get("drift"):
        return 0.0
    base=0.5
    base += min(0.4, 0.05*len(repairs))
    return min(0.95, base)