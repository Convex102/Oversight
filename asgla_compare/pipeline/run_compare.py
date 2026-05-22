from __future__ import annotations
import os
from dataclasses import asdict
from typing import Optional, Dict, Any

from asgla_compare.ingest.excel_ingest import extract_excel_kv
from asgla_compare.ingest.pdf_ingest import extract_pdf_kv
from asgla_compare.compare.diff_engine import compare_kv
from asgla_compare.report.report_writer import write_csv, write_pdf

def run(excel_path: str,
        pdf_path: str,
        out_dir: str,
        logger,
        abs_tolerance: float = 0.5,
        pct_tolerance: float = 0.5,
        min_match_score: float = 75.0) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)

    logger.info("Starting compare run")
    logger.info("Excel: %s", excel_path)
    logger.info("PDF: %s", pdf_path)

    ex = extract_excel_kv(excel_path, logger=logger)
    pd = extract_pdf_kv(pdf_path, logger=logger)

    if not pd.items:
        logger.error("No PDF items extracted. See warnings: %s", pd.warnings)
    if not ex.items:
        logger.error("No Excel items extracted. See warnings: %s", ex.warnings)

    rows = compare_kv(ex.items, pd.items,
                      abs_tolerance=abs_tolerance,
                      pct_tolerance=pct_tolerance,
                      min_match_score=min_match_score)

    csv_path = os.path.join(out_dir, "diff_report.csv")
    pdf_out_path = os.path.join(out_dir, "diff_report.pdf")
    write_csv(rows, csv_path)
    write_pdf(rows, pdf_out_path)

    summary = {
        "excel_items": len(ex.items),
        "pdf_items": len(pd.items),
        "diff_rows": len(rows),
        "csv": csv_path,
        "pdf": pdf_out_path,
        "excel_warnings": ex.warnings,
        "pdf_warnings": pd.warnings,
    }
    logger.info("Compare completed: %s", summary)
    return summary
