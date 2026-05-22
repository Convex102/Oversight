# ASGL Flywheel Engine (V1)

The Flywheel is ASGL's compounding learning engine:

**Human oversight -> structured labels -> better models -> better automation -> more usage -> more labels**

## What it captures
- mapping decisions (accepted/rejected/corrected)
- reviewer overrides & exception confirmations
- review completion summary
- operational run-level signals (confidence, exceptions)

## What it stores (and what it does NOT)
### Stored:
- decision events with provenance hashes
- aggregated features/patterns (safe)
- no raw spreadsheets required

### Not stored:
- raw worksheets
- PII fields
- full client documents

This supports GDPR-safe learning and tenant isolation.

## Active Learning Queue
Creates queue items for:
- low confidence mapping suggestions
- high impact exceptions

Queue items are served in priority order.

## Training Trigger
Basic trigger:
- if dataset rows exceed `min_new_rows`, training job is created.
- training is asynchronous in production (Celery/K8s).

## Telemetry
Emits:
- `flywheel.event`
- `flywheel.health`

## Next: Flywheel V2
- persistent queue + approvals integration
- quiet labeling for frequently repeated templates
- template clustering + schema fingerprinting
- federated tenant learning + global distillation
