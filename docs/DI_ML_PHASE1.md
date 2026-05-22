# DI ML Phase 1 (Embeddings + Ranker + Calibration)

## What changes
- Adds `app/di/ml/*`:
  - ML-grade embeddings using SentenceTransformers (fallback to hashing embeddings if unavailable)
  - Candidate ranker using embedding similarity + priors
  - Confidence calibration function
- Wires semantic mapper to use ML embeddings + calibrated confidence
- Ensures review resolutions emit supervised label examples to telemetry

## Safety
- Outputs remain bounded: approve/queue/block decisions only.
- Abstain-first policy: low confidence -> queue for review.
- Full evidence contract recorded in mapper output + telemetry.

## Config
- `ASGL_EMBED_MODEL` (default: all-MiniLM-L6-v2)
- `ASGL_EMBED_CACHE_DIR` (default: .asgl_cache/embeddings)

## Next (Phase 2)
- Replace calibration with trained isotonic regression / Platt scaling from telemetry
- Train a learned ranker using `di.label_example` events
- Add drift detection model + ingestion table detector

