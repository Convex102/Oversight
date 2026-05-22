# ASGL-P (Pensions Vertical) — Phases 1 to 5 (Wired)

This patch adds the **Pensions** vertical as `ASGL-P`, wired into:
- Pack registry
- Pack listing API (`GET /api/v1/packs`)
- Telemetry events
- Canonical build + deterministic validation

## What you get
- Phase 1: Canonical schema + canonical builder from mapped rows
- Phase 2: Template fingerprint key for tenant reuse
- Phase 3: Validators (NI checks, large deltas)
- Phase 4: Pipeline entrypoint `run_pensions_pack`
- Phase 5: Telemetry events feeding the flywheel

