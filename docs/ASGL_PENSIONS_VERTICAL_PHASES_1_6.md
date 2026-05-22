# ASGL-P (Pensions) — Phases 1 to 6 (Robust + Telemetry + Evidence Export)

This patch upgrades ASGL-P to **state-of-the-art, robust scaffolding** and adds **Phase 6**:

## What’s new vs Phase 1–5

### Robustness
- structured logging across pack / pipeline / validators / templates
- defensive parsing + normalization
- error telemetry (`pack.run.error`)

### Canonical expansion (Phase 1 upgrade)
- adds tranches + events (ready for GMP equalisation & remediation)
- adds member join/exit, gender, postcode
- adds basic provenance fields on results

### Template recognition (Phase 6)
- similarity scoring and best-match lookup per tenant
- in-memory `TemplateStore` (swap for DB later)
- telemetry on template store size + match score

### Trustee Evidence Bundle (Phase 6)
- deterministic evidence JSON export including:
  - counts and severity breakdown
  - findings list
  - canonical snapshot

## Telemetry emitted
- `pack.run.start`
- `pack.run.finish`
- `pack.run.error`
- `pack.pensions.fingerprint` (includes match_score)
- `pack.pensions.template.store_stats`
- `pack.pensions.evidence.exported`

## Integration notes
This patch is compatible with existing ASGL telemetry + DI pipeline:
- Call `run_pensions_pack()` after mapping rows
- Use `findings` to populate reviewer queue
- Store `fingerprint_key` to build tenant template library
- Export `evidence_bundle` to trustee/audit folder

