from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple
import pandas as pd
import numpy as np

from .schema import CanonicalSchema, RoleGroup, ColumnRole
from .exceptions import FeatureFactoryError

@dataclass
class FeatureBundle:
    X: pd.DataFrame
    y: Optional[pd.Series]
    meta: Dict[str, Any]

class FeatureFactory:
    """Role-driven feature extraction.

    The point: downstream models should depend on roles, not domain column names.
    """
    def __init__(self, rolling_windows: Tuple[int, ...] = (3, 6, 12), max_features_per_role: int = 200):
        self.rolling_windows = rolling_windows
        self.max_features_per_role = max_features_per_role

    def build(
        self,
        df: pd.DataFrame,
        schema: CanonicalSchema,
        target_role: RoleGroup = RoleGroup.outcome,
        target_name: Optional[str] = None,
    ) -> FeatureBundle:
        try:
            d = df.copy()

            time_col = schema.time_column.column if schema.time_column else None
            if time_col and time_col in d.columns:
                # Try sort by time
                try:
                    d[time_col] = pd.to_datetime(d[time_col], errors="coerce")
                    d = d.sort_values(time_col)
                except Exception:
                    pass

            # collect feature cols (numeric only)
            roles = {}
            roles.update(schema.states)
            roles.update(schema.flows)
            roles.update(schema.drivers)
            roles.update(schema.controls)

            feats = []
            for name, role in roles.items():
                col = role.column
                if col not in d.columns:
                    continue
                if not pd.api.types.is_numeric_dtype(d[col]):
                    continue
                f = self._features_for_series(d[col].astype(float), prefix=self._safe(name))
                feats.append(f)

            if not feats:
                raise FeatureFactoryError("No numeric features derived from schema roles")

            X = pd.concat(feats, axis=1)

            # target series (optional)
            y = None
            if target_role == RoleGroup.outcome:
                # If outcomes exist, pick by name or first
                if schema.outcomes:
                    key = target_name if (target_name and target_name in schema.outcomes) else next(iter(schema.outcomes.keys()))
                    tcol = schema.outcomes[key].column
                    if tcol in d.columns and pd.api.types.is_numeric_dtype(d[tcol]):
                        y = d[tcol].astype(float)

            meta = {
                "time_column": time_col,
                "n_features": int(X.shape[1]),
                "feature_columns": list(X.columns)[:2000],
            }
            return FeatureBundle(X=X, y=y, meta=meta)

        except Exception as e:
            raise FeatureFactoryError(str(e))

    def _features_for_series(self, s: pd.Series, prefix: str) -> pd.DataFrame:
        out = {}
        out[f"{prefix}__value"] = s
        out[f"{prefix}__diff1"] = s.diff()
        out[f"{prefix}__pct_change"] = s.pct_change().replace([np.inf, -np.inf], np.nan)
        out[f"{prefix}__zscore"] = (s - s.rolling(12, min_periods=3).mean()) / (s.rolling(12, min_periods=3).std() + 1e-9)

        for w in self.rolling_windows:
            out[f"{prefix}__roll_mean_{w}"] = s.rolling(w, min_periods=max(2, w//2)).mean()
            out[f"{prefix}__roll_std_{w}"] = s.rolling(w, min_periods=max(2, w//2)).std()
            out[f"{prefix}__roll_min_{w}"] = s.rolling(w, min_periods=max(2, w//2)).min()
            out[f"{prefix}__roll_max_{w}"] = s.rolling(w, min_periods=max(2, w//2)).max()

        df = pd.DataFrame(out)
        # cap features per role if needed
        if df.shape[1] > self.max_features_per_role:
            df = df.iloc[:, : self.max_features_per_role]
        return df

    def _safe(self, name: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in str(name))[:64]
