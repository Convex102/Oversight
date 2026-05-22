from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional
import pandas as pd


class TableExtractor:
    """Segment a worksheet into candidate tables.

    This is deliberately *heuristic* (working papers are messy). The goal is to
    locate dense regions of data separated by mostly-empty rows.

    Notes:
    - We treat a row as a separator if it has <= `min_non_null` populated cells.
    - We then post-trim all-null rows/cols from each candidate.
    """

    def __init__(self, min_non_null: int = 2, min_rows: int = 6, min_cols: int = 3):
        self.min_non_null = int(min_non_null)
        self.min_rows = int(min_rows)
        self.min_cols = int(min_cols)

    def extract_tables(self, df: pd.DataFrame) -> List[pd.DataFrame]:
        if df is None or df.empty:
            return []

        tables: List[pd.DataFrame] = []
        buf: List[pd.Series] = []

        def flush():
            nonlocal buf, tables
            if not buf:
                return
            t = pd.DataFrame(buf).reset_index(drop=True)
            t = t.dropna(how="all").dropna(how="all", axis=1)
            if t.shape[0] >= self.min_rows and t.shape[1] >= self.min_cols:
                tables.append(t)
            buf = []

        for _, row in df.iterrows():
            nn = int(row.notna().sum())
            if nn >= self.min_non_null:
                buf.append(row)
            else:
                flush()
        flush()

        # Prefer dense tables first
        tables.sort(key=lambda t: (t.notna().sum().sum(), t.shape[0] * t.shape[1]), reverse=True)
        return tables


class MultiHeaderFlattener:
    """Flatten multi-row headers into a single string header.

    Working papers commonly have 2-6 header rows. This flattener:
    - Accepts a `depth` (header row count)
    - Concatenates header parts using `sep`
    - Produces metadata describing the transformation
    """

    def flatten(self, df: pd.DataFrame, depth: int = 2, sep: str = " | ") -> Tuple[pd.DataFrame, Dict[str, Any]]:
        if df is None or df.empty:
            return df, {"header_depth": 0, "flattened_headers": []}

        depth = max(1, min(int(depth), len(df)))
        header_rows = df.iloc[:depth].copy()
        body = df.iloc[depth:].copy().reset_index(drop=True)

        combined_headers: List[str] = []
        for col_idx in range(df.shape[1]):
            parts: List[str] = []
            for r in range(depth):
                val = header_rows.iat[r, col_idx]
                if val is None:
                    continue
                if isinstance(val, float) and pd.isna(val):
                    continue
                s = str(val).strip()
                if s and s.lower() not in ("nan", "none"):
                    parts.append(s)
            if not parts:
                parts.append(f"col_{col_idx}")
            combined_headers.append(sep.join(parts))

        # Ensure uniqueness
        seen = {}
        uniq = []
        for h in combined_headers:
            base = h
            k = seen.get(base, 0)
            if k:
                h = f"{base} ({k+1})"
            seen[base] = k + 1
            uniq.append(h)

        body.columns = uniq
        meta = {
            "header_depth": depth,
            "header_rows": list(range(depth)),
            "flattened_headers": uniq,
        }
        return body, meta


def choose_best_table(tables: List[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Choose the most likely 'main' table from candidates.


    Heuristic: prefer the table with highest non-null count.

    Returns None if list empty.

    """
    if not tables:
        return None
    return sorted(tables, key=lambda t: (t.notna().sum().sum(), t.shape[0] * t.shape[1]), reverse=True)[0]
