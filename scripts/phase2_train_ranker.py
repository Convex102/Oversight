"""Train Phase2Ranker from stored dataset.

Usage:
  python scripts/phase2_train_ranker.py --tenant_id <TENANT_ID>

Requires (for best model):
  pip install scikit-learn numpy sentence-transformers torch

Fallback:
  If sklearn isn't present, trains nearest-centroid model.
"""

import argparse, json
from app.services.phase2_ranker import Phase2Ranker

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant_id", required=True)
    ap.add_argument("--min_rows", type=int, default=100)
    args = ap.parse_args()

    r = Phase2Ranker(args.tenant_id)
    res = r.train(min_rows=args.min_rows)
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
