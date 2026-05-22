from __future__ import annotations
from typing import Any, Dict, Optional, Callable
import importlib

def try_import(path: str):
    try:
        return importlib.import_module(path)
    except Exception:
        return None

def get_callable(module_paths, fn_names):
    for mp in module_paths:
        mod = try_import(mp)
        if not mod: 
            continue
        for fn in fn_names:
            if hasattr(mod, fn) and callable(getattr(mod, fn)):
                return getattr(mod, fn)
    return None

def discover_ingestion():
    return {
        "extract_spreadsheet": get_callable(
            ["app.ingestion.spreadsheet","app.ingestion.spreadsheet_ingestion","app.ingestion.excel","app.asgl_a.ingestion"],
            ["extract_spreadsheet","ingest_spreadsheet","parse_workbook","extract_cells"]
        ),
        "extract_pdf": get_callable(
            ["app.ingestion.pdf","app.ingestion.pdf_ingestion","app.ingestion.documents"],
            ["extract_pdf","parse_pdf","extract_pdf_text"]
        ),
        "extract_tables": get_callable(
            ["app.ingestion.tables","app.ingestion.table_extractor","app.ingestion.pdf_tables"],
            ["extract_tables","parse_tables","extract_pdf_tables"]
        ),
    }