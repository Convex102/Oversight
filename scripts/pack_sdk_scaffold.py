"""Pack SDK scaffold generator (Phase 2 v5)."""

import argparse, json
from pathlib import Path

TEMPLATE = '''from app.services.pack_sdk.types import PackDefinition, PackContext, ValidationIssue

def validator_example(data: dict, ctx: PackContext):
    issues = []
    # issues.append(ValidationIssue(code="EXAMPLE", severity="warning", message="Example warning"))
    return issues

def get_pack(meta=None):
    meta = meta or {}
    return PackDefinition(
        pack_id=meta.get("pack_id"),
        vertical=meta.get("vertical"),
        version=meta.get("version", "0.0.1"),
        title=meta.get("title", meta.get("pack_id")),
        description=meta.get("description", ""),
        schema_fields=list(meta.get("schema_fields", [])),
        validators=[validator_example],
    )
'''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vertical", required=True)
    ap.add_argument("--pack_id", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--version", default="0.0.1")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1] / "packs" / args.vertical / args.pack_id
    root.mkdir(parents=True, exist_ok=True)

    pack_json = {
        "pack_id": args.pack_id,
        "vertical": args.vertical,
        "version": args.version,
        "title": args.title or args.pack_id,
        "description": "Pack description here",
        "schema_fields": ["example.field_1", "example.field_2"]
    }
    (root / "pack.json").write_text(json.dumps(pack_json, indent=2))
    (root / "validators.py").write_text(TEMPLATE)
    print("Created pack at", root)

if __name__ == "__main__":
    main()
