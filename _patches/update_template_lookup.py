"""
PATCH: update_template_lookup.py
==================================
Makes main.py prefer Command_Center_v13.html (with Oversight-P UI).
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

    if 'Command_Center_v13.html' in src:
        print("OK already updated to v13")
        return

    needle = 'for name in ["Command_Center_v9.html", "templates/Command Center.html", "Command Center.html", "index.html"]:'
    replacement = 'for name in ["Command_Center_v13.html", "Command_Center_v12.html", "Command_Center_v11.html", "Command_Center_v9.html", "templates/Command Center.html", "Command Center.html", "index.html"]:'

    if needle not in src:
        print("ERROR: template lookup line not found")
        sys.exit(2)

    src = src.replace(needle, replacement, 1)
    target.write_text(src, encoding="utf-8")
    print(f"PATCHED {target}")
    print("  Template lookup now prefers Command_Center_v13.html")


if __name__ == "__main__":
    main()
