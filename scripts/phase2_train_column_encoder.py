"""Train / Fine-tune Phase 2 Column Encoder on logged template/job pairs.

Usage (inside repo):
  python scripts/phase2_train_column_encoder.py --tenant_id <TENANT_ID>

This script:
1) Reads JSONL dataset from storage key ml/datasets/<tenant>/phase2_column_pairs.jsonl
2) Builds contrastive pairs: (same canonical_field) positive, others negative
3) Fine-tunes a sentence-transformers model using MultipleNegativesRankingLoss
4) Saves the fine-tuned model to disk and optionally uploads it into app storage

Requirements:
  pip install sentence-transformers torch

Optional:
  Set ASGL_UPLOAD_MODEL=1 to upload to storage
"""

import os, json, argparse, random, tempfile, shutil
from typing import List, Dict

from app.storage import storage

def dataset_key(tenant_id: str) -> str:
    safe = str(tenant_id).replace(":", "_").replace("/","_")
    return f"ml/datasets/{safe}/phase2_column_pairs.jsonl"

def read_jsonl(key: str) -> List[Dict]:
    blob = storage.read(key)
    if not blob:
        return []
    lines = blob.decode("utf-8").splitlines()
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out

def build_pairs(rows: List[Dict], max_pairs: int = 50000):
    # Group by label
    by_label = {}
    for r in rows:
        lbl = r.get("canonical_field")
        txt = r.get("column_text")
        if not lbl or not txt:
            continue
        by_label.setdefault(lbl, []).append(txt)
    labels = list(by_label.keys())
    pairs = []
    # positive pairs within same label
    for lbl, texts in by_label.items():
        if len(texts) < 2:
            continue
        for _ in range(min(len(texts)*2, 2000)):
            a, b = random.sample(texts, 2)
            pairs.append((a, b))
            if len(pairs) >= max_pairs:
                return pairs
    return pairs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant_id", required=True)
    ap.add_argument("--base_model", default=os.getenv("ASGL_PHASE2_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"))
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--upload", action="store_true")
    args = ap.parse_args()

    rows = read_jsonl(dataset_key(args.tenant_id))
    if len(rows) < 50:
        raise SystemExit(f"Not enough rows to train: {len(rows)}. Need ~50+.")

    pairs = build_pairs(rows)
    if not pairs:
        raise SystemExit("No training pairs constructed.")

    from sentence_transformers import SentenceTransformer, InputExample
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from torch.utils.data import DataLoader

    model = SentenceTransformer(args.base_model)
    train_examples = [InputExample(texts=[a,b]) for a,b in pairs]
    train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=args.batch)
    train_loss = MultipleNegativesRankingLoss(model)

    outdir = tempfile.mkdtemp(prefix="asgl_phase2_model_")
    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=args.epochs,
        warmup_steps=100,
        show_progress_bar=True,
        output_path=outdir,
    )
    print("Saved model to", outdir)

    if args.upload or os.getenv("ASGL_UPLOAD_MODEL", "0") in ("1","true","TRUE"):
        # Zip directory and upload
        import zipfile
        zpath = os.path.join(tempfile.gettempdir(), f"phase2_model_{args.tenant_id}.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(outdir):
                for f in files:
                    fp = os.path.join(root, f)
                    arc = os.path.relpath(fp, outdir)
                    z.write(fp, arcname=arc)
        key = f"ml/models/tenant_{args.tenant_id}/phase2_column_encoder.zip"
        with open(zpath, "rb") as f:
            storage.write(key, f.read())
        print("Uploaded model to storage key", key)

    # cleanup local
    shutil.rmtree(outdir, ignore_errors=True)

if __name__ == "__main__":
    main()
