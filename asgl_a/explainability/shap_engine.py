
"""
SHAP Engine for ASGL-A
---------------------
Computes post-hoc explanations for flagged validation events.
This module NEVER influences decisions.
"""

import shap

def compute_shap(model, features):
    explainer = shap.Explainer(model)
    shap_values = explainer(features)
    return shap_values
