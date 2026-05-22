
"""
Defines explainable features exposed to SHAP.
Keeps feature space stable and auditable.
"""

EXPLAINABLE_FEATURES = [
    "coa_mismatch_score",
    "variance_absolute",
    "variance_relative",
    "rounding_delta",
    "prior_year_flag_similarity",
    "template_match_strength",
    "ingestion_confidence"
]
