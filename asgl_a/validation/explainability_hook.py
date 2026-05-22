
"""
Invoked only when a validation issue is flagged.
"""

from asgl_a.explainability.explanation_formatter import format_explanation

def run_explainability(shap_values, feature_names):
    return format_explanation(shap_values, feature_names)
