from __future__ import annotations
from typing import Dict, Any, List
from collections import Counter

def summarize_factbundle(factbundle: Dict[str,Any])->Dict[str,Any]:
    facts = (factbundle or {}).get("facts", {}) or {}
    confs=[f.get("confidence",0.0) for f in facts.values() if isinstance(f, dict)]
    low=[k for k,f in facts.items() if isinstance(f, dict) and f.get("confidence",1.0)<0.70]
    return {
        "fact_count": len(facts),
        "avg_confidence": sum(confs)/len(confs) if confs else 0.0,
        "low_confidence_count": len(low),
        "low_confidence_fields": low[:50],
    }

def violations_summary(validations: Dict[str,Any])->Dict[str,Any]:
    viols=validations.get("violations",[]) if isinstance(validations, dict) else []
    sev=Counter([v.get("severity","unknown") for v in viols if isinstance(v, dict)])
    return {"violation_count": len(viols), "severity_counts": dict(sev)}