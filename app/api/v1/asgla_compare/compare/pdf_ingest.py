from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

@dataclass
class PdfExtractionResult:
    items: Dict[str, float]
    meta: Dict[str, Any]
    warnings: List[str]

_CURRENCY_RE = re.compile(r"[^0-9\-\.]+")
_KEY_CLEAN_RE = re.compile(r"\s+")
# Matches patterns like: "Total income received 102,161.00" or "Total tax due: Â£13,991.20"
# Captures the FIRST number after the label â€” avoids picking up prior-year comparatives
# e.g. "Group services 920,000 900,000" â†’ key="group services", value=920000 (not 900000)
_KV_LINE_RE = re.compile(
    r"^(?P<k>[A-Za-z][A-Za-z0-9\s\(\)\&\-\,\.\/]+?)"  # label
    r"\s*[:\-\u2013]?\s*"                                    # optional separator
    r"(?P<v>[-\(Â£$]?[0-9][0-9,]*\.?[0-9]*\)?)\s*"          # FIRST number
    r"(?:[0-9,\.\-\(\)\sÂ£$]*)?$"                           # ignore rest (prior year)
)

def _to_number(s: str) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip()
    if not t:
        return None
    t = t.replace(",", "")
    t = _CURRENCY_RE.sub("", t)
    try:
        return float(t)
    except Exception:
        return None

def _clean_key(k: str) -> str:
    k = str(k).strip().lower()
    k = _KEY_CLEAN_RE.sub(" ", k)
    return k

def _extract_text_pdfplumber(path: str, logger=None) -> str:
    try:
        import pdfplumber  # optional
    except Exception as e:
        raise RuntimeError("pdfplumber not installed. Run: pip install pdfplumber") from e

    text_parts = []
    with pdfplumber.open(path) as pdf:
        for idx, page in enumerate(pdf.pages):
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            text_parts.append(txt)
    if logger:
        logger.info("PDF text extracted with pdfplumber. pages=%d", len(text_parts))
    return "\n".join(text_parts)

def _extract_tb_nominal_kv(lines: list) -> dict:
    """Extract nominal code key-value pairs from a Trial Balance PDF.
    
    TB lines have format: "CODE Description CY_VALUE PY_VALUE Variance% Direction"
    e.g. "3000 Wages and salaries 382,602.59 382,715.72 (113.13) - Decrease"
    
    Returns dict with keys like:
      "tb.3000": 382602.59   (current year)
      "prior_year.3000": 382715.72  (prior year)
      "tb.wages_and_salaries": 382602.59  (by name)
    """
    import re
    items = {}
    # Pattern: starts with 4-digit nominal code
    TB_LINE = re.compile(
        r'^(?P<code>\d{4})\s+'
        r'(?P<desc>[A-Za-z][A-Za-z0-9\s\(\)\-\.\&,/]+?)\s+'
        r'(?P<cy>[\(\-]?[0-9][0-9,]*\.?\d{0,2}\)?)\s+'
        r'(?P<py>[\(\-]?[0-9][0-9,]*\.?\d{0,2}\)?)\s*'
    )
    def _parse_tb_val(s):
        s = s.strip()
        negative = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        try:
            v = float(s.replace(",", ""))
            return -v if negative else v
        except ValueError:
            return None

    for ln in lines:
        ln = ln.strip()
        m = TB_LINE.match(ln)
        if not m:
            continue
        code = m.group("code")
        desc = m.group("desc").strip().lower().replace(" ", "_").replace("/", "_")
        cy = _parse_tb_val(m.group("cy"))
        py = _parse_tb_val(m.group("py"))
        if cy is not None:
            items[f"tb.{code}"] = cy
            items[f"tb.{desc}"] = cy
        if py is not None:
            items[f"prior_year.{code}"] = py
            items[f"prior_year.{desc}"] = py
    return items


# Values that look like non-financial numbers in a PDF
# Years: 2006 (Companies Act), 2024, 2025
# Page/note numbers: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
# Company registration numbers: 6-8 digit integers
# Section references: typically < 20

def _is_financial_pdf_value(key: str, value: float) -> bool:
    """Filter out non-financial numbers extracted from FS PDFs.
    
    Removes: years (2006, 2025), page/note numbers (1-20),
    company registration numbers ON REGISTRATION KEYS ONLY,
    and non-financial key patterns.
    """
    import math
    if value is None or math.isnan(value) or math.isinf(value):
        return False
    
    key_lower = key.lower().strip()
    
    # Filter keys that are clearly not financial line items
    # Check these FIRST before numeric filters
    _NON_FINANCIAL_KEYS = [
        "registered number", "company number", "registration number",
        "as at 31 january", "as at 31 december", "as at 31 march",
        "as at 28 february", "as at 30 april", "as at 30 june",
        "year ended", "period ended", "for the year",
        "companies act", "section 476", "section 477", "section 479",
        "regime within part", "members have not required",
        "accountant", "preparer", "reviewer", "cfo",
        "note", "notes", "reference", "ref:",
        "page ", "page	",
    ]
    for bad in _NON_FINANCIAL_KEYS:
        if bad in key_lower:
            return False
    
    # Filter years — but ONLY for short keys (dates, headers)
    # Long keys with a year-like value are fine (e.g. "profit 2024 to 2025")
    if 1900 <= value <= 2030 and value == int(value) and len(key_lower) < 40:
        return False
    
    # Filter small integers likely to be page/note numbers (1-20)
    if 1 <= value <= 20 and value == int(value):
        return False
    
    # Filter very small values (template placeholders)
    if 0 < abs(value) < 1.0:
        return False
    
    # Company registration numbers: only filter if key EXPLICITLY mentions registration
    _REG_KEYS = {"registered number", "company number", "registration number", "company no"}
    if any(rk in key_lower for rk in _REG_KEYS):
        return False
    
    return True


def extract_pdf_kv(path: str, logger=None) -> PdfExtractionResult:
    """Best-effort PDF extraction:
    - Extract text (pdfplumber)
    - Parse line-by-line for key/value numeric pairs
    - Filters out years, page numbers, registration numbers, section references
    """
    warnings: List[str] = []
    items: Dict[str, float] = {}

    try:
        text = _extract_text_pdfplumber(path, logger=logger)
    except Exception as e:
        warnings.append(str(e))
        if logger: logger.exception("PDF extraction failed")
        return PdfExtractionResult(items={}, meta={"method": "none"}, warnings=warnings)

    lines = [ln.strip() for ln in text.splitlines() if ln and ln.strip()]
    meta = {"method": "pdfplumber", "lines": len(lines)}

    for ln in lines:
        m = _KV_LINE_RE.match(ln)
        if not m:
            continue
        k = _clean_key(m.group("k"))
        v = _to_number(m.group("v"))
        if v is None:
            continue
        if not _is_financial_pdf_value(k, v):
            continue
        if k not in items:
            items[k] = v

    if logger:
        logger.info("PDF extraction complete: %d items (parsed from %d lines)", len(items), len(lines))
        for w in warnings:
            logger.warning(w)

    return PdfExtractionResult(items=items, meta=meta, warnings=warnings)

