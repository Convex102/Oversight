from typing import Dict, List, Tuple, Any
import numpy as np
import pandas as pd

CANONICAL_ROLES = ["date", "client", "forecast", "actual", "attribute"]

try:
    from sentence_transformers import SentenceTransformer
    BERT_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    BERT_CANONICAL = {t: BERT_MODEL.encode([t])[0] for t in CANONICAL_ROLES}
except Exception:  # optional
    BERT_MODEL = None
    BERT_CANONICAL = None


class HybridSemanticEngine:
    """Rule + BERT + LLM-stub based semantic role inference."""

    def __init__(self, llm: Any = None):
        self.llm = llm

    def rule_score(self, col: str, series: pd.Series) -> Dict[str, float]:
        s = {k: 0.0 for k in CANONICAL_ROLES}
        lower = col.lower()
        if any(k in lower for k in ["date", "month", "day"]):
            s["date"] += 3.0
        if any(k in lower for k in ["client", "customer", "account"]):
            s["client"] += 3.0
        if "forecast" in lower or "pred" in lower:
            s["forecast"] += 2.0
        if any(k in lower for k in ["actual", "value", "amount"]):
            s["actual"] += 1.0

        if pd.api.types.is_numeric_dtype(series):
            s["actual"] += 1.5
            s["forecast"] += 1.0
        try:
            parsed = pd.to_datetime(series, errors="coerce")
            if parsed.notna().mean() > 0.7:
                s["date"] += 2.5
        except Exception:
            pass

        nunique = series.nunique(dropna=True)
        if len(series) > 0:
            ratio = nunique / max(len(series), 1)
            if ratio < 0.2:
                s["attribute"] += 0.5
                s["client"] += 0.5
        return s

    def bert_score(self, col: str) -> Dict[str, float]:
        if BERT_MODEL is None or BERT_CANONICAL is None:
            return {k: 0.1 for k in CANONICAL_ROLES}
        emb = BERT_MODEL.encode([col])[0]
        sims = {}
        for role, canon in BERT_CANONICAL.items():
            denom = (np.linalg.norm(emb) * np.linalg.norm(canon)) or 1.0
            sims[role] = float(np.dot(emb, canon) / denom)
        return sims

    def llm_score(self, col: str, sample_values: List[str]) -> Dict[str, float]:
        # Placeholder for pluggable LLM agent
        return {k: 0.2 for k in CANONICAL_ROLES}

    def infer(self, df: pd.DataFrame) -> Tuple[Dict[str, str], Dict[str, Dict[str, float]]]:
        mapping = {}
        scores = {}
        for col in df.columns:
            series = df[col]
            sample = series.astype(str).tolist()[:5]
            r = self.rule_score(col, series)
            b = self.bert_score(col)
            l = self.llm_score(col, sample)
            combined = {k: r.get(k, 0.0) + b.get(k, 0.0) + l.get(k, 0.0) for k in CANONICAL_ROLES}
            scores[col] = combined
            mapping[col] = max(combined, key=combined.get)
        return mapping, scores
