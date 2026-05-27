"""
APPLY ALL PATCHES
==================
Runs every patch script in the correct order.

Usage from repo root:
  python _patches/apply_all.py

Re-runnable: each patch is idempotent.
"""

from __future__ import annotations
import importlib.util
import sys
from pathlib import Path


PATCHES = [
    "fix_compare_routes_math_import.py",
    "wire_orchestrator_into_engagements.py",
    "wire_pension_batch_into_pensions_api.py",
    "wire_pension_batch_into_main.py",
    "update_template_lookup.py",
]


def run_patch(patch_path: Path) -> bool:
    print(f"\n--- {patch_path.name} ---")
    spec = importlib.util.spec_from_file_location("patch_module", patch_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        if hasattr(mod, "main"):
            mod.main()
        return True
    except SystemExit as e:
        if e.code != 0:
            print(f"FAILED with exit code {e.code}")
            return False
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    patches_dir = Path(__file__).parent
    print("OVERSIGHT PATCH APPLICATION")
    print("===========================")

    results = []
    for name in PATCHES:
        path = patches_dir / name
        if not path.exists():
            print(f"\nSKIPPING {name} (not found)")
            results.append((name, False))
            continue
        results.append((name, run_patch(path)))

    print("\n\n=== SUMMARY ===")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print(f"  {'OK' if ok else 'FAIL':6} {name}")
    print(f"\n{passed}/{total} patches applied successfully")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()