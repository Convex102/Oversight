from __future__ import annotations
from typing import Dict, Any
import hashlib, json

def fingerprint_mapping(mapping: Dict[str,Any])->str:
    b=json.dumps(mapping, sort_keys=True).encode("utf-8")
    return hashlib.sha256(b).hexdigest()

def detect_template_drift(previous_fp: str, current_fp: str)->Dict[str,Any]:
    if not previous_fp:
        return {"drift": False, "reason":"no_baseline"}
    if previous_fp != current_fp:
        return {"drift": True, "reason":"mapping_changed", "previous": previous_fp, "current": current_fp}
    return {"drift": False, "reason":"unchanged"}