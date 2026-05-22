
from pathlib import Path

SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf"}

def pick_file(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {p.suffix}. "
            f"Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    return {
        "path": str(p),
        "extension": p.suffix.lower(),
        "type": infer_type(p.suffix.lower())
    }

def infer_type(ext: str):
    if ext in {".xlsx", ".xls"}:
        return "excel"
    if ext == ".csv":
        return "csv"
    if ext == ".pdf":
        return "pdf"
    return "unknown"
