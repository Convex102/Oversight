from __future__ import annotations
from typing import Dict, Any, List
import difflib, re
from app.di.embeddings import best_match

def normalize(s:str)->str:
    return re.sub(r"\s+"," ",str(s).strip().lower())

def suggest_mapping_repairs_v5(mapping: Dict[str,Any], observed_headers: List[str]) -> List[Dict[str,Any]]:
    headers=[h for h in observed_headers if h]
    norm_headers=[normalize(h) for h in headers]
    suggestions=[]
    for field, spec in (mapping or {}).items():
        header = spec.get("header") or spec.get("label") or field
        nh=normalize(header)
        if nh in norm_headers:
            continue
        # 1) fuzzy
        match = difflib.get_close_matches(nh, norm_headers, n=1, cutoff=0.78)
        if match:
            idx = norm_headers.index(match[0])
            suggestions.append({"field": field,"current_header": header,"suggested_header": headers[idx],
                                "confidence": 0.82,"reason": "fuzzy_header_match"})
            continue
        # 2) semantic embedding match
        bm = best_match(header, headers, top_k=1)
        if bm and bm[0]["score"] >= 0.78:
            suggestions.append({"field": field,"current_header": header,"suggested_header": bm[0]["candidate"],
                                "confidence": float(min(0.90, 0.60 + 0.40*bm[0]["score"])),
                                "reason": "semantic_embedding_match","score": bm[0]["score"]})
    return suggestions