# ASGL Governance Suite (V2) — RBAC, Approvals, Export Gates, Drift

V2 extends Governance V1 with:
- RBAC permission model and default roles
- per-tenant governance configuration (policies, approvals, gates)
- approvals manager wired into runtime (policy approvals)
- export gating helper + enforcement hook
- drift monitor primitive with incident escalation
- notification abstraction (log-only by default)

## Data Structures
- `ctx["governance"]["tenant_config"]` : full config snapshot used for this run
- `ctx["governance"]["approvals"]` : approvals attached to run (for export gating)
- `ctx["governance"]["export_gate"]` : allowed/reason used by exporters
- `ctx["governance"]["drift_signal"]` : drift status computed (if enabled)

## Export Gate Enforcement
Downstream export functions should call:
```python
from app.governance.export_gate import enforce_export_gate
enforce_export_gate(ctx)
```
This raises `ExportGateError` if blocked/missing approvals.

## Drift Monitor
If DI layer provides:
- `ctx["di"]["baseline_accuracy"]`
- `ctx["di"]["current_accuracy"]`
the governance runtime computes a drift signal and emits telemetry.

## Next Up (V3)
- UI approval actions (approve/reject from dashboard)
- persistent storage for approvals/incidents/model registry
- policy editor UI
- tenant scoped notification routing
