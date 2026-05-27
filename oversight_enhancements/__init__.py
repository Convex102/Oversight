"""
OVERSIGHT LABS — Enhancement Package
=====================================
SOTA enhancements to the ASGL-A review point pipeline.

Modules:
  field_dependency_graph    — Accounting identity DAG with flywheel learning
  ratio_anomaly_detector    — Directional/proportional ratio checks with BOCPD/Hurst
  review_point_orchestrator — Full pipeline integration layer
  multi_ocr_pdf_ingest      — 4-strategy PDF extraction with table support
  flywheel_resolution_capture — Structured resolution quality capture

Import order matters:
  field_dependency_graph and ratio_anomaly_detector are standalone.
  review_point_orchestrator imports both.
  flywheel_resolution_capture imports field_dependency_graph.
"""

__version__ = "1.0.0"
__all__ = [
    "FieldDependencyGraph",
    "RatioAnomalyDetector",
    "ReviewPointOrchestrator",
    "extract_pdf_kv",
    "capture_resolution",
]
