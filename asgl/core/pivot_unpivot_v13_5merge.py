from __future__ import annotations

from typing import List, Tuple
import re
import pandas as pd


class PivotUnpivot:
    MONTH_PREFIXES: List[str] = [
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec"
    ]

    _RE_YEAR = re.compile(r"\b(19|20)\d{2}\b")
    _RE_QTR = re.compile(r"\bq\s?([1-4])\b", re.IGNORECASE)
    _RE_FY = re.compile(r"\b(fy|year)\s?(19|20)?\d{2}\b", re.IGNORECASE)

    def unpivot_if_needed(self, df: pd.DataFrame) -> pd.DataFrame:
        """Unpivot wide time-series style tables into long format.

        The old implementation only handled month-name headers. Real working
        papers frequently use:
        - Month names (Jan, Feb, ...)
        - Quarters (Q1..Q4)
        - Years (2023, 2024)
        - Fiscal year labels (FY24, Year 2024)

        Heuristic:
        - Identify "time-like" columns.
        - If there are >=3 time-like columns, melt into (id_vars, period, value).
        """

        if df is None or df.empty:
            return df

        try:
            cols = [str(c).strip() for c in df.columns.tolist()]
            time_cols = [c for c in cols if self._is_time_col(c)]
            if len(time_cols) < 3:
                return df

            id_cols = [c for c in cols if c not in time_cols]
            if not id_cols:
                # if everything looks like time, keep first non-null text-ish column as id
                id_guess = self._guess_id_col(df)
                if id_guess:
                    id_cols = [id_guess]
                    time_cols = [c for c in cols if c != id_guess]

            if not id_cols or len(time_cols) < 3:
                return df

            out = df.copy()
            melted = out.melt(id_vars=id_cols, value_vars=time_cols, var_name="period", value_name="value")

            # Coerce numeric value where possible
            melted["value"] = pd.to_numeric(melted["value"], errors="ignore")
            return melted
        except Exception:
            return df

    def _is_time_col(self, col: str) -> bool:
        c = col.strip().lower()
        if not c:
            return False
        if c[:3] in self.MONTH_PREFIXES:
            return True
        if self._RE_QTR.search(c) is not None:
            return True
        if self._RE_YEAR.search(c) is not None:
            return True
        if self._RE_FY.search(c) is not None:
            return True

        # also treat parseable dates as time columns
        try:
            dt = pd.to_datetime(col, errors="coerce")
            return pd.notna(dt)
        except Exception:
            return False

    @staticmethod
    def _guess_id_col(df: pd.DataFrame) -> str | None:
        # pick first column with mostly non-numeric values
        for c in df.columns:
            s = df[c]
            try:
                num = pd.to_numeric(s, errors="coerce")
                if num.notna().mean() < 0.35:
                    return str(c)
            except Exception:
                return str(c)
        return None
