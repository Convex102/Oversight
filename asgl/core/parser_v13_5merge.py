from __future__ import annotations

"""Spreadsheet ingestion for ASGL.

This module intentionally goes beyond `pandas.read_excel()`.

Goals
-----
- Preserve layout signals needed for messy working papers: merged cells,
  indentation, bold totals, hidden rows/cols, and basic formula presence.
- Build a *grid* representation to allow robust table region detection.
- Emit `pandas.DataFrame` outputs for downstream compatibility.

The implementation is still lightweight (pure Python + openpyxl) but uses
modern techniques common in spreadsheet/document-intelligence systems:

- Used-range detection that ignores "format-only" blowups.
- Connected-component table segmentation on occupancy grids.
- Multi-row header inference with merged-cell propagation.
- Strong numeric coercion for accounting formats.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple

import re
import numpy as np
import pandas as pd
from openpyxl import load_workbook


@dataclass
class CellInfo:
    value: Any
    formula: Optional[str] = None
    number_format: Optional[str] = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    indent: int = 0
    align: Optional[str] = None
    fill_rgb: Optional[str] = None
    is_merged: bool = False
    is_hidden: bool = False


@dataclass
class SheetGrid:
    name: str
    values: np.ndarray  # object dtype
    cells: np.ndarray   # object dtype, CellInfo
    used_range: Tuple[int, int, int, int]  # (min_row, min_col, max_row, max_col) 1-based
    merged_ranges: List[Tuple[int, int, int, int]]
    hidden_rows: List[int]
    hidden_cols: List[int]

    def as_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(self.values)


class SpreadsheetParser:
    """Advanced multi-sheet Excel parser.

    Backwards-compatible with the previous interface:
    - `load()` returns Dict[sheet_name, DataFrame] with `header=None`.

    Additional capabilities:
    - `load_grids()` returns rich `SheetGrid` objects.
    """

    def __init__(self, file_path: str, *, max_cells: int = 2_000_000):
        self.file_path = file_path
        self.max_cells = int(max_cells)

    def load(self) -> Dict[str, pd.DataFrame]:
        grids = self.load_grids()
        return {k: g.as_dataframe() for k, g in grids.items()}

    def load_grids(self) -> Dict[str, SheetGrid]:
        # Load twice: with formulas and with data_only values.
        # We intentionally use read_only=False because we need access to:
        # - merged cell ranges
        # - row/col hidden flags
        # - cell styles (indent/bold)
        # For large workbooks, we cap scanning via `max_cells`.
        wb_f = load_workbook(self.file_path, data_only=False, read_only=False)
        wb_v = load_workbook(self.file_path, data_only=True, read_only=False)

        out: Dict[str, SheetGrid] = {}
        for ws_f in wb_f.worksheets:
            name = ws_f.title
            ws_v = wb_v[name]
            grid = self._sheet_to_grid(ws_f, ws_v)
            if grid is not None:
                out[name] = grid
        return out

    # -----------------------------
    # Implementation details
    # -----------------------------
    def _sheet_to_grid(self, ws_f, ws_v) -> Optional[SheetGrid]:
        # Used range detection: only consider rows/cols containing actual values
        # or formulas. Ignore styling-only expansion.
        min_r = None
        min_c = None
        max_r = 0
        max_c = 0

        # Cap to protect against pathological workbooks.
        # We scan the read-only cell generator and track bounds.
        for row in ws_f.iter_rows(values_only=False):
            for cell in row:
                v = cell.value
                if v is None:
                    continue
                # treat empty strings as empty
                if isinstance(v, str) and not v.strip():
                    continue
                r, c = cell.row, cell.column
                min_r = r if min_r is None else min(min_r, r)
                min_c = c if min_c is None else min(min_c, c)
                max_r = max(max_r, r)
                max_c = max(max_c, c)

        if min_r is None or max_r == 0 or max_c == 0:
            return None

        # Safety bound
        if (max_r - min_r + 1) * (max_c - min_c + 1) > self.max_cells:
            # Fall back to a tighter bounding box by limiting to top-left area.
            max_r = min(max_r, min_r + 2000)
            max_c = min(max_c, min_c + 200)

        # Hidden rows/cols
        hidden_rows = [i for i, dim in ws_f.row_dimensions.items() if getattr(dim, "hidden", False)]
        hidden_cols = []
        for k, dim in ws_f.column_dimensions.items():
            if getattr(dim, "hidden", False):
                # `k` is a column letter
                try:
                    from openpyxl.utils.cell import column_index_from_string

                    hidden_cols.append(column_index_from_string(k))
                except Exception:
                    continue

        # Merge ranges (read-only worksheets still expose merged ranges)
        merged_ranges = []
        try:
            for mr in ws_f.merged_cells.ranges:
                merged_ranges.append((mr.min_row, mr.min_col, mr.max_row, mr.max_col))
        except Exception:
            merged_ranges = []

        n_rows = max_r - min_r + 1
        n_cols = max_c - min_c + 1

        values = np.empty((n_rows, n_cols), dtype=object)
        cells = np.empty((n_rows, n_cols), dtype=object)

        # Build a quick merged lookup
        merged_lookup = set()
        for r0, c0, r1, c1 in merged_ranges:
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    merged_lookup.add((r, c))

        for r in range(min_r, max_r + 1):
            for c in range(min_c, max_c + 1):
                cf = ws_f.cell(r, c)
                cv = ws_v.cell(r, c)

                v = cv.value
                if isinstance(v, str) and not v.strip():
                    v = None

                formula = None
                if isinstance(cf.value, str) and cf.value.startswith("="):
                    formula = cf.value

                is_hidden = (r in hidden_rows) or (c in hidden_cols)

                try:
                    bold = bool(getattr(getattr(cf, "font", None), "bold", False))
                    italic = bool(getattr(getattr(cf, "font", None), "italic", False))
                    underline = bool(getattr(getattr(cf, "font", None), "underline", False))
                    indent = int(getattr(getattr(cf, "alignment", None), "indent", 0) or 0)
                    align = getattr(getattr(cf, "alignment", None), "horizontal", None)
                    nf = getattr(cf, "number_format", None)
                    fill = getattr(getattr(cf, "fill", None), "fgColor", None)
                    fill_rgb = getattr(fill, "rgb", None) if fill is not None else None
                except Exception:
                    bold = italic = underline = False
                    indent = 0
                    align = None
                    nf = None
                    fill_rgb = None

                info = CellInfo(
                    value=v,
                    formula=formula,
                    number_format=nf,
                    bold=bold,
                    italic=italic,
                    underline=underline,
                    indent=indent,
                    align=align,
                    fill_rgb=fill_rgb,
                    is_merged=((r, c) in merged_lookup),
                    is_hidden=is_hidden,
                )

                rr = r - min_r
                cc = c - min_c
                values[rr, cc] = v
                cells[rr, cc] = info

        # Propagate merged values (top-left) across merged ranges
        self._propagate_merges(values, merged_ranges, min_r, min_c)

        return SheetGrid(
            name=str(ws_f.title),
            values=values,
            cells=cells,
            used_range=(min_r, min_c, max_r, max_c),
            merged_ranges=merged_ranges,
            hidden_rows=hidden_rows,
            hidden_cols=hidden_cols,
        )

    @staticmethod
    def _propagate_merges(values: np.ndarray, merged_ranges: List[Tuple[int, int, int, int]], min_r: int, min_c: int) -> None:
        for r0, c0, r1, c1 in merged_ranges:
            rr0 = r0 - min_r
            cc0 = c0 - min_c
            rr1 = r1 - min_r
            cc1 = c1 - min_c
            if rr0 < 0 or cc0 < 0 or rr1 >= values.shape[0] or cc1 >= values.shape[1]:
                continue
            v = values[rr0, cc0]
            if v is None:
                continue
            for rr in range(rr0, rr1 + 1):
                for cc in range(cc0, cc1 + 1):
                    if values[rr, cc] is None:
                        values[rr, cc] = v


# -----------------------------
# Helpers used by table/inspection logic
# -----------------------------


def coerce_accounting_number(x: Any) -> Optional[float]:
    """Robust coercion for common accounting number encodings."""
    if x is None:
        return None
    if isinstance(x, (int, float)) and not (isinstance(x, float) and np.isnan(x)):
        return float(x)
    s = str(x).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None

    # Excel error tokens
    if s.startswith("#"):
        return None

    # % values
    is_pct = s.endswith("%")
    s = s.replace("%", "")

    # Parentheses negatives, trailing minus
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    if s.endswith("-") and s[:-1].replace(",", "").replace(".", "").isdigit():
        s = "-" + s[:-1]

    # Currency and separators
    s = s.replace(",", "")
    s = re.sub(r"[£$€]", "", s)
    s = s.replace(" ", "")

    try:
        v = float(s)
        if is_pct:
            v = v / 100.0
        return v
    except Exception:
        return None


def infer_header_depth(values: np.ndarray, max_depth: int = 8) -> int:
    """Infer header depth by scanning from top until a numeric-dominant row."""
    max_depth = max(1, min(int(max_depth), values.shape[0]))
    def _looks_like_year_token(v: Any) -> bool:
        try:
            if isinstance(v, (int, np.integer)):
                return 1900 <= int(v) <= 2100
            if isinstance(v, float) and float(v).is_integer():
                iv = int(v)
                return 1900 <= iv <= 2100
            s = str(v).strip()
            if s.isdigit() and len(s) == 4:
                iv = int(s)
                return 1900 <= iv <= 2100
        except Exception:
            return False
        return False

    for d in range(1, max_depth + 1):
        row = values[d - 1, :]
        nn = 0
        num = 0
        txt = 0
        for v in row:
            if v is None:
                continue
            nn += 1
            # Treat year tokens as header-text (common: column headers 2023/2024)
            if _looks_like_year_token(v):
                txt += 1
                continue
            if coerce_accounting_number(v) is not None:
                num += 1
            else:
                txt += 1
        if nn == 0:
            continue
        # If row is numeric-heavy, we've probably hit the body.
        if num / nn >= 0.7:
            return max(1, d - 1)
        # If row has multiple numeric entries (excluding year tokens) plus at least one text label,
        # it's likely a body row even if the ratio doesn't hit 0.7.
        if num >= 2 and txt >= 1 and num / nn >= 0.5:
            return max(1, d - 1)
        # If row is mostly text and we haven't hit any numeric in first rows,
        # continue.
    return min(3, max_depth)
