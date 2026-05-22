from __future__ import annotations
import re
import math
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import pandas as pd

@dataclass
class ExcelExtractionResult:
    items: Dict[str, float]
    meta: Dict[str, Any]
    warnings: List[str]

_CURRENCY_RE = re.compile(r'[^0-9\-\.]+')
_KEY_CLEAN_RE = re.compile(r'\s+')
_NOMINAL_CODE_RE = re.compile(r'^\d{3,5}$')

# Labels that are WP workings/schedule headers, not financial account descriptions
_WP_WORKINGS_LABELS = {
    "wdv of fixed assets per comps", "wdv of fixed assets per computations",
    "nbv of fixed assets per comps", "nbv of fixed assets per computations",
    "nbv of fixed assets per a/c's", "nbv of fixed assets per accounts",
    "timing difference", "timing differences",
    "deferred tax on timing difference", "deferred tax on timing differences",
    "tax written down value", "written down value",
    "aia claimed", "aia claim", "annual investment allowance",
    "balance to wda", "wdv c/fwd", "wdv b/fwd",
    "pool additions", "general pool",
    "additions qualifying for aia", "additions qualifying",
    "n/c", "n/c:", "nominal code", "time:", "ref:", "date:", "page:",
    "balance b/fwd", "balance c/fwd", "balance brought forward",
    "cost b/fwd", "cost c/fwd", "additions in year", "disposals in year",
    "depreciation b/fwd", "depreciation c/fwd", "charge for year", "nbv c/fwd",
}

_WP_PHRASES = [
    "per comps", "per computations", "per a/c",
    "timing difference", "tax written down",
    "wdv of fixed", "nbv of fixed assets per",
    "aia claimed", "balance to wda",
]

def _is_financial_label(label: str) -> bool:
    label = label.strip()
    if len(label) < 4:
        return False
    if label.endswith(":"):
        return False
    try:
        float(label.replace(",", ""))
        return False
    except ValueError:
        pass
    label_lower = label.lower()
    if label_lower in _WP_WORKINGS_LABELS:
        return False
    for phrase in _WP_PHRASES:
        if phrase in label_lower:
            return False
    return True

def _is_financial_value(value: float) -> bool:
    if value is None or math.isnan(value) or math.isinf(value):
        return False
    return True

def _to_number(x) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        v = float(x)
        return None if (math.isnan(v) or math.isinf(v)) else v
    s = str(x).strip()
    if not s or s.lower() == 'nan':
        return None
    s = s.replace(",", "")
    s = _CURRENCY_RE.sub("", s)
    try:
        return float(s)
    except Exception:
        return None

def _clean_key(k: str) -> str:
    k = str(k).strip().lower()
    k = _KEY_CLEAN_RE.sub(" ", k)
    return k

def _extract_sage_tb(df: pd.DataFrame, items: Dict[str, float]) -> int:
    """Extract from Sage Trial Balance format:
    Col[1]=Nominal Code (4 digits), Col[2]=Account Name, Col[3]=Debit, Col[4]=Credit
    Net value = Debit - Credit
    """
    count = 0
    for i in range(len(df)):
        row = df.iloc[i].tolist()
        if len(row) < 4:
            continue
        code_raw = str(row[1]).strip() if row[1] is not None else ''
        name_raw = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ''
        
        if not _NOMINAL_CODE_RE.match(code_raw):
            continue
        if len(name_raw) < 3 or name_raw.lower() == 'nan':
            continue
        
        debit  = _to_number(row[3]) or 0.0
        credit = _to_number(row[4]) if len(row) > 4 else 0.0
        credit = credit or 0.0
        net = debit - credit
        
        if net == 0.0 and debit == 0.0:
            continue
        
        key = _clean_key(name_raw)
        if key not in items:
            items[key] = net
            count += 1
    return count

def _extract_columnar(df: pd.DataFrame, items: Dict[str, float]) -> int:
    """Extract from generic columnar layout: scan all column pairs for label+value."""
    count = 0
    n_cols = len(df.columns)
    
    for i in range(len(df)):
        row = df.iloc[i].tolist()
        
        # Try all label+value column combinations
        for label_col in range(min(3, n_cols)):
            label_raw = row[label_col] if label_col < len(row) else None
            if not isinstance(label_raw, str) or len(label_raw.strip()) < 4:
                continue
            if not _is_financial_label(label_raw.strip()):
                continue
            
            # Look for a numeric value in adjacent or later columns
            for val_col in range(label_col + 1, min(label_col + 8, n_cols)):
                val_raw = row[val_col] if val_col < len(row) else None
                num = _to_number(val_raw)
                if num is not None and _is_financial_value(num):
                    key = _clean_key(label_raw.strip())
                    if key not in items:
                        items[key] = num
                        count += 1
                    break  # found a value for this label, move to next row label
    return count

def extract_excel_kv(path: str, logger=None, max_rows: int = 5000) -> ExcelExtractionResult:
    """Extract financial key-value pairs from a working papers Excel file.
    
    Strategy:
    1. If sheet contains Sage TB format (col[1]=nominal code, col[2]=name, col[3/4]=debit/credit)
       extract using Sage TB parser for clean net balances
    2. For all other sheets, scan all column pairs for label+value patterns
    
    Filters out WP workings labels, template headers, and short/invalid values.
    """
    items: Dict[str, float] = {}
    warnings: List[str] = []
    xl = pd.ExcelFile(path)
    meta = {"sheets": xl.sheet_names}
    sage_tb_extracted = 0

    for sheet in xl.sheet_names:
        try:
            df = xl.parse(sheet, header=None, nrows=max_rows)
        except Exception as e:
            warnings.append(f"Failed to read sheet '{sheet}': {e}")
            continue

        if len(df) < 3:
            continue

        # Detect Sage TB format: look for rows where col[1] is a 4-digit nominal code
        sample = df.head(30)
        sage_rows = 0
        for i in range(len(sample)):
            row = sample.iloc[i].tolist()
            if len(row) > 2:
                code = str(row[1]).strip() if row[1] is not None else ''
                if _NOMINAL_CODE_RE.match(code):
                    sage_rows += 1
        
        if sage_rows >= 3:
            # This sheet is a Sage TB - use the precise extractor
            n = _extract_sage_tb(df, items)
            sage_tb_extracted += n
            if logger:
                logger.info("Sage TB extraction from sheet '%s': %d items", sheet, n)
        else:
            # Use generic columnar extractor for other sheets
            _extract_columnar(df, items)

    if logger:
        logger.info(
            "Excel extraction: %d items total (%d from Sage TB) from %d sheets",
            len(items), sage_tb_extracted, len(meta["sheets"])
        )

    return ExcelExtractionResult(items=items, meta=meta, warnings=warnings)

