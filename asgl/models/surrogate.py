from typing import Tuple, Callable, Optional, List
import numpy as np
import pandas as pd


def build_simple_surrogate_model(
    df: pd.DataFrame,
) -> Tuple[Optional[Callable[[np.ndarray], np.ndarray]], Optional[pd.DataFrame], Optional[List[str]]]:
    """Build a small linear surrogate model to feed SHAP.

    Target: 'actual' or 'forecast' if present among numeric columns.
    """
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    target_candidates = [c for c in ["actual", "forecast"] if c in numeric_cols]
    if not target_candidates:
        return None, None, None
    target_col = target_candidates[0]
    feature_cols = [c for c in numeric_cols if c != target_col]
    if not feature_cols:
        return None, None, None

    X = df[feature_cols].copy()
    y = df[target_col].copy()
    X = X.fillna(X.mean())
    y = y.fillna(y.mean())
    if X.empty:
        return None, None, None

    X_mat = X.values
    y_vec = y.values
    X_design = np.c_[np.ones(len(X_mat)), X_mat]
    try:
        coef, *_ = np.linalg.lstsq(X_design, y_vec, rcond=None)
    except Exception:
        return None, None, None

    def model(x: np.ndarray) -> np.ndarray:
        x = np.asarray(x)
        if x.ndim == 1:
            x = x.reshape(1, -1)
        x_design = np.c_[np.ones(len(x)), x]
        return x_design @ coef

    return model, X, feature_cols
