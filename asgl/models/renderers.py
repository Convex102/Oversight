import os, uuid
from typing import Optional, Callable
import pandas as pd

try:
    import shap
    HAVE_SHAP = True
except Exception:
    HAVE_SHAP = False
    shap = None

try:
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except Exception:
    HAVE_MPL = False
    plt = None


class SHAPRenderer:
    def __init__(self, artifact_dir: str, logger):
        self.artifact_dir = artifact_dir
        self.log = logger

    def render(self, model: Callable, X: pd.DataFrame) -> Optional[str]:
        if not HAVE_SHAP or not HAVE_MPL:
            self.log("SHAP or matplotlib unavailable; skipping SHAP plot.", level="WARN")
            return None
        if model is None or X is None or X.empty:
            return None
        out = os.path.join(self.artifact_dir, f"shap_{uuid.uuid4().hex}.png")
        try:
            explainer = shap.Explainer(model, X)
            shap_values = explainer(X)
            plt.figure()
            shap.summary_plot(shap_values, X, show=False)
            plt.savefig(out, dpi=200, bbox_inches="tight")
            plt.close()
            return out
        except Exception as e:
            self.log(f"SHAPRenderer error: {e}", level="WARN")
            return None


class ForecastRenderer:
    def __init__(self, artifact_dir: str, logger):
        self.artifact_dir = artifact_dir
        self.log = logger

    def render(self, df: pd.DataFrame, date: str = "date", actual: str = "actual", fc: str = "forecast") -> Optional[str]:
        if not HAVE_MPL:
            self.log("matplotlib unavailable; skipping forecast plot.", level="WARN")
            return None
        if date not in df.columns or (actual not in df.columns and fc not in df.columns):
            return None
        out = os.path.join(self.artifact_dir, f"fc_{uuid.uuid4().hex}.png")
        try:
            d = df.copy()
            d[date] = pd.to_datetime(d[date], errors="coerce")
            d = d.dropna(subset=[date]).sort_values(date)
            if d.empty:
                return None
            plt.figure(figsize=(10, 4))
            if actual in d.columns:
                plt.plot(d[date], d[actual], label="Actual")
            if fc in d.columns:
                plt.plot(d[date], d[fc], label="Forecast")
            plt.legend()
            plt.grid(True)
            plt.tight_layout()
            plt.savefig(out, dpi=200, bbox_inches="tight")
            plt.close()
            return out
        except Exception as e:
            self.log(f"ForecastRenderer error: {e}", level="WARN")
            return None
