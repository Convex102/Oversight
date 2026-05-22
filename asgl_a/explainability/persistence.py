
"""
Persists SHAP explanations for audit and ASGL-C governance.
"""

import json
from datetime import datetime

def persist_explanation(explanation, output_path):
    payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "explanation": explanation
    }
    with open(output_path, "w") as f:
        json.dump(payload, f, indent=2)
