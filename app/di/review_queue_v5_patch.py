"""Drop-in helpers to extend review_queue with learning hooks."""
from __future__ import annotations
from typing import Dict, Any
from app.di.active_learning import record_signal, update_priors_from_resolution

def on_resolve_learning(tenant_id:str, item: Dict[str,Any], resolution: Dict[str,Any]):
    field=item.get("field","")
    proposed=item.get("proposed_value")
    corrected=resolution.get("correct_value", proposed)
    was_correct = (corrected == proposed)
    record_signal(tenant_id, {
        "type":"review_resolution",
        "field": field,
        "proposed": proposed,
        "corrected": corrected,
        "was_correct": was_correct,
        "job_id": item.get("job_id")
    })
    update_priors_from_resolution(tenant_id, field, was_correct)