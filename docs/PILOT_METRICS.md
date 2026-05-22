# Pilot Metrics & Telemetry (ASGL)

This document explains how ASGL tracks pilot KPIs from day 1.

## Why
During a pilot, you want to measure:
- ingestion success rate
- auto-map rate (confidence above threshold)
- review workload (queued items)
- resolution velocity
- learning flywheel outputs: overrides/templates/training labels

## Where data lives
Telemetry is written as append-only JSONL:
`storage://telemetry/<tenant_id>/<YYYY-MM-DD>.jsonl`

A compact rollup lives at:
`storage://telemetry/<tenant_id>/rollup.json`

## Events
- `ingestion.start`
- `ingestion.complete`
- `mapping.proposed`  (n_total, n_auto, n_queued, min_conf)
- `review.opened`
- `review.resolved`   (override/template/training flags)

## API
`GET /api/telemetry/summary`

Returns:
- counters
- by_vertical
- by_day

## Privacy and GDPR
Telemetry hashes workbook identifiers using `ASGL_TELEMETRY_SALT`.
No raw customer data is stored in telemetry.

## Enable/disable
```bash
export ASGL_TELEMETRY_ENABLE=1
export ASGL_TELEMETRY_SALT="<random>"
```

