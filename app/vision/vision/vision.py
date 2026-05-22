"""
Unified Vision / OCR Module for Oversight Platform
===================================================
Fallback order: Tesseract → Gemini → Ollama

Configuration via .env:
    OCR_BACKEND = "auto" (default) | "tesseract" | "gemini" | "ollama"
    OCR_FALLBACK_ORDER = "tesseract,gemini,ollama" (customisable)
    
    GEMINI_API_KEY = "your_key"
    OLLAMA_HOST = "http://localhost:11434"
    OLLAMA_MODEL = "llava"  # or "moondream", "bakllava"
    
    TESSERACT_CMD = "tesseract"  # path if not in PATH
"""

import os
import base64
import logging
import subprocess
from io import BytesIO
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger("Oversight.Vision")

# ── Configuration ──────────────────────────────────────────────────────────
OCR_BACKEND = os.getenv("OCR_BACKEND", "auto").lower()
# Default fallback order: Tesseract → Gemini → Ollama
DEFAULT_FALLBACK = ["tesseract", "gemini", "ollama"]
FALLBACK_ORDER = [b.strip() for b in os.getenv("OCR_FALLBACK_ORDER", ",".join(DEFAULT_FALLBACK)).split(",") if b.strip()]

# Tesseract
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "tesseract")
TESSERACT_AVAILABLE = False

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_AVAILABLE = False

# Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llava")  # llava, moondream, bakllava
OLLAMA_AVAILABLE = False

# ── Backend availability checks ────────────────────────────────────────────
def _check_tesseract() -> bool:
    try:
        result = subprocess.run([TESSERACT_CMD, "--version"], capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False

def _check_gemini() -> bool:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        # Quick test
        genai.list_models()
        return True
    except Exception:
        return False

def _check_ollama() -> bool:
    try:
        import requests
        resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=2)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            return any(m.get("name", "").startswith(OLLAMA_MODEL) for m in models)
        return False
    except Exception:
        return False

# Run checks at import
TESSERACT_AVAILABLE = _check_tesseract()
if GEMINI_API_KEY:
    GEMINI_AVAILABLE = _check_gemini()
OLLAMA_AVAILABLE = _check_ollama()

logger.info(f"OCR backends: Tesseract={TESSERACT_AVAILABLE}, Gemini={GEMINI_AVAILABLE}, Ollama={OLLAMA_AVAILABLE}")

# ── Backend implementations ────────────────────────────────────────────────

def _ocr_with_tesseract(image_bytes: bytes) -> Dict[str, Any]:
    """Extract text from image using Tesseract (local)."""
    try:
        from PIL import Image
        img = Image.open(BytesIO(image_bytes))
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
            img.save(tmp_path)
        result = subprocess.run(
            [TESSERACT_CMD, tmp_path, "stdout", "-l", "eng"],
            capture_output=True, text=True
        )
        os.unlink(tmp_path)
        text = result.stdout.strip()
        return {
            "success": True,
            "text": text,
            "tables": [],
            "method": "tesseract",
            "confidence": 0.8 if len(text) > 50 else 0.4,
            "pages": 1
        }
    except Exception as e:
        logger.exception("Tesseract failed")
        return {"success": False, "error": str(e), "text": "", "tables": []}


def _ocr_with_gemini(image_bytes: bytes, filename: str = "image") -> Dict[str, Any]:
    """Extract text and tables using Gemini multimodal."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = """
        Extract all text and tabular data from this image.
        Return JSON: {"text": "...", "tables": [{"headers": [...], "rows": [[...]]}]}
        """
        response = model.generate_content([
            prompt,
            {"mime_type": "image/png", "data": image_b64}
        ])
        import json
        raw = response.text
        # Parse JSON from markdown
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        data = json.loads(raw)
        return {
            "success": True,
            "text": data.get("text", ""),
            "tables": data.get("tables", []),
            "method": "gemini",
            "confidence": 0.95,
            "pages": 1
        }
    except Exception as e:
        logger.exception("Gemini failed")
        return {"success": False, "error": str(e), "text": "", "tables": []}


def _ocr_with_ollama(image_bytes: bytes) -> Dict[str, Any]:
    """Extract text using Ollama vision model (LLaVA/Moondream)."""
    try:
        import requests
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = "Extract all text from this image. Return only the extracted text."
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False
        }
        resp = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=30)
        if resp.status_code == 200:
            text = resp.json().get("response", "").strip()
            return {
                "success": True,
                "text": text,
                "tables": [],
                "method": "ollama",
                "confidence": 0.85 if len(text) > 50 else 0.5,
                "pages": 1
            }
        else:
            return {"success": False, "error": f"Ollama HTTP {resp.status_code}", "text": ""}
    except Exception as e:
        logger.exception("Ollama failed")
        return {"success": False, "error": str(e), "text": ""}


# ── Unified OCR with fallback chain ────────────────────────────────────────

def extract_text_from_image(image_bytes: bytes, filename: str = "image") -> Dict[str, Any]:
    """
    Extract text and tables from image bytes using the configured backend(s).
    Falls back through FALLBACK_ORDER (default: tesseract → gemini → ollama).
    """
    # Determine order of backends to try
    if OCR_BACKEND == "auto":
        backends_to_try = FALLBACK_ORDER
    else:
        backends_to_try = [OCR_BACKEND]
    
    for backend in backends_to_try:
        if backend == "tesseract" and TESSERACT_AVAILABLE:
            result = _ocr_with_tesseract(image_bytes)
            # Accept if we got substantial text (or any text if not huge image?)
            if result.get("success") and len(result.get("text", "")) > 50:
                return result
        elif backend == "gemini" and GEMINI_AVAILABLE:
            result = _ocr_with_gemini(image_bytes, filename)
            if result.get("success"):
                return result
        elif backend == "ollama" and OLLAMA_AVAILABLE:
            result = _ocr_with_ollama(image_bytes)
            if result.get("success") and len(result.get("text", "")) > 50:
                return result
        # else backend not available or failed, continue
    
    # Final fallback if nothing worked
    return {"success": False, "error": "All OCR backends failed", "text": "", "tables": []}


def extract_text_from_pdf(pdf_bytes: bytes, use_ocr: bool = True) -> Dict[str, Any]:
    """
    Extract text from PDF. If use_ocr=True and PDF is scanned/image-based,
    convert first pages to images and run OCR.
    """
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(BytesIO(pdf_bytes))
        # Try to extract text directly
        full_text = []
        pages = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            full_text.append(txt)
            pages.append({"page": len(pages)+1, "text": txt})
        extracted = "\n".join(full_text)
        if len(extracted.strip()) > 500 or not use_ocr:
            return {"success": True, "text": extracted, "pages": pages, "method": "pypdf2", "tables": []}
        # Scanned PDF – convert to images
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(pdf_bytes, dpi=150, first_page=1, last_page=10)
            ocr_results = []
            ocr_pages = []
            for idx, img in enumerate(images):
                buf = BytesIO()
                img.save(buf, format="PNG")
                ocr = extract_text_from_image(buf.getvalue(), f"pdf_page_{idx+1}.png")
                if ocr.get("success"):
                    ocr_results.append(ocr.get("text", ""))
                    ocr_pages.append({"page": idx+1, "text": ocr.get("text", "")})
            return {
                "success": True,
                "text": "\n".join(ocr_results),
                "pages": ocr_pages,
                "method": "ocr_scanned_pdf",
                "tables": []
            }
        except Exception as e:
            logger.warning(f"PDF to image conversion failed: {e}")
            return {"success": True, "text": extracted, "pages": pages, "method": "pypdf2_partial", "tables": []}
    except Exception as e:
        logger.exception("PDF extraction failed")
        return {"success": False, "error": str(e), "text": "", "pages": []}


def extract_document(content: bytes, filename: str) -> Dict[str, Any]:
    """
    Unified entry point for DI engine and pipeline.
    Detects file type and calls appropriate extractor.
    """
    ext = os.path.splitext(filename.lower())[1]
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff"):
        return extract_text_from_image(content, filename)
    elif ext == ".pdf":
        return extract_text_from_pdf(content, use_ocr=True)
    else:
        # Excel/CSV – not processed by OCR
        return {"success": True, "text": "", "method": "skip", "note": "Not an image or PDF", "pages": []}
