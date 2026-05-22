from typing import List
import pandas as pd


class PivotUnpivot:
    MONTH_PREFIXES: List[str] = [
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec"
    ]

    def unpivot_if_needed(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        try:
            header = df.columns.astype(str).str.strip().str.lower().tolist()
            months = [c for c in header if c[:3] in self.MONTH_PREFIXES]
            id_cols = [c for c in header if c not in months]
            df2 = df.copy()
            if len(months) >= 3 and id_cols:
                melted = df2.melt(
                    id_vars=id_cols,
                    value_vars=months,
                    var_name="month",
                    value_name="value",
                )
                melted["value"] = pd.to_numeric(melted["value"], errors="ignore")
                return melted
            return df2
        except Exception:
            return df
