
"""
Hooks to attach explainability artefacts to ASGL-C governance flow.
"""

from asgl_c.explainability_registry import register_explanation

def attach_explainability(decision_fingerprint, explanation):
    return register_explanation(
        decision_fingerprint=decision_fingerprint,
        explanation_payload=explanation,
        source="ASGL-A-SHAP"
    )
