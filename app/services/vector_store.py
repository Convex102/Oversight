"""Persistent vector store for template retrieval (Phase 1 v2).

Goal: fast, high-quality template retrieval without re-embedding on every job.
Implementation:
- Prefer FAISS if installed (pip install faiss-cpu).
- If not installed, the caller will fall back to in-memory cosine/hashing.

Storage:
- Uses app.storage.storage to read/write index blobs.
- Namespaced per tenant and purpose.

Safety:
- Never raises to callers. All failures are logged and mark store not ready.
"""

from __future__ import annotations

import os, json, time, pickle, hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.logging import get_logger
from app.storage import storage

log = get_logger(__name__)

try:
    import faiss  # type: ignore
    _HAS_FAISS = True
except Exception:
    faiss = None
    _HAS_FAISS = False

def _tokenize(text: str) -> List[str]:
    import re
    text = (text or "").lower()
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]

def _hash_embed(text: str, dim: int = 256) -> List[float]:
    import math
    vec = [0.0] * dim
    for t in _tokenize(text):
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v*v for v in vec)) or 1.0
    return [v/norm for v in vec]

def _try_st_embed(texts: List[str], model_name: str) -> Optional[List[List[float]]]:
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name)
        embs = model.encode(texts, normalize_embeddings=True).tolist()
        return embs
    except Exception as e:
        log.info("vector_embed_fallback", reason=str(e))
        return None

@dataclass
class VectorStoreConfig:
    namespace: str
    embed_model: str
    enabled: bool = True
    dim: int = 256

class VectorStore:
    def __init__(self, cfg: VectorStoreConfig):
        self.cfg = cfg
        self.ready = False
        self._index = None
        self._id_map: List[int] = []

    def _key(self, suffix: str) -> str:
        safe = self.cfg.namespace.replace(":", "_").replace("/","_")
        return f"vector/{safe}/{suffix}"

    def _meta_key(self) -> str:
        return self._key("meta.json")

    def _index_key(self) -> str:
        return self._key("index.faiss")

    def _map_key(self) -> str:
        return self._key("id_map.pkl")

    def _fingerprint(self, templates: List[Any]) -> str:
        h = hashlib.sha256()
        for t in templates:
            h.update(str(getattr(t, "id", "")).encode())
            h.update((getattr(t, "name", "") or "").encode())
            h.update((getattr(t, "description", "") or "").encode())
            # include a stable digest of payload keys only
            payload = getattr(t, "payload", {}) or {}
            if isinstance(payload, dict):
                keys = sorted([str(k) for k in list(payload.keys())])[:200]
                h.update(("|".join(keys)).encode("utf-8"))
        return h.hexdigest()

    def ensure_index(self, templates: List[Any]) -> None:
        """Load index if up-to-date, else rebuild and persist."""
        if not self.cfg.enabled:
            return
        if not _HAS_FAISS:
            log.info("vector_store_no_faiss")
            return
        try:
            fp = self._fingerprint(templates)
            # check meta
            meta = None
            try:
                meta_bytes = storage.read(self._meta_key())
                meta = json.loads(meta_bytes.decode("utf-8")) if meta_bytes else None
            except Exception:
                meta = None
            if meta and meta.get("fingerprint") == fp:
                # load cached index
                idx_bytes = storage.read(self._index_key())
                map_bytes = storage.read(self._map_key())
                if idx_bytes and map_bytes:
                    self._index = faiss.deserialize_index(idx_bytes)
                    self._id_map = pickle.loads(map_bytes)
                    self.ready = True
                    log.info("vector_store_loaded", namespace=self.cfg.namespace, count=len(self._id_map))
                    return

            # rebuild
            texts = [t.text() if hasattr(t, "text") else f"{getattr(t,'name','')}\\n{getattr(t,'description','')}" for t in templates]
            embs = _try_st_embed(texts, self.cfg.embed_model)
            if not embs:
                # fallback hashing
                embs = [_hash_embed(x, dim=self.cfg.dim) for x in texts]
            import numpy as np  # local import to keep base minimal
            X = np.array(embs, dtype="float32")
            dim = X.shape[1]
            index = faiss.IndexFlatIP(dim)
            index.add(X)
            id_map = [int(getattr(t, "id")) for t in templates]

            # persist
            storage.write(self._index_key(), faiss.serialize_index(index))
            storage.write(self._map_key(), pickle.dumps(id_map))
            storage.write(self._meta_key(), json.dumps({
                "fingerprint": fp,
                "count": len(id_map),
                "ts": int(time.time()),
                "embed_model": self.cfg.embed_model,
            }).encode("utf-8"))

            self._index = index
            self._id_map = id_map
            self.ready = True
            log.info("vector_store_built", namespace=self.cfg.namespace, count=len(id_map))
        except Exception as e:
            self.ready = False
            log.exception("vector_store_failed", error=str(e))

    def search(self, query_text: str, k: int = 5) -> List[Tuple[int, float]]:
        if not self.ready or not self._index:
            return []
        try:
            embs = _try_st_embed([query_text], self.cfg.embed_model)
            if not embs:
                embs = [_hash_embed(query_text, dim=self.cfg.dim)]
            import numpy as np
            q = np.array(embs, dtype="float32")
            scores, idxs = self._index.search(q, k)
            out = []
            for i, s in zip(idxs[0].tolist(), scores[0].tolist()):
                if i < 0 or i >= len(self._id_map):
                    continue
                out.append((self._id_map[i], float(s)))
            return out
        except Exception as e:
            log.exception("vector_search_failed", error=str(e))
            return []
