# ASGL-P Phase 10 — Governance Suite (Assignments, Runs, Drift Store, Analytics UI)

Phase 10 turns ASGL-P into a **full governance-grade workflow system**.

## 1) Persisted Drift Store
Drift events are stored in `asgl_runs.sqlite`:
- `drift_events` table

Populates:
- Drift Monitor UI
- SLA workflows (template changes)
- Risk dashboards

Telemetry:
- `pack.pensions.drift.persisted`

## 2) Persisted Pack Runs (analytics-ready)
`pack_runs` table is now listable via API:
- `GET /api/v1/pensions/runs`

Used for:
- audit replays
- evidence retrieval
- throughput metrics

## 3) Review Queue Workbench
Phase 9 queue continues; Phase 10 adds:
- item actions tracked
- role/tenant contexts (lightweight)

## 4) Assignments + RBAC (lightweight)
New module:
- `app/packs/pensions/auth.py`

API:
- `POST /api/v1/pensions/assign`
Requires role manager/admin via header or env.

## 5) Analytics UI
New route:
- `/ui/pensions/analytics`

Shows:
- avg findings per run
- open items
- high severity open
- drift + automation estimates
- recent runs

## 6) Commercial impact
Phase 10 demonstrates:
- governance controls
- operational throughput measurement
- robust audit trails
- scalable workbench UI

This is enterprise-grade and supports premium pricing and procurement.

