# ASGL-A Phase 4 — Companies Act Matrix + Exemptions + Evidence Bundle (Deterministic)

Phase 4 turns ASGL-A into an “audit-ready reviewer assistant” by adding:
1) a statutory requirements matrix (Companies Act) with exemption logic
2) deterministic statutory requirement gap findings (missing/exempt)
3) an evidence bundle artifact for run provenance and audit traceability
4) UI panels for statutory requirement checks and evidence bundle summary

## What’s New

## 1) Statutory Requirements Matrix
File: `app/packs/accountancy/statutory.py`

Provides:
- `REQ_MATRIX` of statutory requirements
- `infer_size_band()` and `infer_framework()`
- exemption evaluation
- `statutory_gap_findings()` which returns:
  - `STATUTORY_REQUIREMENT_MISSING`
  - `STATUTORY_REQUIREMENT_EXEMPT`

This is intended as a deterministic, expandable scaffold.
You can add:
- abridged accounts requirements
- filing exemptions
- additional statutory schedules and disclosures

## 2) Evidence Bundle Artifact (Audit Provenance)
File: `app/packs/accountancy/evidence.py`

Creates a deterministic evidence bundle with:
- schema version
- timestamp
- tenant/client/run metadata
- sha256 hashes of canonical + findings
- evidence refs for key artefacts
- provenance note confirming deterministic behaviour

This is stored into pipeline `ctx["accountancy_evidence_bundle"]`
and the findings list receives an INFO marker:
`EVIDENCE_BUNDLE_CREATED`.

## 3) Pipeline Wiring
File: `app/pipeline/accountancy_pipeline.py`

Adds:
- statutory matrix findings
- evidence bundle generation
- citation mapping for new finding codes

## 4) Reviewer UI Panels
File: `app/api/accountancy_ui_api.py`

Adds:
- “Statutory Requirement” card (missing/exempt)
- “Evidence Bundle” card

## Canonical Inputs Expected
For best performance:

### flags
- size_band: micro/small/medium/large
- framework: FRS102/FRS102A/FRS105/FRS101
- micro_entity, small_entity, reduced_disclosure

### statutory_reports
- directors_report
- strategic_report

### statements
- cashflow

### notes
- accounting_policies, tax, etc.

## Next Phase (Phase 5) Suggestions
- Full Companies Act schedule matrix (expanded list)
- Explicit directors report/strategic report exemption rules
- Abridged accounts + filleted accounts pathways
- Integration of iXBRL tagging and filing readiness

