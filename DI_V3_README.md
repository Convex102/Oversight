# DI v3 Operational Patch

This patch replaces the DI wiring with `run_di_v3_wired` that attempts to call your repo's
existing ingestion modules automatically using discovery in `app/di/adapters.py`.

To make it fully operational with your ingestion implementation, expose one of these functions:
- app.ingestion.spreadsheet.extract_spreadsheet(spreadsheet_path, mapping=...)
- app.ingestion.pdf.extract_pdf(pdf_path)
- app.ingestion.tables.extract_tables(pdf_path)

Each function may return either:
- dict[field] = value
or
- dict[field] = {value, sheet, addr, confidence}

DI v3 will convert these into FactBundle facts with evidence objects.