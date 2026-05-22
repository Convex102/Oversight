from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    _HAS_ST = True
except Exception:
    SentenceTransformer = None
    _HAS_ST = False

# ── Model configuration ────────────────────────────────────────────────────
#
# Default: ProsusAI/finbert — fine-tuned BERT on financial communications.
# Outputs 768-dimensional vectors. Understands accounting domain terminology:
# "trade debtors" ≈ "accounts receivable", "turnover" ≈ "revenue", etc.
#
# Override via env: ASGL_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
# for faster inference at lower accuracy, or any HuggingFace model name.
#
DEFAULT_MODEL = os.getenv("ASGL_EMBED_MODEL", "ProsusAI/finbert")
CACHE_DIR = Path(os.getenv("ASGL_EMBED_CACHE_DIR", ".asgl_cache/embeddings"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Model output dimensions by known model name
_MODEL_DIMS: Dict[str, int] = {
    "ProsusAI/finbert": 768,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "sentence-transformers/all-mpnet-base-v2": 768,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
}

_MODEL: Optional[Any] = None
_MODEL_NAME: Optional[str] = None


def _get_model() -> Optional[Any]:
    global _MODEL, _MODEL_NAME
    if _MODEL is not None and _MODEL_NAME == DEFAULT_MODEL:
        return _MODEL
    if not _HAS_ST:
        return None
    try:
        # FinBERT needs to be loaded via SentenceTransformer with trust
        # For finbert specifically, we wrap it as a SentenceTransformer
        _MODEL = SentenceTransformer(DEFAULT_MODEL)
        _MODEL_NAME = DEFAULT_MODEL
        return _MODEL
    except Exception:
        # Fallback to MiniLM if FinBERT unavailable (e.g. offline)
        try:
            fallback = "sentence-transformers/all-MiniLM-L6-v2"
            _MODEL = SentenceTransformer(fallback)
            _MODEL_NAME = fallback
            return _MODEL
        except Exception:
            return None


def _embed_dim() -> int:
    """Return expected embedding dimension for current model."""
    return _MODEL_DIMS.get(DEFAULT_MODEL, 768)


def _sha(payload: Any) -> str:
    b = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.npy"


def _load_cache(key: str) -> Optional[np.ndarray]:
    p = _cache_path(key)
    if p.exists():
        try:
            return np.load(p)
        except Exception:
            return None
    return None


def _save_cache(key: str, vec: np.ndarray) -> None:
    try:
        np.save(_cache_path(key), vec.astype(np.float32))
    except Exception:
        pass


def _fallback_embed(text: str) -> np.ndarray:
    """BoW fallback — only used when no model is available at all.
    Stable and deterministic but not semantically meaningful.
    """
    dim = _embed_dim()
    vec = np.zeros(dim, dtype=np.float32)
    for token in text.lower().split():
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    n = float(np.linalg.norm(vec) + 1e-8)
    return vec / n


def embed_text(text: str, tenant_id: str = "global", *, cache: bool = True) -> np.ndarray:
    """Embed text using FinBERT (default) or configured model.

    FinBERT is fine-tuned on financial news, earnings calls, and filings.
    It understands that:
      - "revenue" ≈ "turnover" ≈ "sales" ≈ "group services income"
      - "trade debtors" ≈ "accounts receivable" ≈ "receivables"
      - "operating profit" ≈ "EBIT" ≈ "profit from operations"
      - "intercompany" ≈ "IC" ≈ "related party"

    Returns a unit-norm float32 vector of shape (768,) for FinBERT.
    Cached to disk by model + tenant + text hash.
    """
    text = (text or "").strip()
    key = _sha({"t": tenant_id, "text": text, "m": DEFAULT_MODEL})
    if cache:
        cached = _load_cache(key)
        if cached is not None:
            return cached

    model = _get_model()
    if model is None:
        vec = _fallback_embed(text)
    else:
        try:
            raw = model.encode([text], normalize_embeddings=True, show_progress_bar=False)
            vec = np.asarray(raw[0], dtype=np.float32)
        except Exception:
            vec = _fallback_embed(text)

    if cache:
        _save_cache(key, vec)
    return vec


def embed_batch(
    texts: List[str],
    tenant_id: str = "global",
    *,
    cache: bool = True,
    batch_size: int = 64,
) -> np.ndarray:
    """Embed a list of texts efficiently using batched inference.

    Checks cache per-item, batches uncached items for model inference.
    Returns array of shape (len(texts), embed_dim).
    """
    results: List[Optional[np.ndarray]] = [None] * len(texts)
    uncached_indices: List[int] = []
    uncached_texts: List[str] = []

    for i, text in enumerate(texts):
        text = (text or "").strip()
        key = _sha({"t": tenant_id, "text": text, "m": DEFAULT_MODEL})
        cached = _load_cache(key) if cache else None
        if cached is not None:
            results[i] = cached
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)

    if uncached_texts:
        model = _get_model()
        for batch_start in range(0, len(uncached_texts), batch_size):
            batch = uncached_texts[batch_start:batch_start + batch_size]
            if model is not None:
                try:
                    vecs = model.encode(
                        batch,
                        normalize_embeddings=True,
                        show_progress_bar=False,
                        batch_size=batch_size,
                    )
                    vecs = np.asarray(vecs, dtype=np.float32)
                except Exception:
                    vecs = np.stack([_fallback_embed(t) for t in batch])
            else:
                vecs = np.stack([_fallback_embed(t) for t in batch])

            for j, vec in enumerate(vecs):
                idx = uncached_indices[batch_start + j]
                results[idx] = vec
                if cache:
                    orig_text = uncached_texts[batch_start + j]
                    key = _sha({"t": tenant_id, "text": orig_text, "m": DEFAULT_MODEL})
                    _save_cache(key, vec)

    return np.stack([r if r is not None else _fallback_embed("") for r in results])


def _summarize_values(values: Sequence[Any], max_items: int = 16) -> str:
    out: List[str] = []
    for v in values[:max_items]:
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        out.append(s[:80])
    return " | ".join(out)


def embed_column(
    header: str,
    sample_values: Sequence[Any],
    tenant_id: str,
    *,
    dtype: Optional[str] = None,
    vertical: str = "A",
    cache: bool = True,
) -> np.ndarray:
    """Embed a spreadsheet column using header + sample values + metadata."""
    header = (header or "").strip()
    vals = _summarize_values(sample_values)
    payload = f"vertical={vertical};dtype={dtype or 'unknown'};header={header};values={vals}"
    return embed_text(payload, tenant_id=tenant_id, cache=cache)
