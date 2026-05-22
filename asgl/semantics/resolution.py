from typing import Dict, List
import pandas as pd
from .semantic_engine import CANONICAL_ROLES


class ColumnResolution:
    """Resolve conflicts & validate mapping, especially for 'critical' roles."""

    def refine(self, mapping: Dict[str, str], scores: Dict[str, Dict[str, float]]) -> Dict[str, str]:
        refined = dict(mapping)
        critical: List[str] = ["date", "client", "forecast", "actual"]
        by_role: Dict[str, List[str]] = {}
        for col, role in refined.items():
            by_role.setdefault(role, []).append(col)
        for role in critical:
            cols = by_role.get(role, [])
            if len(cols) <= 1:
                continue
            best = max(cols, key=lambda c: scores.get(c, {}).get(role, 0.0))
            for c in cols:
                if c != best:
                    refined[c] = "attribute"
        return refined

    def validate(self, df: pd.DataFrame, mapping: Dict[str, str]) -> Dict[str, str]:
        validated = dict(mapping)
        if "date" not in validated.values():
            for col in df.columns:
                series = df[col]
                try:
                    parsed = pd.to_datetime(series, errors="ignore")
                    if pd.api.types.is_datetime64_any_dtype(parsed):
                        validated[col] = "date"
                        break
                except Exception:
                    continue
        return validated
