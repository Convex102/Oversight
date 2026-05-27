"""
PATCH: wire_orchestrator_into_engagements.py
=============================================
Wires the ReviewPointOrchestrator into engagements_routes._compare_and_raise
without breaking the existing fallback path.

Idempotent: safe to run multiple times — checks for existing wiring first.

Run from repo root:
  python _patches/wire_orchestrator_into_engagements.py
"""

from __future__ import annotations
import sys
from pathlib import Path


def main():
    repo_root = Path(__file__).parent.parent
    target = repo_root / "app" / "api" / "v1" / "engagements_routes.py"

    if not target.exists():
        print(f"FATAL: {target} not found", file=sys.stderr)
        sys.exit(1)

    src = target.read_text(encoding="utf-8")

    if "from oversight_enhancements.review_point_orchestrator" in src:
        print("OK already wired (orchestrator import present)")
        return

    # The insertion point is the line ending the docstring of _compare_and_raise.
    # We find the unique docstring text used in this function and insert right after.
    insertion_marker = '''    The asgla_compare pipeline uses RapidFuzz retrieval + CrossEncoder reranking
    to semantically match field names across documents regardless of label differences.
    """
    ext_a = path_a.suffix.lower()
    ext_b = path_b.suffix.lower()'''

    if insertion_marker not in src:
        print("ERROR: _compare_and_raise docstring/body marker not found")
        print("  Looking for: docstring close + ext_a/ext_b lines")
        sys.exit(2)

    orchestrator_block = '''    The asgla_compare pipeline uses RapidFuzz retrieval + CrossEncoder reranking
    to semantically match field names across documents regardless of label differences.
    """
    # ── ORCHESTRATOR PATH (preferred) ───────────────────────────────────────
    # If oversight_enhancements is installed, delegate to ReviewPointOrchestrator
    # which adds anchor-field filtering, ratio anomaly detection, and field
    # dependency graph chained-point generation on top of the base pipeline.
    # If anything fails, falls through to the legacy implementation below.
    try:
        from oversight_enhancements.review_point_orchestrator import ReviewPointOrchestrator
        # Build a confirmed_mapping dict from key_fields if supplied
        _confirmed = None
        if key_fields:
            _confirmed = {"fields": [
                {"canonical_role": k, "is_key_field": True} for k in key_fields
            ]}
        _orch = ReviewPointOrchestrator(vertical="accountancy")
        return _orch.run(
            path_a=path_a, path_b=path_b,
            label_a=label_a, label_b=label_b,
            materiality_pct=materiality_pct,
            tmp_path=tmp_path,
            engagement_id=0,
            confirmed_mapping=_confirmed,
            regulatory_pack="frs102",
        )
    except ImportError:
        pass
    except Exception as _orch_err:
        logger.warning("Orchestrator path failed, using legacy compare: %s", _orch_err)

    ext_a = path_a.suffix.lower()
    ext_b = path_b.suffix.lower()'''

    src = src.replace(insertion_marker, orchestrator_block, 1)
    target.write_text(src, encoding="utf-8")
    print(f"PATCHED {target}")
    print("  Orchestrator delegation added at start of _compare_and_raise")
    print("  Legacy path preserved as fallback")


if __name__ == "__main__":
    main()
