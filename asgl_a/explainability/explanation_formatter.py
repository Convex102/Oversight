
"""
Converts raw SHAP values into accountant-readable explanations.
"""

def format_explanation(shap_values, feature_names):
    explanations = []
    for feature, value in zip(feature_names, shap_values):
        explanations.append({
            "feature": feature,
            "contribution": round(abs(float(value)), 2)
        })
    explanations.sort(key=lambda x: x["contribution"], reverse=True)
    return explanations
