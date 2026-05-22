
"""
ASGL-A Popup Runner v4
Includes SHAP explainability trigger.
"""

from asgl_a.popup_runner_v3 import run_single, run_comparison
from asgl_a.validation.explainability_hook import run_explainability

def run_with_explainability(result, shap_values=None, feature_names=None):
    if shap_values and feature_names:
        result["explainability"] = run_explainability(shap_values, feature_names)
    return result
