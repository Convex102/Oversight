# DI v4 – Auto-Repair + Review Queue + Persistence

## New APIs
After including the router, you get:
- GET /api/v1/di/jobs
- GET /api/v1/di/jobs/{job_id}/factbundle
- GET /api/v1/di/review-queue
- POST /api/v1/di/review-queue/{item_id}/resolve

## Enable router
In your FastAPI app factory:
```python
from app.di.v4_wiring_patch import apply_di_v4_wiring
app = apply_di_v4_wiring(app)
```

## What v4 adds to the job run
- DI trace + factbundle persisted under `data/di_store/<tenant>/jobs/<job>/...`
- Review queue items created for facts < 0.70 confidence
- Drift repair suggestions for template mappings using observed headers
- Quality summary injected into report payload