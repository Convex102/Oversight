import pandas as pd
from typing import Dict, Any


class DataQualityEngine:
    def score(self, df: pd.DataFrame) -> Dict[str, Any]:
        if df.empty:
            return {"missing": 0.0, "duplicates": 0, "rows": 0, "cols": 0}
        missing_fraction = float(df.isna().mean().mean())
        duplicates = int(df.duplicated().sum())
        return {
            "missing": missing_fraction,
            "duplicates": duplicates,
            "rows": int(len(df)),
            "cols": int(len(df.columns)),
        }
