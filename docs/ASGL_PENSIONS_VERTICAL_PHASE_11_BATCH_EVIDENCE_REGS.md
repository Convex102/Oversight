# ASGL-P Phase 11 — Batch Runner + Evidence Browser + SLA + Pensions Regs Schema Library

This phase adds the missing "enterprise operational layer":

## 1) Evidence Browser
- `/ui/pensions/evidence` lists persisted pack runs
- `/ui/pensions/evidence/view` shows stored evidence bundle + findings json
- PDF download retained

## 2) Batch Runner
- `/ui/pensions/batch` accepts a list of jobs and runs them end-to-end
- each job becomes a persisted run + queue items

## 3) SLA Timers
- SLA status computed from created_at:
  - OK (<48h)
  - DUE_SOON (>=48h)
  - OVERDUE (>=96h)
- shown in Review Queue UI

## 4) Export Tools
- `GET /api/v1/pensions/queue/export.csv` exports queue items as CSV

## 5) Deterministic Pensions Regs Library

Endpoints:

## Why it matters
