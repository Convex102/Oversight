# ASGL-A Compare Patch (Excel working papers vs PDF output)

This patch adds a small, self-contained module that lets an accountant:
- Pick an Excel working paper file and a PDF output file (e.g., TaxCalc/SA302/accounts)
- Extract key-value financial items from both sources
- Align keys with fuzzy matching
- Produce a difference report (CSV + PDF)
- Log everything (debug-friendly)

## Quick start (GUI)
```bash
python asgla_compare_app.py
```

## Quick start (CLI)
```bash
python asgla_compare_app.py --excel path/to/working_papers.xlsx --pdf path/to/output.pdf --out out_dir --debug
```

## Outputs
- `diff_report.csv` : row-by-row differences
- `diff_report.pdf` : accountant-friendly exceptions report
- `logs/asgla_compare.log` : detailed logs

## Dependencies
- Python 3.9+
- `pandas`, `openpyxl` for Excel
- `reportlab` for PDF report output
- Optional: `pdfplumber` for PDF text extraction (recommended)
- Optional: `rapidfuzz` for improved fuzzy matching (falls back to stdlib difflib)

Install optional deps:
```bash
pip install pdfplumber rapidfuzz
```

## Integration notes
The code is designed to be dropped into an existing repo without breaking anything:
- New files only
- No changes to existing modules required
- Logging is isolated and configurable

If you want deeper integration with existing ASGL/ASGL-A extractors, swap the extractor functions in:
- `asgla_compare/ingest/excel_ingest.py`
- `asgla_compare/ingest/pdf_ingest.py`
