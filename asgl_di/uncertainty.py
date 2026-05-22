from __future__ import annotations
from typing import Any, Dict, Optional

def compute_decision_confidence(
    *,
    dq: Optional[Dict[str, Any]] = None,
    asgl_a: Optional[Dict[str, Any]] = None,
    asgl_c: Optional[Dict[str, Any]] = None,
    ocr: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    dq = dq or {}
    asgl_a = asgl_a or {}
    asgl_c = asgl_c or {}
    ocr = ocr or {}

    def _f(x):
        try:
            return float(x)
        except Exception:
            return None

    dq_score = _f(dq.get("score") or dq.get("overall") or dq.get("overall_score"))
    flags = asgl_a.get("flags") or asgl_a.get("issues") or []
    flag_count = len(flags) if isinstance(flags, list) else 0
    cov = _f(asgl_c.get("coverage") or asgl_c.get("mapping_coverage"))
    map_conf = _f(asgl_c.get("confidence") or asgl_c.get("mapping_confidence"))

    ocr_used = bool(ocr.get("used") or ocr.get("enabled") or False)
    ocr_conf = _f(ocr.get("confidence"))

    comps = []
    for v in [dq_score, cov, map_conf]:
        if v is not None:
            comps.append(max(0.0, min(1.0, v)))
    if ocr_used and ocr_conf is not None:
        comps.append(max(0.0, min(1.0, ocr_conf)))

    base = sum(comps)/len(comps) if comps else 0.75
    penalty = min(0.35, 0.03*flag_count)
    confidence = max(0.0, min(1.0, base - penalty))

    ood = 1.0 - confidence
    if ocr_used and (ocr_conf is None or ocr_conf < 0.6):
        ood = min(1.0, ood + 0.15)

    gate = "allow" if confidence >= 0.80 else ("warn" if confidence >= 0.65 else "block")

    return {
        "confidence": round(confidence, 4),
        "ood_risk": round(ood, 4),
        "gating": gate,
        "signals": {
            "dq_score": dq_score,
            "flag_count": flag_count,
            "mapping_coverage": cov,
            "mapping_confidence": map_conf,
            "ocr_used": ocr_used,
            "ocr_confidence": ocr_conf
        }
    }
