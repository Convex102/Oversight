from __future__ import annotations

"""Advanced spreadsheet ingestion for ASGL.

This module turns a workbook into one or more *table candidates* with rich
metadata suitable for downstream semantic graph compilation.

Design goals
------------
1) Robust to messy working papers (merged cells, multi-row headers, blank
   separator rows, titles/footnotes, repeated subtables).
2) Deterministic and explainable (heuristics + scores, not opaque magic).
3) Backwards compatible: emits `pandas.DataFrame`.

Techniques included (state-of-the-practice for spreadsheet DI)
------------------------------------------------------------
- Used-range detection that ignores "format-only" bloat (handled in parser).
- Occupancy-grid morphological closing to bridge small blank gaps.
- Connected-component extraction of dense regions.
- Automatic header depth inference.
- Header propagation + concatenation for merged/multi-row headers.
- Accounting number coercion (parentheses negatives, currency symbols, %).
- Quality inspection: density, type-mix, formula/error counts, totals checks.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from .parser import SpreadsheetParser, SheetGrid, coerce_accounting_number, infer_header_depth


@dataclass
class TableCandidate:
    sheet: str
    bbox: Tuple[int, int, int, int]  # (r0,c0,r1,c1) in *grid* coordinates (0-based)
    df: pd.DataFrame
    meta: Dict[str, Any]
    score: float


class AdvancedSpreadsheetIngestor:
    def __init__(
        self,
        *,
        min_area: int = 18,
        min_density: float = 0.18,
        bridge_gap: int = 1,
        max_header_depth: int = 8,
    ):
        self.min_area = int(min_area)
        self.min_density = float(min_density)
        self.bridge_gap = int(bridge_gap)
        self.max_header_depth = int(max_header_depth)

    def ingest(self, file_path: str) -> List[TableCandidate]:
        parser = SpreadsheetParser(file_path)
        grids = parser.load_grids()
        out: List[TableCandidate] = []
        for name, grid in grids.items():
            out.extend(self._extract_from_sheet(grid))
        out.sort(key=lambda x: x.score, reverse=True)
        return out

    # -----------------------------
    # Sheet extraction
    # -----------------------------
    def _extract_from_sheet(self, grid: SheetGrid) -> List[TableCandidate]:
        values = grid.values

        # Build occupancy ignoring hidden rows/cols.
        occ = values != None  # noqa: E711
        if grid.hidden_rows:
            for r in grid.hidden_rows:
                rr = r - grid.used_range[0]
                if 0 <= rr < occ.shape[0]:
                    occ[rr, :] = False
        if grid.hidden_cols:
            for c in grid.hidden_cols:
                cc = c - grid.used_range[1]
                if 0 <= cc < occ.shape[1]:
                    occ[:, cc] = False

        # Morphological closing (bridge small gaps)
        occ2 = self._bridge_gaps(occ, gap=self.bridge_gap)
        comps = self._connected_components(occ2)

        candidates: List[TableCandidate] = []
        for bbox in comps:
            r0, c0, r1, c1, area, filled = bbox
            if area < self.min_area:
                continue
            density = filled / max(1, area)
            if density < self.min_density:
                continue

            # Expand up to include headers and left labels
            r0e, c0e, r1e, c1e = self._expand_bbox(values, r0, c0, r1, c1)
            sub = values[r0e : r1e + 1, c0e : c1e + 1]
            sub, roff, coff = self._trim_empty(sub)
            r0e, c0e, r1e, c1e = r0e + roff, c0e + coff, r0e + roff + sub.shape[0] - 1, c0e + coff + sub.shape[1] - 1
            if sub.size == 0 or sub.shape[0] < 4 or sub.shape[1] < 2:
                continue

            df, meta = self._to_table_df(grid, sub, (r0e, c0e, r1e, c1e))
            score = self._score(df, meta)
            candidates.append(
                TableCandidate(
                    sheet=grid.name,
                    bbox=(r0e, c0e, r1e, c1e),
                    df=df,
                    meta=meta,
                    score=score,
                )
            )

        # Prefer larger/denser tables first
        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates

    # -----------------------------
    # Core algorithms
    # -----------------------------
    @staticmethod
    def _bridge_gaps(occ: np.ndarray, gap: int = 1) -> np.ndarray:
        """Bridge small blank gaps so that tables with sparse separators stay connected."""
        if gap <= 0:
            return occ.copy()

        out = occ.copy()
        # simple iterative closing: if an empty cell sits between filled cells in row/col, fill it
        for _ in range(gap):
            left = np.zeros_like(out)
            left[:, 1:] = out[:, :-1]
            right = np.zeros_like(out)
            right[:, :-1] = out[:, 1:]
            up = np.zeros_like(out)
            up[1:, :] = out[:-1, :]
            down = np.zeros_like(out)
            down[:-1, :] = out[1:, :]

            bridge_h = left & right
            bridge_v = up & down
            out = out | bridge_h | bridge_v
        return out

    @staticmethod
    def _connected_components(occ: np.ndarray) -> List[Tuple[int, int, int, int, int, int]]:
        """Return bounding boxes for connected components in occupancy grid."""
        h, w = occ.shape
        seen = np.zeros_like(occ, dtype=bool)
        bboxes: List[Tuple[int, int, int, int, int, int]] = []

        for r in range(h):
            for c in range(w):
                if not occ[r, c] or seen[r, c]:
                    continue
                # BFS
                q = [(r, c)]
                seen[r, c] = True
                r0 = r1 = r
                c0 = c1 = c
                filled = 0
                while q:
                    rr, cc = q.pop()
                    filled += 1
                    r0 = min(r0, rr)
                    r1 = max(r1, rr)
                    c0 = min(c0, cc)
                    c1 = max(c1, cc)
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nr, nc = rr + dr, cc + dc
                        if 0 <= nr < h and 0 <= nc < w and occ[nr, nc] and not seen[nr, nc]:
                            seen[nr, nc] = True
                            q.append((nr, nc))

                area = (r1 - r0 + 1) * (c1 - c0 + 1)
                bboxes.append((r0, c0, r1, c1, area, filled))

        # Prefer large dense blocks
        bboxes.sort(key=lambda x: (x[5] / max(1, x[4]), x[5], x[4]), reverse=True)
        return bboxes

    @staticmethod
    def _expand_bbox(values: np.ndarray, r0: int, c0: int, r1: int, c1: int) -> Tuple[int, int, int, int]:
        """Expand bbox upward/left to capture headers/labels."""
        # Expand upward while rows contain meaningful text but few numerics
        rr0 = r0
        for _ in range(12):
            if rr0 <= 0:
                break
            row = values[rr0 - 1, c0 : c1 + 1]
            nn = 0
            num = 0
            for v in row:
                if v is None:
                    continue
                nn += 1
                if coerce_accounting_number(v) is not None:
                    num += 1
            if nn == 0:
                # allow one blank header spacer, but stop on multiple
                rr0 -= 1
                continue
            if num / nn <= 0.35:
                rr0 -= 1
                continue
            break

        # Expand left one or two cols to include row labels
        cc0 = c0
        for _ in range(3):
            if cc0 <= 0:
                break
            col = values[rr0 : r1 + 1, cc0 - 1]
            nn = 0
            txt = 0
            for v in col:
                if v is None:
                    continue
                nn += 1
                if coerce_accounting_number(v) is None:
                    txt += 1
            if nn > 0 and (txt / nn) >= 0.6:
                cc0 -= 1
            else:
                break

        return rr0, cc0, r1, c1

    @staticmethod
    def _trim_empty(sub: np.ndarray) -> Tuple[np.ndarray, int, int]:
        if sub.size == 0:
            return sub, 0, 0
        # trim all-empty rows/cols
        keep_r = np.where(np.any(sub != None, axis=1))[0]  # noqa: E711
        keep_c = np.where(np.any(sub != None, axis=0))[0]  # noqa: E711
        if keep_r.size == 0 or keep_c.size == 0:
            return np.empty((0, 0), dtype=object), 0, 0
        r0, r1 = int(keep_r.min()), int(keep_r.max())
        c0, c1 = int(keep_c.min()), int(keep_c.max())
        return sub[r0 : r1 + 1, c0 : c1 + 1], r0, c0

    # -----------------------------
    # Table rendering + inspection
    # -----------------------------
    def _to_table_df(self, grid: SheetGrid, sub: np.ndarray, bbox: Tuple[int, int, int, int]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        # Split out title rows (single-cell headings above the column headers)
        title_depth = self._infer_title_depth(sub)
        core = sub[title_depth:, :]

        # Infer header depth on the remaining block
        header_depth = infer_header_depth(core, max_depth=self.max_header_depth)
        header_depth = max(1, min(header_depth, min(8, core.shape[0])))

        headers = self._flatten_headers(core[:header_depth, :])
        body = core[header_depth:, :]

        df = pd.DataFrame(body, columns=headers)
        df = self._drop_repeated_header_rows(df, headers)
        df = df.dropna(how="all").dropna(how="all", axis=1).reset_index(drop=True)

        # Attach row hierarchy signals (Excel indentation) where available.
        total_header_depth = title_depth + header_depth
        level = self._row_levels_from_indent(grid, bbox, total_header_depth, sub)
        if level is not None:
            df.insert(0, "__asgl_level", level)

        # Coerce numeric columns aggressively but safely
        numeric_cols = []
        for c in df.columns:
            series = df[c]
            coerced = series.map(coerce_accounting_number)
            if coerced.notna().mean() >= 0.70 and coerced.notna().sum() >= 3:
                df[c] = coerced
                numeric_cols.append(c)

        meta = self._inspect(grid, sub, bbox, total_header_depth, headers, numeric_cols)
        meta["title"] = self._extract_title_text(sub, title_depth)
        meta["scale_hint"] = self._detect_scale_hint(sub[:total_header_depth, :])
        return df, meta

    @staticmethod
    def _infer_title_depth(sub: np.ndarray, max_title_rows: int = 4) -> int:
        """Infer title rows sitting above the column header.

        Title rows are typically:
        - very sparse (<=2 non-null cells)
        - mostly text (no real numbers)
        - often bold and/or merged (merge propagation already handled)
        """
        depth = 0
        for i in range(min(int(max_title_rows), sub.shape[0])):
            row = sub[i, :]
            nn = [v for v in row.tolist() if v is not None and str(v).strip()]
            if not nn:
                depth += 1
                continue
            if len(nn) <= 2:
                # if all values are non-numeric -> title
                nums = sum(1 for v in nn if coerce_accounting_number(v) is not None)
                if nums == 0:
                    depth += 1
                    continue
            break
        return depth

    @staticmethod
    def _extract_title_text(sub: np.ndarray, title_depth: int) -> str | None:
        if title_depth <= 0:
            return None
        rows = sub[:title_depth, :]
        parts = []
        for i in range(rows.shape[0]):
            for v in rows[i, :].tolist():
                if v is None:
                    continue
                s = str(v).strip()
                if s and s.lower() not in {"nan", "none"}:
                    parts.append(s)
        if not parts:
            return None
        # de-duplicate contiguous duplicates
        cleaned = []
        for p in parts:
            if not cleaned or cleaned[-1] != p:
                cleaned.append(p)
        return " | ".join(cleaned)[:500]

    @staticmethod
    def _drop_repeated_header_rows(df: pd.DataFrame, headers: List[str]) -> pd.DataFrame:
        """Remove repeated header lines inside the body.

        Many working papers repeat the header every page. We drop rows where
        a large fraction of cells echo the column header tokens.
        """
        if df is None or df.empty:
            return df
        header_tokens = [str(h).strip().lower() for h in headers]
        keep = []
        for _, row in df.iterrows():
            vals = [str(v).strip().lower() for v in row.tolist() if v is not None]
            if not vals:
                keep.append(True)
                continue
            matches = 0
            for v in vals:
                if v in header_tokens:
                    matches += 1
            # if >= 60% of non-null cells match headers, drop
            keep.append(not (matches / max(1, len(vals)) >= 0.6 and len(vals) >= 3))
        return df.loc[keep].reset_index(drop=True)

    @staticmethod
    def _row_levels_from_indent(grid: SheetGrid, bbox: Tuple[int, int, int, int], header_depth: int, sub: np.ndarray) -> Optional[List[int]]:
        """Return per-row indent levels for the best label column.

        Adds a cheap but powerful hierarchy signal (TB lines / notes often use
        indentation to indicate nesting).
        """
        try:
            r0, c0, r1, c1 = bbox
            # choose label column among first 3 cols with most text
            label_col = 0
            for j in range(min(3, sub.shape[1])):
                col_vals = sub[header_depth:, j]
                txt = sum(1 for v in col_vals if v is not None and coerce_accounting_number(v) is None)
                if txt >= max(2, int(0.25 * len(col_vals))):
                    label_col = j
                    break

            levels: List[int] = []
            for i in range(sub.shape[0] - header_depth):
                rr = r0 + header_depth + i
                cc = c0 + label_col
                ci = grid.cells[rr, cc] if 0 <= rr < grid.cells.shape[0] and 0 <= cc < grid.cells.shape[1] else None
                levels.append(int(getattr(ci, "indent", 0) or 0) if ci is not None else 0)

            # only include if it has signal
            if max(levels) <= 0 and min(levels) >= 0:
                return None
            return levels
        except Exception:
            return None

    @staticmethod
    def _detect_scale_hint(header_block: np.ndarray) -> Dict[str, Any]:
        """Detect unit/scale hints like £000 / '000 / million from headers."""
        txt = " ".join(str(v) for v in header_block.flatten().tolist() if v is not None).lower()
        if not txt:
            return {"factor": 1, "hint": None}
        if "£000" in txt or "'000" in txt or "000s" in txt or "thousand" in txt or "(000" in txt:
            return {"factor": 1000, "hint": "thousands"}
        # millions markers: "£m" / "m" adjacent to currency / explicit word
        if "million" in txt or "£m" in txt or "€m" in txt or "$m" in txt:
            return {"factor": 1_000_000, "hint": "millions"}
        return {"factor": 1, "hint": None}

    @staticmethod
    def _flatten_headers(header_block: np.ndarray, sep: str = " | ") -> List[str]:
        depth, ncols = header_block.shape
        out: List[str] = []
        for j in range(ncols):
            parts: List[str] = []
            for i in range(depth):
                v = header_block[i, j]
                if v is None:
                    continue
                s = str(v).strip()
                if not s or s.lower() in {"nan", "none"}:
                    continue
                parts.append(s)
            if not parts:
                parts = [f"col_{j}"]
            out.append(sep.join(parts))

        # uniqueness
        seen: Dict[str, int] = {}
        uniq: List[str] = []
        for h in out:
            k = seen.get(h, 0)
            if k:
                uniq.append(f"{h} ({k+1})")
            else:
                uniq.append(h)
            seen[h] = k + 1
        return uniq

    def _inspect(
        self,
        grid: SheetGrid,
        sub: np.ndarray,
        bbox: Tuple[int, int, int, int],
        header_depth: int,
        headers: List[str],
        numeric_cols: List[str],
    ) -> Dict[str, Any]:
        r0, c0, r1, c1 = bbox
        occ = (sub != None)  # noqa: E711
        filled = int(occ.sum())
        area = int(sub.shape[0] * sub.shape[1])
        density = float(filled / max(1, area))

        # Formula / merged / hidden counts within bbox (best-effort)
        formula_ct = 0
        merged_ct = 0
        hidden_ct = 0
        indent_levels: List[int] = []

        try:
            cells = grid.cells
            # Determine first non-empty label column for indentation (often 0)
            label_col = 0
            for j in range(min(3, cells.shape[1])):
                col_vals = sub[header_depth:, j]
                txt = sum(1 for v in col_vals if v is not None and coerce_accounting_number(v) is None)
                if txt >= max(2, int(0.25 * len(col_vals))):
                    label_col = j
                    break

            for rr in range(r0, r1 + 1):
                for cc in range(c0, c1 + 1):
                    ci = cells[rr, cc]
                    if ci is None:
                        continue
                    if ci.formula:
                        formula_ct += 1
                    if ci.is_merged:
                        merged_ct += 1
                    if ci.is_hidden:
                        hidden_ct += 1
                # indentation per row in chosen label col (after headers)
                if rr >= r0 + header_depth:
                    ci = cells[rr, c0 + label_col] if (c0 + label_col) < cells.shape[1] else None
                    if ci is not None:
                        indent_levels.append(int(ci.indent or 0))
        except Exception:
            pass

        totals = self._totals_check(sub, header_depth)

        return {
            "sheet": grid.name,
            "used_range": grid.used_range,
            "bbox": {"r0": int(r0), "c0": int(c0), "r1": int(r1), "c1": int(c1)},
            "shape": {"rows": int(sub.shape[0]), "cols": int(sub.shape[1])},
            "density": density,
            "header_depth": int(header_depth),
            "headers": headers,
            "numeric_cols": list(numeric_cols),
            "signals": {
                "formula_cells": int(formula_ct),
                "merged_cells": int(merged_ct),
                "hidden_cells": int(hidden_ct),
                "indent_levels": indent_levels[:250],
            },
            "totals_check": totals,
        }

    @staticmethod
    def _totals_check(sub: np.ndarray, header_depth: int) -> Dict[str, Any]:
        """Attempt to find totals rows and verify numeric consistency."""
        body = sub[header_depth:, :]
        if body.shape[0] < 3:
            return {"found": False}

        # Find candidate total rows by text in first 2 cols
        candidates = []
        for i in range(body.shape[0]):
            for j in range(min(2, body.shape[1])):
                v = body[i, j]
                if v is None:
                    continue
                s = str(v).strip().lower()
                if "total" in s and len(s) <= 40:
                    candidates.append(i)
                    break

        if not candidates:
            return {"found": False}

        # For the first candidate, test if it's a sum of previous block for numeric-ish cols
        i = candidates[0]
        row = body[i, :]
        discrepancies = []
        for j in range(body.shape[1]):
            tv = coerce_accounting_number(row[j])
            if tv is None:
                continue
            prev = [coerce_accounting_number(body[k, j]) for k in range(max(0, i - 25), i)]
            prev = [p for p in prev if p is not None]
            if len(prev) < 2:
                continue
            s = float(sum(prev))
            denom = max(1.0, abs(tv))
            pct = float(abs(tv - s) / denom)
            discrepancies.append({"col": int(j), "total": tv, "sum_prev": s, "pct_diff": pct})

        return {
            "found": True,
            "row_index": int(i),
            "tested_cols": int(len(discrepancies)),
            "discrepancies": sorted(discrepancies, key=lambda d: d["pct_diff"], reverse=True)[:25],
        }

    @staticmethod
    def _score(df: pd.DataFrame, meta: Dict[str, Any]) -> float:
        # Score dense, wide, numeric-rich tables highest.
        if df is None or df.empty:
            return 0.0
        filled = float(df.notna().sum().sum())
        area = float(df.shape[0] * df.shape[1])
        density = float(filled / max(1.0, area))
        num_cols = float(len(meta.get("numeric_cols", []) or []))
        return (density ** 1.2) * (area ** 0.6) * (1.0 + 0.15 * num_cols)
