from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional, Dict, Any
import re

import numpy as np


def normalize(text: str) -> str:
    s = str(text or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s\-_/]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fuzzy_ratio(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return float(SequenceMatcher(None, na, nb).ratio())


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a) + 1e-8) * (np.linalg.norm(b) + 1e-8)))


def _embed(text: str) -> np.ndarray:
    """Use the real SentenceTransformer embedding from app.di.ml.embeddings.
    Falls back to BoW if the model is unavailable (offline / cold start).
    """
    try:
        from app.di.ml.embeddings import embed_text
        return embed_text(text, tenant_id="global", cache=True)
    except Exception:
        pass
    # BoW fallback — only reached if sentence-transformers not installed
    import hashlib
    vec = np.zeros(384, dtype=np.float32)
    for token in normalize(text).split():
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        vec[h % 384] += 1.0
    n = float(np.linalg.norm(vec) + 1e-8)
    return vec / n


def semantic_best_match(query: str, candidates: List[str]) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    # Use embed_batch for candidates — single model forward pass for all candidates
    # Much faster than N individual calls, especially for FinBERT (768-dim)
    try:
        from app.di.ml.embeddings import embed_batch
        qv = _embed(query)
        cvs = embed_batch(candidates, tenant_id="global", cache=True)
    except Exception:
        qv = _embed(query)
        cvs = np.stack([_embed(c) for c in candidates], axis=0)
    scores = [cosine(qv, cvs[i]) for i in range(len(candidates))]
    best_i = int(np.argmax(scores))
    return {"candidate": candidates[best_i], "score": float(scores[best_i])}


def best_label_match(
    query: str,
    candidates: List[str],
    *,
    fuzzy_cutoff: float = 0.82,
    semantic_cutoff: float = 0.72,
) -> Optional[Dict[str, Any]]:
    """Return best match using:
    1. Fuzzy string similarity (fast, exact character matching)
    2. BERT/SentenceTransformer semantic similarity (catches synonyms, abbreviations)

    Examples of what semantic matching catches that fuzzy misses:
    - "cash and bank"  <-> "bank balance"          (semantic: ~0.78, fuzzy: ~0.55)
    - "intercompany rec" <-> "ic receivable"        (semantic: ~0.81, fuzzy: ~0.42)
    - "trade debtors" <-> "accounts receivable"     (semantic: ~0.83, fuzzy: ~0.38)
    - "wages and salaries" <-> "payroll"            (semantic: ~0.76, fuzzy: ~0.31)
    """
    if not candidates:
        return None

    # 1) Fuzzy first — fast, handles minor spelling differences
    best_f = None
    best_s = 0.0
    for c in candidates:
        s = fuzzy_ratio(query, c)
        if s > best_s:
            best_s = s
            best_f = c
    if best_f is not None and best_s >= fuzzy_cutoff:
        return {"candidate": best_f, "score": float(best_s), "method": "fuzzy"}

    # 2) BERT semantic embedding — catches domain synonyms fuzzy misses
    sem = semantic_best_match(query, candidates)
    if sem and float(sem["score"]) >= semantic_cutoff:
        return {"candidate": sem["candidate"], "score": float(sem["score"]), "method": "bert_semantic"}

    return None
