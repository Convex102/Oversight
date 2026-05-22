# DI ML Phase 1.1 — Hardening & Wiring

## Goals
- Make Phase 1 robust in real deployments.
- Ensure ML is optional and failures degrade gracefully.
- Add health/status endpoint and warmup.
- Add configuration knobs and logging.

## Additions
- app/di/ml/config.py (env-driven config)
- app/di/ml/logging.py (central logger helper)
- app/di/ml/health.py (status + warmup)
- app/api/di_health_api.py
  - GET /api/v1/di/ml/status

## Env Vars
- ASGL_DI_ML_ENABLED (default true)
- ASGL_DI_ML_WARMUP (default true)
- ASGL_EMBED_MODEL (default all-MiniLM-L6-v2)
- ASGL_EMBED_DEVICE (auto|cpu|cuda)
- ASGL_EMBED_BATCH_SIZE (default 32)
- ASGL_EMBED_MAX_TEXT_LEN (default 4096)
- ASGL_LOG_LEVEL (default INFO)

## Operational guidance
- In low-resource environments set:
  - ASGL_EMBED_DEVICE=cpu
  - ASGL_EMBED_BATCH_SIZE=8
- If sentence-transformers isn't available or model fails to load, ASGL falls back
  to deterministic hashing embeddings but keeps decisions bounded and auditable.

## Verification
1) Start server.
2) Hit /api/v1/di/ml/status — should report enabled/loaded.
3) Run a mapping job and confirm confidence + decision produced.

