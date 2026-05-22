"""
ASGL Governance Suite (Enterprise)
---------------------------------
This package provides:
 - audit logging + provenance
 - run governance policies (confidence gating, required approvals)
 - model registry + versioning hooks
 - incident reporting + kill switch integration

Design principles:
 - deterministic behavior by default (no hallucination)
 - strong observability (structured logs + telemetry events)
 - tenant isolation and least-privilege patterns (RBAC hooks)
"""

from .audit import AuditLog, AuditEvent
from .policies import GovernancePolicy, PolicyDecision, evaluate_policies
from .models import ModelRegistry, ModelArtifact
from .incidents import Incident, IncidentManager
from .runtime import GovernanceRuntime

__all__ = [
    "AuditLog",
    "AuditEvent",
    "GovernancePolicy",
    "PolicyDecision",
    "evaluate_policies",
    "ModelRegistry",
    "ModelArtifact",
    "Incident",
    "IncidentManager",
    "GovernanceRuntime",
]
