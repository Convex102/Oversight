# DI v5 – Embeddings Repair + Active Learning

## What changes in v5
- Review queue resolutions create training signals per field
- A per-tenant "prior" model shifts confidence up/down per field over time
- Repair suggestions use fuzzy + semantic embeddings (local deterministic embeddings by default)
- New endpoints:
  - GET /api/v1/di-learning/signals
  - GET /api/v1/di-learning/priors

## Enable routers
In app factory:
```python
from app.di.v4_wiring_patch import apply_di_v4_wiring
from app.di.v5_wiring_patch import apply_di_v5_wiring
app = apply_di_v4_wiring(app)
app = apply_di_v5_wiring(app)
```

## Embeddings provider
Default is offline local hashing embeddings.
Set `ASGL_EMBED_PROVIDER=local` (default).

Later you can plug OpenAI/Azure by extending `app/di/embeddings.py`.