
from .file_picker import pick_file

def route_ingestion(path: str):
    picked = pick_file(path)

    if picked["type"] == "excel":
        return ingest_excel(picked)
    if picked["type"] == "csv":
        return ingest_csv(picked)
    if picked["type"] == "pdf":
        return ingest_pdf(picked)

    raise ValueError("No ingestion route available")

def ingest_excel(meta):
    return {"status": "ok", "source": "excel", "data": {}}

def ingest_csv(meta):
    return {"status": "ok", "source": "csv", "data": {}}

def ingest_pdf(meta):
    return {"status": "ok", "source": "pdf", "data": {}}
