# ASGL Flywheel Engine (V2) — Fingerprints, Quiet Labels, Persistent Queue

V2 upgrades Flywheel V1 by adding:

## 1) Schema Fingerprinting (GDPR-safe)
We compute a privacy-safe schema signature:
- normalised header tokens
- inferred type tokens
- hashed signatures

This enables:
- template recognition
- clustering
- repeat detection
- cross-client performance tracking

Telemetry:
- `flywheel.schema_fingerprint`

## 2) Quiet Labeling
Quiet labels are stable mapping patterns learned from repeated templates:
- store minimal mapping payloads
- no raw data stored
- match using `cluster_key(headers)`

Usage:
- on ingestion, do a quiet label lookup
- if hit, pre-fill mapping suggestions in UI (optional)

## 3) Persistent Active Learning Queue
Queue persistence to:
- `data/flywheel/queue_state.json`

Supports:
- local restarts
- multi-run continuity
- offline review flow

## Next (V3)
- governance approval required before training jobs execute
- persistent quiet labels store (DB/S3)
- template clustering using embeddings
- federated tenant distillation (global-safe)
