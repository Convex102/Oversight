"""
ASGL Flywheel Engine (V1)
========================

Purpose
-------
The Flywheel is the compounding engine that turns *human oversight* into
*machine learning performance improvements* without compromising determinism.

It provides:
 - structured capture of review events (labels, overrides, corrections)
 - dataset building (tenant + global-safe)
 - active learning queues (quiet labels, uncertain mappings)
 - model training triggers (manual / scheduled hooks)
 - telemetry emission (coverage, acceptance rate, drift, queue health)

Design principles
-----------------
 - "Decisions, not raw data": store mappings/labels, not sensitive sheets.
 - Tenant isolation first. Global learning uses anonymised patterns only.
 - Fully auditable: provenance hashes + governance hooks.
 - Fail-safe: flywheel must never break the pipeline.
"""

from .events import FlywheelEvent, FlywheelEventType
from .dataset import DatasetStore
from .queue import ActiveLearningQueue
from .engine import FlywheelEngine
from .trainer import TrainingJob, TrainingScheduler

__all__ = [
    "FlywheelEvent",
    "FlywheelEventType",
    "DatasetStore",
    "ActiveLearningQueue",
    "FlywheelEngine",
    "TrainingJob",
    "TrainingScheduler",
]
