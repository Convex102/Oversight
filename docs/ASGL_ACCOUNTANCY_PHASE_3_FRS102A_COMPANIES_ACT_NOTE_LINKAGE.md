# ASGL-A Phase 3 — FRS 102A + Companies Act Schedules + Balance-to-Note Linkage (Deterministic)

Phase 3 upgrades ASGL-A to “technical reviewer” behaviour by:
- adding FRS 102A (small entities) to the guidance library
- adding Companies Act schedule prompts for size bands and statutory reports
- adding balance-to-note linkage for material balances
- producing a weighted disclosure score that penalises missing notes for material items

## What’s New

### 1) FRS 102A Guidance
Adds structured clauses:
- FRS102A overview (eligibility + reduced disclosure set)
- disclosure minimums and statutory context

### 2) Companies Act Schedules
Adds:
- size band inference (micro/small/medium/large/unknown)
- statutory report presence prompts (directors report / strategic report)
- eligibility sanity checks (e.g., FRS102A but not small)

### 3) Balance-to-Note Linkage
Adds a deterministic mapping:
`balances.<key>` → expected notes keys

Example:
- balances.tax → notes.tax
- balances.lease_liabilities → notes.leases or notes.commitments

When balances are material (heuristic materiality), missing supporting notes create a WARN finding:
`NOTE_LINK_MISSING`

### 4) Weighted Disclosure Score
Phase 2 produced a heuristic disclosure score based on checklist presence.
Phase 3 produces:
`DISCLOSURE_SCORE_WEIGHTED`
which penalises the base score for each material balance missing a supporting note.

## Canonical Inputs Expected
For highest confidence, canonical should include:

### flags
- framework: FRS102/FRS102A/FRS105/FRS101
- size_band: micro/small/medium/large
- micro_entity, small_entity, reduced_disclosure
- cashflow_required, policies_required

### notes
- accounting_policies
- tax
- related_parties
- events_after
- going_concern
- financial_instruments
- leases / commitments
- revenue_policy

### balances (Phase 3 linkage)
- tax, deferred_tax, lease_liabilities, financial_instruments, related_party_balances, revenue

### totals (materiality heuristic)
- turnover
- total_assets
- profit_before_tax

## Next Phase (Phase 4) Recommendations
- Full Companies Act statutory schedule matrices by size band
- Detailed FRS 102A disclosure requirements by section
- Explicit exemption logic (e.g., directors report exemptions)
- Balance-to-note extraction improvements and mapping from raw trial balance

