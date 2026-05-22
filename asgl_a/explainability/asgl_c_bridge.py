
"""
Bridge between ASGL-A explainability and ASGL-C governance.
"""

from asgl_c.governance_hooks import attach_explainability

def persist_to_asgl_c(decision_fingerprint, explanation):
    attach_explainability(
        decision_fingerprint=decision_fingerprint,
        explanation=explanation
    )
