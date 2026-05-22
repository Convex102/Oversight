"""Phase 2 v5.1 smoke test."""

import argparse, os

from app.services.active_learning import enqueue_review_item, list_open_items, resolve_item
from app.services.active_overrides import add_override, apply_overrides

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant_id", default="test")
    args = ap.parse_args()

    os.environ["ASGL_ACTIVE_LEARNING_ENABLE"] = "1"
    os.environ["ASGL_OVERRIDES_ENABLE"] = "1"

    item_id = enqueue_review_item(
        tenant_id=args.tenant_id,
        source_column="VAT Rate",
        column_text="header: VAT Rate\nsamples: \ntype: numeric",
        candidates=[{"canonical_field":"canonical.vat_rate","score":0.42}],
        workbook="demo.xlsx",
        sheet="Sheet1",
    )
    assert item_id, "enqueue failed"
    items = list_open_items(args.tenant_id, limit=10)
    assert any(i.get("id") == item_id for i in items), "item not found"

    ok = resolve_item(
        tenant_id=args.tenant_id,
        item_id=item_id,
        chosen_canonical_field="canonical.vat_rate",
        reviewer="smoke",
    )
    assert ok, "resolve failed"

    add_override(
        tenant_id=args.tenant_id,
        source_column="VAT Rate",
        canonical_field="canonical.vat_rate",
        workbook="demo.xlsx",
        sheet="Sheet1",
    )
    ov = apply_overrides(
        tenant_id=args.tenant_id,
        headers=["VAT Rate", "Balance"],
        workbook="demo.xlsx",
        sheet="Sheet1",
    )
    assert ov.get("canonical.vat_rate") == "VAT Rate", "override apply failed"
    print("OK: phase2 v5.1 smoketest passed")

if __name__ == "__main__":
    main()
