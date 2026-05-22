from typing import Dict, Any
import pandas as pd


class TSInspector:
    def analyse(self, df: pd.DataFrame, date_col: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        try:
            if date_col not in df.columns:
                return {}
            local = df.copy()
            local[date_col] = pd.to_datetime(local[date_col], errors="coerce")
            ts = local.dropna(subset=[date_col]).set_index(date_col).sort_index()
            if ts.empty:
                return {}
            idx = ts.index
            gaps = idx.to_series().diff().dt.days.dropna()
            result["len"] = int(len(ts))
            result["start"] = idx.min().isoformat()
            result["end"] = idx.max().isoformat()
            result["gap_mean_days"] = float(gaps.mean()) if not gaps.empty else None
            result["gap_median_days"] = float(gaps.median()) if not gaps.empty else None
        except Exception:
            return {}
        return result
