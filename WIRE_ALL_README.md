# Wiring Patch – What to do in your repo

This patch can't safely overwrite your existing `jobs` endpoints without seeing their exact code,
so it provides **mixins + wired task implementation** that you paste/merge into:

- your FastAPI app factory: include `apply_wiring(app)` from `app/main_wiring_patch.py`
- your POST /api/v1/jobs/compare handler:
  - import `preflight_and_audit_job_submit`
  - call it before you enqueue the Celery task
- your Celery worker task that runs ASGL compare:
  - replace its body with `run_compare_job_wired(...)` pattern

## Typical integration (example snippets)

### 1) FastAPI wiring
```python
from app.main_wiring_patch import apply_wiring
app = apply_wiring(app)
```

### 2) Jobs submit handler preflight
```python
from app.api.jobs_wiring_mixin import preflight_and_audit_job_submit
payload = preflight_and_audit_job_submit(principal, payload)
```

### 3) Celery task body
```python
from app.tasks_wiring_patch import run_compare_job_wired
return run_compare_job_wired(...)
```

### 4) Report fetch audit
```python
from app.api.report_wiring_mixin import audit_report_access
audit_report_access(principal, job_id)
```