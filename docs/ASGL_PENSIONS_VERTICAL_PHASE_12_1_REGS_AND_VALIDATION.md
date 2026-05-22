# Phase 12.1 — Expanded Pensions Regulatory Guidance + Common/Conditional Data Validators

This patch extends the deterministic pensions regs library and adds additional validation signals.

## Added guidance clauses
- Common & Conditional Data (expanded)
- Contingent spouse / dependent benefits
- Scheme events and event-driven controls
- HMRC event reporting context
- Pensions dashboards data readiness and matching
- Lloyds 3 (GMPe) governance context
- Barber equalisation context
- Transitional protection flags and change control

## Added deterministic validation signals
New validator: `validate_common_conditional()`
Emits findings:
- COMMON_DATA_GAP
- CONDITIONAL_DATA_GAP (reserved)
- SPOUSE_DATA_GAP
- DASHBOARD_MATCHING_RISK
- TRANSITIONAL_PROTECTION_GAP

These are merged into findings post-pack validation and flow through:
- citations mapping
- review queue with regulatory context
- coverage heatmap

## Added citation + coverage mapping
Finding codes now map to tags covering:
- Dashboards
- HMRC reporting
- Equalisation / Barber / Lloyds
- Scheme events
- Transitional protection

This supports trustee oversight MI and governance narratives.
