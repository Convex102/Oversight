# ASGL-A Phase 2 — FRS 105/101 + Disclosure Completeness Scoring (Deterministic)

Phase 2 extends the Phase 1 FRS 102 guidance library to include:
- FRS 105 (micro entities)
- FRS 101 (reduced disclosure)
- Companies Act crosswalk (high level)
and introduces a deterministic disclosure completeness scoring engine.

## What’s New

### 1) Extended Guidance Library
`app/packs/accountancy/resources/accountancy_regulations_library.json`
Adds:
- FRS 105 overview + key exemptions (e.g., cashflow)
- FRS 101 overview + eligibility/exemptions
- Companies Act guidance stubs for eligibility and statutory reporting

### 2) Framework Inference
`infer_framework(canonical)` determines which framework to apply using canonical flags:
- flags.framework = FRS102/FRS105/FRS101 OR
- micro_entity → FRS105
- reduced_disclosure → FRS101

### 3) Disclosure Completeness Score
`score_disclosure_completeness(canonical)`:
- chooses a disclosure checklist based on framework
- checks canonical.notes keys
- emits missing note findings
- emits a summary `DISCLOSURE_SCORE` finding (score 0..1)

### 4) Reviewer UI Enhancements
The reviewer view now renders a “Disclosure Completeness” card for DISCLOSURE_SCORE findings,
including missing note list.

## Notes / Expectations
- This remains deterministic and audit-safe (no LLM).
- The score is heuristic until canonical extraction reliably populates notes keys.
- Phase 3 should formalise note requirements and add Companies Act schedules per size band.

## Suggested Canonical Flags
To improve accuracy, the ingestion/mapping should supply:
- flags.framework (FRS102/FRS105/FRS101)
- flags.cashflow_required
- flags.policies_required
- flags.micro_entity / flags.reduced_disclosure
- notes fields: accounting_policies, tax, related_parties, etc.

## Next Phase (Phase 3) Recommendations
- Formal note requirements per entity size and framework
- Companies Act schedule compliance and director report / strategic report requirements
- Materiality-aware disclosure checks and balance-to-note linkage

