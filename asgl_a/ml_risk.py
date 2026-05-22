from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import os
import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Optional deps (guarded)
try:
    from sklearn.ensemble import IsolationForest
    import joblib
    _HAS_SKLEARN = True
except Exception:
    IsolationForest = None
    joblib = None
    _HAS_SKLEARN = False

try:
    import shap
    _HAS_SHAP = True
except Exception:
    shap = None
    _HAS_SHAP = False

try:
    from lime.lime_tabular import LimeTabularExplainer
    _HAS_LIME = True
except Exception:
    LimeTabularExplainer = None
    _HAS_LIME = False


def _sev_score(flags: List[Dict[str, Any]]) -> float:
    sev_map = {"Info": 0.1, "Low": 0.3, "Medium": 0.6, "High": 0.9}
    base = 0.0
    for f in flags:
        base = max(base, sev_map.get(f.get("severity", "Low"), 0.3))
    return float(base)


def _safe_float(x: Any) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except Exception:
        return None


class RiskModel:
    """ML anomaly scoring for ASGL-A.

    Uses IsolationForest trained on flywheel-accumulated financial vectors.
    Produces SHAP feature attribution when model + enough background data exist.
    Always fail-safe: falls back to heuristic scoring if ML unavailable.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.ref_X: Optional[np.ndarray] = None
        self.feature_names: List[str] = []

        # Load from flywheel-trained model store first
        self.model = self._load_trained_model()

        # Load flywheel reference vectors for SHAP/LIME background
        self.ref_X = self._load_flywheel_vectors()

        # If no trained model but we have enough flywheel data, train now
        if self.model is None and self.ref_X is not None and len(self.ref_X) >= 20 and _HAS_SKLEARN:
            self._train_in_place()

        self.artifact_dir = Path(self.cfg.output_root) / "artifacts"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def _model_path(self) -> Path:
        d = Path(os.getenv("ASGL_MODEL_DIR", "data/models")) / "global" / "A"
        d.mkdir(parents=True, exist_ok=True)
        return d / "risk_model.joblib"

    def _load_trained_model(self):
        if not _HAS_SKLEARN:
            return None
        p = self._model_path()
        if p.exists():
            try:
                model = joblib.load(p)
                logger.info("RiskModel: loaded from %s", p)
                return model
            except Exception as e:
                logger.warning("RiskModel: load failed: %s", e)
        return None

    def _load_flywheel_vectors(self) -> Optional[np.ndarray]:
        """Load numeric feature vectors from flywheel JSONL."""
        flywheel_dir = Path(os.getenv("ASGL_FLYWHEEL_DIR", "data/flywheel"))
        # Search across all tenants for global background
        rows = []
        for jsonl_path in flywheel_dir.rglob("*.jsonl"):
            try:
                for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    payload = rec.get("payload", {})
                    feats = []
                    for v in payload.values():
                        try:
                            feats.append(float(v))
                        except (TypeError, ValueError):
                            pass
                    if len(feats) >= 5:
                        rows.append(feats[:10])
            except Exception:
                continue

        if not rows:
            return None
        width = max(len(r) for r in rows)
        padded = [r + [0.0] * (width - len(r)) for r in rows]
        return np.array(padded, dtype=np.float32)

    def _train_in_place(self) -> None:
        """Train IsolationForest on available flywheel data and persist."""
        try:
            model = IsolationForest(n_estimators=200, contamination="auto", random_state=42)
            model.fit(self.ref_X)
            p = self._model_path()
            joblib.dump(model, p)
            self.model = model
            logger.info("RiskModel: trained in-place on %d samples -> %s", len(self.ref_X), p)
        except Exception as e:
            logger.warning("RiskModel: in-place training failed: %s", e)

    def _vector(self, statements: Dict[str, Any], kpis: Dict[str, Any]) -> Tuple[np.ndarray, List[str]]:
        feats: List[Tuple[str, float]] = []

        pnl = statements.get("pnl", {}) if isinstance(statements, dict) else {}
        bs = statements.get("balance_sheet", {}) if isinstance(statements, dict) else {}

        def add(name: str, val: Any):
            f = _safe_float(val)
            feats.append((name, float(f) if f is not None else 0.0))

        add("revenue", pnl.get("revenue"))
        add("gross_profit", pnl.get("gross_profit"))
        add("net_profit", pnl.get("net_profit"))
        add("gross_margin_pct", pnl.get("gross_margin_pct"))
        add("net_margin_pct", pnl.get("net_margin_pct"))
        add("cash", bs.get("cash"))
        add("working_capital", bs.get("working_capital"))
        add("current_ratio", kpis.get("current_ratio"))
        add("quick_ratio", kpis.get("quick_ratio"))
        add("opex_ratio_pct", kpis.get("opex_ratio_pct"))

        X = np.array([v for _, v in feats], dtype=np.float32).reshape(1, -1)
        names = [n for n, _ in feats]
        return X, names

    def _score_model(self, X: np.ndarray) -> Optional[float]:
        if self.model is None:
            return None
        try:
            s = float(self.model.decision_function(X)[0])
            risk = 1.0 / (1.0 + np.exp(3.0 * s))
            return float(np.clip(risk, 0.0, 1.0))
        except Exception:
            return None

    def _render_shap(self, X: np.ndarray, feature_names: List[str]) -> Optional[Dict[str, Any]]:
        if not (getattr(self.cfg, "enable_shap", False) and _HAS_SHAP and self.model is not None):
            return None
        if self.ref_X is None or len(self.ref_X) < 50:
            return None
        try:
            bg = shap.sample(self.ref_X, min(50, len(self.ref_X)), random_state=42)

            def f(z):
                z = np.array(z, dtype=np.float32)
                if z.ndim == 1:
                    z = z.reshape(1, -1)
                return np.array([
                    0.0 if (rs := self._score_model(row.reshape(1, -1))) is None else rs
                    for row in z
                ])

            explainer = shap.KernelExplainer(f, bg)
            sv = explainer.shap_values(X, nsamples=100)
            vals = np.array(sv[0] if isinstance(sv, list) else sv).reshape(-1)
            order = np.argsort(np.abs(vals))[::-1][:10]
            top = [(feature_names[i], float(vals[i])) for i in order]

            import matplotlib.pyplot as plt
            fig_path = self.artifact_dir / f"shap_{os.urandom(4).hex()}.png"
            plt.figure(figsize=(8, 3))
            plt.bar([t[0] for t in top], [t[1] for t in top])
            plt.xticks(rotation=45, ha="right")
            plt.tight_layout()
            plt.savefig(fig_path, dpi=150)
            plt.close()

            return {"top_drivers": [{"feature": n, "impact": v} for n, v in top], "plot": str(fig_path)}
        except Exception as e:
            logger.debug("SHAP render failed: %s", e)
            return None

    def _render_lime(self, X: np.ndarray, feature_names: List[str]) -> Optional[Dict[str, Any]]:
        if not (getattr(self.cfg, "enable_lime", False) and _HAS_LIME and self.model is not None):
            return None
        if self.ref_X is None or len(self.ref_X) < 50:
            return None
        try:
            explainer = LimeTabularExplainer(
                training_data=self.ref_X,
                feature_names=feature_names,
                mode="regression",
                discretize_continuous=True,
            )

            def predict(z):
                z = np.array(z, dtype=np.float32)
                return np.array([
                    0.0 if (rs := self._score_model(row.reshape(1, -1))) is None else rs
                    for row in z
                ])

            exp = explainer.explain_instance(X[0], predict, num_features=min(10, X.shape[1]))
            weights = exp.as_list()
            path = self.artifact_dir / f"lime_{os.urandom(4).hex()}.json"
            path.write_text(json.dumps({"weights": weights}, indent=2))
            return {"weights": [{"feature": f, "weight": float(w)} for f, w in weights], "path": str(path)}
        except Exception as e:
            logger.debug("LIME render failed: %s", e)
            return None

    def score(self, statements: Dict[str, Any], kpis: Dict[str, Any], flags: List[Dict[str, Any]]) -> Dict[str, Any]:
        X, names = self._vector(statements, kpis)
        self.feature_names = names

        heuristic = _sev_score(flags)

        net = _safe_float(statements.get("pnl", {}).get("net_profit"))
        cr = _safe_float(kpis.get("current_ratio"))
        if cr is not None and cr < 1.0:
            heuristic = min(1.0, heuristic + 0.10)
        if net is not None and net < 0:
            heuristic = min(1.0, heuristic + 0.10)

        model_score = self._score_model(X)

        if model_score is None:
            risk = heuristic
            source = "heuristic"
        else:
            risk = float(np.clip(0.5 * heuristic + 0.5 * model_score, 0.0, 1.0))
            source = "isolation_forest_blend"

        return {
            "risk_score": risk,
            "risk_source": source,
            "model_available": model_score is not None,
            "vector_features": names,
            "shap": self._render_shap(X, names),
            "lime": self._render_lime(X, names),
        }
