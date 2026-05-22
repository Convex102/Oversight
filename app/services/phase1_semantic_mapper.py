"""Phase 1: Retrieval + LLM-assisted template selection and field mapping.

This module adds *deep-tech grade* behavior without training:
1) Retrieve similar templates using embeddings over (name + description + mappings).
2) (Optional) Call an LLM to propose field_selections when not provided.
3) Provide full structured logging and safe fallbacks.

Design constraints:
- Works even when ML deps are absent (falls back to lexical similarity).
- Works even when LLM is disabled/unconfigured.
- Never blocks the pipeline; it only improves defaults.
"""

from __future__ import annotations

import os, json, math, re, hashlib, time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.logging import get_logger

log = get_logger(__name__)

def _normalize(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\\s+", " ", s)
    return s

def _tokenize(s: str) -> List[str]:
    s = _normalize(s)
    return [t for t in re.split(r"[^a-z0-9]+", s) if t]

def _lexical_score(a: str, b: str) -> float:
    ta, tb = set(_tokenize(a)), set(_tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def _hash_embed(text: str, dim: int = 256) -> List[float]:
    """Deterministic embedding fallback when sentence-transformers is unavailable.
    Uses hashed token buckets. Not SOTA, but reliable.
    """
    vec = [0.0] * dim
    for t in _tokenize(text):
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    # L2 normalize
    norm = math.sqrt(sum(v*v for v in vec)) or 1.0
    return [v / norm for v in vec]

def _cosine(u: List[float], v: List[float]) -> float:
    return sum(a*b for a,b in zip(u,v))

def _try_st_embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Try to produce real embeddings if sentence-transformers is installed."""
    try:
        from sentence_transformers import SentenceTransformer
        model_name = os.getenv("ASGL_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        model = SentenceTransformer(model_name)
        embs = model.encode(texts, normalize_embeddings=True).tolist()
        return embs
    except Exception as e:
        log.info("semantic_embed_fallback", reason=str(e))
        return None

@dataclass
class TemplateDoc:
    id: int
    name: str
    description: str
    payload: Dict[str, Any]

    def text(self) -> str:
        # include mapping keys for better retrieval
        mapping_keys = []
        try:
            m = (self.payload or {}).get("mappings") or (self.payload or {}).get("field_selections")
            if isinstance(m, dict):
                mapping_keys = list(m.keys())[:200]
        except Exception:
            pass
        return "\\n".join([self.name or "", self.description or "", " ".join(mapping_keys)])

class TemplateRetriever:
    def __init__(self, templates: List[TemplateDoc]):
        self.templates = templates
        self._embs: Optional[List[List[float]]] = None

    def _ensure_embeddings(self) -> None:
        if self._embs is not None:
            return
        texts = [t.text() for t in self.templates]
        st = _try_st_embed(texts)
        if st:
            self._embs = st
        else:
            self._embs = [_hash_embed(x) for x in texts]

    def retrieve(self, query_text: str, top_k: int = 5) -> List[Tuple[TemplateDoc, float]]:
        if not self.templates:
            return []
        self._ensure_embeddings()
        q_embs = _try_st_embed([query_text])
        q = q_embs[0] if q_embs else _hash_embed(query_text)
        scored = []
        for t, e in zip(self.templates, self._embs or []):
            scored.append((t, float(_cosine(q, e))))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

def build_job_fingerprint(meta: Dict[str, Any], preview: Optional[Dict[str, Any]] = None) -> str:
    """Make a retrieval query describing the workbook/table without heavy parsing."""
    parts = []
    if meta:
        parts.append(f"filename={meta.get('filename')}")
        parts.append(f"sheet={meta.get('sheet_name')}")
        parts.append(f"vertical={meta.get('vertical')}")
    if preview:
        headers = preview.get("headers") or []
        parts.append("headers=" + ",".join(headers[:60]))
    return "\\n".join([p for p in parts if p and p != "None"])

def propose_field_selections_with_llm(
    query: str,
    retrieved_templates: List[TemplateDoc],
    schema_hint: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Optional LLM-assisted proposal.
    Requires OPENAI_API_KEY and ASGL_ENABLE_LLM=1.
    """
    if os.getenv("ASGL_ENABLE_LLM", "0") not in ("1","true","TRUE","yes","YES"):
        return None
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY")
    if not api_key:
        log.info("llm_disabled_no_key")
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        tmpl_brief = []
        for t in retrieved_templates[:3]:
            tmpl_brief.append({
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "payload_keys": list((t.payload or {}).keys())[:50],
            })
        system = "You map spreadsheet table columns into a canonical accounting schema. Output JSON only."
        user = {
            "query": query,
            "templates": tmpl_brief,
            "schema_hint": schema_hint or {},
            "task": "Return a JSON object field_selections mapping canonical_field -> source_column_name."
        }
        resp = client.chat.completions.create(
            model=os.getenv("ASGL_LLM_MODEL", "gpt-4o-mini"),
            temperature=0.0,
            messages=[
                {"role":"system","content":system},
                {"role":"user","content":json.dumps(user)}
            ],
        )
        content = resp.choices[0].message.content or ""
        # Extract first JSON object found
        m = re.search(r"\\{[\\s\\S]*\\}", content)
        if not m:
            return None
        obj = json.loads(m.group(0))
        if not isinstance(obj, dict):
            return None
        return obj
    except Exception as e:
        log.exception("llm_proposal_failed", error=str(e))
        return None

def phase1_select_template_and_mapping(
    *,
    templates: List[TemplateDoc],
    meta: Dict[str, Any],
    preview: Optional[Dict[str, Any]] = None,
    top_k: int = 5,
) -> Dict[str, Any]:
    """Returns {selected_template_id, retrieved, field_selections?}.\n
    Never raises.
    """
    t0 = time.time()
    try:
        retriever = TemplateRetriever(templates)
        query = build_job_fingerprint(meta, preview)
        retrieved = retriever.retrieve(query, top_k=top_k)
        out = {
            "query": query,
            "retrieved": [{"id": t.id, "name": t.name, "score": s} for t,s in retrieved],
            "selected_template_id": retrieved[0][0].id if retrieved else None,
        }
        # optional LLM proposal for mapping
        proposed = propose_field_selections_with_llm(query, [t for t,_ in retrieved])
        if proposed:
            out["field_selections"] = proposed
        out["latency_ms"] = int((time.time()-t0)*1000)
        log.info("phase1_template_selection", **out)
        return out
    except Exception as e:
        log.exception("phase1_failed", error=str(e))
        return {"selected_template_id": None, "retrieved": [], "error": str(e)}
