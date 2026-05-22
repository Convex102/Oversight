"""Phase 2 v2: Evaluate column encoder mapping quality.

Evaluates whether embeddings cluster by canonical_field.

Usage:
  python scripts/phase2_eval_column_encoder.py --tenant_id <TENANT_ID> --topk 5

Metrics:
  - Retrieval accuracy: for each example, does another example with same label appear in top-k?
  - Mean reciprocal rank (MRR)

Requires:
  pip install sentence-transformers numpy
"""

import os, json, argparse
from typing import List, Dict, Tuple

from app.storage import storage
from app.services.phase2_column_encoder import _try_embed, _hash_embed
from app.services.model_registry import get_or_load_phase2_model_path

def dataset_key(tenant_id: str) -> str:
    safe = str(tenant_id).replace(":", "_").replace("/","_")
    return f"ml/datasets/{safe}/phase2_column_pairs.jsonl"

def read_rows(key: str) -> List[Dict]:
    blob = storage.read(key)
    if not blob:
        return []
    rows = []
    for ln in blob.decode("utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
    return rows

def embed_texts(texts: List[str], tenant_id: str) -> List[List[float]]:
    base = os.getenv("ASGL_PHASE2_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
    local = get_or_load_phase2_model_path(tenant_id)
    model_name = local or base
    embs = _try_embed(texts, model_name, tenant_id=None)  # already local if fine-tuned
    if embs:
        return embs
    return [_hash_embed(t) for t in texts]

def cosine(u, v):
    return sum(a*b for a,b in zip(u,v))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant_id", required=True)
    ap.add_argument("--topk", type=int, default=5)
    args = ap.parse_args()

    rows = read_rows(dataset_key(args.tenant_id))
    if len(rows) < 50:
        raise SystemExit(f"Need at least 50 rows, got {len(rows)}")

    texts = [r["column_text"] for r in rows if r.get("column_text") and r.get("canonical_field")]
    labels = [r["canonical_field"] for r in rows if r.get("column_text") and r.get("canonical_field")]
    embs = embed_texts(texts, args.tenant_id)

    # Evaluate
    n = len(texts)
    hit = 0
    mrr = 0.0
    for i in range(n):
        sims = []
        for j in range(n):
            if i == j:
                continue
            sims.append((j, cosine(embs[i], embs[j])))
        sims.sort(key=lambda x: x[1], reverse=True)
        top = sims[:args.topk]
        found_rank = None
        for rank, (j, _) in enumerate(top, start=1):
            if labels[j] == labels[i]:
                found_rank = rank
                break
        if found_rank is not None:
            hit += 1
            mrr += 1.0 / found_rank

    acc = hit / n
    mrr = mrr / n
    print(json.dumps({
        "n": n,
        "topk": args.topk,
        "hit_rate": acc,
        "mrr": mrr,
        "model": "fine_tuned" if get_or_load_phase2_model_path(args.tenant_id) else "base_or_hash"
    }, indent=2))

if __name__ == "__main__":
    main()
