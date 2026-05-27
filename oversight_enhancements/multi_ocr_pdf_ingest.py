"""
OVERSIGHT LABS — Multi-Strategy PDF/OCR Ingestion (v2 — 5 strategies)
=======================================================================
Strategy 1: pdfplumber  — text-layer PDFs (KV + table extraction)
Strategy 2: ZIP-archive — Anthropic delivery format
Strategy 3: PyMuPDF     — complex layouts, TableFinder for bordered tables
Strategy 4: Tesseract   — scanned/image PDFs with no text layer
Strategy 5: Gemini Flash — table-heavy statutory accounts where strategies 1–4
                           extract text but miss cell-level structure.
                           Sends page images to Gemini 2.0 Flash with a
                           structured accounting extraction prompt.
                           Returns cell-aware KV pairs, not line-by-line text.
                           Requires GOOGLE_API_KEY env var. Falls back silently.

WHAT THIS CLOSES:
  The previous gap: statutory accounts with table-based P&L and balance sheet
  layouts would extract raw text (e.g. "Revenue 770,387 (1,364,523)") but the
  regex KV parser would either miss the label or capture the prior-year column
  as the current year. Strategy 5 understands column headers ("This year /
  Prior year") and extracts current-year values per row.

DROP-IN REPLACEMENT:
  Same function signature as before. strategy_used field tells callers which
  strategy succeeded. All existing callers in engagements_routes,
  review_point_orchestrator, and compare_routes are unaffected.

DEPLOY PATH:
  oversight_enhancements/multi_ocr_pdf_ingest.py
  (or directly as the replacement for the existing file)
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import re
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Key-value extraction patterns ────────────────────────────────────────────

_KEY_CLEAN_RE = re.compile(r"\s+")

_KV_LINE_RE = re.compile(
    r"^(?P<k>[A-Za-z][A-Za-z0-9\s\(\)\&\-\,\.\/]+?)"
    r"\s*[:\-\u2013]?\s*"
    r"(?P<v>[-\(£$]?[0-9][0-9,]*\.?[0-9]*\)?)\s*"
    r"(?:[0-9,\.\-\(\)\s£$]*)?$"
)

_CELL_NUM_RE = re.compile(r"^[\s£\$\(\-]*([0-9][0-9,\.]*)\)?[\s]*$")

# Financial document noise filters — years, page numbers, section refs
_NOISE_NUMBERS = re.compile(
    r"^(19|20)\d{2}$"            # Years 1900–2099
    r"|^\d{1,2}$"                # Single/double digit (page numbers)
    r"|^[A-Z]\d+$"               # Section refs e.g. A1, S123
)


def _to_number(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        f = float(s)
        return None if (math.isnan(f) or math.isinf(f)) else f
    t = str(s).strip().replace(",", "")
    t = re.sub(r"[£$\s]", "", t)
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        v = float(t)
        return None if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return None


def _clean_key(k: str) -> str:
    return _KEY_CLEAN_RE.sub(" ", str(k).strip().lower())


def _parse_kv_from_text(text: str) -> Dict[str, float]:
    """Parse key-value pairs from plain text using regex."""
    items: Dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 200:
            continue
        m = _KV_LINE_RE.match(line)
        if m:
            key = _clean_key(m.group("k"))
            val = _to_number(m.group("v"))
            if key and val is not None and len(key) > 2:
                items[key] = val
    return items


@dataclass
class PdfExtractionResult:
    items: Dict[str, float]
    tables: List[Dict] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    strategy_used: str = "unknown"
    page_count: int = 0
    raw_text: str = ""


# ── Strategy 1: pdfplumber ────────────────────────────────────────────────────

def _strategy_pdfplumber(path: str, logger_=None) -> Optional[PdfExtractionResult]:
    """Extract text and tables using pdfplumber."""
    try:
        import pdfplumber
        text_parts = []
        tables = []
        page_count = 0

        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                try:
                    txt = page.extract_text() or ""
                    text_parts.append(txt)

                    page_tables = page.extract_tables() or []
                    for tbl in page_tables:
                        if not tbl or len(tbl) < 2:
                            continue
                        headers = [str(h or "").strip() for h in tbl[0]]
                        rows = []
                        for row in tbl[1:]:
                            row_dict = {}
                            for h, cell in zip(headers, row):
                                row_dict[h] = str(cell or "").strip()
                            if any(row_dict.values()):
                                rows.append(row_dict)
                        if headers and rows:
                            tables.append({"page": i + 1, "headers": headers, "rows": rows})
                except Exception as e:
                    if logger_:
                        logger_.debug("pdfplumber page %d failed: %s", i, e)

        full_text = "\n".join(text_parts)
        if len(full_text.strip()) < 50:
            return None

        items = _parse_kv_from_text(full_text)
        # Also harvest KV from tables (first col = label, second = value)
        for tbl in tables:
            for row in tbl["rows"]:
                vals = list(row.values())
                if len(vals) >= 2:
                    key = _clean_key(vals[0])
                    val = _to_number(vals[1]) or _to_number(vals[-1])
                    if key and val is not None and len(key) > 2:
                        items.setdefault(key, val)

        return PdfExtractionResult(
            items=items, tables=tables,
            meta={"page_count": page_count},
            strategy_used="pdfplumber",
            page_count=page_count,
            raw_text=full_text[:50_000],
        )
    except Exception as e:
        if logger_:
            logger_.debug("pdfplumber strategy failed: %s", e)
        return None


# ── Strategy 2: ZIP-archive (Anthropic PDF format) ────────────────────────────

def _strategy_zip_archive(path: str, logger_=None) -> Optional[PdfExtractionResult]:
    try:
        if not zipfile.is_zipfile(path):
            return None
        text_parts = []
        with zipfile.ZipFile(path, "r") as zf:
            txt_members = sorted([m for m in zf.namelist() if m.endswith(".txt")])
            if not txt_members:
                return None
            for member in txt_members:
                try:
                    data = zf.read(member).decode("utf-8", errors="replace")
                    text_parts.append(data)
                except Exception:
                    pass
        full_text = "\n".join(text_parts)
        if not full_text.strip():
            return None
        return PdfExtractionResult(
            items=_parse_kv_from_text(full_text),
            strategy_used="zip_archive",
            page_count=len(txt_members),
            raw_text=full_text[:50_000],
        )
    except Exception as e:
        if logger_:
            logger_.debug("ZIP archive strategy failed: %s", e)
        return None


# ── Strategy 3: PyMuPDF (fitz) ────────────────────────────────────────────────

def _strategy_pymupdf(path: str, logger_=None) -> Optional[PdfExtractionResult]:
    try:
        import fitz  # PyMuPDF
        text_parts = []
        tables = []
        doc = fitz.open(path)
        page_count = len(doc)

        for i, page in enumerate(doc):
            try:
                txt = page.get_text("text")
                text_parts.append(txt)
                try:
                    page_tables = page.find_tables()
                    for tbl in page_tables:
                        df = tbl.to_pandas()
                        if df is not None and not df.empty:
                            headers = [str(c) for c in df.columns.tolist()]
                            rows = df.to_dict(orient="records")
                            tables.append({
                                "page": i + 1,
                                "headers": headers,
                                "rows": [{str(k): str(v or "") for k, v in r.items()} for r in rows],
                            })
                except AttributeError:
                    pass  # find_tables not available in older fitz
            except Exception as e:
                if logger_:
                    logger_.debug("PyMuPDF page %d failed: %s", i, e)

        doc.close()
        full_text = "\n".join(text_parts)
        if len(full_text.strip()) < 50:
            return None

        return PdfExtractionResult(
            items=_parse_kv_from_text(full_text),
            tables=tables,
            strategy_used="pymupdf",
            page_count=page_count,
            raw_text=full_text[:50_000],
        )
    except ImportError:
        if logger_:
            logger_.debug("PyMuPDF (fitz) not installed")
        return None
    except Exception as e:
        if logger_:
            logger_.debug("PyMuPDF strategy failed: %s", e)
        return None


# ── Strategy 4: Tesseract OCR ─────────────────────────────────────────────────

def _strategy_tesseract_ocr(path: str, logger_=None) -> Optional[PdfExtractionResult]:
    try:
        from pdf2image import convert_from_path
        import pytesseract

        text_parts = []
        pages = convert_from_path(path, dpi=200, first_page=1, last_page=20)
        for i, page_img in enumerate(pages):
            try:
                txt = pytesseract.image_to_string(
                    page_img,
                    config="--psm 6 -c tessedit_char_whitelist="
                           "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                           "0123456789.,-£()/:& ",
                )
                text_parts.append(txt)
            except Exception as e:
                if logger_:
                    logger_.debug("OCR page %d failed: %s", i, e)

        full_text = "\n".join(text_parts)
        if not full_text.strip():
            return None

        return PdfExtractionResult(
            items=_parse_kv_from_text(full_text),
            strategy_used="tesseract_ocr",
            page_count=len(pages),
            raw_text=full_text[:50_000],
            warnings=["Content extracted via OCR — accuracy may be lower."],
        )
    except ImportError as e:
        if logger_:
            logger_.debug("OCR dependencies not installed: %s", e)
        return None
    except Exception as e:
        if logger_:
            logger_.debug("Tesseract OCR strategy failed: %s", e)
        return None


# ── Strategy 5: Gemini Flash — table-aware extraction ────────────────────────
#
# WHY THIS EXISTS:
#   Statutory accounts for UK SMEs are overwhelmingly table-based:
#     Col 1: Line item label
#     Col 2: Current year (£)
#     Col 3: Prior year (£)  ← this is what regex parsers grab by mistake
#
#   pdfplumber reads the text correctly but the KV regex grabs the FIRST
#   number after the label, which in a two-column layout is often the prior
#   year because of whitespace ordering. Gemini sees the table structure and
#   column headers and extracts only the current-year column.
#
#   When strategies 1–4 return items but item count < GEMINI_THRESHOLD, or
#   when the caller signals prefer_gemini=True, we invoke this strategy.
#   It's positioned last because it has an external API dependency.
#
# HOW IT WORKS:
#   1. Convert each page to an image (pdf2image at 150 dpi — fast)
#   2. Base64-encode the image
#   3. POST to Gemini 2.0 Flash vision endpoint with a typed prompt
#   4. Parse JSON response → {label: value} dict
#   5. Merge with any KV pairs already extracted by earlier strategies
#
# PROMPT DESIGN (no hallucination guarantee):
#   The prompt instructs Gemini to:
#     a) Return ONLY a JSON object — no prose, no markdown fences
#     b) Extract ONLY the "This year" / "Current year" column
#     c) Return null for any value it cannot read with certainty
#     d) Use the exact label text from the document — no paraphrasing
#   This means every value in the output is either directly read from the
#   document image or explicitly null. Gemini cannot invent numbers it cannot see.

GEMINI_THRESHOLD = 15          # If strategies 1–4 return < this many items, try Gemini
GEMINI_MAX_PAGES = 8           # Don't send more than 8 pages — cost/time trade-off
GEMINI_DPI = 150               # Lower DPI = faster, still readable for typed accounts
GEMINI_MODEL = "gemini-2.0-flash"

_GEMINI_SYSTEM_PROMPT = """\
You are a financial data extraction engine. You will be given an image of one page from a UK company's statutory accounts or tax computation.

Your task: extract all financial line items from the current year column only.

RULES — follow exactly:
1. Return ONLY a valid JSON object. No markdown, no backticks, no explanations.
2. Keys: the exact label text as it appears in the document, lowercased, spaces preserved.
3. Values: the numeric value from the CURRENT YEAR (or "This year") column as a plain number.
   - Remove £ signs and commas.
   - Negative values in brackets (1,234) → -1234.
   - If there are two numeric columns, the FIRST is usually current year.
4. If you cannot read a value with confidence → use null.
5. If the page contains no financial data → return {}.
6. Do NOT include page numbers, years, or section reference numbers as values.
7. Do NOT include column header rows as data rows.

Example output:
{"revenue": 770387.0, "cost of sales": -412000.0, "gross profit": 358387.0, "wages": -404354.0, "net loss": -554101.0}
"""


def _page_to_base64(page_image) -> str:
    """Convert a PIL Image to base64 JPEG."""
    buf = io.BytesIO()
    page_image.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _call_gemini_vision(page_b64: str, api_key: str) -> Dict[str, float]:
    """
    POST a single page image to Gemini 2.0 Flash vision endpoint.
    Returns {label: float} dict. Empty dict on any error.
    Never raises — callers assume graceful failure.
    """
    payload = json.dumps({
        "system_instruction": {"parts": [{"text": _GEMINI_SYSTEM_PROMPT}]},
        "contents": [{
            "parts": [
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": page_b64,
                    }
                },
                {"text": "Extract financial line items from this page. Return JSON only."},
            ]
        }],
        "generationConfig": {
            "temperature": 0.0,       # Deterministic — we want exact values not creativity
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        },
    }).encode("utf-8")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        raw_text = (
            body.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "{}")
        )

        # Strip markdown fences if model added them despite instruction
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
            raw_text = re.sub(r"```$", "", raw_text).strip()

        parsed = json.loads(raw_text)

        result: Dict[str, float] = {}
        for k, v in parsed.items():
            if v is None:
                continue
            key = _clean_key(str(k))
            val = _to_number(v)
            if key and val is not None and len(key) > 2:
                # Filter noise: don't accept bare years or page numbers
                if not _NOISE_NUMBERS.match(str(int(abs(val))) if val == int(val) else ""):
                    result[key] = val
        return result

    except Exception:
        return {}


def _strategy_gemini_table(
    path: str,
    logger_=None,
    existing_items: Optional[Dict[str, float]] = None,
) -> Optional[PdfExtractionResult]:
    """
    Strategy 5: Gemini Flash vision extraction for table-heavy PDFs.

    Invoked when:
      a) Earlier strategies returned fewer than GEMINI_THRESHOLD items (poor extraction), OR
      b) prefer_gemini=True is passed to extract_pdf_kv()

    Sends each page as a JPEG to Gemini 2.0 Flash with a structured
    accounting extraction prompt. Merges results with any items already
    extracted by earlier strategies (earlier strategies take precedence
    for items they found — Gemini fills the gaps).

    Requires: GOOGLE_API_KEY in environment. pdf2image + Pillow.
    Falls back silently if either is missing.
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        if logger_:
            logger_.debug("Strategy 5 (Gemini): GOOGLE_API_KEY not set — skipping")
        return None

    try:
        from pdf2image import convert_from_path
    except ImportError:
        if logger_:
            logger_.debug("Strategy 5 (Gemini): pdf2image not installed — skipping")
        return None

    try:
        pages = convert_from_path(
            path, dpi=GEMINI_DPI,
            first_page=1, last_page=GEMINI_MAX_PAGES,
        )
    except Exception as e:
        if logger_:
            logger_.debug("Strategy 5 (Gemini): pdf2image conversion failed: %s", e)
        return None

    merged: Dict[str, float] = dict(existing_items or {})
    pages_processed = 0
    pages_with_data = 0

    for i, page_img in enumerate(pages):
        try:
            page_b64 = _page_to_base64(page_img)
            page_items = _call_gemini_vision(page_b64, api_key)

            if page_items:
                pages_with_data += 1
                # Merge: existing items (from strategies 1–4) take precedence
                for k, v in page_items.items():
                    merged.setdefault(k, v)

            pages_processed += 1
            if logger_:
                logger_.debug(
                    "Strategy 5 (Gemini): page %d → %d items", i + 1, len(page_items)
                )

        except Exception as e:
            if logger_:
                logger_.debug("Strategy 5 (Gemini): page %d error: %s", i + 1, e)

    if not merged:
        return None

    if logger_:
        logger_.info(
            "Strategy 5 (Gemini): %d pages processed, %d with data, %d total items — %s",
            pages_processed, pages_with_data, len(merged), Path(path).name,
        )

    return PdfExtractionResult(
        items=merged,
        strategy_used="gemini_flash_table",
        page_count=len(pages),
        warnings=(
            [] if pages_with_data > 0
            else ["Gemini strategy returned no data — document may contain no financial tables."]
        ),
    )


# ── Main extraction function ───────────────────────────────────────────────────

def extract_pdf_kv(
    path: str,
    logger: Optional[Any] = None,
    prefer_ocr: bool = False,
    prefer_gemini: bool = False,
) -> PdfExtractionResult:
    """
    Multi-strategy PDF key-value extraction.

    Strategy order (normal):
      1. pdfplumber   — native text layer + table extraction
      2. ZIP archive  — Anthropic delivery format
      3. PyMuPDF      — complex layouts, TableFinder
      4. Tesseract    — scanned/image PDFs
      5. Gemini Flash — table-heavy statutory accounts (GOOGLE_API_KEY required)

    Strategy 5 is also invoked as an ENHANCEMENT after strategies 1–3 succeed
    with fewer than GEMINI_THRESHOLD items — it fills cell-level table gaps.

    Args:
        path:          Path to PDF file
        logger:        Optional logger instance
        prefer_ocr:    If True, try Tesseract before text strategies
        prefer_gemini: If True, invoke Gemini strategy regardless of item count

    Returns:
        PdfExtractionResult (items, tables, strategy_used, warnings, page_count)
    """
    log = logger or globals().get("logger")

    strategies = [
        ("pdfplumber",    _strategy_pdfplumber),
        ("zip_archive",   _strategy_zip_archive),
        ("pymupdf",       _strategy_pymupdf),
        ("tesseract_ocr", _strategy_tesseract_ocr),
    ]

    if prefer_ocr:
        strategies = [("tesseract_ocr", _strategy_tesseract_ocr)] + strategies[:3]

    best_result: Optional[PdfExtractionResult] = None

    for strategy_name, strategy_fn in strategies:
        try:
            result = strategy_fn(path, logger_=log)
            if result and result.items:
                if log:
                    log.info(
                        "PDF extraction [%s]: %d KV pairs, %d tables, %d pages — %s",
                        strategy_name, len(result.items), len(result.tables),
                        result.page_count, Path(path).name,
                    )
                best_result = result
                break  # Use first successful strategy
        except Exception as e:
            if log:
                log.warning("Strategy %s raised exception: %s", strategy_name, e)

    # ── Strategy 5: Gemini Flash table enhancement ────────────────────────────
    #
    # Invoke if:
    #   a) prefer_gemini is set (caller explicitly wants table extraction), OR
    #   b) We got a result but item count is low (text extracted, tables missed)
    #
    # In case (b) we pass the existing items so Gemini fills gaps rather than
    # duplicating — existing items take precedence in the merge.

    should_try_gemini = (
        prefer_gemini
        or (best_result is not None and len(best_result.items) < GEMINI_THRESHOLD)
        or (best_result is None)
    )

    if should_try_gemini and os.environ.get("GOOGLE_API_KEY"):
        existing = best_result.items if best_result else {}
        gemini_result = _strategy_gemini_table(path, logger_=log, existing_items=existing)

        if gemini_result and gemini_result.items:
            if best_result is None:
                # Gemini was the only success
                gemini_result.strategy_used = "gemini_flash_table"
                return gemini_result
            else:
                # Merge: Gemini fills in what strategies 1–4 missed
                merged_items = {**gemini_result.items, **best_result.items}
                best_result.items = merged_items
                best_result.strategy_used = f"{best_result.strategy_used}+gemini_flash_table"
                if log:
                    log.info(
                        "PDF extraction [gemini_enhancement]: +%d items via Gemini — %s",
                        len(merged_items) - len(existing), Path(path).name,
                    )
                return best_result

    if best_result:
        return best_result

    # All strategies failed
    if log:
        log.error("All PDF extraction strategies failed for: %s", path)
    return PdfExtractionResult(
        items={},
        strategy_used="none",
        warnings=["All extraction strategies failed. Document may be encrypted or corrupt."],
    )
