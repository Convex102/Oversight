from __future__ import annotations
from typing import List, Dict, Any, Optional
import os, math, hashlib
import numpy as np

def _hash_vec(text: str, dim: int=256) -> np.ndarray:
    # Deterministic local embedding: hashing trick (offline-safe)
    h = hashlib.sha256(text.encode("utf-8")).digest()
    rng = np.random.default_rng(int.from_bytes(h[:8], "little"))
    v = rng.standard_normal(dim).astype(np.float32)
    v /= (np.linalg.norm(v) + 1e-8)
    return v

def embed(texts: List[str]) -> np.ndarray:
    # Pluggable provider hook (OpenAI/Azure/etc.) disabled by default
    provider = os.getenv("ASGL_EMBED_PROVIDER","local")
    dim = int(os.getenv("ASGL_EMBED_DIM","256"))
    if provider == "local":
        return np.stack([_hash_vec(t, dim) for t in texts], axis=0)
    # future: add openai/azure endpoint calls
    return np.stack([_hash_vec(t, dim) for t in texts], axis=0)

def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / ((np.linalg.norm(a)+1e-8)*(np.linalg.norm(b)+1e-8)))

def best_match(query: str, candidates: List[str], top_k:int=3) -> List[Dict[str,Any]]:
    if not candidates:
        return []
    qv = embed([query])[0]
    cvs = embed(candidates)
    scores = [(candidates[i], cosine(qv, cvs[i])) for i in range(len(candidates))]
    scores.sort(key=lambda x: x[1], reverse=True)
    return [{"candidate": c, "score": s} for c,s in scores[:top_k]]