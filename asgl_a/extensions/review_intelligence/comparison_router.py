
from .file_picker import pick_file

def route_comparison(path_a: str, path_b: str):
    a = pick_file(path_a)
    b = pick_file(path_b)

    if a["type"] != b["type"]:
        return {
            "status": "warning",
            "message": "Comparing different file types",
            "a": a["type"],
            "b": b["type"]
        }

    if a["type"] == "excel":
        return compare_excel(a, b)
    if a["type"] == "csv":
        return compare_csv(a, b)
    if a["type"] == "pdf":
        return compare_pdf(a, b)

    raise ValueError("Unsupported comparison type")

def compare_excel(a, b):
    return {"status": "ok", "type": "excel", "diffs": []}

def compare_csv(a, b):
    return {"status": "ok", "type": "csv", "diffs": []}

def compare_pdf(a, b):
    return {"status": "ok", "type": "pdf", "diffs": []}
