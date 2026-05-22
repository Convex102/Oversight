"""Phase 2 v6 demo flow."""

import argparse, os
from app.services.active_learning import enqueue_review_item, list_open_items, resolve_item
from app.services.active_overrides import apply_overrides

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant_id", default="test")
    args = ap.parse_args()
    os.environ["ASGL_ACTIVE_LEARNING_ENABLE"] = "1"
    os.environ["ASGL_OVERRIDES_ENABLE"] = "1"
    os.environ["ASGL_TEMPLATE_AUTOLEARN_ENABLE"] = "1"

    enqueue_review_item(
        tenant_id=args.tenant_id,
        source_column="Account Code",
        column_text="header: Account Code\ntype: text",
        candidates=[{"canonical_field":"canonical.account_code","score":0.33}],
        workbook="demo.xlsx",
        sheet="Sheet1",
    )

    items = list_open_items(args.tenant_id, limit=10)
    print("Open items:", len(items))
    if items:
        it = items[0]
        chosen = (it.get("candidates") or [])[0].get("canonical_field")
        ok = resolve_item(
            tenant_id=args.tenant_id,
            item_id=it["id"],
            chosen_canonical_field=chosen,
            reviewer="demo",
        )
        print("Resolved:", ok)
        ov = apply_overrides(tenant_id=args.tenant_id, headers=[it.get("source_column")], workbook=it.get("workbook"), sheet=it.get("sheet"))
        print("Overrides now:", ov)

if __name__ == "__main__":
    main()
