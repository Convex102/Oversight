
"""
ASGL-A Popup Runner v3
---------------------
Supports:
- Excel (.xlsx, .xls)
- CSV (.csv)
- PDF (.pdf)

Handles:
- Ingestion
- Comparison
- Excel → PDF workflows (working papers → PDF accounts)

Delegates all logic to review_intelligence routers.
"""

from asgl_a.extensions.review_intelligence.ingestion_router import route_ingestion
from asgl_a.extensions.review_intelligence.comparison_router import route_comparison

def run_single(path: str):
    """Run ingestion for a single file."""
    result = route_ingestion(path)
    return {
        "mode": "single",
        "result": result
    }

def run_comparison(path_a: str, path_b: str):
    """Run comparison between two files (Excel/CSV/PDF)."""
    result = route_comparison(path_a, path_b)
    return {
        "mode": "comparison",
        "result": result
    }

def run_excel_to_pdf(excel_path: str, pdf_path: str):
    """
    Explicit working-paper (Excel) → PDF accounts comparison.
    """
    return run_comparison(excel_path, pdf_path)
