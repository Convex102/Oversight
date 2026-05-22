# ASGL-P Phase 13 — Dashboards ERI + Data Cleansing/Health + PI Exercises + Member Comms MI

This phase extends the pensions vertical into operational MI areas commonly required during:
- dashboards readiness
- remediation/data cleanse
- pensions increase exercises
- member comms and governance reporting

## 1) Expanded regs guidance
Regs library upgraded to v1.2.0 with clauses for:
- Dashboards ERI evidence and outcomes MI
- Data cleansing governance and revalidation
- Data health monitoring/MI
- Pensions increase exercises controls
- Member comms audit evidence

## 2) New deterministic reporting layer
New module:
- `app/packs/pensions/reporting.py`

Reports:
- DataHealthReport
- Dashboard ERI evidence (matching proxy + exception counts)

These reports are embedded in pack response under `reports`.

## 3) New API endpoints
- `/api/v1/pensions/reports/data-health`
- `/api/v1/pensions/reports/dashboard-eri`

## 4) New UI pages
- `/ui/pensions/data-health`
- `/ui/pensions/dashboard-eri`
- `/ui/pensions/pensions-increase` (enterprise scaffold)
- `/ui/pensions/member-comms` (enterprise scaffold)

## 5) How it fits ASGL pipeline
These MI layers are deterministic and extend evidence/telemetry:
- pack findings -> citations -> queue
- runs are persisted and queryable
- MI pages consume persisted runs to show trustee-ready reporting

Next iterations can add:
- PI rate tables validation + batch calculation verification
- Comms provider integration (email/SMS/portal)
- data cleansing change logs and approvals

