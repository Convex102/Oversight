# Phase 2 v7 — Active Learning Reviewer UI

This upgrade provides a polished, enterprise-grade reviewer UI served from:

`GET /api/active-learning/ui`

## Features
- Search + vertical filter
- Two-panel workflow (queue + review)
- Candidate scores with visual confidence bars
- Notes field + structured resolve
- Optimistic updates and toasts
- Uses stable UI endpoint: `/api/active-learning/open/summary`

## Suggested env flags
```bash
export ASGL_ACTIVE_LEARNING_ENABLE=1
export ASGL_OVERRIDES_ENABLE=1
export ASGL_TEMPLATE_AUTOLEARN_ENABLE=1
```

