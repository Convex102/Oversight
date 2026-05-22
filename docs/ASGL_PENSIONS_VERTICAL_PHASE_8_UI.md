# ASGL-P Phase 8 — Enterprise UI (Theme-Matched) + Policy + PDF Evidence

Phase 8 adds **server-rendered enterprise UI** matching the ASGL theme.

## New UI Routes
- `/ui/pensions` — dashboard
- `/ui/pensions/runner` — runner UI to test end-to-end flow
- `/ui/pensions/review` — review queue scaffold (template-based)
- `/ui/pensions/drift` — drift monitor scaffold
- `/ui/pensions/templates` — template library UI
- `/ui/pensions/policy` — policy editor UI
- `/ui/pensions/evidence/pdf` — deterministic PDF evidence download

## Why server-rendered?
- zero frontend build
- works locally + on simple hosts
- consistent theme via shared layout function

## Policy Store
- SQLite store `asgl_policies.sqlite`
- per-template guardrails:
  - autopilot_enabled + min score
  - lock template
  - require manual review

## PDF Evidence
- `app/packs/pensions/pdf.py` uses reportlab if installed.
- deterministic render: no model output, no hallucinations.

## Telemetry
- `ui.pensions.runner.run`
- `pack.pensions.policy.updated`
- `pack.pensions.evidence.pdf.rendered`

