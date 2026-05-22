from typing import Dict, Any


class ModelRouter:
    """Simple rule-based router selecting a downstream model family."""

    def select(self, ts_meta: Dict[str, Any]) -> str:
        length = int(ts_meta.get("len", 0) or 0)
        gap_mean = ts_meta.get("gap_mean_days")
        if length == 0:
            return "none"
        if length > 36:
            return "hmm_cnn_bilstm"
        if length < 12:
            return "amira"
        if gap_mean is not None and gap_mean > 40:
            return "mc_shap_sparse"
        return "mc_shap"
