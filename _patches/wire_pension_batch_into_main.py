"""
PATCH: wire_pension_batch_into_main.py
========================================
Adds the pension_batch_router to main.py's optional routers list.
"""

from __future__ import annotations
import sys
from pathlib import Path


def main():
    repo_root = Path(__file__).parent.parent
    target = repo_root / "app" / "main.py"

    if not target.exists():
        print(f"FATAL: {target} not found", file=sys.stderr)
        sys.exit(1)

    src = target.read_text(encoding="utf-8")

    if 'app.packs.pensions.pension_batch_task' in src:
        print("OK already wired (pension_batch_task import present)")
        return

    # Find the optional routers list (tuple of module/attr triplets)
    needle = '("app.api.pensions_api",          "router", None),'

    if needle not in src:
        print("ERROR: pensions_api optional router entry not found")
        sys.exit(2)

    # Add the new entry right after the pensions_api entry
    new_entry = needle + '\n    ("app.packs.pensions.pension_batch_task", "pension_batch_router", None),'

    src = src.replace(needle, new_entry, 1)
    target.write_text(src, encoding="utf-8")
    print(f"PATCHED {target}")
    print("  Added pension_batch_router to optional routers list")
    print("  Server will mount: POST /api/v1/pensions/batch-* endpoints")


if __name__ == "__main__":
    main()
