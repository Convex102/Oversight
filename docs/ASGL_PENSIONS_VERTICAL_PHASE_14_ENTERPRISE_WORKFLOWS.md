# ASGL-P Phase 14 — Enterprise Workflows: PI Engine + Comms Store + Cleanse Change Log + Telemetry

This phase converts Phase 13 scaffolds into robust enterprise-grade subsystems:

## 1) PI Exercise Engine (deterministic)
New module: `app/packs/pensions/pi_engine.py`
- deterministic PI increase application using date-range rate table
- category-based rate matching
- exception tracking for missing rate mappings
- auditable result notes
- logging around start/complete/parse failures

UI:
- `/ui/pensions/pensions-increase` now runs PI exercises and renders results
API:
- `POST /api/v1/pensions/pi/run`
Telemetry:
- `pack.pensions.pi.run`

## 2) Member Comms Store + MI
New store: `app/packs/pensions/comms_store.py`
- persists comms events in sqlite table `member_comms`
- robust upsert + listing with json meta
API:
- `POST /api/v1/pensions/comms/upsert`
- `GET /api/v1/pensions/comms/list`
UI:
- `/ui/pensions/member-comms` + `/view`
Telemetry:
- `pack.pensions.comms.upsert`

## 3) Data Cleanse Change Log
New store: `app/packs/pensions/cleanse_store.py`
- persists cleanse changes `data_cleanse_changes`
- captures approvals, reasons, evidence metadata
API:
- `POST /api/v1/pensions/cleanse/upsert`
- `GET /api/v1/pensions/cleanse/list`
UI:
- `/ui/pensions/data-cleanse` + `/view`
Telemetry:
- `pack.pensions.cleanse.upsert`

## 4) Robust wiring
All new modules:
- use structured logging
- cap payload sizes to prevent abuse
- integrate with telemetry
- follow existing UI theme and navigation patterns

