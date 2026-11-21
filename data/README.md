# Data

`raw/` holds the four CSV exports exactly as supplied (7 Sep – 15 Nov 2026). They are never modified: the pipeline
reads them as text and writes nothing back. Cleaned data lives only in memory (`src.engine.load_clean()`).

| File | Grain | Rows | Key |
|---|---|---|---|
| `daily_sales.csv` | dark store × platform product × day | 186,285 (765 exact duplicates) | date, dark_store_id, platform_product_id |
| `dark_stores.csv` | dark store | 202 | dark_store_id → warehouse_id, city |
| `products.csv` | platform × product | 45 (15 SKUs × 3 apps) | (platform, platform_product_id) → sku_code |
| `purchase_orders.csv` | PO line = warehouse × SKU × Monday | 2,694 | po_id; (po_date, warehouse_id, sku_code) |

To analyse a new week, either replace these files, pass `--data-dir` to `scripts/generate_reports.py`, or upload the
four files in the app's sidebar.
