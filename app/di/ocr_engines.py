from app.vision.vision import extract_document
import time

def run_engine(engine: str, pdf_path: str):
    with open(pdf_path, "rb") as f:
        content = f.read()
    result = extract_document(content, pdf_path)
    return {
        "engine": engine,
        "pages": len(result.get("pages", [])),
        "text": result.get("text", ""),
        "blocks": result.get("tables", []),
        "confidence": result.get("confidence", 0.0),
        "ts": int(time.time())
    }

def ocr_fallback(pdf_path: str, engines=None):
    with open(pdf_path, "rb") as f:
        content = f.read()
    result = extract_document(content, pdf_path)
    if result.get("success"):
        return {
            "ok": True,
            "engine": result.get("method", "unified"),
            "pages": len(result.get("pages", [])),
            "text": result.get("text", ""),
            "blocks": result.get("tables", []),
            "confidence": result.get("confidence", 0.0),
        }
    else:
        return {"ok": False, "error": result.get("error", "OCR failed")}
