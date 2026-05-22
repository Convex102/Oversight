
"""
ASGL-C Explainability Registry
------------------------------
Stores and links SHAP explanations to decision fingerprints.
"""

import json
from datetime import datetime
from pathlib import Path

REGISTRY_DIR = Path("asgl_c/data/explainability")

def register_explanation(
    decision_fingerprint: str,
    explanation_payload: dict,
    source: str = "ASGL-A"
):
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

    record = {
        "decision_fingerprint": decision_fingerprint,
        "source": source,
        "timestamp": datetime.utcnow().isoformat(),
        "explanation": explanation_payload
    }

    path = REGISTRY_DIR / f"{decision_fingerprint}.json"
    with open(path, "w") as f:
        json.dump(record, f, indent=2)

    return path
